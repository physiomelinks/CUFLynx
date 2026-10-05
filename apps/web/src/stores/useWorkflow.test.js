import { describe, it, expect, vi, beforeEach } from 'vitest'

vi.mock('../lib/api', () => ({
  getWorkflow: vi.fn(),
  loadWorkflow: vi.fn(),
  uploadWorkflow: vi.fn(),
  closeWorkflow: vi.fn(),
  openWorkflowView: vi.fn(),
  runWorkflow: vi.fn(),
  getWorkflowStatus: vi.fn(),
  cancelWorkflow: vi.fn(),
  errorMessage: (e) => e?.response?.data?.detail || String(e?.message || e),
}))

import {
  getWorkflow,
  getWorkflowStatus,
  loadWorkflow,
  openWorkflowView,
  runWorkflow,
} from '../lib/api'
import { TARGET_VIEW, stepState, useWorkflow, workflowTabs } from './useWorkflow'

const PLAN = {
  workflow_name: 'chain_rest',
  target: { module_type: 'chain', version: 'v1', instance: 'rest' },
  steps: [
    {
      id: 'fit_a', target: { module_type: 'lin_a', version: 'v1', instance: 'fit_a' },
      submodule_path: 'A',
      calibrates: [{ model_name: 'p_mod', target_instance_name: 'p_A' }],
      fixed_from: [], priors_from: [],
    },
    {
      id: 'rest', target: { module_type: 'chain', version: 'v1', instance: 'rest' },
      submodule_path: '',
      calibrates: [{ model_name: 'q_mod_C', target_instance_name: 'q_C' }],
      fixed_from: [{ from_step: 'fit_a', model_name: 'p_mod', in_step: 'p_mod_A' }],
      priors_from: [],
    },
  ],
}
const DESCRIBED = {
  loaded: true, path: '/w/calibration_workflow.json', output_dir: '/out',
  workflow: { workflow_name: 'chain_rest', steps: [{ id: 'fit_a' }, { id: 'rest' }] },
  plan: PLAN,
  status: { steps: { fit_a: { status: 'done', stale: false },
                     rest: { status: 'done', stale: true } } },
}

beforeEach(() => {
  vi.clearAllMocks()
})

describe('workflowTabs', () => {
  it('gives one tab per step, in CA order, then the supermodule', () => {
    const tabs = workflowTabs(DESCRIBED)
    expect(tabs.map((t) => t.id)).toEqual(['fit_a', 'rest', TARGET_VIEW])
    expect(tabs[0]).toMatchObject({ kind: 'step', submodulePath: 'A', detail: 'lin_a · fit_a' })
    expect(tabs[2]).toMatchObject({ kind: 'target', label: 'chain' })
    expect(tabs[1].fixedFrom[0].from_step).toBe('fit_a')
  })

  it('still shows the steps of a workflow CA could not resolve', () => {
    const tabs = workflowTabs({
      loaded: true,
      plan_error: 'no module library',
      workflow: {
        workflow_name: 'w',
        target: { module_type: 'soma', version: 's', instance: 'rest_balance' },
        steps: [{ id: 'a', target: { module_type: 'i_M', instance: 'x' }, fixed_from: [],
                  priors_from: ['b'] },
                { id: 'b', target: { module_type: 'soma', instance: 'rest_balance' },
                  fixed_from: ['a'] }],
      },
    })
    expect(tabs.map((t) => t.id)).toEqual(['a', 'b', TARGET_VIEW])
    expect(tabs[1].fixedFrom).toEqual([{ from_step: 'a' }])
    expect(tabs[0].priorsFrom).toEqual([{ step: 'b', kind: 'mvnormal' }])
    expect(tabs[2].label).toBe('soma')
  })

  it('has no tabs with no workflow open', () => {
    expect(workflowTabs({ loaded: false })).toEqual([])
  })
})

describe('stepState', () => {
  it('prefers the live run, then CA\'s record, marking stale results', () => {
    expect(stepState(DESCRIBED, {}, 'fit_a')).toBe('done')
    expect(stepState(DESCRIBED, {}, 'rest')).toBe('stale')
    expect(stepState(DESCRIBED, { rest: 'running' }, 'rest')).toBe('running')
    // a step the run reused shows what CA recorded for it
    expect(stepState(DESCRIBED, { fit_a: 'reused' }, 'fit_a')).toBe('done')
    expect(stepState({ loaded: true }, {}, 'fit_a')).toBe('not_run')
  })
})

describe('useWorkflow', () => {
  it('opens a workflow and reports CA\'s refusal verbatim', async () => {
    loadWorkflow.mockResolvedValueOnce(DESCRIBED)
    const wf = useWorkflow()
    await wf.open({ path: '/w/calibration_workflow.json', outputsDir: '/o' })
    expect(loadWorkflow).toHaveBeenCalledWith(
      { path: '/w/calibration_workflow.json', runDir: '', outputsDir: '/o' })
    expect(wf.loaded.value).toBe(true)
    expect(wf.name.value).toBe('chain_rest')
    expect(wf.tabs.value).toHaveLength(3)

    loadWorkflow.mockRejectedValueOnce({ response: { data: { detail: 'step "x" is wrong' } } })
    expect(await wf.open({ path: '/bad' })).toBeNull()
    expect(wf.error.value).toBe('step "x" is wrong')
    expect(wf.loaded.value).toBe(true) // the open one stays open
  })

  it('selecting a tab keeps the view the server returned', async () => {
    openWorkflowView.mockResolvedValueOnce({
      model_id: 'm1', workflow_view: { view: 'rest', fixed: [{ model_name: 'p_mod_A' }] },
    })
    const wf = useWorkflow()
    const result = await wf.select('rest')
    expect(result.model_id).toBe('m1')
    expect(wf.selected.value).toBe('rest')
    expect(wf.view.value.fixed[0].model_name).toBe('p_mod_A')
  })

  it('runs, follows each step, and re-reads CA\'s record when done', async () => {
    vi.useFakeTimers()
    runWorkflow.mockResolvedValueOnce({ job_id: 'j1' })
    getWorkflowStatus
      .mockResolvedValueOnce({ state: 'running', lines: ['a'], next_offset: 1,
                               step_states: { fit_a: 'running', rest: 'queued' },
                               current_step: 'fit_a' })
      .mockResolvedValueOnce({ state: 'done', lines: ['b'], next_offset: 2,
                               step_states: { fit_a: 'done', rest: 'done' },
                               current_step: null })
    getWorkflow.mockResolvedValueOnce(DESCRIBED)
    const wf = useWorkflow({ intervalMs: 10 })
    await wf.run({ fromStep: 'fit_a', numCores: 2 })
    expect(runWorkflow).toHaveBeenCalledWith({ fromStep: 'fit_a', only: '', numCores: 2 })
    expect(wf.running.value).toBe(true)
    expect(wf.currentStep.value).toBe('fit_a')
    expect(wf.stateOf('rest')).toBe('queued')
    await vi.advanceTimersByTimeAsync(20)
    expect(wf.jobState.value).toBe('done')
    expect(wf.lines.value).toEqual(['a', 'b'])
    expect(getWorkflow).toHaveBeenCalled()
    vi.useRealTimers()
  })

  it('a refused run is an error, not a hang', async () => {
    runWorkflow.mockRejectedValueOnce({ response: { data: { detail: 'a calibration is running' } } })
    const wf = useWorkflow()
    await wf.run()
    expect(wf.jobState.value).toBe('error')
    expect(wf.jobError.value).toBe('a calibration is running')
  })
})
