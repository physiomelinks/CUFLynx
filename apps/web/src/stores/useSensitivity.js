import { ref, computed } from 'vue'
import {
  startSensitivity,
  getSensitivityStatus,
  cancelSensitivity,
} from '../lib/api'

/**
 * Drives a sensitivity-analysis job: start, poll status, expose the streamed
 * log + the final indices. Trimmed sibling of useCalibration — the engines emit
 * no per-iteration history, so there's just a terminal log followed by the final
 * heatmap data.
 *
 * Completed runs are *accumulated* into `results` (newest last) rather than
 * overwriting each other, so the user can keep e.g. a global Sobol run and a
 * local finite-difference run side by side and switch between them to compare
 * (and eventually different gradient sources). `selectedId` picks which saved
 * run the heatmap shows; `indices`/`paramNames`/`outputNames` are derived from
 * it so the AnalysisPanel needs no special-casing.
 */
export function useSensitivity(options = {}) {
  const intervalMs = options.intervalMs ?? 1000
  const state = ref('idle') // idle | running | done | error | cancelled
  const lines = ref([])
  const error = ref('')

  // Accumulated history of completed runs + which one is shown.
  const results = ref([]) // [{ id, label, at, method, settings, indices, paramNames, outputNames }]
  const selectedId = ref(null)

  let jobId = null
  let offset = 0
  let timer = null
  let nextId = 1
  let pendingSettings = null

  // The selected run drives the heatmap (kept as computeds for the panel).
  const selected = computed(
    () => results.value.find((r) => r.id === selectedId.value) ?? null,
  )
  const indices = computed(() => selected.value?.indices ?? null)
  const paramNames = computed(() => selected.value?.paramNames ?? [])
  const outputNames = computed(() => selected.value?.outputNames ?? [])

  // Human-readable label summarising what produced a run, so saved runs are
  // distinguishable in the comparison selector.
  function makeLabel(id, settings) {
    const s = settings ?? {}
    if (s.method === 'local') {
      const parts = ['Local', (s.gradient_method ?? 'FD'), (s.nominal ?? 'current')]
      if (s.run_calibration_first) parts.push('calib-first')
      return `#${id} ${parts.join(' · ')}`
    }
    if (s.method === 'sobol') {
      return `#${id} Sobol · ${s.sample_type ?? 'saltelli'} · n${s.num_samples ?? '?'}`
    }
    return `#${id} ${s.method ?? 'run'}`
  }

  // Clears the live-run transient state only; saved results are preserved.
  function reset() {
    if (timer) clearTimeout(timer)
    timer = null
    jobId = null
    offset = 0
    state.value = 'idle'
    lines.value = []
    error.value = ''
  }

  function clearResults() {
    results.value = []
    selectedId.value = null
  }

  function removeResult(id) {
    results.value = results.value.filter((r) => r.id !== id)
    if (selectedId.value === id) {
      selectedId.value = results.value.length
        ? results.value[results.value.length - 1].id
        : null
    }
  }

  function selectResult(id) {
    if (results.value.some((r) => r.id === id)) selectedId.value = id
  }

  function saveResult(s) {
    const id = nextId++
    results.value = [
      ...results.value,
      {
        id,
        label: makeLabel(id, pendingSettings),
        at: new Date().toLocaleTimeString(),
        method: pendingSettings?.method ?? null,
        settings: pendingSettings ? { ...pendingSettings } : null,
        indices: s.indices,
        paramNames: s.param_names ?? [],
        outputNames: s.output_names ?? [],
      },
    ]
    selectedId.value = id
  }

  async function start(modelId, settings, currentParams = null) {
    reset()
    pendingSettings = settings ? { ...settings } : null
    state.value = 'running'
    try {
      const { job_id } = await startSensitivity(modelId, settings, currentParams)
      jobId = job_id
      await poll()
    } catch (e) {
      state.value = 'error'
      error.value = e?.response?.data?.detail || String(e)
    }
  }

  async function poll() {
    if (!jobId) return
    try {
      const s = await getSensitivityStatus(jobId, offset)
      if (s.lines?.length) {
        lines.value = lines.value.concat(s.lines)
        offset = s.next_offset
      }
      state.value = s.state
      if (s.state === 'running') {
        timer = setTimeout(poll, intervalMs)
      } else {
        // Save (rather than overwrite) a successful run so it can be compared.
        if (s.state === 'done' && s.indices) saveResult(s)
        error.value = s.error || ''
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
        await cancelSensitivity(jobId)
      } catch {
        /* best effort */
      }
      state.value = 'cancelled'
    }
  }

  const running = computed(() => state.value === 'running')

  return {
    state,
    lines,
    indices,
    paramNames,
    outputNames,
    error,
    running,
    results,
    selectedId,
    selectResult,
    removeResult,
    clearResults,
    start,
    cancel,
    reset,
  }
}
