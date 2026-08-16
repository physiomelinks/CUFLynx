<script setup>
import { ref, computed, watch } from 'vue'
import { renderMath, renderOutputLabel } from '../lib/math'
import { fmtSci } from '../lib/format'
import { niceTicks, fmtTick } from '../lib/plot'
import ScatterChart from './ScatterChart.vue'

const props = defineProps({
  // Sensitivity: { S1: {outName: {param: val}}, ST: {...}, local: {...} }
  indices: { type: Object, default: null },
  paramNames: { type: Array, default: () => [] },
  outputNames: { type: Array, default: () => [] },
  // qname -> LaTeX/plotting name, for the heatmap row labels.
  paramLabels: { type: Object, default: () => ({}) },
  // Local SA only: the nominal (linearisation) parameter point (aligned with
  // paramNames) and a short description of where it came from.
  nominal: { type: Array, default: null },
  nominalSource: { type: String, default: null },
  // Calibration: one error per observable, aligned with errorLabels.
  percentError: { type: Array, default: null },
  stdError: { type: Array, default: null },
  errorLabels: { type: Array, default: () => [] },
  // Issue #159: the cost and per-observable errors of whatever the sliders
  // currently say, and a baseline to compare them against (the calibration best
  // fit, or a pinned parameter set). Both {cost, items:[{label, percent_error,
  // std_error, cost}]}; null when unknown, which is not the same as zero.
  currentCost: { type: Object, default: null },
  baselineCost: { type: Object, default: null },
  // UQ: per-parameter posteriors [{qname, mean, std, q05, q50, q95, bins, counts}].
  uqParams: { type: Array, default: () => [] },
  uqMethod: { type: String, default: null },
  // Saved sensitivity runs for comparison: [{ id, label, at }]. The currently
  // shown run's matrix is in `indices`; this list lets the user switch between
  // saved runs (e.g. global Sobol vs local FD) without overwriting.
  savedResults: { type: Array, default: () => [] },
  selectedResultId: { type: [Number, String], default: null },
  // Emulator (CA #333): the trained emulator's metadata and the held-out points
  // it was scored on. Both come from circulatory_autogen's own files -- the
  // statistics say how wrong the emulator is, the points say where.
  emulatorMetadata: { type: Object, default: null },
  emulatorErrorPoints: { type: Object, default: null },
  emulatorInUse: { type: Boolean, default: false },
  // The calibration best fit scored twice (#333): by the solver and by the
  // emulator, at the *same* parameters and through the same cost path. Same
  // shape as `currentCost`, so the bars need no special case for either. Null
  // where that side could not be measured -- no emulator, or no calibration.
  bestFitModelCost: { type: Object, default: null },
  bestFitEmulatorCost: { type: Object, default: null },
  // Whether the last calibration ran on the emulator. It decides which side the
  // toggle starts on, so what is first on screen is what the calibration
  // actually minimised and its reported cost reconciles rather than puzzles.
  calibrationUsedEmulator: { type: Boolean, default: false },
})

const emit = defineEmits(['select-result', 'remove-result', 'clear-results'])

// ---- Emulator error --------------------------------------------------------
// Everything here is read from CA's emulator_metadata.json / emulator_validation.npz;
// nothing is recomputed. R2 alone cannot rank features -- one can score well and
// still read systematically high -- so the table carries the statistics that can.
const hasEmulator = computed(() => !!props.emulatorMetadata)

const emulatorRows = computed(() => {
  const meta = props.emulatorMetadata
  if (!meta) return []
  return (meta.feature_labels ?? []).map((label, i) => ({
    label,
    r2: meta.feature_r2?.[i] ?? null,
    rmse: meta.feature_rmse?.[i] ?? null,
    mae: meta.feature_mae?.[i] ?? null,
    bias: meta.feature_bias?.[i] ?? null,
    maxAbs: meta.feature_max_abs_error?.[i] ?? null,
    nrmse: meta.feature_nrmse?.[i] ?? null,
  }))
})

/** Which feature the parity/residual plots show. */
const emulatorFeature = ref(0)
watch(emulatorRows, (rows) => {
  if (emulatorFeature.value >= rows.length) emulatorFeature.value = 0
})

const emulatorFeatureOptions = computed(() =>
  emulatorRows.value.map((r, i) => ({ label: r.label, value: i })),
)

function fmtStat(value, digits = 4) {
  if (value == null || Number.isNaN(Number(value))) return '—'
  const n = Number(value)
  // Small numbers in a table of R2s are unreadable in fixed notation.
  if (n !== 0 && Math.abs(n) < 1e-3) return n.toExponential(1)
  return n.toFixed(digits)
}

/**
 * Parity plot: predicted against true, in *data* units. ScatterChart does the
 * scaling, so the 1:1 line is drawn in the same space as the points it is judged
 * against — as a CSS-rotated div it was only a true diagonal when the box
 * happened to be square. Both axes share one range; a parity plot with
 * independent axes makes any emulator look perfect.
 */
const parityPoints = computed(() => {
  const pts = props.emulatorErrorPoints
  const col = emulatorFeature.value
  if (!pts?.y_true?.length) return null
  const truth = pts.y_true.map((row) => Number(row[col]))
  const pred = pts.y_pred.map((row) => Number(row[col]))
  const lo = Math.min(...truth, ...pred)
  const hi = Math.max(...truth, ...pred)
  return {
    lo,
    hi,
    points: truth.map((t, i) => ({
      x: t,
      y: pred[i],
      title: `simulated ${t}, emulated ${pred[i]}`,
    })),
  }
})

/**
 * Residual against each parameter: where in the space the emulator goes wrong,
 * which is the question a single score cannot answer.
 */
/**
 * Residuals, normalised by the ground truth, so the axis carries a size and not
 * just a zero line: 0.05 is "5% out", which is readable without knowing what
 * this feature's units are or how large it usually is. A raw residual of 3e-4
 * says nothing on its own.
 *
 * Where a truth value is zero the fraction is undefined, so that feature falls
 * back to normalising by the spread of its own truths (what CA's nRMSE does) and
 * the caption says so. Silently plotting Infinity, or quietly dropping those
 * points, would both misreport the error.
 */
