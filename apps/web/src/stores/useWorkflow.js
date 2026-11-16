import { ref, computed } from 'vue'
import {
  cancelWorkflow,
  closeWorkflow,
  errorMessage,
  getWorkflow,
  getWorkflowStatus,
  loadWorkflow,
  openWorkflowView,
  runWorkflow,
  saveWorkflowStepInput,
  unsetParameters,
  uploadWorkflow,
} from '../lib/api'

/** The tab id of the model a workflow's results are merged into (CA's TARGET_VIEW). */
export const TARGET_VIEW = 'target'

const ARRAY_SUFFIX = /_(module|vessel)_array\.(csv|json)$/

/** A model's file name: a module array is a path, or {module_array, parameters}. */
function arrayFile(model) {
  const path = typeof model === 'string' ? model : model?.module_array
  return path ? path.split(/[\\/]/).pop() : null
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
    return `runs as ${modelLabel(selection.module)} at ${selection.vessel}`
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
  const tabs = steps.map((s) => ({
    id: s.id,
    kind: 'step',
    // CA's step kind; only a reduction has no model, so no tab to show
    stepKind: s.kind ?? 'calibration',
    viewable: (s.kind ?? 'calibration') !== 'reduction',
    label: s.id,
    detail: s.kind === 'reduction'
      ? `keeps the top ${s.reduction?.top_n ?? '?'} by Sobol ${s.reduction?.index_type ?? 'ST'}`
      : modelLabel(s.model ?? fileById[s.id]?.model),
    // a selection of the target's instances: what CA runs it as
    selection: selectionWords(s.selection),
    missing: missingInputs(fileById[s.id]),
    sources: Object.keys(SOURCE_WORDS)
      .filter((key) => s[key])
      .map((key) => ({ key, step: s[key] })),
    // where the step's model sits in the target: a submodule or vessel ('' or none: as is)
    place: s.place ?? s.submodule ?? s.vessel ?? null,
    calibrates: s.calibrates ?? [],
    // CA's plan names every value a step is given ({from_step, model_name, in_step});
    // the file alone only names the steps.
    fixedFrom: (s.fixed_from ?? []).map((f) => (typeof f === 'string' ? { from_step: f } : f)),
    priorsFrom: (s.priors_from ?? []).map((p) =>
      typeof p === 'string' ? { step: p, kind: 'mvnormal' } : p,
    ),
  }))
  tabs.push({
    id: TARGET_VIEW,
    kind: 'target',
    stepKind: null,
    viewable: true,
    selection: '',
    missing: [],
    sources: [],
    label: target ? (arrayFile(target) ? modelLabel(target) : target.module_type) : 'target',
    detail: target
      ? (arrayFile(target) ? `module array · ${arrayFile(target)}` : `supermodule · ${target.parameterisation}`)
      : 'target',
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

  async function open({ path = '', runDir = '', file = null, outputsDir = '' }) {
    const d = await guarded(() =>
      file ? uploadWorkflow(file, outputsDir) : loadWorkflow({ path, runDir, outputsDir }),
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
  async function select(tabId) {
    busy.value = true
    error.value = ''
    try {
      const result = await openWorkflowView(tabId)
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

  async function run({ fromStep = '', only = '', numCores = 1 } = {}) {
    resetJob()
    jobState.value = 'running'
    try {
      const { job_id } = await runWorkflow({ fromStep, only, numCores })
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

  return {
    described,
    loaded,
    tabs,
    name,
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
    select,
    saveStepInput,
    run,
    cancel,
    stateOf,
  }
}
