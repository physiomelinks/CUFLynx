import { ref, shallowRef } from 'vue'

/** Stores the time series from the most recent simulate / protocol run. */
export function useSimResult() {
  // { time: number[], outputs: { qname: number[] } }
  const result = shallowRef(null)
  // protocol runs: [{ time, outputs }] per experiment
  const experiments = shallowRef([])
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
    experiments.value = []
    status.value = 'ok'
    lastRunMs.value = elapsedMs
  }

  function setExperiments(exps, warns = [], elapsedMs = null) {
    experiments.value = exps ?? []
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
  }
}