const normalisedResiduals = computed(() => {
  const pts = props.emulatorErrorPoints
  const col = emulatorFeature.value
  if (!pts?.residual?.length) return null
  const residuals = pts.residual.map((row) => Number(row[col]))
  const truths = (pts.y_true ?? []).map((row) => Number(row[col]))

  const usable = truths.length === residuals.length && truths.every((t) => t !== 0)
  if (usable) {
    return { basis: 'truth', values: residuals.map((r, i) => r / truths[i]) }
  }
  const spread = truths.length ? Math.max(...truths) - Math.min(...truths) : 0
  if (spread > 0) {
    return { basis: 'spread', values: residuals.map((r) => r / spread) }
  }
  // Nothing to normalise against: show the residual itself rather than invent a scale.
  return { basis: 'raw', values: residuals }
})

const residualByParam = computed(() => {
  const pts = props.emulatorErrorPoints
  const norm = normalisedResiduals.value
  if (!norm) return []
  const worst = Math.max(...norm.values.map((r) => Math.abs(r))) || 1
  return (pts.param_entry_labels ?? []).map((label, p) => {
    const values = pts.theta.map((row) => Number(row[p]))
    return {
      label,
      worst,
      basis: norm.basis,
      // Symmetric about zero, so a systematic offset reads as an offset rather
      // than being re-centred away by the axis.
      lo: Math.min(...values),
      hi: Math.max(...values),
      points: values.map((v, i) => ({
        x: v,
        y: norm.values[i],
        title: `${label} = ${v}, normalised residual ${norm.values[i]}`,
      })),
    }
  })
})

/** What the residual axis is divided by, for the axis label and the caption. */
const RESIDUAL_BASIS = {
  truth: { axis: 'residual / truth', note: 'each residual divided by its own ground-truth value, so 0.05 is 5% out' },
  spread: { axis: 'residual / range', note: 'divided by the spread of this feature’s truths, because at least one of them is zero' },
  raw: { axis: 'residual', note: 'left unnormalised: every truth for this feature is the same value' },
}
const residualBasis = computed(
  () => RESIDUAL_BASIS[normalisedResiduals.value?.basis ?? 'raw'],
)

// ---- Sensitivity heatmap ---------------------------------------------------
// Sobol runs carry S1/ST; a local (finite-difference) run carries a single
// 'local' matrix of relative sensitivities. Offer whichever kinds are present.
const TYPE_LABELS = {
  S1: 'First-order (S₁)',
  ST: 'Total-order (Sₜ)',
  local: 'Local (∂lnY/∂lnP)',
}
const TYPE_ORDER = ['S1', 'ST', 'local']
const availableTypes = computed(() =>
  TYPE_ORDER.filter((t) => props.indices?.[t]),
)
const indexType = ref('ST')
watch(
  availableTypes,
  (types) => {
    if (!types.length || types.includes(indexType.value)) return
    // Keep the long-standing default of total-order for Sobol runs.
    indexType.value = types.includes('ST') ? 'ST' : types[0]
  },
  { immediate: true },
)
const isLocal = computed(() => indexType.value === 'local')

// A local SA run carries a 'local' matrix (Sobol runs carry S1/ST instead). Only
// for such a run do we show the nominal parameter point it was linearised about.
const isLocalRun = computed(() => !!props.indices?.local)
const nominalPairs = computed(() => {
  if (!isLocalRun.value || !Array.isArray(props.nominal)) return []
  return props.paramNames.map((qname, i) => ({
    qname,
    label: renderMath(props.paramLabels[qname] ?? qname),
    value: fmtSci(props.nominal[i]),
  }))
})

const hasSensitivity = computed(
  () =>
    props.indices &&
    props.paramNames.length > 0 &&
    props.outputNames.length > 0,
)

// indices[type][outName][param] -> value (may be missing / non-finite)
function valueAt(outName, param) {
  const v = props.indices?.[indexType.value]?.[outName]?.[param]
  return typeof v === 'number' && Number.isFinite(v) ? v : null
}

// Local coefficients are signed and unbounded; scale colours by the largest
// magnitude in the current matrix so the diverging ramp uses its full range.
const maxAbsLocal = computed(() => {
  if (!isLocal.value) return 1
  let m = 0
  for (const out of props.outputNames) {
    for (const p of props.paramNames) {
      const v = valueAt(out, p)
      if (v != null) m = Math.max(m, Math.abs(v))
    }
  }
  return m || 1
})

// Viridis-ish ramp for Sobol indices (∈ [0,1]); blue–white–red diverging ramp
// for signed local sensitivities.
const RAMP = [
  [68, 1, 84],
  [59, 82, 139],
  [33, 145, 140],
  [94, 201, 98],
  [253, 231, 37],
]
const DIVERGING = [
  [33, 102, 172],
  [247, 247, 247],
  [178, 24, 43],
]

function lerpRamp(ramp, t) {
  t = Math.max(0, Math.min(1, t))
  const seg = t * (ramp.length - 1)
  const i = Math.min(ramp.length - 2, Math.floor(seg))
  const f = seg - i
  const [r1, g1, b1] = ramp[i]
  const [r2, g2, b2] = ramp[i + 1]
  const r = Math.round(r1 + (r2 - r1) * f)
  const g = Math.round(g1 + (g2 - g1) * f)
  const b = Math.round(b1 + (b2 - b1) * f)
  return `rgb(${r}, ${g}, ${b})`
}

function colorFor(value) {
  if (value == null) return 'transparent'
  if (isLocal.value) {
    // Map [-maxAbs, +maxAbs] -> [0, 1] across the diverging ramp (0 -> centre).
    const t = Math.max(-1, Math.min(1, value / maxAbsLocal.value))
    return lerpRamp(DIVERGING, (t + 1) / 2)
  }
  return lerpRamp(RAMP, value) // Sobol indices clamp to [0, 1]
}

// Light text on the dark (high-magnitude) cells, dark text on the light centre.
function textColorFor(value) {
  if (value == null) return 'inherit'
  if (isLocal.value) {
    return Math.abs(value) / maxAbsLocal.value > 0.5 ? '#eee' : '#111'
  }
  return Math.max(0, Math.min(1, value)) > 0.55 ? '#111' : '#eee'
}

