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

/**
 * Map an arbitrary value to its [0, SLIDER_STEPS] position on ``s``'s track.
 *
 * Split out from valueToSlider so a saved run's parameter can be marked on the
 * slider (#126) using the very same log/linear mapping the handle uses — a
 * second implementation would drift and put the marker somewhere the handle
 * never sits.
 */
export function positionFor(s, value) {
  if (s.max === s.min) return 0
  const v = clamp(value, s.min, s.max)
  if (isLogSlider(s)) {
    const lo = Math.log(s.min)
    const hi = Math.log(s.max)
    return Math.round((SLIDER_STEPS * (Math.log(v) - lo)) / (hi - lo))
  }
  return Math.round((SLIDER_STEPS * (v - s.min)) / (s.max - s.min))
}

/** Map a slider's current value to its [0, SLIDER_STEPS] track position. */
export function valueToSlider(s) {
  return positionFor(s, s.value)
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
  // qname -> value for parameters that are *not* calibrated but whose value the
  // user set in the params editor (#350). Kept apart from `sliders` because they
  // have no range, no handle and nothing to drag: they are a fixed point the
  // solver is given, not a degree of freedom.
  const fixedValues = reactive({})

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
      // Every model variable this one slider drives (issue #193). A params_for_id
      // row naming several vessels is one parameter that varies in all of them
      // simultaneously, so it gets one handle and writes to all of its qnames.
      // Always at least [qname], so no consumer has to special-case the group.
      // For a modifier these are its *modified* qnames.
      qnames: opts.qnames?.length ? [...opts.qnames] : [qname],
      // Something to tell the user about this parameter (a group whose members
      // started from different values); null for the ordinary case.
      warning: opts.warning ?? null,
      // 'modifier' when this slider carries a dimensionless θ that expands to
      // θ·baseline per member (#208); 'free' otherwise.
      kind: opts.kind ?? 'free',
      operation: opts.operation ?? null,
      // Per-member model default ({qname: baseline}); the baselineᵢ of
      // θ·baselineᵢ. Members absent here are unresolved and skipped.
      baselines: opts.baselines ? { ...opts.baselines } : null,
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

  /**
   * Apply loaded values ({ qname: value }) onto the sliders, clamped to each
   * slider's range. Only touches sliders that exist (unknown qnames are ignored);
   * used by "Reset to saved" after loading an .npy/.csv file (issue #106).
   */
  function applyValues(values) {
    for (const [qname, value] of Object.entries(values || {})) {
      const slider = sliders[qname]
      if (slider) slider.value = clamp(value, slider.min, slider.max)
    }
  }

  function clear() {
    for (const key of Object.keys(sliders)) delete sliders[key]
    for (const key of Object.keys(fixedValues)) delete fixedValues[key]
  }

  /**
   * Set (or clear, with null) the value of a parameter that is not calibrated,
   * so the solver is given it instead of the model's own (#350).
   *
   * A qname that has a slider is ignored: that parameter *is* calibrated, its
   * slider owns the value, and storing a second one would mean the run and the
   * handle on screen could disagree.
   */
  function setFixedValue(qname, value) {
    if (sliders[qname]) return
    if (value == null || !Number.isFinite(Number(value))) delete fixedValues[qname]
    else fixedValues[qname] = Number(value)
  }

  function clearFixedValues() {
    for (const key of Object.keys(fixedValues)) delete fixedValues[key]
  }

  /**
   * Param dict ({ qname: value }) for /simulate and /protocol/run.
   *
   * A grouped slider (#193) contributes one entry per member, because the model
   * has no single variable for the group -- the components are what the solver
   * sets. This is the one place the group is expanded, so everything downstream
   * (the engine, the calibration start point, local SA's nominal point) sees the
   * same fully-specified point it always did.
   */
  const paramDict = computed(() => {
    const out = {}
    // Parameters the study does not calibrate, whose value the user set in the
    // params editor's baseline column (#350). They have no slider -- that is what
    // "not calibrated" means here -- so without this the solver would keep using
    // the model's own value and the edit would appear to do nothing. Written
    // first so a slider always wins: for a calibrated parameter the slider is the
    // live handle, and a stale fixed value must not override where it was dragged.
    for (const [qname, value] of Object.entries(fixedValues)) out[qname] = value
    for (const key of Object.keys(sliders)) {
      const s = sliders[key]
      if (s.kind === 'modifier') {
        // The slider's value is θ; the model gets θ·baseline per member. A
        // member with no resolved baseline is skipped (the row already warned)
        // rather than handed θ as if it were a physical value.
        for (const qname of s.qnames ?? [key]) {
          const baseline = s.baselines?.[qname]
          if (baseline != null) out[qname] = s.value * baseline
        }
        continue
      }
      for (const qname of s.qnames ?? [key]) out[qname] = s.value
    }
    return out
  })

  /**
   * The θ-aware point for the analysis routes (calibration start-from-current,
   * local-SA nominal): one entry per slider under its *anchor* key -- the
   * slider's own key, which for a modifier is modifies[0]. Those consumers
   * match by param_names[i][0], which for a modifier IS modifies[0], so θ
   * lands in the modifier's slot; expanding it as paramDict does would hand
   * them a physical value where CA samples θ.
   */
  const analysisDict = computed(() => {
    const out = {}
    for (const key of Object.keys(sliders)) out[key] = sliders[key].value
    return out
  })

  /**
   * The modifier block for /api/cost_sensitivity: differenced in θ server-side.
   * Empty array when there are none, so callers can send it unconditionally.
   */
  const modifierSpecs = computed(() => {
    const out = []
    for (const key of Object.keys(sliders)) {
      const s = sliders[key]
      if (s.kind !== 'modifier') continue
      out.push({
        name: s.name_for_plotting ?? key,
        anchor: key,
        targets: [...(s.qnames ?? [key])],
        operation: s.operation ?? 'scale',
        baselines: { ...(s.baselines ?? {}) },
        value: s.value,
        bounds: [s.min, s.max],
      })
    }
    return out
  })

  /** Current qname order — the order an .npy vector is saved/loaded in (#106). */
  const order = computed(() => Object.keys(sliders))

  const count = computed(() => Object.keys(sliders).length)

  return {
    sliders,
    fixedValues,
    addSlider,
    removeSlider,
    setValue,
    setFixedValue,
    clearFixedValues,
    resetToInit,
    applyValues,
    clear,
    paramDict,
    analysisDict,
    modifierSpecs,
    order,
    count,
  }
}
