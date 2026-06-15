import { ref, computed } from 'vue'
import { startUQ, getUQStatus, cancelUQ } from '../lib/api'

/**
 * Drives a UQ job: start, poll status, expose the streamed log and the final
 * per-parameter posterior distributions. Sibling of useSensitivity — the engine
 * emits no incremental history, so there's just a terminal log then the result.
 */
export function useUQ(options = {}) {
  const intervalMs = options.intervalMs ?? 1000
  const state = ref('idle') // idle | running | done | error | cancelled
  const lines = ref([])
  const method = ref(null) // 'mcmc' | 'laplace'
  // [{ qname, mean, std, q05, q50, q95, bins, counts }, ...]
  const params = ref([])
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
    method.value = null
    params.value = []
    error.value = ''
  }

  async function start(modelId, settings) {
    reset()
    state.value = 'running'
    try {
      const { job_id } = await startUQ(modelId, settings)
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
      const s = await getUQStatus(jobId, offset)
      if (s.lines?.length) {
        lines.value = lines.value.concat(s.lines)
        offset = s.next_offset
      }
      state.value = s.state
      if (s.state === 'running') {
        timer = setTimeout(poll, intervalMs)
      } else {
        method.value = s.method
        params.value = s.params ?? []
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
        await cancelUQ(jobId)
      } catch {
        /* best effort */
      }
      state.value = 'cancelled'
    }
  }

  const running = computed(() => state.value === 'running')

  return { state, lines, method, params, error, running, start, cancel, reset }
}
