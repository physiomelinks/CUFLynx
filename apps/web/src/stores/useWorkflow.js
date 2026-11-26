import { ref, computed, watch } from 'vue'
import { figureFunction } from '../lib/workflowFigures'
import { runTargetFor, targetAfterRun } from '../lib/workflowTarget'
import {
  cancelWorkflow,
  closeWorkflow,
  deleteWorkflowStep,
  listWorkflows,
  moveWorkflowStep,
  renameWorkflowStep,
  drawWorkflowLink,
  deleteWorkflowLink,
  placeWorkflowNode,
  placeWorkflowNodes,
  makeWorkflowSweep,
  setWorkflowValues,
  setWorkflowValueOrigin,
  combineWorkflowSteps,
  makeWorkflowFigure,
  chooseWorkflowParameters,
  addWorkflowDraft,
  buildWorkflowTabs,
  deleteWorkflowDraft,
  errorMessage,
  followWorkingDir as postWorkingDir,
  getWorkflow,
  getWorkflowStatus,
  getWorkflowWarmUp,
  loadWorkflow,
  openWorkflowView,
  runWorkflow,
  saveWorkflowStepInput,
  unsetParameters,
  uploadWorkflow,
  workflowProvenanceUrl,
} from '../lib/api'

/** The tab id of the model a workflow's results are merged into (CA's TARGET_VIEW). */
export const TARGET_VIEW = 'target'

const ARRAY_SUFFIX = /_(module|vessel)_array\.(csv|json)$/

/** A model's file name: a module array is a path, or {module_array, parameters}. */
function arrayFile(model) {
  const path = typeof model === 'string' ? model : model?.module_array
  return path ? path.split(/[\\/]/).pop() : null
}

/** An external Python model's file name ({external_model_path}), or null. */
export function externalFile(model) {
  const path = model?.external_model_path
  return typeof path === 'string' && path ? path.split(/[\\/]/).pop() : null
}

/** The file name of a CellML workflow model (`cellml_model_path`), or null. */
export function cellmlFile(model) {
  const path = model?.cellml_model_path
  return typeof path === 'string' && path ? path.split(/[\\/]/).pop() : null
}

/**
 * A workflow model in a few words: a library module as "type · parameterisation", a
 * module array as its prefix (with `full`, as type / version / parameterisation, or the
 * array's file name), a selection of the target's instances by their names.
 */
export function modelLabel(model, full = false) {
  if (!model) return ''
  if (Array.isArray(model.select)) {
    return `${full ? 'selection of the target: ' : 'selection · '}${model.select.join(', ')}`
  }
  const file = arrayFile(model)
  if (file) return full ? file : file.replace(ARRAY_SUFFIX, '')
  const py = externalFile(model)
  if (py) return full ? `external Python model ${py}` : py
  const cellml = cellmlFile(model)
  if (cellml) return full ? `CellML model ${cellml}` : cellml
  return full
    ? `${model.module_type} / ${model.version} / ${model.parameterisation}`
    : `${model.module_type} · ${model.parameterisation}`
}

/** What a step that is not a calibration does, in a few words (CA's step kinds). */
export const KIND_WORDS = {
  sensitivity: 'sensitivity analysis',
  reduction: 'reduction',
  emulation: 'emulator',
  identifiability: 'identifiability',
  set: 'set by hand',
  sweep: 'parameter sweep',
  simulate: 'simulation',
  figure: 'figures',
}

/**
 * Why a step has no model to show, or '' when it has one: a reduction, a set step or a
 * figure step has none, and a sweep or simulation run on the target (`on_target`) has none
 * of its own.
 */
export function noViewReason(kind, fileStep) {
  if (kind === 'reduction') return 'a reduction has no model to show'
  if (kind === 'set') return 'a set step gives values to the target and has no model to show'
  if (kind === 'figure') return 'a figure step draws from other steps and has no model: its figures are in Analysis'
  if ((kind === 'sweep' || kind === 'simulate') && fileStep?.on_target) {
    return `a ${kind === 'sweep' ? 'sweep' : 'simulation'} on the target has no model of its own: open the target`
  }
  return ''
}

