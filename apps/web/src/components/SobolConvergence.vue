<script setup>
import { computed, ref, watch } from 'vue'
import { Line } from 'vue-chartjs'
import {
  Chart as ChartJS,
  LinearScale,
  LogarithmicScale,
  PointElement,
  LineElement,
  LineController,
  Legend,
  Tooltip,
} from 'chart.js'
import { PALETTE } from '../lib/plot'

ChartJS.register(LinearScale, LogarithmicScale, PointElement, LineElement, LineController,
  Legend, Tooltip)

/**
 * How a Sobol run's indices settled as base samples were added: CA's
 * `sobol_convergence.json` (`{ n, outputs: { out: { S1|ST: { param: [[v, conf], ...] } } } }`),
 * one index of one output at a time. A line still moving at the last `n` says the run was
 * too short for that index.
 *
 * At most `PALETTE.length` parameters are drawn: those with the largest ST over every output,
 * chosen once, so a parameter keeps its colour when another output is picked.
 */
const props = defineProps({
  convergence: { type: Object, required: true },
  labels: { type: Object, default: () => ({}) },
})

const outputs = computed(() => Object.keys(props.convergence.outputs ?? {}))
const output = ref(null)
const index = ref('ST')
watch(outputs, (list) => {
  if (!list.includes(output.value)) output.value = list[0] ?? null
}, { immediate: true })

const lastValue = (series) => {
  for (let k = (series?.length ?? 0) - 1; k >= 0; k--) {
    const v = series[k]?.[0]
    if (Number.isFinite(v)) return v
  }
  return null
}

const allParams = computed(() => {
  const seen = new Set()
  for (const out of Object.values(props.convergence.outputs ?? {})) {
    for (const p of Object.keys(out?.ST ?? out?.S1 ?? {})) seen.add(p)
  }
  return [...seen]
})

const shown = computed(() => {
  const score = (p) => Math.max(0, ...Object.values(props.convergence.outputs ?? {})
    .map((out) => Math.abs(lastValue(out?.ST?.[p]) ?? 0)))
  return [...allParams.value]
    .sort((a, b) => score(b) - score(a))
    .slice(0, PALETTE.length)
    .sort((a, b) => allParams.value.indexOf(a) - allParams.value.indexOf(b))
})

const label = (p) => props.labels[p] ?? p

const data = computed(() => {
  const n = props.convergence.n ?? []
  const byParam = props.convergence.outputs?.[output.value]?.[index.value] ?? {}
  return {
    datasets: shown.value.map((p, i) => ({
      label: label(p),
      data: n.map((x, k) => ({ x, y: byParam[p]?.[k]?.[0] ?? null, conf: byParam[p]?.[k]?.[1] })),
      borderColor: PALETTE[i],
      backgroundColor: PALETTE[i],
      borderWidth: 2,
      pointRadius: 4,
      spanGaps: false,
    })),
  }
})

const exact = (v) => (Number.isFinite(v) ? String(Number(v.toPrecision(4))) : '—')

const options = computed(() => ({
  responsive: true,
  maintainAspectRatio: false,
  animation: false,
  parsing: false,
  scales: {
    x: {
      type: 'logarithmic',
      title: { display: true, text: 'base samples' },
      // a tick at each n CA evaluated, rather than the log scale's own 10, 20, 50
      afterBuildTicks: (axis) => {
        axis.ticks = (props.convergence.n ?? []).map((value) => ({ value }))
      },
      // the log scale's own labels show only 1, 2 and 5 times a power of ten
      ticks: { callback: (value) => String(value) },
    },
    y: { type: 'linear', title: { display: true, text: index.value } },
  },
  interaction: { mode: 'nearest', intersect: false },
  plugins: {
    legend: { display: true, position: 'bottom' },
    tooltip: {
      callbacks: {
        title: (items) => `${items[0]?.raw?.x ?? ''} base samples`,
        label: (item) =>
          `${item.dataset.label}: ${exact(item.raw.y)} ± ${exact(item.raw.conf)}`,
      },
    },
  },
}))
</script>

<template>
  <div class="sobol-convergence" data-testid="sobol-convergence">
    <div class="pickers">
      <label>Output
        <select v-model="output" data-testid="sobol-convergence-output">
          <option v-for="o in outputs" :key="o" :value="o">{{ o }}</option>
        </select>
      </label>
      <label>Index
        <select v-model="index" data-testid="sobol-convergence-index">
          <option value="ST">ST (total)</option>
          <option value="S1">S1 (first order)</option>
        </select>
      </label>
      <span v-if="allParams.length > shown.length" class="muted" data-testid="sobol-convergence-note">
        the {{ shown.length }} of {{ allParams.length }} parameters with the largest ST
      </span>
    </div>
    <div class="chart-box">
      <Line :data="data" :options="options" />
    </div>
  </div>
</template>

<style scoped>
.pickers {
  display: flex;
  gap: 1rem;
  align-items: center;
  font-size: 0.85rem;
  margin-bottom: 0.3rem;
}
.muted {
  opacity: 0.75;
}
.chart-box {
  height: 260px;
  border: 1px solid var(--p-content-border-color, #333);
  border-radius: 6px;
  padding: 0.5rem;
}
</style>
