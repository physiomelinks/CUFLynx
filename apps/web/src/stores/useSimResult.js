import { ref, shallowRef } from 'vue'

/** Stores the time series from the most recent simulate / protocol run. */
export function useSimResult() {
  // { time: number[], outputs: { qname: number[] } }
  const result = shallowRef(null)
  const status = ref('idle') // idle | running | ok | error
  const message = ref('')
  const lastRunMs = ref(null)

  function setRunning() {
    status.value = 'running'
    message.value = ''
  }

  function setResult(data, elapsedMs = null) {
    result.value = data
    status.value = 'ok'
    lastRunMs.value = elapsedMs
  }

  function setError(msg) {
    status.value = 'error'
    message.value = msg
  }

  return { result, status, message, lastRunMs, setRunning, setResult, setError }
}
