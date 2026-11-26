import { describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import WorkflowStepPrep from './WorkflowStepPrep.vue'
import WorkflowDraftChoice from './WorkflowDraftChoice.vue'
import WorkflowSetValuesForm from './WorkflowSetValuesForm.vue'

const DialogStub = {
  props: ['visible', 'header'],
  template: '<div v-if="visible"><h2>{{ header }}</h2><slot /><slot name="footer" /></div>',
}
const ButtonStub = {
  props: ['label', 'disabled'],
  emits: ['click'],
  template: '<button v-bind="$attrs" :disabled="disabled" @click="$emit(\'click\')">{{ label }}</button>',
}
const stubs = { Dialog: DialogStub, Button: ButtonStub }

const KIR = { module_type: 'i_K1', version: 'v1', parameterisation: 'krapivinsky1998' }
const workflow = {
  target: { module_array: 'enterocyte_module_array.csv' },
  steps: [
    { id: 'kir', model: KIR },
    { id: 'kir_again', model: KIR },
    { id: 'whole_cell' },
    { id: 'energy_cost', kind: 'sweep', on_target: true },
  ],
}

describe('WorkflowStepPrep', () => {
  it('drop-downs drawn as the boxes they give; the chosen box drags from the drop-down', async () => {
    const prep = mount(WorkflowStepPrep, { props: { workflow, stepKinds: ['calibration', 'set'] },
      attachTo: document.body })
    const models = prep.find('[data-testid="workflow-prep-models"]')
    expect(models.text()).toContain('choose a model')
    expect(models.attributes('draggable')).toBe('false')
    await models.trigger('click')
    const options = prep.findAll('[data-testid="workflow-prep-models-list"] .sp-option')
    // each model once, as the chart names it
    expect(options.map((o) => o.find('.sp-title').text())).toEqual(['enterocyte_module_array.csv', 'i_K1 · krapivinsky1998'])
    expect(options[1].text()).toContain('used by kir, kir_again')
    await options[1].trigger('click')
    expect(prep.find('[data-testid="workflow-prep-models-list"]').exists()).toBe(false)
    expect(models.text()).toContain('i_K1 · krapivinsky1998')
    expect(models.attributes('draggable')).toBe('true')
    const data = { setData: vi.fn(), effectAllowed: '' }
    await models.trigger('dragstart', { dataTransfer: data })
    expect(data.setData).toHaveBeenCalledWith('application/x-cuflynx-new-step', JSON.stringify({ model: KIR }))

    // the combine step needs a newer CA: shown, and not chosen
    const actions = prep.find('[data-testid="workflow-prep-actions"]')
    await actions.trigger('click')
    const combine = prep.find('[data-testid="workflow-prep-option-action:combine"]')
    expect(combine.classes()).toContain('off')
    await combine.trigger('click')
    expect(models.text()).toContain('i_K1')
    // choosing an action replaces the model's box
    await prep.find('[data-testid="workflow-prep-option-action:set"]').trigger('click')
    expect(actions.text()).toContain('Set values by hand')
    expect(actions.classes()).toContain('set')
    expect(models.text()).toContain('choose a model')
    await prep.find('[data-testid="workflow-prep-place"]').trigger('click')
    expect(prep.emitted('place')).toEqual([[{ action: 'set' }]])
    // a box in the open list drags straight onto the chart, and becomes the one chosen
    await models.trigger('click')
    const drag = { setData: vi.fn(), effectAllowed: '' }
    await prep.find('[data-testid="workflow-prep-models-list"] .sp-option').trigger('dragstart', { dataTransfer: drag })
    expect(drag.setData).toHaveBeenCalledWith('application/x-cuflynx-new-step',
      JSON.stringify({ model: workflow.target }))
    expect(models.text()).toContain('enterocyte_module_array.csv')
    await new Promise((r) => setTimeout(r, 0))
    // an action this CA cannot make does not drag
    await actions.trigger('click')
    expect(prep.find('[data-testid="workflow-prep-option-action:combine"]').attributes('draggable')).toBe('false')
    // nor a figure box, without CA's figure step
    expect(prep.find('[data-testid="workflow-prep-option-action:figure"]').classes()).toContain('off')
    // Escape closes an open drop-down
    await models.trigger('click')
    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }))
    await prep.vm.$nextTick()
    expect(prep.find('[data-testid="workflow-prep-models-list"]').exists()).toBe(false)
    prep.unmount()
  })

  it('offers a Draw figures box where CA has the figure step', async () => {
    const prep = mount(WorkflowStepPrep, { props: { workflow: { target: 'm.cellml', steps: [] },
      stepKinds: ['calibration', 'figure'] }, attachTo: document.body })
    await prep.find('[data-testid="workflow-prep-actions"]').trigger('click')
    const figure = prep.find('[data-testid="workflow-prep-option-action:figure"]')
    expect(figure.classes()).not.toContain('off')
    await figure.trigger('click')
    await prep.find('[data-testid="workflow-prep-place"]').trigger('click')
    expect(prep.emitted('place')).toEqual([[{ action: 'figure' }]])
    prep.unmount()
  })
})

describe('WorkflowDraftChoice', () => {
  it("offers the tabs that make steps, a simulation, and choosing parameters (waiting for CA)", async () => {
    const c = mount(WorkflowDraftChoice, { props: { draft: { id: 'new-1', model: KIR },
      panels: ['calibration', 'uq', 'nope'], modelWords: 'i_K1 · krapivinsky1998' }, global: { stubs } })
    expect(c.text()).toContain('What should new-1 do?')
    expect(c.find('[data-testid="workflow-draft-choice-nope"]').exists()).toBe(false)
    await c.find('[data-testid="workflow-draft-choice-uq"]').trigger('click')
    await c.find('[data-testid="workflow-draft-choice-simulation"]').trigger('click')
    expect(c.emitted('choose')).toEqual([[{ panel: 'uq' }], [{ simulation: true }]])
    expect(c.find('.dc-item.hand').attributes('disabled')).toBeDefined()
  })
})

describe('WorkflowSetValuesForm', () => {
  it('sets each value with its reason', async () => {
    const save = vi.fn().mockResolvedValue('')
    const f = mount(WorkflowSetValuesForm, { props: { draft: 'new-4', save }, global: { stubs } })
    const row = f.find('[data-testid="workflow-set-row-0"]')
    const [name, value, reason] = row.findAll('input')
    await name.setValue('Kir/K_o')
    await value.setValue(5.4)
    await f.find('[data-testid="workflow-set-save"]').trigger('click')
    expect(f.find('[data-testid="workflow-set-error"]').text()).toContain('say why')
    expect(save).not.toHaveBeenCalled()
    await reason.setValue('extracellular K+, Smith 2001')
    await f.find('[data-testid="workflow-set-point"]').setValue(true)
    await f.find('[data-testid="workflow-set-save"]').trigger('click')
    await flushPromises()
    expect(save).toHaveBeenCalledWith({ draft: 'new-4', point: true, entries: [
      { parameter: 'Kir/K_o', value: 5.4, reason: 'extracellular K+, Smith 2001' }] })
    expect(f.emitted('close')).toHaveLength(1)
  })
})
