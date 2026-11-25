import { ref, computed } from 'vue'
import {
  startParamReduction,
  getParamReductionStatus,
  cancelParamReduction,
  startUQ,
  getUQStatus,
  cancelUQ,
  getSobolProgress,
  errorMessage,
} from '../lib/api'
import { useSobolFollow } from '../lib/sobolProgress'

/**
 * A polled analysis job: start, stream the log, keep the final status.
 *
 * `api` is `{ start(modelId, settings, currentParams), status(jobId, offset),
 * cancel(jobId) }`, and `sobolProgress(jobId)` for a job that may run a Sobol
 * analysis; `result` is the last status the server returned for a finished run and
 * `progress` the run's CA progress file (CA #702), as the last poll read it.
 */
function usePolledJob(api, options = {}) {
  const intervalMs = options.intervalMs ?? 1000
  const state = ref('idle') // idle | running | done | error | cancelled
  const lines = ref([])
  const error = ref('')
  const result = ref(null)
  const progress = ref(null)
  const sobol = api.sobolProgress ? useSobolFollow(api.sobolProgress, options) : null
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
    error.value = ''
    result.value = null
    progress.value = null
    sobol?.reset()
  }

  async function start(modelId, settings, currentParams = null) {
    reset()
    state.value = 'running'
    try {
      const { job_id } = await api.start(modelId, settings, currentParams)
      jobId = job_id
      sobol?.follow(jobId)
      await poll()
    } catch (e) {
      state.value = 'error'
      error.value = errorMessage(e)
    }
  }

  async function poll() {
    if (!jobId) return
    try {
      const s = await api.status(jobId, offset)
      if (s.lines?.length) {
        lines.value = lines.value.concat(s.lines)
        offset = s.next_offset
      }
      state.value = s.state
      progress.value = s.progress ?? null
      if (s.state === 'running') {
        timer = setTimeout(poll, intervalMs)
      } else {
        sobol?.finish(jobId)
        result.value = s.state === 'done' ? s : null
        error.value = s.error || ''
      }
    } catch (e) {
      state.value = 'error'
      error.value = errorMessage(e)
    }
  }

  async function cancel() {
    if (timer) clearTimeout(timer)
    timer = null
    if (jobId) {
      try {
        await api.cancel(jobId)
        sobol?.finish(jobId)
        progress.value = (await api.status(jobId, offset)).progress ?? null
      } catch {
        /* best effort */
      }
      state.value = 'cancelled'
    }
  }

  const running = computed(() => state.value === 'running')
  const sobolProgress = sobol?.data ?? ref(null)
  return { state, lines, error, result, progress, sobolProgress, running, start, cancel, reset }
}

/** Parameter reduction over a Sobol run (CA's `param_reduction`). */
export function useParamReduction(options = {}) {
  const job = usePolledJob(
    {
      start: startParamReduction,
      status: getParamReductionStatus,
      cancel: cancelParamReduction,
      sobolProgress: (id) => getSobolProgress(id, 'param_reduction'),
    },
    options,
  )
  const reduction = computed(() => job.result.value?.reduction ?? null)
  return { ...job, reduction }
}

/**
 * CA's identifiability analysis, run through the UQ runner's laplace method with
 * the `ia_options` the panel built. A Laplace run reports a posterior (`params`),
 * a profile-likelihood run its `profiles`.
 */
export function useIdentifiabilityAnalysis(options = {}) {
  const job = usePolledJob(
    { start: (id, settings) => startUQ(id, settings), status: getUQStatus, cancel: cancelUQ },
    options,
  )
  const params = computed(() => job.result.value?.params ?? [])
  const profiles = computed(() => job.result.value?.profiles ?? null)
  return { ...job, params, profiles }
}
