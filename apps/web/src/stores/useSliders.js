import { reactive, computed } from 'vue'

const LOG_RANGE_THRESHOLD = 1e4
const LOG_MIN_THRESHOLD = 1e-3

/** Integer resolution of the underlying (linear) PrimeVue Slider track. */
export const SLIDER_STEPS = 1000

/**
 * Heuristic: use a log slider when the range
 * spans more than four orders of magnitude, or the lower bound is tiny. A range
 * that touches or crosses zero can't be log-mapped, so it stays linear.
 */
export function shouldUseLog(min, max) {
  if (min <= 0) return false
  const lo = Math.min(Math.abs(min), Math.abs(max))
  const hi = Math.max(Math.abs(min), Math.abs(max))
  if (min < LOG_MIN_THRESHOLD) return true
  return hi / lo > LOG_RANGE_THRESHOLD
}

export function clamp(value, min, max) {
  return Math.min(max, Math.max(min, value))
}

/** A log slider is only valid for a strictly-positive range. */
function isLogSlider(s) {
  return s.log && s.min > 0 && s.max > 0
}

/** Map a slider's current value to its [0, SLIDER_STEPS] track position. */
export function valueToSlider(s) {
  if (s.max === s.min) return 0
  const v = clamp(s.value, s.min, s.max)
  if (isLogSlider(s)) {
    const lo = Math.log(s.min)
    const hi = Math.log(s.max)
    return Math.round((SLIDER_STEPS * (Math.log(v) - lo)) / (hi - lo))
  }
  return Math.round((SLIDER_STEPS * (v - s.min)) / (s.max - s.min))
}

/** Map a [0, SLIDER_STEPS] track position back to a value (log or linear). */
export function sliderToValue(s, pos) {
  const frac = pos / SLIDER_STEPS
  if (isLogSlider(s)) {
    const lo = Math.log(s.min)
    const hi = Math.log(s.max)
    return Math.exp(lo + frac * (hi - lo))
  }
  return s.min + frac * (s.max - s.min)
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
    const rawValue = clamp(opts.value ?? (min + max) / 2, min, max)
    sliders[qname] = {
      qname,
      min,
      max,
      log,
      value: rawValue,
      // The value the slider was created with, for "reset to init".
      init: rawValue,
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

  /** Reset every slider's value back to the value it was created with. */
  function resetToInit() {
    for (const key of Object.keys(sliders)) sliders[key].value = sliders[key].init
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

  return {
    sliders,
    addSlider,
    removeSlider,
    setValue,
    resetToInit,
    clear,
    paramDict,
    count,
  }
}
