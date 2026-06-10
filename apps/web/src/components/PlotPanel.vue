<script setup>
import { computed } from 'vue'
import { Line } from 'vue-chartjs'
import {
  Chart as ChartJS,
  LinearScale,
  PointElement,
  LineElement,
  ScatterController,
  LineController,
  Tooltip,
  Legend,
} from 'chart.js'
import Select from 'primevue/select'
import { buildChartData } from '../lib/plot'

ChartJS.register(
  LinearScale,
  PointElement,
  LineElement,
  ScatterController,
  LineController,
  Tooltip,
  Legend,
)

const props = defineProps({
  simResult: { type: Object, default: null },
  dataItems: { type: Array, default: () => [] },
})

const chartData = computed(() =>
  buildChartData(props.simResult, { dataItems: props.dataItems }),
)

const chartOptions = {
  responsive: true,
  maintainAspectRatio: false,
  animation: false,
  scales: {
    x: { type: 'linear', title: { display: true, text: 'time' } },
    y: { type: 'linear' },
  },
  plugins: { legend: { position: 'bottom' } },
}

defineExpose({ chartData })
</script>

<template>
  <section class="plot-panel">
    <div class="chart-wrap">
      <Line :data="chartData" :options="chartOptions" />
    </div>
  </section>
</template>

<style scoped>
.plot-panel {
  height: 100%;
  padding: 0.75rem;
}
.chart-wrap {
  position: relative;
  height: 100%;
  min-height: 320px;
}
</style>