// Compact cell label: exponential for very large/small magnitudes.
function fmtCell(value) {
  if (value == null) return '–'
  const a = Math.abs(value)
  return a !== 0 && (a >= 100 || a < 0.01) ? value.toExponential(1) : value.toFixed(2)
}

// ---- Calibration error bars ------------------------------------------------
// ---- ...measured against the model or against the emulator (#333) ----------
// A calibration run with "use the emulator" on fits the *surrogate*, so the
// error vectors it writes describe the emulator's features and its best cost is
// an em cost. The same best fit run through the solver is the other half of the
// answer, and until now there was no way to ask for it. Both sides arrive
// already computed (one request, one point), so this switches between two
// payloads and never triggers a run.
const bothSourcesAvailable = computed(
  () =>
    !!props.bestFitModelCost?.items?.length && !!props.bestFitEmulatorCost?.items?.length,
)
// Off by default, like `compare` below and for the same reason: a second set of
// bars on every chart is a comparison when you asked for one and clutter when you
// did not. It is a *comparison* toggle, not a source switch -- the single-source
// bars stay on the forward model, and ticking this adds the emulator beside it.
const compareEmulator = ref(false)
const calibrationSource = computed(() => {
  if (!bothSourcesAvailable.value) return null
  return props.bestFitModelCost
})
// Said in words, not only by a checked box: the two can differ a lot, and a
// screenshot of the bars has to say which model produced them.
const calibrationSourceLabel = computed(() => 'the forward model')

/** One side's rows for a field, with the observables it could not score dropped. */
function sourceSeries(field) {
  const items = (calibrationSource.value?.items ?? []).filter((i) => i[field] != null)
  return { values: items.map((i) => i[field]), labels: items.map((i) => i.label) }
}

const hasCalibration = computed(
  () =>
    !!calibrationSource.value ||
    (Array.isArray(props.percentError) &&
      props.percentError.length > 0 &&
      Array.isArray(props.stdError) &&
      props.stdError.length > 0),
)

// Green where the fit is good, red where the error is large (so problem
// observables stand out at a glance). |value| beyond `hi` is fully red.
function barColors(values, hi) {
  return (values ?? []).map((v) => {
    const t = Math.max(0, Math.min(1, Math.abs(v) / hi))
    const r = Math.round(112 + (232 - 112) * t)
    const g = Math.round(173 + (74 - 173) * t)
    const b = Math.round(71 + (95 - 71) * t)
    return `rgb(${r}, ${g}, ${b})`
  })
}

// One zero-centered HTML bar per observable. Widths are normalised to the
// largest |error| in that chart; positive errors extend right of centre,
// negative left. `hi` sets the green->red colour scale; `fmt` the value label.
function errorBars(values, hi, fmt, labels = null) {
  const vals = values ?? []
  const names = labels ?? props.errorLabels
  const maxAbs = Math.max(1e-9, ...vals.map((v) => Math.abs(v)))
  const colors = barColors(vals, hi)
  return vals.map((v, i) => {
    const halfPct = (Math.min(1, Math.abs(v) / maxAbs) * 50).toFixed(2)
    return {
      label: names[i] ?? `obs ${i}`,
      color: colors[i],
      width: `${halfPct}%`,
      left: v >= 0 ? '50%' : `${(50 - Number(halfPct)).toFixed(2)}%`,
      text: fmt(v),
    }
  })
}

// The selected source's rows where there is a choice, and the calibration's own
// vectors where there is not -- the same bars either way, only the numbers
// behind them change.
const percentBars = computed(() => {
  const fmt = (v) => `${v.toFixed(1)}%`
  if (!calibrationSource.value) return errorBars(props.percentError, 20, fmt)
  const { values, labels } = sourceSeries('percent_error')
  return errorBars(values, 20, fmt, labels)
})
const stdBars = computed(() => {
  const fmt = (v) => `${v.toFixed(2)}σ`
  if (!calibrationSource.value) return errorBars(props.stdError, 3, fmt)
  const { values, labels } = sourceSeries('std_error')
  return errorBars(values, 3, fmt, labels)
})

// ---- Current parameters vs the best fit (#159) -----------------------------
// Off by default: a second set of bars on every chart is a comparison when you
// asked for one and clutter when you did not.
const compare = ref(false)

const hasCurrentCost = computed(() => !!props.currentCost?.items?.length)
const comparable = computed(() => hasCurrentCost.value || !!props.baselineCost?.items?.length)

function formatCost(value) {
  if (value == null || !Number.isFinite(value)) return '—'
  const abs = Math.abs(value)
  if (abs !== 0 && (abs < 1e-3 || abs >= 1e5)) return value.toExponential(3)
  return value.toPrecision(4).replace(/\.?0+$/, '')
}

/**
 * Bars for a cost payload's errors, in one flat colour.
 *
 * Deliberately not the green->red scale the calibration bars use: those encode
 * *how bad* an error is, and reusing them here would make two series that must
 * be told apart look like one series in two moods. A comparison needs identity,
 * so each set gets a colour of its own.
 */
function costBars(payload, field, fmt, color) {
  const items = payload?.items ?? []
  const values = items.map((i) => i[field]).filter((v) => v != null)
  const maxAbs = Math.max(1e-9, ...values.map((v) => Math.abs(v)))
  return items
    .filter((i) => i[field] != null)
    .map((i) => {
      const halfPct = (Math.min(1, Math.abs(i[field]) / maxAbs) * 50).toFixed(2)
      return {
        label: i.label,
        color,
        width: `${halfPct}%`,
        left: i[field] >= 0 ? '50%' : `${(50 - Number(halfPct)).toFixed(2)}%`,
        text: fmt(i[field]),
      }
    })
}

const CURRENT_COLOUR = '#5b9bd5'
const BASELINE_COLOUR = '#a142f4'
// Distinct from both, since an emulator comparison can be on screen beside a
// current-vs-baseline one.
const EMULATOR_COLOUR = '#e8710a'