/**
 * A reduction in a few words, from CA's options (the plan's `reduction`, else the file's
 * `settings.param_reduction_options`): a Sobol reduction keeps a fixed number, a PCA one
 * as many as its modes need, up to `n_params` when given.
 */
export function reductionWords(options) {
  const method = options?.method ?? 'sobol'
  const index = options?.index_type ?? 'ST'
  if (method === 'sobol') return `keeps the top ${options?.n_params ?? '?'} by Sobol ${index}`
  const most = options?.n_params != null ? `, at most ${options.n_params}` : ''
  return `${method === 'pca' ? 'PCA' : method} reduction by ${index}${most}`
}

/** A set step's values in a few words: "u_D = 0.1". */
export function setWords(entries) {
  return (entries ?? []).map((e) => `${e.parameter} = ${e.value}`).join(', ')
}

/** The keys by which a step takes something from another, as the step list reads them. */
export const SOURCE_WORDS = {
  emulator_from: 'on the emulator of',
  result_from: 'at the fit of',
  sensitivity_from: 'ranks the parameters of',
  params_for_id_from: 'fits what was kept by',
}

/**
 * What a selection step runs as, from CA's plan (`selection`): the library module of its
 * one instance, placed there, or the module array its instances form. '' for any other step.
 */
export function selectionWords(selection) {
  if (!selection) return ''
  if (selection.runs_as === 'library_module') {
    // `place` (CA #716); `vessel` from a CA before it
    return `runs as ${modelLabel(selection.module)} at ${selection.place ?? selection.vessel}`
  }
  return `runs as the module array of ${(selection.records ?? []).join(', ')}`
}

/**
 * The inputs a calibration step's file names none of: 'obs_data', 'params_for_id' (unless
 * it fits what a reduction kept). The Workflow tab offers to give it each.
 */
export function missingInputs(fileStep) {
  if (!fileStep || (fileStep.kind ?? 'calibration') !== 'calibration' || !fileStep.model) return []
  const missing = []
  if (!fileStep.obs_data) missing.push('obs_data')
  if (!fileStep.params_for_id && !fileStep.params_for_id_from) missing.push('params_for_id')
  return missing
}

/** The target's tab label: a module array's prefix, an external model's file, a module type. */
function targetLabel(target) {
  if (!target) return 'target'
  if (arrayFile(target) || externalFile(target) || cellmlFile(target)) return modelLabel(target)
  return target.module_type
}

function targetDetail(target) {
  if (!target) return 'target'
  if (arrayFile(target)) return `module array · ${arrayFile(target)}`
  if (externalFile(target)) return `external Python model · ${externalFile(target)}`
  if (cellmlFile(target)) return `CellML model · ${cellmlFile(target)}`
  return `supermodule · ${target.parameterisation}`
}

/**
 * One tab per step, in the order CA runs them, then the target.
 *
 * Built from CA's plan (which resolved each step against the module library) when
 * there is one, else from the file itself, so a workflow whose library is not set
 * still shows its steps.
 */
