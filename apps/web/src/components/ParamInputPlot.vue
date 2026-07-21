<script>
// Fixed chart geometry so an HTML timeline header can be aligned with the plot's
// drawing area (the ProtocolInfoEditor imports these).
export const AXIS_W = 44 // fixed y-axis width (px)
export const RIGHT_PAD = 8 // chart right padding (px)
// pre_time is shown in a small fixed slot (this fraction of the sim time) with a
// jagged break, so a large pre_time doesn't dominate the axis. The editor uses
// the same fraction for its timeline so header + plot stay aligned.
export const PRE_FRAC = 0.12
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

import { fmtAxis } from '../lib/format'

ChartJS.register(LinearScale, PointElement, LineElement, LineController, Tooltip)

const props = defineProps({
  // { time: number[], values: number[] } for one controlled param in an experiment.
  // null/empty → an empty plot (axes + subexp lines only).
  series: { type: Object, default: null },
  preTime: { type: Number, default: 0 }, // warmup, shown in the broken-axis pre slot
  totalSim: { type: Number, default: 1 }, // total simulated time (x max)
  // Interior subexperiment boundary times → vertical dashed lines.
  boundaries: { type: Array, default: () => [] },
  title: { type: String, default: '' },
})

// Display width of the pre_time slot (0 when there's no warmup). The real
// pre_time is compressed into this slot, with a jagged break to show the jump.
const preSlot = computed(() => {
  const pre = Number(props.preTime) || 0
  if (pre <= 0) return 0
  return Math.max((Number(props.totalSim) || 1) * PRE_FRAC, 1e-6)
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
  const slot = preSlot.value
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
    // warmup hold, compressed into the pre slot (from -preSlot, not -preTime)
    const data = slot > 0 ? [{ x: -slot, y: v0 }] : []
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
  // observation start (t=0) — light green so it's distinct from the param line
  datasets.push(vline(0, yMin, yMax, 'rgba(120,205,120,0.95)', undefined))
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
      min: -preSlot.value,
      max: Number(props.totalSim) || 1,
      title: { display: true, text: 'time' },
      // hide negative ticks — the pre slot is compressed/not-to-scale
      ticks: { callback: (v) => (v < 0 ? '' : v) },
    },
    y: {
      type: 'linear',
      afterFit: (s) => {
        s.width = AXIS_W
      },
      // Scientific notation for large/small parameter magnitudes (e.g. 1e-9
      // compliances) so the axis labels stay short and readable.
      ticks: { maxTicksLimit: 4, font: { size: 9 }, callback: (v) => fmtAxis(v) },
    },
  },
  plugins: { legend: { display: false } },
}))

// Draw a jagged break across the pre_time slot to show the axis is not continuous
// (it "jumps" from 0 to -preTime). Also labels the real -preTime at the far left.
const breakPlugin = {
  id: 'preBreak',
  afterDatasetsDraw(chart) {
    const slot = preSlot.value
    if (!slot) return
    const { ctx, chartArea, scales } = chart
    const { top, bottom } = chartArea
    const xb = scales.x.getPixelForValue(-slot * 0.5)
    ctx.save()
    ctx.strokeStyle = 'rgba(120,120,120,0.9)'
    ctx.lineWidth = 1.25
    for (const off of [-2.5, 2.5]) {
      ctx.beginPath()
      const amp = 2.5
      const step = 5
      let k = 0
      for (let y = bottom; y >= top; y -= step) {
        const x = xb + off + (k % 2 === 0 ? -amp : amp)
        if (k === 0) ctx.moveTo(x, y)
        else ctx.lineTo(x, y)
        k++
      }
      ctx.stroke()
    }
    ctx.fillStyle = 'rgba(120,120,120,0.9)'
    ctx.font = '9px sans-serif'
    ctx.textAlign = 'left'
    ctx.fillText(`-${Number(props.preTime) || 0}`, scales.x.getPixelForValue(-slot) + 1, bottom - 2)
    ctx.restore()
  },
}

defineExpose({ chartData, chartOptions, preSlot })
</script>

<template>
  <div class="pip">
    <div v-if="title" class="pip-title" :title="title">{{ title }}</div>
    <div class="pip-chart">
      <Line :data="chartData" :options="chartOptions" :plugins="[breakPlugin]" />
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
