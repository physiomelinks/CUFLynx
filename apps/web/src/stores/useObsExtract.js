import { ref, computed } from 'vue'
import {
  scanDatasets,
  startObsExtract,
  getObsExtractStatus,
  cancelObsExtract,
} from '../lib/api'

/**
 * Drives an obs_data extraction: scan a directory, run the job, stream its log.
 *
 * A store rather than component-local state for the same reason the four
 * analysis subsystems are stores: the poll has to survive the dialog being
 * closed. Extraction of a few hundred recordings takes minutes, and a user who
 * closes the dialog to look at something else must not silently abandon it.
 *
 * The poll loop is `useSensitivity`'s, deliberately: same `offset` accumulation,
 * same terminal states, so there is one shape to learn for every long job in
 * this app.
 */
export function useObsExtract(options = {}) {
  const intervalMs = options.intervalMs ?? 1000
  const state = ref('idle') // idle | scanning | running | done | error | cancelled
  const lines = ref([])
  const error = ref('')
  const warning = ref('')
  // The last scan: { root, datasets, groups, warnings, suggested_binding }
  const scan = ref(null)
  // The finished job's result, including the obs_data document itself.
  const result = ref(null)

  let jobId = null
  let offset = 0
  let timer = null

  const running = computed(() => state.value === 'running')
  const scanning = computed(() => state.value === 'scanning')

  function reset() {
    if (timer) clearTimeout(timer)
    timer = null
    jobId = null
    offset = 0
    state.value = 'idle'
    lines.value = []
    error.value = ''
    warning.value = ''
    result.value = null
  }

  async function rescan(payload) {
    state.value = 'scanning'
    error.value = ''
    try {
      scan.value = await scanDatasets(payload)
      state.value = 'idle'
      return scan.value
    } catch (e) {
      state.value = 'error'
      error.value = e?.response?.data?.detail || String(e)
      return null
    }
  }

  async function start(config, opts = {}) {
    reset()
    state.value = 'running'
    try {
      const { job_id } = await startObsExtract(config, opts)
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
      const status = await getObsExtractStatus(jobId, offset)
      if (status.lines?.length) lines.value = lines.value.concat(status.lines)
      offset = status.next_offset ?? offset
      warning.value = status.warning || ''
      if (status.state === 'running') {
        timer = setTimeout(poll, intervalMs)
        return
      }
      state.value = status.state
      error.value = status.error || ''
      // A cancelled run can still carry a partial result, and keeping it is the
      // point of cancelling rather than closing the dialog.
      result.value = status.result ?? null
    } catch (e) {
      state.value = 'error'
      error.value = e?.response?.data?.detail || String(e)
    }
  }

  async function cancel() {
    if (!jobId) return
    try {
      await cancelObsExtract(jobId)
    } catch {
      // Already finished, or already gone; the poll settles the real state.
    }
  }

  return {
    state, lines, error, warning, scan, result,
    running, scanning, rescan, start, cancel, reset,
  }
}
