import { describe, it, expect, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'

vi.mock('../lib/api', () => ({
  exportWorkflow: vi.fn(async () => ({
    filename: 'chain_analysed.omex', download_url: '/api/phlynx/archive/t/chain_analysed.omex',
    member_count: 31,
  })),
  errorMessage: (e) => String(e?.message || e),
}))

import WorkflowPanel from './WorkflowPanel.vue'
import { exportWorkflow } from '../lib/api'

const stubs = {
  Message: { template: '<div class="msg" v-bind="$attrs"><slot /></div>' },
  Button: {
    props: ['label', 'disabled'],
    emits: ['click'],
    template: '<button v-bind="$attrs" :disabled="disabled" @click="$emit(\'click\')">{{ label }}</button>',
  },
  InputText: {
    props: ['modelValue', 'size'],
    emits: ['update:modelValue', 'keyup'],
    template: '<input v-bind="$attrs" :value="modelValue" @input="$emit(\'update:modelValue\', $event.target.value)" />',
  },
  InputNumber: true,
  FileBrowserDialog: true,
}

const tabs = [
  { id: 'fit_a', kind: 'step', label: 'fit_a', detail: 'lin_a · fit_a', place: 'A',
    calibrates: [{ target_parameterisation_name: 'p_A' }], fixedFrom: [], priorsFrom: [] },
  { id: 'rest', kind: 'step', label: 'rest', detail: 'chain · rest', place: '',
    calibrates: [{ target_parameterisation_name: 'q_C' }], fixedFrom: [{ from_step: 'fit_a' }],
    priorsFrom: [] },
  { id: 'target', kind: 'target', label: 'chain', detail: '' },
]
const described = {
  loaded: true, path: '/w/calibration_workflow.json', output_dir: '/out',
  workflow: { workflow_name: 'chain_rest', description: 'A, then C' },
  plan: {},
}

const mountPanel = (props) => mount(WorkflowPanel, { props, global: { stubs } })

describe('WorkflowPanel', () => {
  it('shows each step\'s kind and sources, and opens no tab for a reduction', async () => {
    const analysed = [
      { id: 'screen', kind: 'step', stepKind: 'sensitivity', viewable: true, label: 'screen',
        detail: 'chain · screened', calibrates: [], fixedFrom: [], priorsFrom: [], sources: [] },
      { id: 'reduce', kind: 'step', stepKind: 'reduction', viewable: false, label: 'reduce',
        detail: 'keeps the top 1 by Sobol ST', calibrates: [], fixedFrom: [], priorsFrom: [],
        sources: [{ key: 'sensitivity_from', step: 'screen' }] },
      { id: 'target', kind: 'target', label: 'chain', detail: '' },
    ]
    const panel = mountPanel({ described, tabs: analysed })
    expect(panel.find('[data-testid="workflow-kind-screen"]').text()).toBe('sensitivity analysis')
    const reduce = panel.find('[data-testid="workflow-step-reduce"]')
    expect(reduce.text()).toContain('ranks the parameters of screen')
    await reduce.trigger('click')
    expect(panel.emitted('select')).toBeUndefined()
    await panel.find('[data-testid="workflow-step-screen"]').trigger('click')
    expect(panel.emitted('select')).toEqual([['screen']])
  })

  it('exports the workflow as an archive, offered as a plain link', async () => {
    const panel = mountPanel({ described, tabs, outputsDir: '/out' })
    await panel.find('[data-testid="workflow-export"]').trigger('click')
    await flushPromises()
    expect(exportWorkflow).toHaveBeenCalledWith('/out')
    const link = panel.find('[data-testid="workflow-exported"] a')
    expect(link.attributes('href')).toBe('/api/phlynx/archive/t/chain_analysed.omex')
    expect(link.attributes('download')).toBe('chain_analysed.omex')
    expect(panel.find('[data-testid="workflow-exported"]').text()).toContain('31 files')
  })

  it('opens a workflow by path, and says when no library is set', async () => {
    const panel = mountPanel({ described: { loaded: false }, moduleLibraryDirs: [] })
    expect(panel.find('[data-testid="workflow-open-settings"]').exists()).toBe(true)
    await panel.find('[data-testid="workflow-path"]').setValue(' /w/calibration_workflow.json ')
    await panel.find('[data-testid="workflow-open"]').trigger('click')
    expect(panel.emitted('open')).toEqual([[{ path: '/w/calibration_workflow.json' }]])
  })

  it('offers the workflow an imported archive carried', async () => {
    const panel = mountPanel({
      described: { loaded: false }, moduleLibraryDirs: ['/lib'],
      offered: { filename: 'calibration_workflow.json', path: '/up/calibration_workflow.json' },
    })
    await panel.find('[data-testid="workflow-open-offered"]').trigger('click')
    expect(panel.emitted('open')).toEqual([[{ path: '/up/calibration_workflow.json' }]])
  })

  it('lists the steps with what each calibrates and is given', () => {
    const panel = mountPanel({ described, tabs, states: { fit_a: 'done', rest: 'stale' } })
    const rest = panel.find('[data-testid="workflow-step-rest"]')
    expect(rest.text()).toContain('calibrates q_C')
    expect(rest.text()).toContain('fixed from fit_a')
    expect(rest.text()).toContain('stale')
    expect(panel.find('[data-testid="workflow-step-fit_a"]').text()).toContain('→ A in the target')
  })

  it('runs all, from the selected step, or only it', async () => {
    const panel = mountPanel({ described, tabs, selected: 'rest' })
    await panel.find('[data-testid="workflow-run"]').trigger('click')
    await panel.find('[data-testid="workflow-run-from"]').trigger('click')
    await panel.find('[data-testid="workflow-run-only"]').trigger('click')
    expect(panel.emitted('run').map(([w]) => w)).toEqual([
      { numCores: 1 }, { fromStep: 'rest', numCores: 1 }, { only: 'rest', numCores: 1 },
    ])
  })

  it('cannot run a workflow CA could not resolve, and shows why', () => {
    const panel = mountPanel({ described: { ...described, plan_error: 'no lin_a' }, tabs })
    expect(panel.find('[data-testid="workflow-plan-error"]').text()).toContain('no lin_a')
    expect(panel.find('[data-testid="workflow-run"]').attributes('disabled')).toBeDefined()
  })

  it('shows progress and lets a run be cancelled', async () => {
    const panel = mountPanel({ described, tabs, jobState: 'running', currentStep: 'rest' })
    expect(panel.find('[data-testid="workflow-progress"]').text()).toContain('rest (2 of 2)')
    expect(panel.find('[data-testid="workflow-run"]').attributes('disabled')).toBeDefined()
    await panel.find('[data-testid="workflow-cancel"]').trigger('click')
    expect(panel.emitted('cancel')).toHaveLength(1)
  })

  it('shows what the open tab was given and found', () => {
    const panel = mountPanel({
      described, tabs, selected: 'rest',
      view: {
        view: 'rest', kind: 'step', model: { module_type: 'chain', version: 'v1', parameterisation: 'rest' },
        fixed: [{ model_name: 'p_mod_A', value: 2, from_step: 'fit_a' }],
        calibrated: [{ model_name: 'q_mod_C', value: 3 }],
        waiting_for: [], stale: ['fit_a'],
      },
    })
    const fixed = panel.find('[data-testid="workflow-fixed"]').text()
    expect(fixed).toContain('p_mod_A')
    expect(fixed).toContain('fit_a')
    expect(panel.find('[data-testid="workflow-calibrated"]').text()).toContain('q_mod_C')
    expect(panel.text()).toContain('Inputs changed since fit_a ran')
  })
})

describe('WorkflowPanel: selection steps', () => {
  const selectionTabs = [
    { id: 'left', kind: 'step', label: 'left', detail: 'selection · C', place: '',
      selection: 'runs as lin · base at C', missing: ['params_for_id'],
      calibrates: [], fixedFrom: [], priorsFrom: [] },
    { id: 'right', kind: 'step', label: 'right', detail: 'selection · D, E', place: '',
      selection: 'runs as the module array of D, E', missing: ['obs_data'],
      calibrates: [], fixedFrom: [], priorsFrom: [] },
    { id: 'target', kind: 'target', label: 'whole', detail: '' },
  ]

  it('says what each selection runs as', () => {
    const wrapper = mountPanel({ described, tabs: selectionTabs })
    expect(wrapper.find('[data-testid="workflow-selection-left"]').text()).toBe('runs as lin · base at C')
    expect(wrapper.find('[data-testid="workflow-selection-right"]').text()).toContain('D, E')
  })

  it("asks for a step's unset parameters in its protocol", async () => {
    const wrapper = mountPanel({ described, tabs: selectionTabs,
                                 unset: { step_id: 'left', names: ['C/x', 'C/y'] } })
    expect(wrapper.find('[data-testid="workflow-unset"]').text()).toContain('C/x, C/y')
    await wrapper.find('[data-testid="workflow-unset-edit"]').trigger('click')
    expect(wrapper.emitted('edit-input')[0][0]).toEqual(
      { stepId: 'left', role: 'obs_data', preselect: ['C/x', 'C/y'] })
  })

  it('offers a step without an input to create or open one, without opening its tab', async () => {
    const wrapper = mountPanel({ described, tabs: selectionTabs })
    expect(wrapper.find('[data-testid="workflow-missing-left-params_for_id"]').exists()).toBe(true)
    // params_for_id comes from a file; obs_data can also be made in the protocol editor
    expect(wrapper.find('[data-testid="workflow-create-left-params_for_id"]').exists()).toBe(false)
    await wrapper.find('[data-testid="workflow-create-right-obs_data"]').trigger('click')
    expect(wrapper.emitted('edit-input')[0][0]).toEqual({ stepId: 'right', role: 'obs_data', preselect: [] })
    expect(wrapper.emitted('select')).toBeUndefined()

    await wrapper.find('[data-testid="workflow-pick-left-params_for_id"]').trigger('click')
    const input = wrapper.find('[data-testid="workflow-input-file"]')
    // JSON is the form CA reads; a CSV is still offered, and converted by the server
    expect(input.attributes('accept').split(',')).toEqual(
      expect.arrayContaining(['.json', '.csv']))
    const file = new File(['vessel_name,param_name\n'], 'p.csv', { type: 'text/csv' })
    Object.defineProperty(input.element, 'files', { value: [file], configurable: true })
    await input.trigger('change')
    expect(wrapper.emitted('pick-input')[0][0]).toEqual({ stepId: 'left', role: 'params_for_id', file })
    expect(wrapper.emitted('select')).toBeUndefined()
  })
})
