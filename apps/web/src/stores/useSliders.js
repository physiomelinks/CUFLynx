import { reactive, computed } from 'vue'

const LOG_RANGE_THRESHOLD = 1e4
const LOG_MIN_THRESHOLD = 1e-3

/**
 * Heuristic ported from cellml_explorer.html: use a log slider when the range
 * spans more than four orders of magnitude, or the lower bound is tiny.
 */
export function shouldUseLog(min, max) {
  const lo = Math.min(Math.abs(min), Math.abs(max))
  const hi = Math.max(Math.abs(min), Math.abs(max))
  if (min <= 0) return hi > LOG_RANGE_THRESHOLD
  if (min < LOG_MIN_THRESHOLD) return true
  return lo === 0 ? hi > LOG_RANGE_THRESHOLD : hi / lo > LOG_RANGE_THRESHOLD
}

export function clamp(value, min, max) {
  return Math.min(max, Math.max(min, value))
}

/**
 * Factory for an isolated slider store. The app instantiates one singleton and
 * shares it via provide/inject; tests create fresh stores per case.
 */
export function useSliders() {
  const sliders = reactive({})

  function addSlider(qname, opts = {}) {
    const min = opts.min ?? 0
    const max = opts.max ?? 1
    const log = opts.log ?? shouldUseLog(min, max)
    const rawValue = opts.value ?? (min + max) / 2
    sliders[qname] = {
      qname,
      min,
      max,
      log,
      value: clamp(rawValue, min, max),
      name_for_plotting: opts.name_for_plotting ?? qname,
    }
    return sliders[qname]
  }

  function removeSlider(qname) {
    delete sliders[qname]
  }

  function setValue(qname, value) {
    const slider = sliders[qname]
    if (slider) slider.value = clamp(value, slider.min, slider.max)
  }

  function clear() {
    for (const key of Object.keys(sliders)) delete sliders[key]
  }

  /** Param dict ({ qname: value }) for /simulate and /protocol/run. */
  const paramDict = computed(() => {
    const out = {}
    for (const key of Object.keys(sliders)) out[key] = sliders[key].value
    return out
  })

  const count = computed(() => Object.keys(sliders).length)

  return { sliders, addSlider, removeSlider, setValue, clear, paramDict, count }
}
