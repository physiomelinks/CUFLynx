import { ref, computed } from 'vue'
import {
  startParamReduction,
  getParamReductionStatus,
  cancelParamReduction,
  startUQ,
  getUQStatus,
  cancelUQ,
  errorMessage,
} from '../lib/api'

/**
 * A polled analysis job: start, stream the log, keep the final status.
 *
 * `api` is `{ start(modelId, settings, currentParams), status(jobId, offset),
 * cancel(jobId) }`; `result` is the last status the server returned for a
 * finished run.
 */
function usePolledJob(api, options = {}) {
  const intervalMs = options.intervalMs ?? 1000
  const state = ref('idle') // idle | running | done | error | cancelled
  const lines = ref([])
  const error = ref('')
  const result = ref(null)
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
  }

  async function start(modelId, settings, currentParams = null) {
    reset()
    state.value = 'running'
    try {
      const { job_id } = await api.start(modelId, settings, currentParams)
      jobId = job_id
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
      if (s.state === 'running') {
        timer = setTimeout(poll, intervalMs)
      } else {
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
      } catch {
        /* best effort */
      }
      state.value = 'cancelled'
    }
  }

  const running = computed(() => state.value === 'running')
  return { state, lines, error, result, running, start, cancel, reset }
}

/** Parameter reduction over a Sobol run (CA's `param_reduction`). */
export function useParamReduction(options = {}) {
  const job = usePolledJob(
    { start: startParamReduction, status: getParamReductionStatus, cancel: cancelParamReduction },
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