export function workflowTabs(described) {
  if (!described?.loaded) return []
  const planSteps = described.plan?.steps
  const fileSteps = described.workflow?.steps ?? []
  const steps = planSteps ?? fileSteps
  const target = described.plan?.target ?? described.workflow?.target ?? null
  const fileById = Object.fromEntries(fileSteps.map((f) => [f.id, f]))
  const tabs = steps.map((s) => {
    const kind = s.kind ?? 'calibration'
    const file = fileById[s.id]
    const noView = noViewReason(kind, file)
    let detail = modelLabel(s.model ?? file?.model)
    if (kind === 'reduction') {
      detail = reductionWords(s.reduction ?? file?.settings?.param_reduction_options)
    } else if (kind === 'set') {
      detail = setWords(s.set ?? file?.set)
    } else if (kind === 'figure') {
      detail = figureFunction(file ?? s)
    } else if (noView) {
      detail = 'on the target'
    }
    return {
    id: s.id,
    kind: 'step',
    // CA's step kind; a reduction, a set step and a sweep on the target have no model
    stepKind: kind,
    viewable: !noView,
    noViewReason: noView,
    label: s.id,
    detail,
    // a selection of the target's instances: what CA runs it as
    selection: selectionWords(s.selection),
    missing: missingInputs(fileById[s.id]),
    sources: Object.keys(SOURCE_WORDS)
      .filter((key) => s[key])
      .map((key) => ({ key, step: s[key] })),
    // where the step's model sits in the target: a submodule or module ('' or none: as is);
    // `vessel` is the key a CA before #716 used for `module`
    place: s.place ?? s.submodule ?? s.module ?? s.vessel ?? null,
    calibrates: s.calibrates ?? [],
    // CA's plan names every value a step is given ({from_step, model_name, in_step});
    // the file alone only names the steps.
    fixedFrom: (s.fixed_from ?? []).map((f) => (typeof f === 'string' ? { from_step: f } : f)),
    priorsFrom: (s.priors_from ?? []).map((p) =>
      typeof p === 'string' ? { step: p, kind: 'mvnormal' } : p,
    ),
    }
  })
  tabs.push({
    id: TARGET_VIEW,
    kind: 'target',
    stepKind: null,
    viewable: true,
    selection: '',
    missing: [],
    sources: [],
    label: targetLabel(target),
    detail: targetDetail(target),
    place: '',
    calibrates: [],
    fixedFrom: [],
    priorsFrom: [],
  })
  return tabs
}

/**
 * A step's state for its tab dot: the live run's word wins while there is one,
 * then CA's record of the run directory (done / not run, and stale when an input
 * changed since).
 */
export function stepState(described, jobStates, stepId) {
  const live = jobStates?.[stepId]
  if (live && live !== 'reused') return live
  const recorded = described?.status?.steps?.[stepId]
  if (!recorded) return 'not_run'
  if (recorded.status === 'done') return recorded.stale ? 'stale' : 'done'
  return 'not_run'
}

/** The provenance files CA writes into a workflow run, as the server serves them. */
export const PROVENANCE_FILES = { svg: 'provenance.svg', graph: 'provenance_graph.json' }

/**
 * `{svg, graph}` URLs of the run's provenance that CA has written (null when neither). The
 * server gives each file's modification time, which each URL carries so a rerun's diagram
 * is fetched again rather than shown from the browser's cache.
 */
export function provenanceLinks(described) {
  const have = described?.provenance ?? {}
  const links = {}
  for (const [key, name] of Object.entries(PROVENANCE_FILES)) {
    if (have[key]) links[key] = workflowProvenanceUrl(name, have[key])
  }
  return Object.keys(links).length ? links : null
}

/**
 * Whether a run that was going has stopped, between two lists of job states (`running`,
 * `done`, ...): each calibration, sensitivity and UQ run is a step of the study workflow,
 * so the open workflow is re-read when one ends.
 */
export function anyRunEnded(now, before) {
  return !!before && now.some((state, i) => before[i] === 'running' && state !== 'running')
}

