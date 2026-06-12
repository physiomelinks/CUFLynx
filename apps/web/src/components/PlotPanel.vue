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
} from 'chart.js'
import katex from 'katex'
import 'katex/dist/katex.min.css'
import { buildChartData } from '../lib/plot'

ChartJS.register(
  LinearScale,
  PointElement,
  LineElement,
  ScatterController,
  LineController,
  Tooltip,
)

const props = defineProps({
  simResult: { type: Object, default: null },
  dataItems: { type: Array, default: () => [] },
  title: { type: String, default: '' },
  varLabel: { type: String, default: '' },
  tag: { type: String, default: '' },
  stepped: { type: Boolean, default: false },
})

const chartData = computed(() =>
  buildChartData(props.simResult, {
    dataItems: props.dataItems,
    varLabel: props.varLabel,
    stepped: props.stepped,
  }),
)

// Custom HTML legend (below) renders LaTeX labels, so disable the canvas one.
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

function escapeHtml(s) {
  return String(s)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
}

// Only treat labels with LaTeX markup (braces, backslash commands, carets) as
// math; plain qnames like "aortic_root/v" render as readable text.
function looksLikeLatex(s) {
  return /[\\{}^]/.test(s)
}

/** Render a LaTeX label to HTML, falling back to escaped text. */
function renderMath(s) {
  if (!s) return ''
  if (!looksLikeLatex(s)) return escapeHtml(s)
  try {
    return katex.renderToString(String(s), { throwOnError: false })
  } catch {
    return escapeHtml(s)
  }
}

defineExpose({ chartData })
</script>

<template>
  <section class="plot-panel">
    <div v-if="tag || title" class="plot-head">
      <span v-if="tag" class="plot-tag" data-testid="plot-tag">{{ tag }}</span>
      <h3
        v-if="title"
        class="plot-title"
        data-testid="plot-title"
        v-html="renderMath(title)"
      />
    </div>
    <div class="chart-wrap">
      <Line :data="chartData" :options="chartOptions" />
    </div>
    <ul class="legend" data-testid="legend">
      <li v-for="(d, i) in chartData.datasets" :key="i" class="legend-item">
        <svg class="swatch" width="22" height="10" aria-hidden="true">
          <circle
            v-if="d.legendStyle === 'point'"
            cx="11"
            cy="5"
            r="3.5"
            :fill="d.borderColor"
          />
          <line
            v-else
            x1="1"
            y1="5"
            x2="21"
            y2="5"
            :stroke="d.borderColor"
            stroke-width="2"
            :stroke-dasharray="d.legendStyle === 'dash' ? '4 2' : undefined"
          />
        </svg>
        <span class="legend-label" v-html="renderMath(d.mathLabel)" />
        <span v-if="d.suffix" class="legend-suffix">{{ d.suffix }}</span>
      </li>
    </ul>
  </section>
</template>

<style scoped>
.plot-panel {
  display: flex;
  flex-direction: column;
  height: 100%;
  padding: 0.5rem;
}
.plot-head {
  display: flex;
  align-items: center;
  gap: 0.4rem;
  margin: 0 0 0.25rem;
}
.plot-tag {
  font-size: 0.65rem;
  text-transform: uppercase;
  letter-spacing: 0.04em;
  background: var(--p-primary-color, #5b9bd5);
  color: #fff;
  border-radius: 3px;
  padding: 0.05rem 0.35rem;
}
.plot-title {
  margin: 0;
  font-size: 0.8rem;
  font-weight: 600;
  opacity: 0.85;
}
.chart-wrap {
  position: relative;
  flex: 1;
  min-height: 160px;
}
.legend {
  list-style: none;
  margin: 0.4rem 0 0;
  padding: 0;
  display: flex;
  flex-wrap: wrap;
  gap: 0.25rem 0.9rem;
  font-size: 0.78rem;
}
.legend-item {
  display: inline-flex;
  align-items: center;
  gap: 0.3rem;
}
.swatch {
  flex: 0 0 auto;
}
.legend-suffix {
  opacity: 0.6;
  font-style: italic;
}
</style>
