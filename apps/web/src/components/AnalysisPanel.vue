<script setup>
import { ref, computed } from 'vue'
import { Bar } from 'vue-chartjs'
import { renderMath } from '../lib/math'
import {
  Chart as ChartJS,
  CategoryScale,
  LinearScale,
  BarElement,
  BarController,
  Legend,
  Tooltip,
} from 'chart.js'

ChartJS.register(
  CategoryScale,
  LinearScale,
  BarElement,
  BarController,
  Legend,
  Tooltip,
)

const props = defineProps({
  // Sensitivity: { S1: {outName: {param: val}}, ST: {...} }
  indices: { type: Object, default: null },
  paramNames: { type: Array, default: () => [] },
  outputNames: { type: Array, default: () => [] },
  // qname -> LaTeX/plotting name, for the heatmap row labels.
  paramLabels: { type: Object, default: () => ({}) },
  // Calibration: one error per observable, aligned with errorLabels.
  percentError: { type: Array, default: null },
  stdError: { type: Array, default: null },
  errorLabels: { type: Array, default: () => [] },
})

// ---- Sensitivity heatmap ---------------------------------------------------
const indexType = ref('ST') // 'S1' | 'ST'

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

// A compact viridis-ish ramp (dark purple -> teal -> green -> yellow).
const RAMP = [
  [68, 1, 84],
  [59, 82, 139],
  [33, 145, 140],
  [94, 201, 98],
  [253, 231, 37],
]

function colorFor(value) {
  if (value == null) return 'transparent'
  const t = Math.max(0, Math.min(1, value)) // Sobol indices clamp to [0, 1]
  const seg = t * (RAMP.length - 1)
  const i = Math.min(RAMP.length - 2, Math.floor(seg))
  const f = seg - i
  const [r1, g1, b1] = RAMP[i]
  const [r2, g2, b2] = RAMP[i + 1]
  const r = Math.round(r1 + (r2 - r1) * f)
  const g = Math.round(g1 + (g2 - g1) * f)
  const b = Math.round(b1 + (b2 - b1) * f)
  return `rgb(${r}, ${g}, ${b})`
}

// Dark text on the light (yellow/green) end, light text on the dark end.
function textColorFor(value) {
  if (value == null) return 'inherit'
  return Math.max(0, Math.min(1, value)) > 0.55 ? '#111' : '#eee'
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

const percentData = computed(() => ({
  labels: props.errorLabels,
  datasets: [
    {
      label: '% error',
      data: props.percentError ?? [],
      backgroundColor: barColors(props.percentError, 20),
    },
  ],
}))

const stdData = computed(() => ({
  labels: props.errorLabels,
  datasets: [
    {
      label: 'error (std)',
      data: props.stdError ?? [],
      backgroundColor: barColors(props.stdError, 3),
    },
  ],
}))

function barOptions(yTitle) {
  return {
    responsive: true,
    maintainAspectRatio: false,
    animation: false,
    scales: {
      x: { ticks: { autoSkip: false } },
      y: { type: 'linear', title: { display: true, text: yTitle } },
    },
    plugins: { legend: { display: false } },
  }
}
const percentOptions = barOptions('percent error (%)')
const stdOptions = barOptions('error (number of std)')
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
        <div class="analysis-toolbar">
          <span class="toolbar-label">Index</span>
          <div class="type-toggle">
            <button
              class="toggle-btn"
              :class="{ active: indexType === 'S1' }"
              data-testid="index-s1"
              @click="indexType = 'S1'"
            >
              First-order (S₁)
            </button>
            <button
              class="toggle-btn"
              :class="{ active: indexType === 'ST' }"
              data-testid="index-st"
              @click="indexType = 'ST'"
            >
              Total-order (Sₜ)
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
                  v-html="renderMath(out)"
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
                  {{ valueAt(out, param) == null ? '–' : valueAt(out, param).toFixed(2) }}
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
          <div class="chart-box">
            <Bar
              :data="percentData"
              :options="percentOptions"
              data-testid="percent-error-chart"
            />
          </div>
        </section>
        <section class="error-chart">
          <h3>Error in standard deviations per observable</h3>
          <div class="chart-box">
            <Bar :data="stdData" :options="stdOptions" data-testid="std-error-chart" />
          </div>
        </section>
      </template>
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
.chart-box {
  height: 280px;
  border: 1px solid var(--p-content-border-color, #333);
  border-radius: 6px;
  padding: 0.5rem;
}
</style>
