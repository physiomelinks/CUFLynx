<script setup>
import { computed, ref } from 'vue'
import { Line } from 'vue-chartjs'
import {
  Chart as ChartJS,
  LinearScale,
  LogarithmicScale,
  PointElement,
  LineElement,
  LineController,
  Filler,
  Legend,
  Tooltip,
} from 'chart.js'
import { PALETTE, shadeForStart } from '../lib/plot'

ChartJS.register(
  LinearScale,
  LogarithmicScale,
  PointElement,
  LineElement,
  LineController,
  Filler,
  Legend,
  Tooltip,
)

const props = defineProps({
  // [[best, …up to top-10], …] one row per generation
  costHistory: { type: Array, default: () => [] },
  // display-friendly param names (one per param)
  paramNames: { type: Array, default: () => [] },
  // [[v0, v1, …], …] normalised best param values, one row per generation
  paramHistory: { type: Array, default: () => [] },
  // Per-start cost curves for multi-start gradient descent:
  // startCosts[start] = [cost per iteration]. Empty for GA / single-start runs.
  startCosts: { type: Array, default: () => [] },
  // Per-start parameter trajectories for multi-start gradient descent:
  // { param_names, starts } where starts[start][iteration] = [val per param].
  startParams: { type: Object, default: () => ({ param_names: [], starts: [] }) },
  // qname -> { min, max, … } from params_for_id, used to normalise the per-start
  // parameter plot to [0, 1] against each param's allowable range.
  paramSpecs: { type: Object, default: () => ({}) },
  // Cost-gradient (dJ/dp) history for gradient-based runs (CA #296). Single-start:
  // one gradient vector per iteration ([[dJ/dp per param], …]). Empty otherwise.
  gradHistory: { type: Array, default: () => [] },
  // Multi-start cost-gradient trajectories: { param_names, starts } where
  // starts[start][iteration] = [dJ/dp per param]. Empty for GA / single-start runs.
  startGrads: { type: Object, default: () => ({ param_names: [], starts: [] }) },
})

const hasData = computed(
  () =>
    props.costHistory.length > 0 ||
    multiStart.value ||
    hasStartParams.value ||
    hasGradient.value,
)
// Multi-start when CA emitted per-start cost curves with more than one start.
const multiStart = computed(
  () => props.startCosts.length > 0 && props.startCosts.some((c) => c && c.length),
)
const generations = computed(() => props.costHistory.map((_, i) => i))

// Multi-start: one cost-vs-iteration line per start. Single-start / GA: the
// best-cost line plus a shaded band over each generation's top-10 spread.
const costData = computed(() => {
  if (multiStart.value) {
    const n = props.startCosts.length
    const maxLen = Math.max(...props.startCosts.map((c) => (c ? c.length : 0)))
    // One line per start, all shades of a single base colour (start 0 darkest),
    // to match the per-parameter multi-start plot below.
    return {
      labels: Array.from({ length: maxLen }, (_, i) => i),
      datasets: props.startCosts.map((curve, i) => ({
        label: `start ${i}`,
        data: curve ?? [],
        borderColor: shadeForStart(PALETTE[0], i, n),
        backgroundColor: shadeForStart(PALETTE[0], i, n),
        borderWidth: 2,
        pointRadius: 0,
      })),
    }
  }
  const best = props.costHistory.map((row) => row[0])
  const worst = props.costHistory.map((row) => Math.max(...row))
  return {
    labels: generations.value,
    datasets: [
      {
        label: 'best cost',
        data: best,
        borderColor: PALETTE[0],
        backgroundColor: PALETTE[0],
        borderWidth: 2,
        pointRadius: 0,
        order: 0,
      },
      // Band: draw the worst (top-10 max) then fill down to the best line.
      {
        label: 'top-10 best',
        data: worst,
        borderColor: 'transparent',
        backgroundColor: 'rgba(91, 155, 213, 0.15)',
        pointRadius: 0,
        fill: '-1',
        order: 1,
      },
    ],
  }
})

// --- Cost gradient (issue #86) ---------------------------------------------
// Gradient-based (L-BFGS-B) runs stream dJ/dp per iteration (CA #296). The
// gradient is a vector, so to plot it "just like the cost" (one scalar curve per
// start, or a single line) reduce each iterate's vector to its infinity norm
// (max |component|) — the same measure CA prints as |grad|_inf and which decays
// to 0 as the descent reaches a stationary point.
function infNorm(vec) {
  if (!Array.isArray(vec)) return undefined
  let m = 0
  for (const v of vec) {
    const a = Math.abs(v)
    if (a > m) m = a
  }
  return m
}

