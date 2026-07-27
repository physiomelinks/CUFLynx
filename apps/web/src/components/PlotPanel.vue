<script setup>
import { computed, ref } from 'vue'
import { Line } from 'vue-chartjs'
import Dialog from 'primevue/dialog'
import Button from 'primevue/button'
import InputText from 'primevue/inputtext'
import SciNumberInput from './SciNumberInput.vue'
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
import { fmtSci, fmtAxis, fmtSigFigs } from '../lib/format'

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
  // CellML units for the plotted variable and for the x axis (#125), shown
  // verbatim (they are identifiers such as `mmHg` / `per_second`, not typeset
  // expressions). Empty or `dimensionless` suppresses the annotation.
  yUnit: { type: String, default: '' },
  xUnit: { type: String, default: '' },
  // x-axis title. Defaults to time; a phase-plane cell (issue #124) names the
  // variable whose series `simResult.xValues` carries — and then `xUnit` is that
  // variable's unit rather than the time unit.
  xLabel: { type: String, default: 'time' },
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

// Reading a value off a trace should not require aiming. Chart.js hit-tests each
// *sample* with `distance^2 < (hitRadius + radius)^2` and the traces draw with
// pointRadius 0, so by default the cursor had to land inside a 1px circle around
// a sample. Widening that radius alone isn't enough: hit-testing is per point,
// not per line segment, so on a steep stretch consecutive samples sit far apart
// vertically and the gap between them stays dead — and maximizing the plot
// (issue #115) stretches those gaps further, which is where it was worst.
//
// `intersect: false` drops the containment test altogether: Chart.js then reports
// the nearest sample by distance wherever the cursor is in the plot area, so the
// tooltip tracks the curve continuously. hitRadius still governs the inRange path
// used for hover styling, so it stays widened.
const HIT_RADIUS = 12

// A unit worth showing: blank and `dimensionless` annotate nothing.
function shown(unit) {
  const u = (unit ?? '').trim()
  return u && u !== 'dimensionless' ? u : ''
}

// Unit conversion (#125). `null` = show the model's own unit; otherwise
// { unit, factor } where `factor` converts **from the model's unit** — it is
// always absolute, never relative to whatever is currently on screen, so the
// displayed scale can be read straight off it. Display only — the simulation,
// obs_data and exported pipeline keep the model's units.
const conversion = ref(null)

// The model's own unit, i.e. what the values mean before any conversion.
const originalUnit = computed(() => shown(props.yUnit))

// The unit the plot is currently displayed in.
const displayUnit = computed(() => conversion.value?.unit || originalUnit.value)

// Values as displayed. Scaling here rather than in buildChartData keeps the
// conversion a presentation concern and leaves the shared plot lib untouched.
// Obs overlays are in the model's units too, so they scale with the traces —
// otherwise a converted plot would compare against an unconverted target.
const displayData = computed(() => {
  const f = conversion.value?.factor
  const base = chartData.value
  if (!f || f === 1) return base
  return {
    ...base,
    datasets: base.datasets.map((d) => ({
      ...d,
      data: d.data.map((p) => ({ ...p, y: p.y * f })),
    })),
  }
})

// The y axis carries no title: the variable is already named above the plot in
// LaTeX, and repeating it in plain canvas text was redundant. The unit lives in
// the header instead, where it can be clicked to convert.
//
// The x axis does carry one, because nothing else names it: `time`, or the x
// variable of a phase-plane cell (#124), suffixed with its unit (#125).
const xTitle = computed(() => {
  const label = props.xLabel || 'time'
  const unit = shown(props.xUnit)
  return unit ? `${label} [${unit}]` : label
})

// --- convert-unit dialog ---------------------------------------------------
const convertOpen = ref(false)
const newUnit = ref('')
const newFactor = ref(1)

// Prefill with the conversion in force, so the dialog shows what is currently
// applied and an edit adjusts it rather than starting from a blank slate.
function openConvert() {
  newUnit.value = displayUnit.value
  newFactor.value = conversion.value?.factor ?? 1
  convertOpen.value = true
}

// The factor always multiplies the model's own values, so re-applying replaces
// the conversion instead of compounding on top of the displayed one.
function applyConvert() {
  const unit = String(newUnit.value ?? '').trim()
  const factor = Number(newFactor.value)
  if (!unit || !Number.isFinite(factor) || factor === 0) return
  conversion.value = { unit, factor }
  convertOpen.value = false
}

// The conversion in force, phrased as an equivalence: 1 <model unit> = f <shown>.
// Formatted with fmtSci — the same formatter SciNumberInput displays with — so
// the factor in the summary and the factor in the field always read identically.
const conversionSummary = computed(() => {
  if (!conversion.value) return ''
  const from = originalUnit.value || 'model units'
  return `1 ${from} = ${fmtSci(conversion.value.factor)} ${conversion.value.unit}`
})

// Back to the model's own unit.
function resetConvert() {
  conversion.value = null
  convertOpen.value = false
}

