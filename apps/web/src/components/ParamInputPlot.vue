<script>
// Fixed chart geometry so an HTML timeline header can be aligned with the plot's
// drawing area (the ProtocolInfoEditor imports these).
export const AXIS_W = 44 // fixed y-axis width (px)
export const RIGHT_PAD = 8 // chart right padding (px)
</script>

<script setup>
import { computed } from 'vue'
import { Line } from 'vue-chartjs'
import {
  Chart as ChartJS,
  LinearScale,
  PointElement,
  LineElement,
  LineController,
  Tooltip,
} from 'chart.js'

ChartJS.register(LinearScale, PointElement, LineElement, LineController, Tooltip)

const props = defineProps({
  // { time: number[], values: number[] } for one controlled param in an experiment.
  // null/empty → an empty plot (axes + subexp lines only).
  series: { type: Object, default: null },
  preTime: { type: Number, default: 0 }, // warmup, shown left of t=0
  totalSim: { type: Number, default: 1 }, // total simulated time (x max)
  // Interior subexperiment boundary times → vertical dashed lines.
  boundaries: { type: Array, default: () => [] },
  title: { type: String, default: '' },
})

function vline(x, yMin, yMax, color, dash) {
  return {
    label: 'line',
    data: [
      { x, y: yMin },
      { x, y: yMax },
    ],
    borderColor: color,
    borderDash: dash,
    borderWidth: 1,
    pointRadius: 0,
  }
}

const chartData = computed(() => {
  const pre = Number(props.preTime) || 0
  const time = props.series?.time ?? []
  const values = props.series?.values ?? []
  const n = Math.min(time.length, values.length)

  let yMin = Infinity
  let yMax = -Infinity
  for (const v of values) {
    if (v < yMin) yMin = v
    if (v > yMax) yMax = v
  }
  if (!Number.isFinite(yMin)) {
    yMin = 0
    yMax = 1
  }
  if (yMin === yMax) {
    yMin -= 1
    yMax += 1
  }

  const datasets = []
  if (n > 0) {
    const v0 = values[0]
    const data = pre > 0 ? [{ x: -pre, y: v0 }] : [] // warmup hold at the first value
    for (let i = 0; i < n; i++) data.push({ x: time[i], y: values[i] })
    datasets.push({
      label: 'input',
      data,
      borderColor: '#5b9bd5',
      borderWidth: 1.5,
      pointRadius: 0,
      tension: 0,
    })
  }
  // observation start (t=0)
  datasets.push(vline(0, yMin, yMax, 'rgba(91,155,213,0.9)', undefined))
  // pre_time start marker
  if (pre > 0) datasets.push(vline(-pre, yMin, yMax, 'rgba(127,127,127,0.45)', [2, 3]))
  // interior subexperiment boundaries
  for (const b of props.boundaries) {
    datasets.push(vline(b, yMin, yMax, 'rgba(127,127,127,0.7)', [6, 4]))
  }
  return { datasets }
})

const chartOptions = computed(() => ({
  responsive: true,
  maintainAspectRatio: false,
  animation: false,
  layout: { padding: { left: 0, right: RIGHT_PAD, top: 4, bottom: 0 } },
  scales: {
    x: {
      type: 'linear',
      min: -(Number(props.preTime) || 0),
      max: Number(props.totalSim) || 1,
      title: { display: true, text: 'time' },
    },
    y: {
      type: 'linear',
      afterFit: (s) => {
        s.width = AXIS_W
      },
      ticks: { maxTicksLimit: 4, font: { size: 9 } },
    },
  },
  plugins: { legend: { display: false } },
}))

defineExpose({ chartData, chartOptions })
</script>

<template>
  <div class="pip">
    <div v-if="title" class="pip-title" :title="title">{{ title }}</div>
    <div class="pip-chart">
      <Line :data="chartData" :options="chartOptions" />
    </div>
  </div>
</template>

<style scoped>
.pip {
  display: flex;
  flex-direction: column;
  min-width: 0;
}
.pip-title {
  font-size: 0.72rem;
  opacity: 0.8;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.pip-chart {
  position: relative;
  height: 120px;
}
</style>