const currentPercentBars = computed(() =>
  costBars(props.currentCost, 'percent_error', (v) => `${v.toFixed(1)}%`, CURRENT_COLOUR),
)
const currentStdBars = computed(() =>
  costBars(props.currentCost, 'std_error', (v) => `${v.toFixed(2)}σ`, CURRENT_COLOUR),
)
const baselinePercentBars = computed(() =>
  costBars(props.baselineCost, 'percent_error', (v) => `${v.toFixed(1)}%`, BASELINE_COLOUR),
)
// Forward model vs emulator at the SAME best fit -- the gap between the pairs is
// the surrogate's error there, drawn the way the current-vs-baseline pairs are.
const modelPercentBars = computed(() =>
  costBars(props.bestFitModelCost, 'percent_error', (v) => `${v.toFixed(1)}%`, CURRENT_COLOUR),
)
const modelStdBars = computed(() =>
  costBars(props.bestFitModelCost, 'std_error', (v) => `${v.toFixed(2)}σ`, CURRENT_COLOUR),
)
const emulatorPercentBars = computed(() =>
  costBars(props.bestFitEmulatorCost, 'percent_error', (v) => `${v.toFixed(1)}%`, EMULATOR_COLOUR),
)
const emulatorStdBars = computed(() =>
  costBars(props.bestFitEmulatorCost, 'std_error', (v) => `${v.toFixed(2)}σ`, EMULATOR_COLOUR),
)

const baselineStdBars = computed(() =>
  costBars(props.baselineCost, 'std_error', (v) => `${v.toFixed(2)}σ`, BASELINE_COLOUR),
)

// ---- UQ posterior densities ------------------------------------------------
const PLOT_W = 260
const PLOT_H = 60

const hasUQ = computed(() => Array.isArray(props.uqParams) && props.uqParams.length > 0)

// Build the SVG geometry for one parameter's posterior: a histogram silhouette
// (area polygon from bins/counts), a shaded q05–q95 band and a mean line.
function densityGeometry(p) {
  const bins = p.bins ?? []
  const counts = p.counts ?? []
  const xmin = bins[0]
  const xmax = bins[bins.length - 1]
  const xspan = xmax - xmin || 1
  const maxCount = Math.max(1, ...counts)
  const xOf = (v) => ((v - xmin) / xspan) * PLOT_W
  const yOf = (c) => PLOT_H - (c / maxCount) * PLOT_H

  const pts = [`0,${PLOT_H}`]
  for (let i = 0; i < counts.length; i++) {
    const y = yOf(counts[i]).toFixed(2)
    pts.push(`${xOf(bins[i]).toFixed(2)},${y}`, `${xOf(bins[i + 1]).toFixed(2)},${y}`)
  }
  pts.push(`${PLOT_W},${PLOT_H}`)

  const bandX = xOf(p.q05)
  return {
    points: pts.join(' '),
    meanX: xOf(p.mean).toFixed(2),
    bandX: bandX.toFixed(2),
    bandW: Math.max(0, xOf(p.q95) - bandX).toFixed(2),
    // The parameter scale. The plot is deliberately stretched to the row width,
    // which would distort text drawn inside it, so the tick *marks* live in the
    // SVG and their labels are HTML positioned by percentage.
    ticks: niceTicks(xmin, xmax).map((v) => ({
      at: xOf(v).toFixed(2),
      pct: (((v - xmin) / xspan) * 100).toFixed(3),
      text: fmtTick(v),
    })),
  }
}

const uqPlots = computed(() =>
  props.uqParams.map((p) => ({ ...p, geom: densityGeometry(p) })),
)

const uqMethodLabel = computed(() =>
  props.uqMethod === 'laplace' ? 'Laplace' : props.uqMethod === 'mcmc' ? 'MCMC' : '',
)
</script>

