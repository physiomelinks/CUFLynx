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
  series: { type: Object, default: null },
  // Interior subexperiment boundary times → vertical dashed lines.
  boundaries: { type: Array, default: () => [] },
  title: { type: String, default: '' },
})

const chartData = computed(() => {
  const time = props.series?.time ?? []
  const values = props.series?.values ?? []
  const n = Math.min(time.length, values.length)
  const data = []
  for (let i = 0; i < n; i++) data.push({ x: time[i], y: values[i] })

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

  const datasets = [
    {
      label: 'input',
      data,
      borderColor: '#5b9bd5',
      borderWidth: 1.5,
      pointRadius: 0,
      tension: 0, // controlled inputs are steps/ramps — no smoothing
    },
  ]
  for (const b of props.boundaries) {
    datasets.push({
      label: 'subexp',
      data: [
        { x: b, y: yMin },
        { x: b, y: yMax },
      ],
      borderColor: 'rgba(127,127,127,0.7)',
      borderDash: [6, 4],
      borderWidth: 1,
      pointRadius: 0,
    })
  }
  return { datasets }
})

const chartOptions = {
  responsive: true,
  maintainAspectRatio: false,
  animation: false,
  scales: {
    x: { type: 'linear', title: { display: true, text: 'time' } },
    y: { type: 'linear' },
  },
  plugins: { legend: { display: false } },
}

defineExpose({ chartData })
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
