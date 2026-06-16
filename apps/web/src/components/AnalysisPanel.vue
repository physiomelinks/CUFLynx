<script setup>
import { ref, computed, watch } from 'vue'
import { renderMath, renderOutputLabel } from '../lib/math'

const props = defineProps({
  // Sensitivity: { S1: {outName: {param: val}}, ST: {...}, local: {...} }
  indices: { type: Object, default: null },
  paramNames: { type: Array, default: () => [] },
  outputNames: { type: Array, default: () => [] },
  // qname -> LaTeX/plotting name, for the heatmap row labels.
  paramLabels: { type: Object, default: () => ({}) },
  // Calibration: one error per observable, aligned with errorLabels.
  percentError: { type: Array, default: null },
  stdError: { type: Array, default: null },
  errorLabels: { type: Array, default: () => [] },
  // UQ: per-parameter posteriors [{qname, mean, std, q05, q50, q95, bins, counts}].
  uqParams: { type: Array, default: () => [] },
  uqMethod: { type: String, default: null },
  // Saved sensitivity runs for comparison: [{ id, label, at }]. The currently
  // shown run's matrix is in `indices`; this list lets the user switch between
  // saved runs (e.g. global Sobol vs local FD) without overwriting.
  savedResults: { type: Array, default: () => [] },
  selectedResultId: { type: [Number, String], default: null },
})

const emit = defineEmits(['select-result', 'remove-result', 'clear-results'])

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
const hasCalibration = computed(
  () =>
    Array.isArray(props.percentError) &&
    props.percentError.length > 0 &&
    Array.isArray(props.stdError) &&
    props.stdError.length > 0,
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
function errorBars(values, hi, fmt) {
  const vals = values ?? []
  const maxAbs = Math.max(1e-9, ...vals.map((v) => Math.abs(v)))
  const colors = barColors(vals, hi)
  return vals.map((v, i) => {
    const halfPct = (Math.min(1, Math.abs(v) / maxAbs) * 50).toFixed(2)
    return {
      label: props.errorLabels[i] ?? `obs ${i}`,
      color: colors[i],
      width: `${halfPct}%`,
      left: v >= 0 ? '50%' : `${(50 - Number(halfPct)).toFixed(2)}%`,
      text: fmt(v),
    }
  })
}

const percentBars = computed(() => errorBars(props.percentError, 20, (v) => `${v.toFixed(1)}%`))
const stdBars = computed(() => errorBars(props.stdError, 3, (v) => `${v.toFixed(2)}σ`))

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
      <p v-if="!hasCalibration" class="empty-hint">
        Run a calibration to see per-observable fit errors.
      </p>
      <template v-else>
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
          </svg>
        </div>
      </div>
    </section>
  </div>
</template>

<style scoped>
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
</style>