// Custom HTML legend (below) renders LaTeX labels, so disable the canvas one.
const chartOptions = computed(() => ({
  responsive: true,
  maintainAspectRatio: false,
  animation: false,
  interaction: { mode: 'nearest', axis: 'xy', intersect: false },
  elements: { point: { hitRadius: HIT_RADIUS } },
  scales: {
    x: { type: 'linear', title: { display: true, text: xTitle.value }, ticks: sciTicks },
    y: { type: 'linear', ticks: sciTicks },
  },
  plugins: {
    legend: { display: false },
    tooltip: {
      callbacks: {
        // Title is the shared x value of the hovered points. On a phase-plane
        // cell that x is another variable rather than the time, but it wants the
        // same brief 3 s.f. treatment either way.
        title: (items) => (items.length ? fmtSigFigs(items[0].parsed.x, 3) : ''),
        // The canvas tooltip can't render LaTeX, so use the plain `label`.
        label: (ctx) => {
          const value = fmtSci(ctx.parsed.y)
          const name = ctx.dataset?.label
          return name ? `${name}: ${value}` : value
        },
      },
    },
  },
}))

defineExpose({
  chartData,
  displayData,
  chartOptions,
  originalUnit,
  displayUnit,
  conversion,
})
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
      <!--
        The unit sits beside the variable (named above in LaTeX) rather than on
        the y axis, where repeating the variable name was redundant. It is a
        button because clicking it converts the plot to another unit (#125) —
        a canvas axis title could not have been clicked.
      -->
      <button
        v-if="displayUnit"
        type="button"
        class="plot-unit"
        :class="{ converted: !!conversion }"
        :title="
          conversion
            ? `Displayed in ${conversion.unit} (model unit: ${yUnit}). Click to convert.`
            : 'Click to convert this plot to another unit'
        "
        data-testid="plot-unit"
        @click="openConvert"
      >
        [{{ displayUnit }}]
      </button>
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
        :data="displayData"
        :options="chartOptions"
      />
    </div>
    <Dialog
      v-model:visible="convertOpen"
      modal
      header="Convert unit"
      :style="{ width: '22rem' }"
      data-testid="convert-unit-dialog"
    >
      <div class="convert-form">
        <div class="convert-row">
          <span>Original unit</span>
          <code data-testid="convert-original-unit">{{ originalUnit || '(none)' }}</code>
        </div>
        <div class="convert-row">
          <span>Currently shown in</span>
          <code data-testid="convert-current-unit">{{ displayUnit || '(none)' }}</code>
        </div>
        <p
          v-if="conversionSummary"
          class="convert-current"
          data-testid="convert-current-summary"
        >
          {{ conversionSummary }}
        </p>
        <p v-else class="convert-current" data-testid="convert-current-summary">
          No conversion applied — values are in the model's own unit.
        </p>
        <hr class="convert-sep" />
        <label class="convert-row">
          <span>New unit</span>
          <InputText v-model="newUnit" size="small" data-testid="convert-unit-name" />
        </label>
        <label class="convert-row">
          <span>Multiply {{ originalUnit || 'model' }} values by</span>
          <SciNumberInput
            v-model="newFactor"
            class="convert-factor"
            data-testid="convert-unit-factor"
          />
        </label>
        <p class="convert-hint">
          Scientific notation is accepted (e.g. <code>7.5e-3</code>). The factor
          always applies to the model's own values, so it replaces the current
          conversion rather than compounding on it. Display only — the simulation,
          obs_data and exported pipeline keep the model's units.
        </p>
      </div>
      <template #footer>
        <Button
          v-if="conversion"
          label="Reset"
          text
          size="small"
          data-testid="convert-unit-reset"
          @click="resetConvert"
        />
        <Button
          label="Apply"
          size="small"
          data-testid="convert-unit-apply"
          @click="applyConvert"
        />
      </template>
    </Dialog>
    <ul class="legend" data-testid="legend">
      <li v-for="(d, i) in displayData.datasets" :key="i" class="legend-item">
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
.plot-unit {
  border: none;
  background: none;
  padding: 0 0.15rem;
  font: inherit;
  font-size: 0.78rem;
  color: inherit;
  opacity: 0.65;
  cursor: pointer;
}
.plot-unit:hover {
  opacity: 1;
  text-decoration: underline;
}
/* Converted away from the model's own unit: flag it so a rescaled axis is never
   mistaken for the model's values. */
.plot-unit.converted {
  opacity: 1;
  font-style: italic;
}
.convert-form {
  display: flex;
  flex-direction: column;
  gap: 0.6rem;
}
.convert-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 0.6rem;
}
.convert-hint {
  margin: 0;
  font-size: 0.75rem;
  opacity: 0.7;
}
.convert-current {
  margin: 0;
  font-size: 0.78rem;
  opacity: 0.85;
}
.convert-factor {
  width: 9rem;
  text-align: right;
}
.convert-sep {
  border: none;
  border-top: 1px solid var(--p-content-border-color, #ddd);
  margin: 0.1rem 0;
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