/** Drives the open calibration workflow: load, tabs, run and poll. */
export function useWorkflow(options = {}) {
  const intervalMs = options.intervalMs ?? 1000
  const described = ref({ loaded: false })
  const busy = ref(false)
  const error = ref('')
  const selected = ref(null)
  const view = ref(null) // the open tab's workflow_view from the server
  // A calibration_workflow.json an imported archive carried ({filename, path}).
  const offered = ref(null)
  // A step whose model has parameters nothing gives a value ({step_id, names}), from
  // opening its tab or from a run: a protocol in its obs_data sets them.
  const unset = ref(null)

  const jobState = ref('idle') // idle | running | done | error | cancelled
  const jobStates = ref({})
  const currentStep = ref(null)
  const lines = ref([])
  const jobError = ref('')
  let jobId = null
  let offset = 0
  let timer = null

  const loaded = computed(() => !!described.value?.loaded)
  const tabs = computed(() => workflowTabs(described.value))
  const running = computed(() => jobState.value === 'running')
  const name = computed(
    () => described.value?.workflow?.workflow_name ?? described.value?.plan?.workflow_name ?? '',
  )
  // The run's provenance diagram and graph, by URL, once CA has written them.
  const provenance = computed(() => provenanceLinks(described.value))

  async function guarded(fn) {
    busy.value = true
    error.value = ''
    try {
      return await fn()
    } catch (e) {
      error.value = errorMessage(e)
      return null
    } finally {
      busy.value = false
    }
  }

  async function refresh() {
    const d = await guarded(getWorkflow)
    if (d) described.value = d
    return d
  }

  async function open({ path = '', runDir = '', file = null, workingDir = '' }) {
    const d = await guarded(() =>
      file ? uploadWorkflow(file, workingDir) : loadWorkflow({ path, runDir, workingDir }),
    )
    if (d) {
      described.value = d
      selected.value = null
      view.value = null
      offered.value = null
      unset.value = null
      resetJob()
    }
    return d
  }

  /** The working dir changed: show its study workflow, not another folder's. */
  async function followWorkingDir(dir) {
    const before = described.value?.path ?? null
    const d = await guarded(() => postWorkingDir(dir))
    if (!d) return
    if ((d.path ?? null) !== before) {
      selected.value = null
      view.value = null
      unset.value = null
    }
    described.value = d
  }

  async function close() {
    const d = await guarded(closeWorkflow)
    if (d) {
      described.value = { loaded: false }
      selected.value = null
      view.value = null
      unset.value = null
      resetJob()
    }
  }

  /** Load one tab as the current study; returns the import-shaped response. */
  async function select(tabId, workingDir = '', options = {}) {
    busy.value = true
    error.value = ''
    try {
      const result = await openWorkflowView(tabId, workingDir, options)
      selected.value = tabId
      view.value = result.workflow_view ?? null
      if (unset.value?.step_id === tabId) unset.value = null
      return result
    } catch (e) {
      error.value = errorMessage(e)
      unset.value = unsetParameters(e) ?? unset.value
      return null
    } finally {
      busy.value = false
    }
  }

  /**
   * Write a step's obs_data (a document) or params_for_id (JSON, or CSV text the server
   * converts to JSON); a step naming none is given a file. CA re-plans, and the step's unset parameters are asked again on its next
   * open or run. Returns the server's {path, workflow}, or null (error says why).
   */
  async function saveStepInput(stepId, role, content) {
    const result = await saveWorkflowStepInput(stepId, role, content)
    described.value = result.workflow
    if (unset.value?.step_id === stepId) unset.value = null
    return result
  }

  function resetJob() {
    if (timer) clearTimeout(timer)
    timer = null
    jobId = null
    offset = 0
    jobState.value = 'idle'
    jobStates.value = {}
    currentStep.value = null
    lines.value = []
    jobError.value = ''
  }

  async function run({ fromStep = '', only = '', numCores = 1, writeCalibrated = false } = {}) {
    resetJob()
    jobState.value = 'running'
    try {
      const { job_id } = await runWorkflow({ fromStep, only, numCores, writeCalibrated })
      jobId = job_id
      await poll()
    } catch (e) {
      jobState.value = 'error'
      jobError.value = errorMessage(e)
    }
  }

  async function poll() {
    if (!jobId) return
    try {
      const s = await getWorkflowStatus(jobId, offset)
      if (s.lines?.length) {
        lines.value = lines.value.concat(s.lines)
        offset = s.next_offset
      }
      jobStates.value = s.step_states ?? {}
      currentStep.value = s.current_step ?? null
      if (s.unset_parameters) unset.value = s.unset_parameters
      if (s.state === 'running') {
        timer = setTimeout(poll, intervalMs)
        return
      }
      jobError.value = s.error || ''
      jobState.value = s.state
      // CA's record of the run directory is what the tabs show from now on.
      await refresh()
    } catch (e) {
      jobState.value = 'error'
      jobError.value = errorMessage(e)
    }
  }

  // The background build of the tabs (GET /api/workflow/warm-up), polled while it builds.
  // Paused: the rest would take longer than the server builds unasked (buildTabs).
  const warmUp = ref(null)
  let warmTimer = null
  function followWarmUp(status) {
    warmUp.value = status ?? null
    if (warmTimer) clearTimeout(warmTimer)
    warmTimer = null
    if (warmUp.value?.state !== 'building') return
    warmTimer = setTimeout(async () => {
      warmTimer = null
      try {
        followWarmUp(await getWorkflowWarmUp())
      } catch {
        // a box that failed says so when clicked; the build carries on regardless
      }
    }, intervalMs * 2)
  }
  watch(() => described.value?.warm_up, followWarmUp)

  /** Build every tab ahead (true), or none (false: a click still builds its own). */
  async function buildTabs(everything) {
    const status = await guarded(() => buildWorkflowTabs(everything))
    if (status) followWarmUp(status)
  }

  async function cancel() {
    if (!jobId) return
    try {
      await cancelWorkflow(jobId)
    } catch (e) {
      jobError.value = errorMessage(e)
    }
  }

  function stateOf(stepId) {
    return stepState(described.value, jobStates.value, stepId)
  }

  // -- the Workflow view: which step the next panel run is, and editing the steps -------
  // {mode: 'default' | 'new_step' | 'edit' | 'new_workflow', kind?, step?} (lib/workflowTarget)
  const target = ref({ mode: 'default' })
  // The working dir's workflows ({path, name, steps, modified}).
  const workflows = ref([])
  // A delete that would take more steps with it, waiting to be confirmed:
  // {step, deleted, rewired, dropped}.
  const pendingDelete = ref(null)

  function newWorkflow() {
    target.value = { mode: 'new_workflow' }
  }
  function newStep(kind) {
    target.value = { mode: 'new_step', kind }
  }
  function editStep(stepId) {
    target.value = { mode: 'edit', step: stepId }
  }
  function useDefault() {
    target.value = { mode: 'default' }
  }
  /** The `workflow_target` a run from the panel making `kind` steps sends. */
  function runTarget(kind) {
    return runTargetFor(target.value, kind, described.value)
  }
  /** A run of `kind` has started: a new step or workflow is made once. */
  function ranTarget(kind) {
    target.value = targetAfterRun(target.value, kind)
  }

  /**
   * The working dir's workflows. `reopen`: with none open here (a server restarted since
   * one was), open the one the list names as the working dir's (`open`), so the Workflow
   * view shows it instead of an empty chart. Opening a workflow loads no model as the study.
   */
  async function listIn(workingDir, { reopen = false } = {}) {
    const found = await guarded(() => listWorkflows(workingDir))
    workflows.value = found?.workflows ?? []
    if (reopen && !described.value?.loaded && found?.open) {
      await open({ path: found.open, workingDir })
    }
    return workflows.value
  }

  /**
   * Delete a step: done at once when the neighbours can be reconnected; else the steps that
   * would go with it wait in `pendingDelete` for `confirmDelete`.
   */
  async function deleteStep(stepId, confirm = false) {
    const result = await guarded(() => deleteWorkflowStep(stepId, confirm))
    if (!result) return null
    if (result.applied) {
      pendingDelete.value = null
      described.value = result.workflow
      if (result.deleted.includes(selected.value)) selected.value = null
      if (target.value.mode === 'edit' && result.deleted.includes(target.value.step)) useDefault()
    } else {
      pendingDelete.value = { step: stepId, ...result }
    }
    return result
  }
  async function confirmDelete() {
    if (pendingDelete.value) return deleteStep(pendingDelete.value.step, true)
    return null
  }
  function cancelDelete() {
    pendingDelete.value = null
  }

  /**
   * Rename a step; returns '' when done, else why not (the server's reason). The step the
   * next run changes, and the selected tab, follow the new name.
   */
  async function renameStep(stepId, to) {
    try {
      described.value = await renameWorkflowStep(stepId, to)
    } catch (e) {
      return errorMessage(e)
    }
    const name = String(to).trim()
    if (target.value.mode === 'edit' && target.value.step === stepId) target.value = { mode: 'edit', step: name }
    if (selected.value === stepId) selected.value = name
    return ''
  }

  // -- editing the chart: new steps, arrows, where the boxes sit (workflow_layout) ------
  /** Run an edit; '' when done (the new description shown), else the server's reason. */
  async function layoutEdit(call) {
    try {
      described.value = await call()
      return ''
    } catch (e) {
      return errorMessage(e)
    }
  }
  /**
   * Put a new step on the chart and make it the one the next run makes: whatever is run
   * next becomes that step, with the arrows drawn to it. Returns its id, or null.
   */
  async function addDraft(col = null, row = null, onTarget = false, model = null, action = null) {
    try {
      const d = await addWorkflowDraft(col, row, onTarget, model, action)
      described.value = d
      // a box after the target (a simulation) or an action box is made by its own form;
      // a model box waits for what is chosen for it; a plain new step for the next run
      if (!onTarget && !action && !model) target.value = { mode: 'new_step', draft: d.draft }
      return d.draft
    } catch (e) {
      error.value = errorMessage(e)
      return null
    }
  }
  function chooseDraft(draft) {
    target.value = { mode: 'new_step', draft }
  }
  function deleteDraft(draft) {
    if (target.value.draft === draft) useDefault()
    return layoutEdit(() => deleteWorkflowDraft(draft))
  }
  const link = (arrow) => layoutEdit(() => drawWorkflowLink(arrow))
  const unlink = (arrow) => layoutEdit(() => deleteWorkflowLink(arrow))
  const place = (node, col, row) => layoutEdit(() => placeWorkflowNode(node, col, row))
  const placeMany = (positions) => layoutEdit(() => placeWorkflowNodes(positions))
  const makeSweep = (form) => layoutEdit(() => makeWorkflowSweep(form))
  const setValues = (form) => layoutEdit(() => setWorkflowValues(form))
  // where a value of the target comes from (the origin boxes' form)
  const setValueOrigin = (form) => layoutEdit(() => setWorkflowValueOrigin(form))
  const combine = (form) => layoutEdit(() => combineWorkflowSteps(form))
  const makeFigure = (form) => layoutEdit(() => makeWorkflowFigure(form))
  const chooseParameters = (form) => layoutEdit(() => chooseWorkflowParameters(form))

  async function moveStep(stepId, index) {
    const d = await guarded(() => moveWorkflowStep(stepId, index))
    if (d) described.value = d
    return d
  }

  return {
    target,
    workflows,
    pendingDelete,
    newWorkflow,
    newStep,
    editStep,
    useDefault,
    runTarget,
    ranTarget,
    listIn,
    deleteStep,
    confirmDelete,
    cancelDelete,
    moveStep,
    renameStep,
    addDraft,
    chooseDraft,
    deleteDraft,
    link,
    unlink,
    place,
    placeMany,
    makeSweep,
    setValues,
    setValueOrigin,
    combine,
    makeFigure,
    chooseParameters,
    described,
    loaded,
    tabs,
    name,
    provenance,
    warmUp,
    buildTabs,
    busy,
    error,
    selected,
    view,
    offered,
    unset,
    jobState,
    jobStates,
    currentStep,
    lines,
    jobError,
    running,
    refresh,
    open,
    close,
    followWorkingDir,
    select,
    saveStepInput,
    run,
    cancel,
    stateOf,
  }
}
