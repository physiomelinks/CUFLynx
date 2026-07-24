import { ref, computed } from 'vue'
import {
  startCalibration,
  getCalibrationStatus,
  getCalibrationProgress,
  cancelCalibration,
  calibratedModelUrl as apiCalibratedModelUrl,
} from '../lib/api'

/**
 * Apply calibrated best-fit values to the slider store: update existing
 * sliders, and add a slider for any calibrated param without one (using its
 * params_for_id min/max when known, else a range around the value).
 */
export function applyBestParams(slidersStore, paramSpecs, bestParams) {
  for (const [qname, value] of Object.entries(bestParams || {})) {
    if (slidersStore.sliders[qname]) {
      slidersStore.setValue(qname, value)
      continue
    }
    const spec = paramSpecs?.[qname]
    if (spec) {
      slidersStore.addSlider(qname, { min: spec.min, max: spec.max, value })
    } else {
      const span = Math.abs(value) || 1
      slidersStore.addSlider(qname, { min: value - span, max: value + span, value })
    }
  }
}

/** Drives a calibration job: start, poll status, expose streamed log + result. */
export function useCalibration(options = {}) {
  const intervalMs = options.intervalMs ?? 1000
  const state = ref('idle') // idle | running | done | error | cancelled
  const lines = ref([])
  const bestParams = ref(null)
  const cost = ref(null)
  const error = ref('')
  // Path (server-side) of the calibrated CellML saved on finish (issue #114); a
  // truthy value means a downloadable model exists for this job.
  const calibratedModelPath = ref(null)
  // Live per-generation history for the Progress tab charts.
  const costHistory = ref([]) // [[best, …top-10], …] one row per generation
  const paramHistory = ref({ paramNames: [], generations: [] })
  // Per-start cost curves for multi-start gradient descent: startCosts[start] =
  // [cost per iteration]. Empty for GA / single-start runs.
  const startCosts = ref([])
  // Per-start parameter trajectories for multi-start gradient descent:
  // { param_names, starts } where starts[start][iteration] = [val per param].
  // Empty for GA / single-start runs.
  const startParams = ref({ param_names: [], starts: [] })
  // Cost-gradient (dJ/dp) history for gradient-based runs, used by the Progress
  // tab's cost/gradient toggle. Single-start: one gradient vector per iteration.
  // Multi-start: { param_names, starts } like startParams. Empty for GA runs.
  const gradHistory = ref([])
  const startGrads = ref({ param_names: [], starts: [] })
  // Final per-observable fit errors for the Analysis tab bar charts.
  const percentError = ref(null) // [number] one per observable
  const stdError = ref(null) // [number] one per observable
  const errorLabels = ref([]) // display names, one per observable

  let jobId = null
  const jobIdRef = ref(null) // reactive mirror of jobId for computed URLs
  let offset = 0
  let timer = null

  function reset() {
    if (timer) clearTimeout(timer)
    timer = null
    jobId = null
    jobIdRef.value = null
    offset = 0
    state.value = 'idle'
    lines.value = []
    bestParams.value = null
    cost.value = null
    calibratedModelPath.value = null
    error.value = ''
    costHistory.value = []
    startCosts.value = []
    startParams.value = { param_names: [], starts: [] }
    gradHistory.value = []
    startGrads.value = { param_names: [], starts: [] }
    paramHistory.value = { paramNames: [], generations: [] }
    percentError.value = null
    stdError.value = null
    errorLabels.value = []
  }

  async function fetchProgress() {
    if (!jobId) return
    try {
      const p = await getCalibrationProgress(jobId)
      costHistory.value = p.cost_history ?? []
      startCosts.value = p.start_costs ?? []
      startParams.value = p.start_params ?? { param_names: [], starts: [] }
      gradHistory.value = p.grad_history ?? []
      startGrads.value = p.start_grads ?? { param_names: [], starts: [] }
      paramHistory.value = {
        paramNames: p.param_names ?? [],
        generations: p.param_history ?? [],
      }
    } catch {
      /* history not written yet (early run) or job gone; keep last values */
    }
  }

  async function start(modelId, settings, currentParams = null) {
    reset()
    state.value = 'running'
    try {
      const { job_id } = await startCalibration(modelId, settings, currentParams)
      jobId = job_id
      jobIdRef.value = job_id
      await poll()
    } catch (e) {
      state.value = 'error'
      error.value = e?.response?.data?.detail || String(e)
    }
  }

  async function poll() {
    if (!jobId) return
    try {
      const s = await getCalibrationStatus(jobId, offset)
      if (s.lines?.length) {
        lines.value = lines.value.concat(s.lines)
        offset = s.next_offset
      }
      await fetchProgress()
      if (s.state === 'running') {
        state.value = 'running'
        timer = setTimeout(poll, intervalMs)
      } else {
        // Populate the results BEFORE flipping `state` to its terminal value.
        // Watchers keyed on `state` (e.g. App.vue applying best-fit values to
        // the sliders) read `bestParams` synchronously when the state change
        // fires, so it must already be set — otherwise they see a stale null
        // and the sliders never update.
        bestParams.value = s.best_params
        cost.value = s.cost
        calibratedModelPath.value = s.calibrated_model_path || null
        percentError.value = s.percent_error
        stdError.value = s.std_error
        errorLabels.value = s.error_labels ?? []
        error.value = s.error || ''
        state.value = s.state
      }
    } catch (e) {
      state.value = 'error'
      error.value = e?.response?.data?.detail || String(e)
    }
  }

  async function cancel() {
    if (timer) clearTimeout(timer)
    timer = null
    if (jobId) {
      try {
        await cancelCalibration(jobId)
      } catch {
        /* best effort */
      }
      state.value = 'cancelled'
    }
  }

  const running = computed(() => state.value === 'running')
  // Download URL for the saved calibrated model, or null when none exists (#114).
  const calibratedModelUrl = computed(() =>
    jobIdRef.value && calibratedModelPath.value
      ? apiCalibratedModelUrl(jobIdRef.value)
      : null,
  )

  return {
    state,
    lines,
    bestParams,
    cost,
    calibratedModelPath,
    calibratedModelUrl,
    error,
    costHistory,
    startCosts,
    startParams,
    gradHistory,
    startGrads,
    paramHistory,
    percentError,
    stdError,
    errorLabels,
    running,
    start,
    cancel,
    reset,
  }
}