<template>
  <div class="analysis-panel" data-testid="analysis-panel">
    <!-- Sensitivity --------------------------------------------------------->
    <section class="analysis-section">
      <h2>Sensitivity</h2>
      <p v-if="!hasSensitivity" class="empty-hint">
        Run a sensitivity analysis to see the heatmap.
      </p>
      <template v-else>
        <div v-if="savedResults.length" class="saved-runs" data-testid="saved-runs">
          <span class="toolbar-label">Runs</span>
          <div class="run-chips">
            <span
              v-for="r in savedResults"
              :key="r.id"
              class="run-chip"
              :class="{ active: r.id === selectedResultId }"
              :data-testid="`run-chip-${r.id}`"
              :title="r.at ? `saved ${r.at}` : ''"
              @click="emit('select-result', r.id)"
            >
              {{ r.label }}
              <button
                class="run-x"
                title="remove this saved run"
                :data-testid="`run-remove-${r.id}`"
                @click.stop="emit('remove-result', r.id)"
              >
                ×
              </button>
            </span>
          </div>
          <button
            v-if="savedResults.length > 1"
            class="run-clear"
            data-testid="clear-runs"
            @click="emit('clear-results')"
          >
            Clear all
          </button>
        </div>

        <div
          v-if="nominalPairs.length"
          class="nominal-row"
          data-testid="nominal-row"
        >
          <span class="toolbar-label">Nominal</span>
          <div class="nominal-chips">
            <span v-for="p in nominalPairs" :key="p.qname" class="nominal-chip">
              <span class="nominal-name" v-html="p.label" />
              <span class="nominal-val">{{ p.value }}</span>
            </span>
          </div>
          <span v-if="nominalSource" class="nominal-source">from {{ nominalSource }}</span>
        </div>

        <div class="analysis-toolbar">
          <span class="toolbar-label">Index</span>
          <div class="type-toggle">
            <button
              v-for="t in availableTypes"
              :key="t"
              class="toggle-btn"
              :class="{ active: indexType === t }"
              :data-testid="`index-${t.toLowerCase()}`"
              @click="indexType = t"
            >
              {{ TYPE_LABELS[t] }}
            </button>
          </div>
        </div>

        <div class="table-wrap">
          <table class="heatmap" data-testid="heatmap-table">
            <thead>
              <tr>
                <th class="corner">parameter \ output</th>
                <th
                  v-for="out in outputNames"
                  :key="out"
                  class="col-head"
                  :title="out"
                  v-html="renderOutputLabel(out)"
                />
              </tr>
            </thead>
            <tbody>
              <tr v-for="param in paramNames" :key="param">
                <th
                  class="row-head"
                  :title="param"
                  v-html="renderMath(paramLabels[param] ?? param)"
                />
                <td
                  v-for="out in outputNames"
                  :key="out"
                  class="cell"
                  :style="{
                    backgroundColor: colorFor(valueAt(out, param)),
                    color: textColorFor(valueAt(out, param)),
                  }"
                  :title="`${param} → ${out}`"
                >
                  {{ fmtCell(valueAt(out, param)) }}
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </template>
    </section>

    <!-- Calibration --------------------------------------------------------->
    <section class="analysis-section">
      <h2>Calibration</h2>

      <!--
        The cost of the current parameters, in the tab where fit is judged
        (#159). Shown whether or not a calibration has run: a manual
        perturbation has a cost too, and that was the whole gap.
      -->
      <!--
        Each figure carries its own series colour (#221), the same one its bars
        and legend swatch use below. The two numbers sat in body text, so which
        cost belonged to which series had to be read from the caption alone.
        CURRENT_COLOUR / BASELINE_COLOUR stay the single definition.
      -->
      <div v-if="comparable" class="cost-summary" data-testid="analysis-cost">
        <div class="cost-figure">
          <span class="cost-caption">current parameters</span>
          <strong
            class="cost-number"
            :style="{ color: CURRENT_COLOUR }"
            data-testid="analysis-cost-current"
          >
            {{ formatCost(currentCost?.cost) }}
          </strong>
        </div>
        <div v-if="baselineCost" class="cost-figure">
          <span class="cost-caption">{{ baselineCost.label ?? 'baseline' }}</span>
          <strong
            class="cost-number"
            :style="{ color: BASELINE_COLOUR }"
            data-testid="analysis-cost-baseline"
          >
            {{ formatCost(baselineCost.cost) }}
          </strong>
        </div>
        <label v-if="baselineCost" class="cost-compare">
          <input v-model="compare" type="checkbox" data-testid="compare-costs" />
          compare current on the charts
        </label>
      </div>

      <template v-if="bothSourcesAvailable && compareEmulator">
        <section class="error-chart">
          <h3>Percentage error — forward model vs emulator</h3>
          <div class="chart-legend" data-testid="emulator-compare-legend">
            <span class="legend-item">
              <span class="legend-swatch" :style="{ background: CURRENT_COLOUR }" />
              forward model
            </span>
            <span class="legend-item">
              <span class="legend-swatch" :style="{ background: EMULATOR_COLOUR }" />
              emulator
            </span>
          </div>
          <div class="bar-list" data-testid="emulator-percent-chart">
            <div v-for="(b, i) in modelPercentBars" :key="`em-p${i}`" class="bar-row">
              <span class="bar-label" v-html="renderMath(b.label)" />
              <div class="bar-track">
                <span class="bar-zero" />
                <span class="bar-fill" :style="{ left: b.left, width: b.width, background: b.color }" />
                <span
                  v-if="emulatorPercentBars[i]"
                  class="bar-fill bar-fill-baseline"
                  :style="{
                    left: emulatorPercentBars[i].left,
                    width: emulatorPercentBars[i].width,
                    background: emulatorPercentBars[i].color,
                  }"
                />
              </div>
              <span class="bar-value">{{ b.text }}</span>
            </div>
          </div>
        </section>
        <section class="error-chart">
          <h3>Error in standard deviations — forward model vs emulator</h3>
          <div class="bar-list" data-testid="emulator-std-chart">
            <div v-for="(b, i) in modelStdBars" :key="`em-s${i}`" class="bar-row">
              <span class="bar-label" v-html="renderMath(b.label)" />
              <div class="bar-track">
                <span class="bar-zero" />
                <span class="bar-fill" :style="{ left: b.left, width: b.width, background: b.color }" />
                <span
                  v-if="emulatorStdBars[i]"
                  class="bar-fill bar-fill-baseline"
                  :style="{
                    left: emulatorStdBars[i].left,
                    width: emulatorStdBars[i].width,
                    background: emulatorStdBars[i].color,
                  }"
                />
              </div>
              <span class="bar-value">{{ b.text }}</span>
            </div>
          </div>
        </section>
      </template>

      <div
        v-if="bothSourcesAvailable"
        class="cost-summary emulator-compare-row"
        data-testid="calibration-source"
      >
        <label class="cost-compare">
          <input
            v-model="compareEmulator"
            type="checkbox"
            data-testid="compare-with-emulator"
          />
          compare with the emulator
        </label>
      </div>

      <p v-if="!hasCalibration && !comparable" class="empty-hint">
        Run a calibration to see per-observable fit errors.
      </p>
      <template v-if="comparable && compare">
        <section class="error-chart">
          <h3>Percentage error — current vs {{ baselineCost?.label ?? 'baseline' }}</h3>
          <div class="chart-legend" data-testid="compare-legend">
            <span class="legend-item">
              <span class="legend-swatch" :style="{ background: CURRENT_COLOUR }" />
              current parameters
            </span>
            <span class="legend-item">
              <span class="legend-swatch" :style="{ background: BASELINE_COLOUR }" />
              {{ baselineCost?.label ?? 'baseline' }}
            </span>
          </div>
          <div class="bar-list" data-testid="compare-percent-chart">
            <div v-for="(b, i) in currentPercentBars" :key="`c${i}`" class="bar-row">
              <span class="bar-label" v-html="renderMath(b.label)" />
              <div class="bar-track">
                <span class="bar-zero" />
                <span class="bar-fill" :style="{ left: b.left, width: b.width, background: b.color }" />
                <span
                  v-if="baselinePercentBars[i]"
                  class="bar-fill bar-fill-baseline"
                  :style="{
                    left: baselinePercentBars[i].left,
                    width: baselinePercentBars[i].width,
                    background: baselinePercentBars[i].color,
                  }"
                />
              </div>
              <span class="bar-value">{{ b.text }}</span>
            </div>
          </div>
        </section>
        <section class="error-chart">
          <h3>Error in standard deviations — current vs {{ baselineCost?.label ?? 'baseline' }}</h3>
          <div class="chart-legend" data-testid="compare-legend">
            <span class="legend-item">
              <span class="legend-swatch" :style="{ background: CURRENT_COLOUR }" />
              current parameters
            </span>
            <span class="legend-item">
              <span class="legend-swatch" :style="{ background: BASELINE_COLOUR }" />
              {{ baselineCost?.label ?? 'baseline' }}
            </span>
          </div>
          <div class="bar-list" data-testid="compare-std-chart">
            <div v-for="(b, i) in currentStdBars" :key="`s${i}`" class="bar-row">
              <span class="bar-label" v-html="renderMath(b.label)" />
              <div class="bar-track">
                <span class="bar-zero" />
                <span class="bar-fill" :style="{ left: b.left, width: b.width, background: b.color }" />
                <span
                  v-if="baselineStdBars[i]"
                  class="bar-fill bar-fill-baseline"
                  :style="{
                    left: baselineStdBars[i].left,
                    width: baselineStdBars[i].width,
                    background: baselineStdBars[i].color,
                  }"
                />
              </div>
              <span class="bar-value">{{ b.text }}</span>
            </div>
          </div>
        </section>
      </template>
      <!--
        v-else-if: the comparison *replaces* these rather than adding to them.
        Ticked, the comparison charts already carry the best fit as their second
        series, so leaving these below would plot the same numbers twice.
      -->
      <template v-else-if="hasCalibration">
        <!--
          Which model the errors below describe (#333). A calibration on the
          emulator fits the surrogate, so its errors and its cost are the
          emulator's; the forward model's, at the same best fit, are the other
          half of the answer. Both were measured once, when the calibration
          finished -- ticking this switches payloads, it does not run anything.
        -->
        <div v-if="bothSourcesAvailable" class="source-toggle">
          <span class="cost-caption" data-testid="calibration-source-label">
            errors and cost from <strong>{{ calibrationSourceLabel }}</strong> at the
            calibration best fit —
            <span data-testid="calibration-source-cost">{{
              formatCost(calibrationSource?.cost)
            }}</span>
            <template v-if="compareEmulator">
              · emulator
              <span data-testid="calibration-emulator-cost">{{
                formatCost(bestFitEmulatorCost?.cost)
              }}</span>
            </template>
          </span>
        </div>
        <section class="error-chart">
          <h3>Percentage error per observable</h3>
          <div class="bar-list" data-testid="percent-error-chart">
            <div v-for="(b, i) in percentBars" :key="i" class="bar-row">
              <span class="bar-label" v-html="renderMath(b.label)" />
              <div class="bar-track">
                <span class="bar-zero" />
                <span
                  class="bar-fill"
                  :style="{ left: b.left, width: b.width, background: b.color }"
                />
              </div>
              <span class="bar-value">{{ b.text }}</span>
            </div>
          </div>
        </section>
        <section class="error-chart">
          <h3>Error in standard deviations per observable</h3>
          <div class="bar-list" data-testid="std-error-chart">
            <div v-for="(b, i) in stdBars" :key="i" class="bar-row">
              <span class="bar-label" v-html="renderMath(b.label)" />
              <div class="bar-track">
                <span class="bar-zero" />
                <span
                  class="bar-fill"
                  :style="{ left: b.left, width: b.width, background: b.color }"
                />
              </div>
              <span class="bar-value">{{ b.text }}</span>
            </div>
          </div>
        </section>
      </template>
    </section>

    <!-- Emulator ------------------------------------------------------------>
    <section class="analysis-section">
      <h2>
        Emulator
        <span v-if="emulatorInUse" class="uq-method"> · in use for analyses</span>
      </h2>
      <p v-if="!hasEmulator" class="empty-hint">
        Train an emulator in the Emulator tab to see how well it reproduces the
        model.
      </p>
      <template v-else>
        <table class="emu-error-table" data-testid="emulator-error-table">
          <thead>
            <tr>
              <th>Feature</th><th>R²</th><th>nRMSE</th><th>RMSE</th>
              <th>MAE</th><th>Bias</th><th>Max |err|</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="(row, i) in emulatorRows" :key="row.label">
              <td class="emu-error-label" v-html="renderMath(row.label)" />
              <td>{{ fmtStat(row.r2) }}</td>
              <td>{{ fmtStat(row.nrmse) }}</td>
              <td>{{ fmtStat(row.rmse, 3) }}</td>
              <td>{{ fmtStat(row.mae, 3) }}</td>
              <td>{{ fmtStat(row.bias, 3) }}</td>
              <td>{{ fmtStat(row.maxAbs, 3) }}</td>
            </tr>
          </tbody>
        </table>
        <p class="emu-error-note">
          Measured on held-out points the emulator was never fitted to. Bias is
          signed (prediction − truth), so a systematically high emulator is
          distinguishable from a merely noisy one; nRMSE is relative to each
          feature's own spread, so features in different units can be compared.
        </p>

        <p v-if="!emulatorErrorPoints" class="empty-hint" data-testid="emu-no-points">
          This emulator was trained before circulatory_autogen saved its held-out
          points, so only the summary above is available. Retrain it to see where
          the error falls.
        </p>
        <template v-else>
          <label class="field emu-feature-pick">
            <span>Feature</span>
            <!-- A native select: this panel mounts without the PrimeVue plugin
                 in tests, and one dropdown is not worth changing that. -->
            <select
              v-model="emulatorFeature"
              class="emu-feature-select"
              data-testid="emu-feature-select"
            >
              <option v-for="o in emulatorFeatureOptions" :key="o.value" :value="o.value">
                {{ o.label }}
              </option>
            </select>
          </label>

          <section class="error-chart">
            <h3>Predicted vs simulated</h3>
            <ScatterChart
              data-testid="emu-parity"
              :points="parityPoints?.points ?? []"
              :x-domain="[parityPoints?.lo ?? 0, parityPoints?.hi ?? 1]"
              :y-domain="[parityPoints?.lo ?? 0, parityPoints?.hi ?? 1]"
              x-label="simulated"
              y-label="emulated"
              guide="diagonal"
              square
            />
            <small class="emu-error-note">
              Points on the diagonal are exact. Both axes share one range — a
              parity plot with independent axes makes any emulator look perfect.
            </small>
          </section>

          <section
            v-for="param in residualByParam"
            :key="param.label"
            class="error-chart"
          >
            <h3>Normalised residual vs {{ param.label }}</h3>
            <ScatterChart
              data-testid="emu-residual"
              :points="param.points"
              :x-domain="[param.lo, param.hi]"
              :y-domain="[-param.worst, param.worst]"
              :x-label="param.label"
              :y-label="residualBasis.axis"
              guide="zero"
            />
            <small class="emu-error-note">
              Structure here — a trend, or a cluster far from the line — means the
              emulator is wrong in a particular part of the parameter space, not
              uniformly. The axis is {{ residualBasis.note }}.
            </small>
          </section>
        </template>
      </template>
    </section>

    <!-- UQ ------------------------------------------------------------------>
    <section class="analysis-section">
      <h2>UQ<span v-if="uqMethodLabel" class="uq-method"> · {{ uqMethodLabel }}</span></h2>
      <p v-if="!hasUQ" class="empty-hint">
        Run a UQ analysis to see parameter posteriors.
      </p>
      <div v-else class="uq-list">
        <div v-for="(p, i) in uqPlots" :key="i" class="uq-row" data-testid="uq-row">
          <div class="uq-head">
            <span class="uq-label" v-html="renderMath(paramLabels[p.qname] ?? p.qname)" />
            <span class="uq-stats">
              {{ p.mean.toPrecision(3) }} ± {{ p.std.toPrecision(2) }}
              <span class="uq-ci">
                90% CI [{{ p.q05.toPrecision(3) }}, {{ p.q95.toPrecision(3) }}]
              </span>
            </span>
          </div>
          <svg
            class="uq-plot"
            viewBox="0 0 260 60"
            preserveAspectRatio="none"
            data-testid="uq-density"
          >
            <rect :x="p.geom.bandX" y="0" :width="p.geom.bandW" height="60" class="uq-band" />
            <polygon :points="p.geom.points" class="uq-area" />
            <line :x1="p.geom.meanX" y1="0" :x2="p.geom.meanX" y2="60" class="uq-mean" />
            <line
              v-for="t in p.geom.ticks"
              :key="'t' + t.at"
              class="uq-tick"
              :x1="t.at"
              y1="54"
              :x2="t.at"
              y2="60"
            />
          </svg>
          <div class="uq-axis" data-testid="uq-axis">
            <span v-for="t in p.geom.ticks" :key="'l' + t.at" :style="{ left: t.pct + '%' }">
              {{ t.text }}
            </span>
          </div>
        </div>
      </div>
    </section>
  </div>
</template>

<style scoped>
.emu-error-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 0.75rem;
}
.emu-error-table th,
.emu-error-table td {
  text-align: right;
  padding: 0.15rem 0.3rem;
  border-bottom: 1px solid var(--p-content-border-color, #eee);
}
.emu-error-table th:first-child,
.emu-error-table td:first-child {
  text-align: left;
}
.emu-error-label {
  max-width: 16rem;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.emu-error-note {
  font-size: 0.7rem;
  opacity: 0.75;
}
.emu-feature-select {
  font-size: 0.75rem;
  max-width: 18rem;
}
.emu-feature-pick {
  display: flex;
  align-items: center;
  gap: 0.4rem;
  font-size: 0.8rem;
  margin: 0.4rem 0;
}
/* The plots themselves are ScatterChart; only their headings are styled here. */
.analysis-panel {
  display: flex;
  flex-direction: column;
  gap: 1.25rem;
  padding: 0.5rem;
}
.analysis-section > h2 {
  margin: 0 0 0.5rem;
  font-size: 1.05rem;
  font-weight: 600;
  border-bottom: 1px solid var(--p-content-border-color, #333);
  padding-bottom: 0.25rem;
}
.empty-hint {
  opacity: 0.6;
  padding: 1rem;
}
.analysis-toolbar {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  margin-bottom: 0.5rem;
}
.toolbar-label {
  font-size: 0.8rem;
  opacity: 0.7;
}
.saved-runs {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  flex-wrap: wrap;
  margin-bottom: 0.4rem;
}
/* Nominal (linearisation) point for a local SA run — label left, values right. */
.nominal-row {
  display: flex;
  align-items: baseline;
  gap: 0.5rem;
  flex-wrap: wrap;
  margin-bottom: 0.4rem;
}
.nominal-chips {
  display: flex;
  flex-wrap: wrap;
  gap: 0.3rem;
}
.nominal-chip {
  display: inline-flex;
  align-items: baseline;
  gap: 0.3rem;
  font-size: 0.74rem;
  padding: 0.15rem 0.45rem;
  border: 1px solid var(--p-content-border-color, #333);
  border-radius: 6px;
  white-space: nowrap;
}
.nominal-name {
  opacity: 0.8;
}
.nominal-val {
  font-variant-numeric: tabular-nums;
}
.nominal-source {
  font-size: 0.72rem;
  opacity: 0.55;
}
.run-chips {
  display: flex;
  flex-wrap: wrap;
  gap: 0.3rem;
}
.run-chip {
  display: inline-flex;
  align-items: center;
  gap: 0.25rem;
  font-size: 0.74rem;
  padding: 0.15rem 0.45rem;
  border: 1px solid var(--p-content-border-color, #333);
  border-radius: 999px;
  cursor: pointer;
  opacity: 0.65;
  white-space: nowrap;
}
.run-chip.active {
  opacity: 1;
  border-color: #5b9bd5;
  background: rgba(91, 155, 213, 0.15);
}
.run-x {
  background: transparent;
  border: none;
  color: inherit;
  cursor: pointer;
  opacity: 0.6;
  font-size: 0.9rem;
  line-height: 1;
  padding: 0;
}
.run-x:hover {
  opacity: 1;
  color: #e84a5f;
}
.run-clear {
  background: transparent;
  border: none;
  color: inherit;
  opacity: 0.6;
  cursor: pointer;
  font-size: 0.74rem;
  text-decoration: underline;
}
.type-toggle {
  display: inline-flex;
  border: 1px solid var(--p-content-border-color, #333);
  border-radius: 4px;
  overflow: hidden;
}
.toggle-btn {
  background: transparent;
  border: none;
  color: inherit;
  opacity: 0.6;
  padding: 0.3rem 0.6rem;
  cursor: pointer;
  font-size: 0.8rem;
}
.toggle-btn + .toggle-btn {
  border-left: 1px solid var(--p-content-border-color, #333);
}
.toggle-btn.active {
  opacity: 1;
  background: var(--p-primary-color, #5b9bd5);
  color: #fff;
}
.table-wrap {
  overflow: auto;
}
.heatmap {
  border-collapse: collapse;
  font-size: 0.75rem;
}
.heatmap th,
.heatmap td {
  border: 1px solid var(--p-content-border-color, #333);
  padding: 0.3rem 0.5rem;
  white-space: nowrap;
}
.corner {
  text-align: left;
  font-weight: 600;
  opacity: 0.7;
}
.col-head {
  max-width: 160px;
  overflow: hidden;
  text-overflow: ellipsis;
  font-weight: 600;
}
/* The [operation] suffix is plain text, not math — keep it upright/unstyled. */
.op-label {
  font-weight: 400;
  font-style: normal;
  white-space: nowrap;
}
.row-head {
  text-align: left;
  font-family: monospace;
  position: sticky;
  left: 0;
  background: var(--p-content-background, #1e1e1e);
  max-width: 220px;
  overflow: hidden;
  text-overflow: ellipsis;
}
.cell {
  text-align: center;
  font-variant-numeric: tabular-nums;
}
.error-chart + .error-chart {
  margin-top: 1rem;
}
.error-chart h3 {
  margin: 0 0 0.5rem;
  font-size: 0.9rem;
  font-weight: 600;
}
.bar-list {
  display: flex;
  flex-direction: column;
  gap: 0.35rem;
  padding: 0.25rem 0;
}
.bar-row {
  display: grid;
  grid-template-columns: 9em 1fr 4.5em;
  align-items: center;
  gap: 0.5rem;
  font-size: 0.8rem;
}
.bar-label {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.bar-track {
  position: relative;
  height: 16px;
  background: var(--p-content-hover-background, rgba(127, 127, 127, 0.12));
  border-radius: 3px;
}
.bar-zero {
  position: absolute;
  left: 50%;
  top: 0;
  bottom: 0;
  width: 1px;
  background: var(--p-content-border-color, #555);
}
.bar-fill {
  position: absolute;
  top: 2px;
  bottom: 2px;
  min-width: 1px;
  border-radius: 2px;
}
.bar-value {
  text-align: right;
  font-variant-numeric: tabular-nums;
  opacity: 0.85;
}
.uq-method {
  font-size: 0.8rem;
  font-weight: 400;
  opacity: 0.6;
}
.uq-list {
  display: flex;
  flex-direction: column;
  gap: 0.75rem;
}
.uq-row {
  display: flex;
  flex-direction: column;
  gap: 0.2rem;
}
.uq-head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 0.5rem;
  font-size: 0.8rem;
}
.uq-label {
  font-weight: 600;
}
.uq-stats {
  font-variant-numeric: tabular-nums;
  opacity: 0.8;
}
.uq-ci {
  opacity: 0.65;
  margin-left: 0.35rem;
}
.uq-plot {
  width: 100%;
  height: 60px;
  border: 1px solid var(--p-content-border-color, #333);
  border-radius: 4px;
  background: var(--p-content-hover-background, rgba(127, 127, 127, 0.06));
}
.uq-area {
  fill: rgba(91, 155, 213, 0.45);
  stroke: var(--p-primary-color, #5b9bd5);
  stroke-width: 1;
  vector-effect: non-scaling-stroke;
}
.uq-band {
  fill: rgba(112, 173, 71, 0.18);
}
.uq-mean {
  stroke: #ffc000;
  stroke-width: 1.5;
  vector-effect: non-scaling-stroke;
}
.uq-tick {
  stroke: var(--p-content-border-color, #888);
  stroke-width: 1;
  vector-effect: non-scaling-stroke;
}
.uq-axis {
  position: relative;
  height: 0.95rem;
  font-size: 0.65rem;
  opacity: 0.7;
}
.uq-axis span {
  position: absolute;
  transform: translateX(-50%);
  white-space: nowrap;
}

/* Cost summary and the current-vs-baseline comparison (#159). */
.cost-summary {
  display: flex;
  align-items: flex-end;
  gap: 1.5rem;
  margin-bottom: 0.75rem;
  padding: 0.45rem 0.6rem;
  border: 1px solid var(--p-content-border-color, #d5d5d5);
  border-radius: 4px;
}
.cost-figure {
  display: flex;
  flex-direction: column;
  gap: 0.1rem;
}
.cost-caption {
  font-size: 0.68rem;
  text-transform: uppercase;
  letter-spacing: 0.07em;
  opacity: 0.7;
}
.cost-number {
  font-variant-numeric: tabular-nums;
  font-size: 1.05rem;
}
.chart-legend {
  display: flex;
  gap: 1rem;
  align-items: center;
  margin: 0 0 0.35rem 0;
  font-size: 0.78rem;
  color: var(--text-color-secondary, #666);
}
.legend-item {
  display: inline-flex;
  align-items: center;
  gap: 0.35rem;
}
.legend-swatch {
  width: 0.85rem;
  height: 0.55rem;
  border-radius: 2px;
  display: inline-block;
  flex: none;
}
.cost-compare {
  margin-left: auto;
  display: flex;
  align-items: center;
  gap: 0.35rem;
  font-size: 0.78rem;
  cursor: pointer;
}
/* Which model the calibration errors describe (#333). The tick box comes first
   and the answer in words beside it: a checked box alone says nothing in a
   screenshot, and the two sides can differ by a lot. */
.source-toggle {
  display: flex;
  align-items: center;
  gap: 0.75rem;
  margin-bottom: 0.6rem;
}
.source-toggle .cost-compare {
  margin-left: 0;
}
/* The baseline bar sits over the current one, half height, so both are readable
   where they overlap. */
.bar-fill-baseline {
  height: 45%;
  top: 27%;
  opacity: 0.95;
}
</style>
