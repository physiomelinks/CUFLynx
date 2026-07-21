<script setup>
import { computed } from 'vue'
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
import { PALETTE } from '../lib/plot'

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
})

const hasData = computed(() => props.costHistory.length > 0 || multiStart.value)
// Multi-start when CA emitted per-start cost curves with more than one start.
const multiStart = computed(
  () => props.startCosts.length > 0 && props.startCosts.some((c) => c && c.length),
)
const generations = computed(() => props.costHistory.map((_, i) => i))

// Multi-start: one cost-vs-iteration line per start. Single-start / GA: the
// best-cost line plus a shaded band over each generation's top-10 spread.
const costData = computed(() => {
  if (multiStart.value) {
    const maxLen = Math.max(...props.startCosts.map((c) => (c ? c.length : 0)))
    return {
      labels: Array.from({ length: maxLen }, (_, i) => i),
      datasets: props.startCosts.map((curve, i) => ({
        label: `start ${i}`,
        data: curve ?? [],
        borderColor: PALETTE[i % PALETTE.length],
        backgroundColor: PALETTE[i % PALETTE.length],
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
    y: { type: 'logarithmic', title: { display: true, text: 'cost' } },
  },
  plugins: { legend: { display: true, position: 'bottom' } },
}))

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
        <h3>Cost vs {{ xLabel }}</h3>
        <div class="chart-box">
          <Line :data="costData" :options="costOptions" />
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
