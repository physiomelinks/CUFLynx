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
  it('offers each model once and the actions, as a box to drag onto the chart', async () => {
    const prep = mount(WorkflowStepPrep, { props: { workflow, stepKinds: ['calibration', 'set'] } })
    const models = prep.find('[data-testid="workflow-prep-models"]').findAll('option').slice(1)
    expect(models.map((o) => o.text())).toEqual([expect.stringContaining('enterocyte'), expect.stringContaining('i_K1')])
    // the combine step needs a newer CA
    const actions = prep.find('[data-testid="workflow-prep-actions"]').findAll('option').slice(1)
    expect(actions.map((o) => [o.element.value, o.element.disabled])).toEqual([['combine', true], ['set', false]])
    expect(prep.find('[data-testid="workflow-prep-box"]').exists()).toBe(false)

    await prep.find('[data-testid="workflow-prep-models"]').setValue(models[1].element.value)
    const box = prep.find('[data-testid="workflow-prep-box"]')
    expect(box.text()).toContain('used by kir, kir_again')
    const data = { setData: vi.fn(), effectAllowed: '' }
    await box.trigger('dragstart', { dataTransfer: data })
    expect(data.setData).toHaveBeenCalledWith('application/x-cuflynx-new-step', JSON.stringify({ model: KIR }))

    // choosing an action replaces the model's box
    await prep.find('[data-testid="workflow-prep-actions"]').setValue('set')
    expect(prep.find('[data-testid="workflow-prep-models"]').element.value).toBe('')
    await prep.find('[data-testid="workflow-prep-place"]').trigger('click')
    expect(prep.emitted('place')).toEqual([[{ action: 'set' }]])
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
