import { describe, it, expect, vi, beforeEach } from 'vitest'

vi.mock('../lib/api', () => ({
  getWorkflow: vi.fn(),
  loadWorkflow: vi.fn(),
  uploadWorkflow: vi.fn(),
  closeWorkflow: vi.fn(),
  followWorkingDir: vi.fn(),
  openWorkflowView: vi.fn(),
  runWorkflow: vi.fn(),
  getWorkflowStatus: vi.fn(),
  cancelWorkflow: vi.fn(),
  saveWorkflowStepInput: vi.fn(),
  listWorkflows: vi.fn(),
  deleteWorkflowStep: vi.fn(),
  moveWorkflowStep: vi.fn(),
  renameWorkflowStep: vi.fn(),
  addWorkflowDraft: vi.fn(),
  drawWorkflowLink: vi.fn(),
  workflowProvenanceUrl: (name, v) => `/api/workflow/provenance/${name}?v=${v}`,
  unsetParameters: (e) => e?.response?.data?.detail?.unset_parameters ?? null,
  errorMessage: (e) =>
    e?.response?.data?.detail?.message || e?.response?.data?.detail || String(e?.message || e),
}))

import {
  deleteWorkflowStep,
  listWorkflows,
  moveWorkflowStep,
  renameWorkflowStep,
  addWorkflowDraft,
  drawWorkflowLink,
  followWorkingDir,
  getWorkflow,
  getWorkflowStatus,
  loadWorkflow,
  openWorkflowView,
  runWorkflow,
  saveWorkflowStepInput,
} from '../lib/api'
import {
  KIND_WORDS,
  anyRunEnded,
  TARGET_VIEW,
  missingInputs,
  modelLabel,
  provenanceLinks,
  reductionWords,
  selectionWords,
  stepState,
  useWorkflow,
  workflowTabs,
} from './useWorkflow'