// Single-start |grad|_inf per iteration.
const gradMagHistory = computed(() => (props.gradHistory ?? []).map(infNorm))
// Multi-start |grad|_inf per iteration, one curve per start (mirrors startCosts).
const startGradCurves = computed(() =>
  (props.startGrads?.starts ?? []).map((start) => (start ?? []).map(infNorm)),
)
// Gradient data is present only for gradient-based runs; the toggle hides otherwise.
const hasGradient = computed(
  () => gradMagHistory.value.length > 0 || startGradCurves.value.some((c) => c && c.length),
)

// Plotted quantity: 'cost' (default) or 'gradient'. Force cost when no gradient.
const metric = ref('cost')
const activeMetric = computed(() => (hasGradient.value ? metric.value : 'cost'))
function setMetric(m) {
  metric.value = m
}

// Gradient magnitude plotted exactly like the cost: one line per start for
// multi-start, a single line for single-start (no top-10 band — L-BFGS-B has a
// single trajectory).
const gradData = computed(() => {
  if (multiStart.value) {
    const curves = startGradCurves.value
    const n = curves.length
    const maxLen = Math.max(0, ...curves.map((c) => (c ? c.length : 0)))
    return {
      labels: Array.from({ length: maxLen }, (_, i) => i),
      datasets: curves.map((curve, i) => ({
        label: `start ${i}`,
        data: curve ?? [],
        borderColor: shadeForStart(PALETTE[0], i, n),
        backgroundColor: shadeForStart(PALETTE[0], i, n),
        borderWidth: 2,
        pointRadius: 0,
      })),
    }
  }
  return {
    labels: gradMagHistory.value.map((_, i) => i),
    datasets: [
      {
        label: '|gradient|',
        data: gradMagHistory.value,
        borderColor: PALETTE[0],
        backgroundColor: PALETTE[0],
        borderWidth: 2,
        pointRadius: 0,
      },
    ],
  }
})

// The series the top chart draws, switched by the cost/gradient toggle.
const displayData = computed(() =>
  activeMetric.value === 'gradient' ? gradData.value : costData.value,
)

// Multi-start parameter trajectories: each parameter a distinct colour, each
// start a distinct shade of that colour (start 0 darkest). P×S lines total.
const hasStartParams = computed(
  () =>
    (props.startParams?.param_names ?? []).length > 0 &&
    (props.startParams?.starts ?? []).some((c) => c && c.length),
)

// CA labels the streamed params `vessel param` (its qname with '/' -> ' '), so
// index the params_for_id ranges by that same form to look up each param's
// allowable [min, max].
const rangesByLabel = computed(() => {
  const out = {}
  for (const [qname, spec] of Object.entries(props.paramSpecs ?? {})) {
    if (spec && Number.isFinite(spec.min) && Number.isFinite(spec.max)) {
      out[String(qname).replaceAll('/', ' ')] = spec
    }
  }
  return out
})

// Normalise a value to [0, 1] against its param range (0 = min, 1 = max). Falls
// back to the raw value when the range is unknown or degenerate.
function normToRange(v, range) {
  if (v == null || !range) return v
  const span = range.max - range.min
  if (!(span > 0)) return v
  return (v - range.min) / span
}

const startParamData = computed(() => {
  const names = props.startParams?.param_names ?? []
  const starts = props.startParams?.starts ?? []
  const ranges = rangesByLabel.value
  const n = starts.length
  const maxLen = Math.max(0, ...starts.map((c) => (c ? c.length : 0)))
  const datasets = []
  names.forEach((name, p) => {
    const base = PALETTE[p % PALETTE.length]
    const range = ranges[name]
    starts.forEach((curve, s) => {
      datasets.push({
        label: name,
        data: (curve ?? []).map((row) =>
          Array.isArray(row) ? normToRange(row[p], range) : undefined,
        ),
        borderColor: shadeForStart(base, s, n),
        backgroundColor: shadeForStart(base, s, n),
        borderWidth: 2,
        pointRadius: 0,
        // Legend shows one entry per parameter (its start-0 base colour).
        _legend: s === 0,
      })
    })
  })
  return { labels: Array.from({ length: maxLen }, (_, i) => i), datasets }
})

const paramData = computed(() => ({
  labels: props.paramHistory.map((_, i) => i),
  datasets: props.paramNames.map((name, i) => ({
    label: name,
    data: props.paramHistory.map((row) => row[i]),
    borderColor: PALETTE[i % PALETTE.length],
    backgroundColor: PALETTE[i % PALETTE.length],
    borderWidth: 2,
    pointRadius: 0,
  })),
}))

