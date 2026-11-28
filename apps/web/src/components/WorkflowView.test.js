import { describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import WorkflowView from './WorkflowView.vue'

const ButtonStub = {
  props: ['label', 'disabled'],
  emits: ['click'],
  template: '<button v-bind="$attrs" :disabled="disabled" @click="$emit(\'click\')">{{ label }}</button>',
}
const stubs = {
  WorkflowPanel: { template: '<div><slot name="header" /><slot name="steps" /></div>' },
  WorkflowDiagram: true,
  WorkflowEdgeDetail: true,
  WorkflowStepDetail: true,
  WorkflowLinkEditor: true,
  WorkflowDraftChoice: true,
  WorkflowSetValuesForm: true,
  WorkflowStepPrep: true,
  Select: true,
  Message: { template: '<div v-bind="$attrs"><slot /></div>' },
  Button: ButtonStub,
}

function view(props) {
  return mount(WorkflowView, { props: { target: { mode: 'default' }, ...props }, global: { stubs } })
}

describe('WorkflowView: a simulation waiting for its obs_data', () => {
  it('says what varies, and adds the simulation once the study has an obs_data', async () => {
    const makeSweep = vi.fn().mockResolvedValue('')
    const v = view({ simulationDraft: 'new-2', studyHasObs: false, editor: { makeSweep } })
    const bar = v.find('[data-testid="workflow-simulation-waiting"]')
    expect(bar.text()).toContain('params_to_change')
    expect(bar.text()).toContain('Give it an obs_data')
    expect(v.find('[data-testid="workflow-simulation-add"]').attributes('disabled')).toBeDefined()
    await v.setProps({ studyHasObs: true })
    await v.find('[data-testid="workflow-simulation-add"]').trigger('click')
    await flushPromises()
    // the step names no parameters of its own: the obs_data's protocol says what varies
    expect(makeSweep).toHaveBeenCalledWith({ draft: 'new-2' })
    expect(v.emitted('simulation-made')).toEqual([[]])
  })

  it("shows the server's reason when the simulation is refused", async () => {
    const makeSweep = vi.fn().mockResolvedValue('a simulation needs a protocol')
    const v = view({ simulationDraft: 'new-2', studyHasObs: true, editor: { makeSweep } })
    await v.find('[data-testid="workflow-simulation-add"]').trigger('click')
    await flushPromises()
    expect(v.find('[data-testid="workflow-edit-error"]').text()).toContain('needs a protocol')
    expect(v.emitted('simulation-made')).toBeUndefined()
  })
})

describe('WorkflowView: the boxes of values by origin', () => {
  const box = (id, title, names) => ({ id, title, mark: id, to: ['model'],
    values: names.map((name) => ({ name, value: 1, units: '', note: '', calibrated_by: [] })) })

  it('keeps the opened box open as an edit moves its values, and records through the editor', async () => {
    const setValueOrigin = vi.fn().mockResolvedValue('')
    const kinds = ['measured', 'set_by_hand']
    const v = view({ editor: { setValueOrigin }, origins: { editable: true, origins: kinds, boxes: [
      box('unrecorded', 'Origin not recorded', ['m/a', 'm/b'])] } })
    v.findComponent({ name: 'WorkflowDiagram' }).vm.$emit('origin', 'unrecorded')
    await flushPromises()
    const detail = () => v.findComponent({ name: 'WorkflowEdgeDetail' })
    expect(detail().props('originKinds')).toEqual(kinds)
    await detail().props('saveOrigin')({ name: 'm/a', origin: 'set_by_hand' })
    expect(setValueOrigin).toHaveBeenCalledWith({ name: 'm/a', origin: 'set_by_hand' })
    // described again: m/a in its new box, the opened box read again by its id
    await v.setProps({ origins: { editable: true, origins: kinds, boxes: [
      box('set_by_hand', 'Values set by hand', ['m/a']), box('unrecorded', 'Origin not recorded', ['m/b'])] } })
    expect(detail().props('origin').values.map((r) => r.name)).toEqual(['m/b'])
    expect(detail().props('boxes').map((b) => b.id)).toEqual(['set_by_hand', 'unrecorded'])
    // its last value moved too: the box stays open, empty
    await v.setProps({ origins: { editable: true, origins: kinds, boxes: [
      box('set_by_hand', 'Values set by hand', ['m/a', 'm/b'])] } })
    expect(detail().props('origin')).toMatchObject({ id: 'unrecorded', title: 'Origin not recorded', values: [] })
    detail().vm.$emit('open-origin', 'set_by_hand')
    await flushPromises()
    expect(detail().props('origin').id).toBe('set_by_hand')
  })

  it('offers no edit when the target has no parameters file', async () => {
    const v = view({ editor: {}, origins: { editable: false, boxes: [box('unrecorded', 'Origin not recorded', ['m/a'])] } })
    v.findComponent({ name: 'WorkflowDiagram' }).vm.$emit('origin', 'unrecorded')
    await flushPromises()
    expect(v.findComponent({ name: 'WorkflowEdgeDetail' }).props('originKinds')).toEqual([])
  })
})