const PLAN = {
  workflow_name: 'chain_rest',
  target: { module_type: 'chain', version: 'v1', parameterisation: 'rest' },
  steps: [
    {
      id: 'fit_a', model: { module_type: 'lin_a', version: 'v1', parameterisation: 'fit_a' },
      place: 'A',
      calibrates: [{ model_name: 'p_mod', target_parameterisation_name: 'p_A' }],
      fixed_from: [], priors_from: [],
    },
    {
      id: 'rest', model: { module_type: 'chain', version: 'v1', parameterisation: 'rest' },
      place: '',
      calibrates: [{ model_name: 'q_mod_C', target_parameterisation_name: 'q_C' }],
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
  it('marks each step\'s kind and what it takes, and gives a reduction no model tab', () => {
    const tabs = workflowTabs({
      loaded: true,
      plan: {
        target: { module_type: 'chain', version: 'v1', parameterisation: 'screened' },
        steps: [
          { id: 'screen', kind: 'sensitivity',
            model: { module_type: 'chain', version: 'v1', parameterisation: 'screened' } },
          { id: 'reduce', kind: 'reduction', model: null, sensitivity_from: 'screen',
            reduction: { method: 'sobol', n_params: 1, index_type: 'ST' } },
          { id: 'fit', kind: 'calibration', params_for_id_from: 'reduce',
            model: { module_type: 'chain', version: 'v1', parameterisation: 'screened' } },
        ],
      },
    })
    const byId = Object.fromEntries(tabs.map((t) => [t.id, t]))
    expect(byId.screen.stepKind).toBe('sensitivity')
    expect(byId.reduce).toMatchObject({ viewable: false, detail: 'keeps the top 1 by Sobol ST',
      sources: [{ key: 'sensitivity_from', step: 'screen' }] })
    expect(byId.fit).toMatchObject({ viewable: true,
      sources: [{ key: 'params_for_id_from', step: 'reduce' }] })
    expect(byId.target.viewable).toBe(true)
  })

  it('gives one tab per step, in CA order, then the supermodule', () => {
    const tabs = workflowTabs(DESCRIBED)
    expect(tabs.map((t) => t.id)).toEqual(['fit_a', 'rest', TARGET_VIEW])
    expect(tabs[0]).toMatchObject({ kind: 'step', place: 'A', detail: 'lin_a · fit_a' })
    expect(tabs[2]).toMatchObject({ kind: 'target', label: 'chain' })
    expect(tabs[1].fixedFrom[0].from_step).toBe('fit_a')
  })

  it('names a module-array target by its prefix and file', () => {
    const tabs = workflowTabs({
      loaded: true,
      workflow: {
        workflow_name: 'w', target: 'resources/circulation_module_array.json',
        steps: [{ id: 'a', model: { module_array: 'r/aorta_module_array.csv' } }],
      },
    })
    expect(tabs[0].detail).toBe('aorta')
    expect(tabs[1]).toMatchObject({ label: 'circulation',
                                    detail: 'module array · circulation_module_array.json' })
  })

  it('still shows the steps of a workflow CA could not resolve', () => {
    const tabs = workflowTabs({
      loaded: true,
      plan_error: 'no module library',
      workflow: {
        workflow_name: 'w',
        target: { module_type: 'soma', version: 's', parameterisation: 'rest_balance' },
        steps: [{ id: 'a', model: 'arrays/aorta_module_array.csv', fixed_from: [],
                  priors_from: ['b'] },
                { id: 'b', model: { module_type: 'soma', parameterisation: 'rest_balance' },
                  fixed_from: ['a'] }],
      },
    })
    expect(tabs.map((t) => t.id)).toEqual(['a', 'b', TARGET_VIEW])
    expect(tabs[1].fixedFrom).toEqual([{ from_step: 'a' }])
    expect(tabs[0].priorsFrom).toEqual([{ step: 'b', kind: 'mvnormal' }])
    expect(tabs[2].label).toBe('soma')
    // a module array is shown by its prefix, a library module by type and parameterisation
    expect(tabs[0].detail).toBe('aorta')
    expect(tabs[1].detail).toBe('soma · rest_balance')
  })

  it('names an external Python model by its file, as a step and as the target', () => {
    const model = { external_model_path: '/w/heat1d/heat1d_model.py' }
    expect(modelLabel(model)).toBe('heat1d_model.py')
    expect(modelLabel(model, true)).toBe('external Python model heat1d_model.py')
    const tabs = workflowTabs({
      loaded: true,
      plan: { target: model, steps: [{ id: 'fit-k', kind: 'calibration', model }] },
    })
    expect(tabs[0]).toMatchObject({ detail: 'heat1d_model.py', viewable: true })
    expect(tabs[1]).toMatchObject({ label: 'heat1d_model.py',
                                    detail: 'external Python model · heat1d_model.py' })
  })

  it('names a CellML model (a study workflow) by its file, as a step and as the target', () => {
    const model = { cellml_model_path: '../../inputs/model/s/LV.cellml' }
    expect(modelLabel(model)).toBe('LV.cellml')
    expect(modelLabel(model, true)).toBe('CellML model LV.cellml')
    const tabs = workflowTabs({
      loaded: true,
      workflow: { target: model, steps: [{ id: 'calibration-1', model }] },
    })
    expect(tabs[0].detail).toBe('LV.cellml')
    expect(tabs[1]).toMatchObject({ label: 'LV.cellml', detail: 'CellML model · LV.cellml' })
  })

  it('gives a set step and a sweep on the target no tab, and words for every kind', () => {
    const tabs = workflowTabs({
      loaded: true,
      workflow: { workflow_name: 'h', steps: [
        { id: 'boundary', kind: 'set', set: [{ parameter: 'heat/u_D', value: 0.1 }] },
        { id: 'scan', kind: 'sweep', on_target: true, sweep: {} },
        { id: 'grid', kind: 'sweep', model: { external_model_path: 'm.py' }, sweep: {} },
      ] },
      plan: { target: { external_model_path: 'm.py' }, steps: [
        { id: 'boundary', kind: 'set', model: null,
          set: [{ parameter: 'heat/u_D', value: 0.1, target_model_name: 'u_D_heat' }] },
        { id: 'scan', kind: 'sweep', model: null },
        { id: 'grid', kind: 'sweep', model: { external_model_path: 'm.py' } },
      ] },
    })
    const byId = Object.fromEntries(tabs.map((t) => [t.id, t]))
    expect(byId.boundary).toMatchObject({ viewable: false, detail: 'heat/u_D = 0.1' })
    expect(byId.boundary.noViewReason).toContain('set step')
    expect(byId.scan).toMatchObject({ viewable: false, detail: 'on the target' })
    expect(byId.scan.noViewReason).toContain('open the target')
    expect(byId.grid).toMatchObject({ viewable: true, detail: 'm.py' })
    expect(KIND_WORDS.set).toBe('set by hand')
    expect(KIND_WORDS.sweep).toBe('parameter sweep')
  })

  it("reads a reduction's options as CA keeps them, a PCA one with no fixed count", () => {
    expect(reductionWords({ method: 'sobol', n_params: 3, index_type: 'S1' }))
      .toBe('keeps the top 3 by Sobol S1')
    expect(reductionWords({ method: 'pca', index_type: 'ST' })).toBe('PCA reduction by ST')
    expect(reductionWords({ method: 'pca', n_params: 4 })).toBe('PCA reduction by ST, at most 4')
    // from the file, when CA could not plan the workflow
    const tabs = workflowTabs({ loaded: true, workflow: { workflow_name: 'w', steps: [
      { id: 'r', kind: 'reduction', sensitivity_from: 's',
        settings: { param_reduction_options: { method: 'pca' } } }] } })
    expect(tabs[0].detail).toBe('PCA reduction by ST')
  })

  it("links the run's provenance once CA has written it, a new URL per rewrite", () => {
    expect(provenanceLinks({ loaded: true })).toBeNull()
    expect(provenanceLinks({ loaded: true, provenance: { svg: 5, graph: 6 } })).toEqual({
      svg: '/api/workflow/provenance/provenance.svg?v=5',
      graph: '/api/workflow/provenance/provenance_graph.json?v=6',
    })
    expect(provenanceLinks({ provenance: { graph: 6 } })).toEqual({
      graph: '/api/workflow/provenance/provenance_graph.json?v=6' })
  })

  it('has no tabs with no workflow open', () => {
    expect(workflowTabs({ loaded: false })).toEqual([])
  })
})

describe('anyRunEnded (each run is a study-workflow step)', () => {
  it('is true when a running job stops, whichever way', () => {
    expect(anyRunEnded(['done', 'idle', 'idle'], ['running', 'idle', 'idle'])).toBe(true)
    expect(anyRunEnded(['idle', 'idle', 'error'], ['idle', 'idle', 'running'])).toBe(true)
    expect(anyRunEnded(['idle', 'cancelled', 'idle'], ['idle', 'running', 'idle'])).toBe(true)
  })

  it('is false while runs go on, when one starts, and on the first look', () => {
    expect(anyRunEnded(['running', 'idle', 'idle'], ['running', 'idle', 'idle'])).toBe(false)
    expect(anyRunEnded(['running', 'idle', 'idle'], ['done', 'idle', 'idle'])).toBe(false)
    expect(anyRunEnded(['done', 'idle', 'idle'], undefined)).toBe(false)
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
    await wf.open({ path: '/w/calibration_workflow.json', workingDir: '/o' })
    expect(loadWorkflow).toHaveBeenCalledWith(
      { path: '/w/calibration_workflow.json', runDir: '', workingDir: '/o' })
    expect(wf.loaded.value).toBe(true)
    expect(wf.name.value).toBe('chain_rest')
    expect(wf.tabs.value).toHaveLength(3)

    loadWorkflow.mockRejectedValueOnce({ response: { data: { detail: 'step "x" is wrong' } } })
    expect(await wf.open({ path: '/bad' })).toBeNull()
    expect(wf.error.value).toBe('step "x" is wrong')
    expect(wf.loaded.value).toBe(true) // the open one stays open
  })

  it('a new working dir shows its own study workflow, dropping another\'s tab', async () => {
    loadWorkflow.mockResolvedValueOnce(DESCRIBED)
    openWorkflowView.mockResolvedValueOnce({ model_id: 'm1', workflow_view: { view: 'rest' } })
    const wf = useWorkflow()
    await wf.open({ path: '/w/calibration_workflow.json', workingDir: '/o' })
    await wf.select('rest', '/o')

    followWorkingDir.mockResolvedValueOnce({ loaded: false })
    await wf.followWorkingDir('/new')
    expect(followWorkingDir).toHaveBeenCalledWith('/new')
    expect(wf.loaded.value).toBe(false)
    expect(wf.selected.value).toBeNull()
    expect(wf.view.value).toBeNull()
  })

  it('the same workflow kept across a working dir change keeps its tab', async () => {
    loadWorkflow.mockResolvedValueOnce(DESCRIBED)
    openWorkflowView.mockResolvedValueOnce({ model_id: 'm1', workflow_view: { view: 'rest' } })
    const wf = useWorkflow()
    await wf.open({ path: '/w/calibration_workflow.json', workingDir: '/o' })
    await wf.select('rest', '/o')
    followWorkingDir.mockResolvedValueOnce({ ...DESCRIBED })
    await wf.followWorkingDir('/other')
    expect(wf.selected.value).toBe('rest')
  })

  it('selecting a tab keeps the view the server returned', async () => {
    openWorkflowView.mockResolvedValueOnce({
      model_id: 'm1', workflow_view: { view: 'rest', fixed: [{ model_name: 'p_mod_A' }] },
    })
    const wf = useWorkflow()
    const result = await wf.select('rest', '/work')
    expect(openWorkflowView).toHaveBeenCalledWith('rest', '/work', {})
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
    expect(runWorkflow).toHaveBeenCalledWith({ fromStep: 'fit_a', only: '', numCores: 2,
                                               writeCalibrated: false })
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

describe('selection steps (PhLynx #492)', () => {
  const SELECTION = {
    loaded: true,
    workflow: {
      workflow_name: 'from_phlynx',
      target: { module_array: 'whole_module_array.json', parameters: 'whole_parameters.csv' },
      steps: [
        { id: 'left', model: { select: ['C'] }, obs_data: 'left_obs_data.json' },
        { id: 'right', model: { select: ['D', 'E'] }, params_for_id: 'right_params_for_id.csv' },
        { id: 'kept', model: { select: ['D'] }, obs_data: 'o.json', params_for_id_from: 'rank' },
        { id: 'rank', kind: 'reduction', sensitivity_from: 'left' },
      ],
    },
    plan: {
      steps: [
        { id: 'left', model: { select: ['C'] }, calibrates: [], fixed_from: [], priors_from: [],
          selection: { runs_as: 'library_module', vessel: 'C',
                       module: { module_type: 'lin', version: 'v1', parameterisation: 'base' } } },
        { id: 'right', model: { select: ['D', 'E'] }, calibrates: [], fixed_from: [],
          priors_from: [], selection: { runs_as: 'module_array', records: ['D', 'E'] } },
      ],
    },
  }

  it('names a selection by its instances, and what CA runs it as', () => {
    expect(modelLabel({ select: ['D', 'E'] })).toBe('selection · D, E')
    expect(modelLabel({ select: ['C'] }, true)).toBe('selection of the target: C')
    const [left, right] = workflowTabs(SELECTION)
    expect(left.detail).toBe('selection · C')
    expect(left.selection).toBe('runs as lin · base at C')
    expect(right.selection).toBe('runs as the module array of D, E')
  })

  it("lists the inputs a calibration step's file names none of", () => {
    const [left, right] = workflowTabs(SELECTION)
    expect(left.missing).toEqual(['params_for_id'])
    expect(right.missing).toEqual(['obs_data'])
    const steps = SELECTION.workflow.steps
    expect(missingInputs(steps[2])).toEqual([]) // fits what a reduction kept
    expect(missingInputs(steps[3])).toEqual([]) // a reduction takes neither
    expect(selectionWords(null)).toBe('')
  })

  it("keeps a step's unset parameters from opening its tab, and drops them once it opens", async () => {
    openWorkflowView.mockRejectedValueOnce({
      response: { data: { detail: { message: 'step "left": 1 parameter(s) have no value: C/x.',
                                    unset_parameters: { step_id: 'left', names: ['C/x'] } } } },
    })
    const wf = useWorkflow()
    expect(await wf.select('left')).toBeNull()
    expect(wf.error.value).toBe('step "left": 1 parameter(s) have no value: C/x.')
    expect(wf.unset.value).toEqual({ step_id: 'left', names: ['C/x'] })
    openWorkflowView.mockResolvedValueOnce({ model_id: 'm', workflow_view: { view: 'left' } })
    await wf.select('left')
    expect(wf.unset.value).toBeNull()
  })

  it('keeps the unset parameters a run stopped on', async () => {
    runWorkflow.mockResolvedValueOnce({ job_id: 'j2' })
    getWorkflowStatus.mockResolvedValueOnce({
      state: 'error', lines: [], next_offset: 0, step_states: { left: 'failed' },
      error: 'step left: no value', unset_parameters: { step_id: 'left', names: ['C/x'] },
    })
    getWorkflow.mockResolvedValueOnce(SELECTION)
    const wf = useWorkflow()
    await wf.run()
    expect(wf.jobState.value).toBe('error')
    expect(wf.unset.value).toEqual({ step_id: 'left', names: ['C/x'] })
  })

  it("writes a step's input and takes the workflow CA re-planned", async () => {
    saveWorkflowStepInput.mockResolvedValueOnce({ path: '/w/right_obs_data.json', workflow: SELECTION })
    const wf = useWorkflow()
    wf.unset.value = { step_id: 'right', names: ['D/x'] }
    const result = await wf.saveStepInput('right', 'obs_data', { protocol_info: {} })
    expect(saveWorkflowStepInput).toHaveBeenCalledWith('right', 'obs_data', { protocol_info: {} })
    expect(result.path).toBe('/w/right_obs_data.json')
    expect(wf.described.value).toEqual(SELECTION)
    expect(wf.unset.value).toBeNull()
  })
})

describe('useWorkflow: the Workflow view', () => {
  const STUDY = {
    loaded: true,
    path: '/w/workflow/study/calibration_workflow.json',
    workflow: { workflow_name: 'study', steps: [{ id: 'calibration-1' }, { id: 'mcmc-1', kind: 'uq', result_from: 'calibration-1' }] },
  }

  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('a delete that takes other steps waits for a confirmation', async () => {
    const wf = useWorkflow()
    wf.described.value = STUDY
    wf.editStep('mcmc-1')
    deleteWorkflowStep.mockResolvedValueOnce({
      applied: false, deleted: ['calibration-1', 'mcmc-1'], rewired: [], dropped: [],
    })
    await wf.deleteStep('calibration-1')
    expect(wf.pendingDelete.value.deleted).toEqual(['calibration-1', 'mcmc-1'])
    expect(wf.described.value).toEqual(STUDY)

    const after = { ...STUDY, workflow: { workflow_name: 'study', steps: [] } }
    deleteWorkflowStep.mockResolvedValueOnce({
      applied: true, deleted: ['calibration-1', 'mcmc-1'], rewired: [], dropped: [], workflow: after,
    })
    await wf.confirmDelete()
    expect(deleteWorkflowStep).toHaveBeenLastCalledWith('calibration-1', true)
    expect(wf.pendingDelete.value).toBeNull()
    expect(wf.described.value).toEqual(after)
    // the step the next run was to change is gone: back to the default
    expect(wf.target.value).toEqual({ mode: 'default' })
  })

  it('a move shows the workflow the server wrote, and a refused one says why', async () => {
    const wf = useWorkflow()
    moveWorkflowStep.mockResolvedValueOnce(STUDY)
    await wf.moveStep('mcmc-1', 0)
    expect(moveWorkflowStep).toHaveBeenCalledWith('mcmc-1', 0)
    expect(wf.described.value).toEqual(STUDY)
    moveWorkflowStep.mockRejectedValueOnce({ response: { data: { detail: 'takes from' } } })
    await wf.moveStep('mcmc-1', 0)
    expect(wf.error.value).toBe('takes from')
  })

  it('a new step is made once; the run target names the open workflow', () => {
    const wf = useWorkflow()
    wf.described.value = STUDY
    wf.newStep('calibration')
    expect(wf.runTarget('calibration')).toEqual({ mode: 'new_step', workflow: STUDY.path })
    wf.ranTarget('calibration')
    expect(wf.runTarget('calibration')).toEqual({ mode: 'default', workflow: STUDY.path })
  })

  it('a rename shows the new workflow, and the step being edited follows it', async () => {
    const wf = useWorkflow()
    wf.described.value = STUDY
    wf.editStep('calibration-1')
    const renamed = { ...STUDY, workflow: { workflow_name: 'study', steps: [{ id: 'fit_lv' }] } }
    renameWorkflowStep.mockResolvedValueOnce(renamed)
    expect(await wf.renameStep('calibration-1', 'fit_lv')).toBe('')
    expect(renameWorkflowStep).toHaveBeenCalledWith('calibration-1', 'fit_lv')
    expect(wf.described.value).toEqual(renamed)
    expect(wf.target.value).toEqual({ mode: 'edit', step: 'fit_lv' })
    renameWorkflowStep.mockRejectedValueOnce({ response: { data: { detail: 'another step is already called' } } })
    expect(await wf.renameStep('fit_lv', 'x')).toBe('another step is already called')
  })

  it('New step puts a box on the chart that the next run makes; a refused arrow says why', async () => {
    const wf = useWorkflow()
    wf.described.value = STUDY
    addWorkflowDraft.mockResolvedValueOnce({ ...STUDY, draft: 'new-1' })
    expect(await wf.addDraft()).toBe('new-1')
    expect(wf.target.value).toEqual({ mode: 'new_step', draft: 'new-1' })
    expect(wf.runTarget('calibration')).toMatchObject({ mode: 'new_step', draft: 'new-1' })
    drawWorkflowLink.mockRejectedValueOnce({ response: { data: { detail: 'CA says no' } } })
    expect(await wf.link({ source: 'calibration-1', target: 'new-1', key: 'fixed_from' }))
      .toBe('CA says no')
    drawWorkflowLink.mockResolvedValueOnce({ ...STUDY, layout: { links: [1] } })
    expect(await wf.link({ source: 'calibration-1', target: 'new-1', key: 'fixed_from' })).toBe('')
    expect(wf.described.value.layout).toEqual({ links: [1] })
  })

  it('lists the working dir workflows', async () => {
    const wf = useWorkflow()
    listWorkflows.mockResolvedValueOnce({ workflows: [{ path: 'a', name: 'study', steps: [] }] })
    await wf.listIn('/w')
    expect(listWorkflows).toHaveBeenCalledWith('/w')
    expect(wf.workflows.value.map((w) => w.name)).toEqual(['study'])
  })
})
