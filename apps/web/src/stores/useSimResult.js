import { ref, shallowRef } from 'vue'

/** Stores the time series from the most recent simulate / protocol run. */
export function useSimResult() {
  // { time: number[], outputs: { qname: number[] } }
  const result = shallowRef(null)
  // protocol runs: [{ time, outputs }] per experiment
  const experiments = shallowRef([])
  // What the run's parameters cost against the loaded obs_data (#159), or null.
  // Its own ref because the two setters below are exclusive -- a protocol run
  // nulls `result` -- and the cost belongs to the run either way.
  const cost = shallowRef(null)
  const warnings = ref([])
  const status = ref('idle') // idle | running | ok | error
  const message = ref('')
  const lastRunMs = ref(null)

  function setRunning() {
    status.value = 'running'
    message.value = ''
    warnings.value = []
  }

  function setResult(data, elapsedMs = null) {
    result.value = data
    cost.value = data?.cost ?? null
    experiments.value = []
    status.value = 'ok'
    lastRunMs.value = elapsedMs
  }

  function setExperiments(exps, warns = [], elapsedMs = null, runCost = null) {
    experiments.value = exps ?? []
    cost.value = runCost
    result.value = null
    warnings.value = warns ?? []
    status.value = 'ok'
    lastRunMs.value = elapsedMs
  }

  function setError(msg) {
    status.value = 'error'
    message.value = msg
  }

  return {
    result,
    experiments,
    warnings,
    status,
    message,
    lastRunMs,
    setRunning,
    setResult,
    setExperiments,
    setError,
    cost,
  }
}
