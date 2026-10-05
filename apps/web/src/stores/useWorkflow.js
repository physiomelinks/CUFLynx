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
  uploadWorkflow,
} from '../lib/api'

/** The tab id of the supermodule a workflow calibrates (CA's TARGET_VIEW). */
export const TARGET_VIEW = 'target'

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
  const label = (t) => (t ? `${t.module_type} · ${t.instance}` : '')
  const tabs = steps.map((s) => ({
    id: s.id,
    kind: 'step',
    label: s.id,
    detail: label(s.target),
    submodulePath: s.submodule_path ?? s.submodule ?? null,
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
    label: target ? target.module_type : 'target',
    detail: target ? `supermodule · ${target.instance}` : 'supermodule',
    submodulePath: '',
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
      resetJob()
    }
  }

  /** Load one tab as the current study; returns the import-shaped response. */
  async function select(tabId) {
    const result = await guarded(() => openWorkflowView(tabId))
    if (result) {
      selected.value = tabId
      view.value = result.workflow_view ?? null
    }
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
    run,
    cancel,
    stateOf,
  }
}