// X axis is "iteration" for multi-start gradient descent, "generation" for GA.
const xLabel = computed(() => (multiStart.value ? 'iteration' : 'generation'))
const costOptions = computed(() => ({
  responsive: true,
  maintainAspectRatio: false,
  animation: false,
  scales: {
    x: {
      type: 'linear',
      title: { display: true, text: xLabel.value },
      // Iterations/generations are whole numbers — no fractional ticks.
      ticks: { stepSize: 1, precision: 0 },
    },
    // Cost spans orders of magnitude (log); the gradient magnitude decays toward
    // 0, which a log scale can't render, so plot it linearly.
    y:
      activeMetric.value === 'gradient'
        ? { type: 'linear', title: { display: true, text: 'cost gradient (|grad|∞)' } }
        : { type: 'logarithmic', title: { display: true, text: 'cost' } },
  },
  plugins: { legend: { display: true, position: 'bottom' } },
}))

// Heading + axis wording for the top chart, tracking the cost/gradient toggle.
const metricLabel = computed(() => (activeMetric.value === 'gradient' ? 'cost gradient' : 'cost'))

// Parameter values vs iteration, normalised to each param's params_for_id range
// (0 = min, 1 = max). Legend lists each parameter once, via its start-0 dataset,
// so P×S lines don't flood it.
const startParamOptions = {
  responsive: true,
  maintainAspectRatio: false,
  animation: false,
  scales: {
    x: {
      type: 'linear',
      title: { display: true, text: 'iteration' },
      ticks: { stepSize: 1, precision: 0 },
    },
    y: {
      type: 'linear',
      min: 0,
      max: 1,
      title: { display: true, text: 'normalised value' },
    },
  },
  plugins: {
    legend: {
      display: true,
      position: 'bottom',
      labels: { filter: (item, data) => data.datasets[item.datasetIndex]?._legend !== false },
    },
  },
}

const paramOptions = {
  responsive: true,
  maintainAspectRatio: false,
  animation: false,
  scales: {
    x: {
      type: 'linear',
      title: { display: true, text: 'generation' },
      // Generations are whole numbers — no fractional ticks on the x axis.
      ticks: { stepSize: 1, precision: 0 },
    },
    y: {
      type: 'linear',
      min: 0,
      max: 1,
      title: { display: true, text: 'normalised value' },
    },
  },
  plugins: { legend: { display: true, position: 'bottom' } },
}
</script>

<template>
  <div class="progress-panel" data-testid="progress-panel">
    <p v-if="!hasData" class="empty-hint">
      Run a calibration to see cost and parameter progress.
    </p>
    <template v-else>
      <section class="progress-chart">
        <div class="chart-head">
          <h3>{{ metricLabel === 'cost' ? 'Cost' : 'Cost gradient' }} vs {{ xLabel }}</h3>
          <!-- Gradient-based runs stream the cost gradient too (CA #296); let the
               user toggle the plotted quantity. Hidden for GA / population runs. -->
          <div v-if="hasGradient" class="metric-toggle" data-testid="metric-toggle">
            <button
              type="button"
              data-testid="metric-cost"
              :class="{ active: activeMetric === 'cost' }"
              @click="setMetric('cost')"
            >
              Cost
            </button>
            <button
              type="button"
              data-testid="metric-gradient"
              :class="{ active: activeMetric === 'gradient' }"
              @click="setMetric('gradient')"
            >
              Gradient
            </button>
          </div>
        </div>
        <div class="chart-box">
          <Line :data="displayData" :options="costOptions" />
        </div>
      </section>
      <section v-if="hasStartParams" class="progress-chart">
        <h3>Normalised parameter values vs iteration (per start)</h3>
        <div class="chart-box">
          <Line :data="startParamData" :options="startParamOptions" />
        </div>
      </section>
      <section v-if="paramHistory.length" class="progress-chart">
        <h3>Best parameter values vs generation</h3>
        <div class="chart-box">
          <Line :data="paramData" :options="paramOptions" />
        </div>
      </section>
    </template>
  </div>
</template>

<style scoped>
.progress-panel {
  display: flex;
  flex-direction: column;
  gap: 1rem;
  padding: 0.5rem;
}
.progress-chart h3 {
  margin: 0 0 0.5rem;
  font-size: 0.95rem;
  font-weight: 600;
}
.chart-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 0.5rem;
}
.metric-toggle {
  display: inline-flex;
  margin-bottom: 0.5rem;
  border: 1px solid var(--p-content-border-color, #333);
  border-radius: 6px;
  overflow: hidden;
}
.metric-toggle button {
  border: none;
  background: transparent;
  color: inherit;
  padding: 0.2rem 0.6rem;
  font-size: 0.8rem;
  cursor: pointer;
}
.metric-toggle button.active {
  background: var(--p-primary-color, #5b9bd5);
  color: #fff;
}
.chart-box {
  height: 300px;
  border: 1px solid var(--p-content-border-color, #333);
  border-radius: 6px;
  padding: 0.5rem;
}
.empty-hint {
  opacity: 0.6;
  padding: 1rem;
}
</style>
