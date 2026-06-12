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
  title: { type: String, default: '' },
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
    <h3 v-if="title" class="plot-title" data-testid="plot-title">{{ title }}</h3>
    <div class="chart-wrap">
      <Line :data="chartData" :options="chartOptions" />
    </div>
  </section>
</template>

<style scoped>
.plot-panel {
  display: flex;
  flex-direction: column;
  height: 100%;
  padding: 0.5rem;
}
.plot-title {
  margin: 0 0 0.25rem;
  font-size: 0.8rem;
  font-weight: 600;
  opacity: 0.85;
}
.chart-wrap {
  position: relative;
  flex: 1;
  min-height: 180px;
}
</style>
