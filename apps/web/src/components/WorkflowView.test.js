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
