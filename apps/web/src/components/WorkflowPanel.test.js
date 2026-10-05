import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import WorkflowPanel from './WorkflowPanel.vue'

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
  { id: 'fit_a', kind: 'step', label: 'fit_a', detail: 'lin_a · fit_a', submodulePath: 'A',
    calibrates: [{ target_instance_name: 'p_A' }], fixedFrom: [], priorsFrom: [] },
  { id: 'rest', kind: 'step', label: 'rest', detail: 'chain · rest', submodulePath: '',
    calibrates: [{ target_instance_name: 'q_C' }], fixedFrom: [{ from_step: 'fit_a' }],
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
    expect(panel.find('[data-testid="workflow-step-fit_a"]').text()).toContain('submodule A')
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
        view: 'rest', kind: 'step', target: { module_type: 'chain', version: 'v1', instance: 'rest' },
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
