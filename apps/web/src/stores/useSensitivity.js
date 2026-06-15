import { ref, computed } from 'vue'
import {
  startSensitivity,
  getSensitivityStatus,
  cancelSensitivity,
} from '../lib/api'

/**
 * Drives a sensitivity-analysis job: start, poll status, expose the streamed
 * log + the final Sobol indices. Trimmed sibling of useCalibration — the Sobol
 * engine emits no per-iteration history, so there's no live progress, just a
 * terminal log followed by the final heatmap data.
 */
export function useSensitivity(options = {}) {
  const intervalMs = options.intervalMs ?? 1000
  const state = ref('idle') // idle | running | done | error | cancelled
  const lines = ref([])
  // { S1: {out: {param: val}}, ST: {...} }
  const indices = ref(null)
  const paramNames = ref([])
  const outputNames = ref([])
  const error = ref('')

  let jobId = null
  let offset = 0
  let timer = null

  function reset() {
    if (timer) clearTimeout(timer)
    timer = null
    jobId = null
    offset = 0
    state.value = 'idle'
    lines.value = []
    indices.value = null
    paramNames.value = []
    outputNames.value = []
    error.value = ''
  }

  async function start(modelId, settings) {
    reset()
    state.value = 'running'
    try {
      const { job_id } = await startSensitivity(modelId, settings)
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
        indices.value = s.indices
        paramNames.value = s.param_names ?? []
        outputNames.value = s.output_names ?? []
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
    start,
    cancel,
    reset,
  }
}
