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
import { buildChartData } from '../lib/plot'
import { renderMath } from '../lib/math'
import { fmtSci, fmtAxis } from '../lib/format'

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
  removable: { type: Boolean, default: false },
  // When true this plot is expanded to fill the middle window (issue #115); the
  // button then offers to restore. `maximizable` gates the affordance entirely.
  maximizable: { type: Boolean, default: false },
  maximized: { type: Boolean, default: false },
})

defineEmits(['remove', 'toggle-maximize'])

const chartData = computed(() =>
  buildChartData(props.simResult, {
    dataItems: props.dataItems,
    varLabel: props.varLabel,
    stepped: props.stepped,
  }),
)

// Model values span a huge range (compliances ~1e-9, large resistances), so raw
// JS numbers like 0.0000000015 or 1500000 are unreadable in the cursor tooltip
// and on the ticks. Format both with the shared fmtSci/fmtAxis (issue #107).
const sciTicks = { callback: (v) => fmtAxis(v) }

// Chart.js hit-tests a point with `distance^2 < (hitRadius + radius)^2`, and the
// traces draw with pointRadius 0, so the stock hitRadius of 1 leaves a 1px target
// around each sample — the cursor had to land almost exactly on the line to read
// a value. Widen it so hovering near the trace is enough. Kept modest on purpose:
// large values start capturing whichever line happens to be nearest rather than
// the one under the cursor.
const HIT_RADIUS = 12

// Custom HTML legend (below) renders LaTeX labels, so disable the canvas one.
const chartOptions = {
  responsive: true,
  maintainAspectRatio: false,
  animation: false,
  elements: { point: { hitRadius: HIT_RADIUS } },
  scales: {
    x: { type: 'linear', title: { display: true, text: 'time' }, ticks: sciTicks },
    y: { type: 'linear', ticks: sciTicks },
  },
  plugins: {
    legend: { display: false },
    tooltip: {
      callbacks: {
        // Title is the shared x value of the hovered points.
        title: (items) => (items.length ? fmtSci(items[0].parsed.x) : ''),
        // The canvas tooltip can't render LaTeX, so use the plain `label`.
        label: (ctx) => {
          const value = fmtSci(ctx.parsed.y)
          const name = ctx.dataset?.label
          return name ? `${name}: ${value}` : value
        },
      },
    },
  },
}

defineExpose({ chartData, chartOptions })
</script>

<template>
  <section class="plot-panel">
    <div v-if="tag || title || removable || maximizable" class="plot-head">
      <span v-if="tag" class="plot-tag" data-testid="plot-tag">{{ tag }}</span>
      <h3
        v-if="title"
        class="plot-title"
        data-testid="plot-title"
        v-html="renderMath(title)"
      />
      <button
        v-if="maximizable"
        type="button"
        class="plot-maximize"
        :title="maximized ? 'Restore plot' : 'Maximize plot'"
        :aria-label="maximized ? 'Restore plot' : 'Maximize plot'"
        :aria-pressed="maximized"
        data-testid="plot-maximize"
        @click="$emit('toggle-maximize')"
      >
        <i :class="maximized ? 'pi pi-window-minimize' : 'pi pi-window-maximize'" />
      </button>
      <button
        v-if="removable"
        type="button"
        class="plot-remove"
        title="Remove plot"
        aria-label="Remove plot"
        data-testid="plot-remove"
        @click="$emit('remove')"
      >
        ✕
      </button>
    </div>
    <div class="chart-wrap">
      <!--
        Remount the chart when the maximize state changes (issue #115): Chart.js
        with maintainAspectRatio:false grows the canvas to fill the maximized
        window but doesn't shrink it back on restore (the enlarged canvas keeps
        inflating its auto-height container), leaving the y-axis stretched. A key
        tied to `maximized` destroys the stale canvas so a fresh one sizes to the
        restored cell.
      -->
      <Line
        :key="maximized ? 'maximized' : 'normal'"
        :data="chartData"
        :options="chartOptions"
      />
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
.plot-maximize {
  margin-left: auto;
  border: none;
  background: none;
  color: inherit;
  cursor: pointer;
  opacity: 0.5;
  font-size: 0.8rem;
  line-height: 1;
  padding: 0.1rem 0.25rem;
}
.plot-maximize:hover {
  opacity: 1;
}
.plot-remove {
  margin-left: auto;
  border: none;
  background: none;
  color: inherit;
  cursor: pointer;
  opacity: 0.5;
  font-size: 0.85rem;
  line-height: 1;
  padding: 0.1rem 0.25rem;
}
/* When a maximize button precedes it, sit next to it rather than re-pushing right. */
.plot-maximize + .plot-remove {
  margin-left: 0;
}
.plot-remove:hover {
  opacity: 1;
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
