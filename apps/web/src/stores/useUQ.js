import { ref, computed } from 'vue'
import { startUQ, getUQStatus, getUQProgress, cancelUQ } from '../lib/api'

/**
 * Drives a UQ job: start, poll status, expose the streamed log, the chain as it grows, and
 * the final per-parameter posterior distributions.
 *
 * The chain is polled on its own slower timer (#244). CA rewrites mcmc_chain.npy every
 * checkpoint, so asking more often than it is written returns the same array again — at the
 * size of a chain, that is the most expensive way to learn nothing.
 */
export function useUQ(options = {}) {
  const intervalMs = options.intervalMs ?? 1000
  // Slower than the log poll: a chain is orders of magnitude bigger than a line of text, and
  // CA only rewrites it every chain_save_every steps anyway.
  const chainIntervalMs = options.chainIntervalMs ?? 4000
  const state = ref('idle') // idle | running | done | error | cancelled
  const lines = ref([])
  const method = ref(null) // 'mcmc' | 'laplace'
  // [{ qname, mean, std, q05, q50, q95, bins, counts }, ...]
  const params = ref([])
  const error = ref('')
  // Set when a result is real but qualified -- a posterior from a cancelled run's partial chain.
  const warning = ref('')
  // The three live views of the chain; null until the run writes its first checkpoint.
  const progress = ref(null)

  let jobId = null
  let offset = 0
  let timer = null
  let chainTimer = null

  function reset() {
    if (timer) clearTimeout(timer)
    if (chainTimer) clearTimeout(chainTimer)
    timer = null
    chainTimer = null
    jobId = null
    offset = 0
    state.value = 'idle'
    lines.value = []
    method.value = null
    params.value = []
    error.value = ''
    warning.value = ''
    progress.value = null
  }

  async function start(modelId, settings) {
    reset()
    state.value = 'running'
    try {
      const { job_id } = await startUQ(modelId, settings)
      jobId = job_id
      await poll()
      await pollChain()
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
        warning.value = s.warning || ''
        // One last look at the chain now the run has stopped.
        //
        // CA writes the finished chain as the very last thing it does, *after* the poll that
        // saw the run still going -- and with an emulator the whole run can take less time
        // than one chain poll interval, so without this there was nothing to draw at all and
        // the section vanished the moment `running` went false. It also picks up the partial
        // chain of a cancelled or failed run, which is the other half of what CA #418 is for.
        await pollChain()
      }
    } catch (e) {
      state.value = 'error'
      // A 404 means the server no longer knows this job -- it was restarted, or the app is
      // talking to a different one. That is not "the run failed", and reporting it as a bare
      // Axios error sends someone looking for a bug in their model.
      error.value =
        e?.response?.status === 404
          ? 'The server is no longer tracking this run (it was restarted, or replaced). The'
            + ' sampling process may have been stopped with it; any chain it had already'
            + ' written is still in the run directory.'
          : e?.response?.data?.detail || String(e)
    }
  }

  /**
   * Fetch the chain, and keep fetching while the run is going.
   *
   * A failure here is deliberately silent: the chain is a picture of a run that is otherwise
   * fine, and turning "the plot is a few seconds stale" into an error state on the run itself
   * would be a worse trade than showing the previous chain for another tick.
   */
  async function pollChain() {
    if (!jobId) return
    try {
      const p = await getUQProgress(jobId)
      if (p.steps > 0) progress.value = p
    } catch {
      /* keep the last chain we had */
    }
    // Only keep a timer going while the run is; a finished run is fetched once, by poll().
    if (state.value === 'running') {
      chainTimer = setTimeout(pollChain, chainIntervalMs)
    }
  }

  /**
   * Pick up the posterior the server salvages from a cancelled run's partial chain.
   *
   * The salvage happens when the runner process actually exits, which is after the cancel
   * request returns -- so one immediate poll would usually miss it. A few short retries cover
   * the gap without keeping a timer alive for a run that is over.
   */
  async function collectCancelledResults(attempts = 6, delayMs = 500) {
    for (let attempt = 0; attempt < attempts; attempt++) {
      try {
        const s = await getUQStatus(jobId, offset)
        if (s?.params?.length) {
          params.value = s.params
          method.value = s.method ?? method.value
          warning.value = s.warning || ''
          return
        }
      } catch {
        return
      }
      await new Promise((resolve) => setTimeout(resolve, delayMs))
    }
  }

  async function cancel() {
    if (timer) clearTimeout(timer)
    if (chainTimer) clearTimeout(chainTimer)
    timer = null
    chainTimer = null
    if (jobId) {
      try {
        await cancelUQ(jobId)
      } catch {
        /* best effort */
      }
      state.value = 'cancelled'
      // A cancelled run still has every step it sampled before it stopped.
      await pollChain()
      await collectCancelledResults()
    }
  }

  const running = computed(() => state.value === 'running')

  return { state, lines, method, params, error, warning, running, progress, start, cancel,
    reset }
}
