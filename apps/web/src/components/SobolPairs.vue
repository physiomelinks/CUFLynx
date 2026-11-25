<script setup>
import { computed, ref, watch } from 'vue'
import { fmtTick, niceTicks } from '../lib/plot'

/**
 * A Sobol design, one parameter against another: every sample that ran, the failed ones
 * in red on top (CA #702's `sobol_sample_status.npy`), so a region of parameter space
 * where the model fails shows as a red cluster. `design` is `/sobol-progress`'s:
 * `{ names, bounds, rows, status, counts }`, the backend having kept every failure and a
 * spread of the rest.
 */
const props = defineProps({
  design: { type: Object, required: true },
  labels: { type: Object, default: () => ({}) },
})

const OK = '#5b9bd5'
const FAILED = '#e84a5f'
const W = 360
const H = 300
const M = { left: 56, right: 28, top: 10, bottom: 40 }

const names = computed(() => props.design.names ?? [])
const label = (name) => props.labels[name] ?? name
const xi = ref(0)
const yi = ref(1)
watch(names, (n) => {
  if (xi.value >= n.length) xi.value = 0
  if (yi.value >= n.length) yi.value = Math.min(1, n.length - 1)
})

function range(i) {
  const b = props.design.bounds?.[i]
  if (Array.isArray(b) && Number.isFinite(b[0]) && Number.isFinite(b[1]) && b[1] > b[0]) {
    return [b[0], b[1]]
  }
  const vals = (props.design.rows ?? []).map((r) => r[i]).filter(Number.isFinite)
  if (!vals.length) return [0, 1]
  const lo = Math.min(...vals)
  const hi = Math.max(...vals)
  return hi > lo ? [lo, hi] : [lo - 1, hi + 1]
}

const xr = computed(() => range(xi.value))
const yr = computed(() => range(yi.value))
const sx = (v) => M.left + ((v - xr.value[0]) / (xr.value[1] - xr.value[0])) * (W - M.left - M.right)
const sy = (v) => H - M.bottom - ((v - yr.value[0]) / (yr.value[1] - yr.value[0])) * (H - M.top - M.bottom)

const points = computed(() => {
  const rows = props.design.rows ?? []
  const status = props.design.status ?? []
  const out = rows.map((row, k) => ({ row, failed: status[k] === 2 }))
  // failures last, so they are drawn over the samples that ran
  return [...out.filter((p) => !p.failed), ...out.filter((p) => p.failed)]
})

const xticks = computed(() => niceTicks(xr.value[0], xr.value[1], 4))
const yticks = computed(() => niceTicks(yr.value[0], yr.value[1], 4))
const counts = computed(() => props.design.counts ?? {})

const exact = (v) => (Number.isFinite(v) ? String(Number(v.toPrecision(5))) : '—')

function tip(p) {
  const a = label(names.value[xi.value])
  const b = label(names.value[yi.value])
  return `${p.failed ? 'failed' : 'ran'}: ${a} = ${exact(p.row[xi.value])}, ${b} = ${exact(p.row[yi.value])}`
}
</script>

<template>
  <div class="sobol-pairs" data-testid="sobol-pairs">
    <p class="muted" data-testid="sobol-pairs-counts">
      {{ counts.ran + counts.failed }} of {{ counts.total }} samples run,
      <span :class="{ 'failed-text': counts.failed }">{{ counts.failed }} failed</span>
      <span v-if="design.rows?.length < counts.ran + counts.failed">
        (every failure and {{ design.rows.length - counts.failed }} of the others shown)
      </span>
    </p>
    <p v-if="names.length < 2" class="muted">One parameter: there is no pair to plot.</p>
    <template v-else>
      <div class="pickers">
        <label>x
          <select v-model.number="xi" data-testid="sobol-pairs-x">
            <option v-for="(n, i) in names" :key="n" :value="i">{{ label(n) }}</option>
          </select>
        </label>
        <label>y
          <select v-model.number="yi" data-testid="sobol-pairs-y">
            <option v-for="(n, i) in names" :key="n" :value="i">{{ label(n) }}</option>
          </select>
        </label>
        <span class="legend">
          <span class="dot" :style="{ background: OK }" /> ran
          <span class="dot" :style="{ background: FAILED }" /> failed
        </span>
      </div>
      <svg :viewBox="`0 0 ${W} ${H}`" :width="W" :height="H" role="img"
           :aria-label="`Sobol design: ${label(names[yi])} against ${label(names[xi])}`">
        <g class="axis">
          <line :x1="M.left" :x2="W - M.right" :y1="H - M.bottom" :y2="H - M.bottom" />
          <line :x1="M.left" :x2="M.left" :y1="M.top" :y2="H - M.bottom" />
          <g v-for="t in xticks" :key="`x${t}`">
            <text :x="sx(t)" :y="H - M.bottom + 14" text-anchor="middle">{{ fmtTick(t) }}</text>
          </g>
          <g v-for="t in yticks" :key="`y${t}`">
            <text :x="M.left - 6" :y="sy(t) + 4" text-anchor="end">{{ fmtTick(t) }}</text>
          </g>
          <text :x="(M.left + W - M.right) / 2" :y="H - 6" text-anchor="middle">
            {{ label(names[xi]) }}
          </text>
          <text :transform="`translate(12 ${(M.top + H - M.bottom) / 2}) rotate(-90)`"
                text-anchor="middle">{{ label(names[yi]) }}</text>
        </g>
        <circle
          v-for="(p, k) in points"
          :key="k"
          :cx="sx(p.row[xi])"
          :cy="sy(p.row[yi])"
          :r="p.failed ? 3.5 : 2.5"
          :fill="p.failed ? FAILED : OK"
          :fill-opacity="p.failed ? 0.95 : 0.5"
          :data-failed="p.failed ? '1' : '0'"
        >
          <title>{{ tip(p) }}</title>
        </circle>
      </svg>
    </template>
  </div>
</template>

<style scoped>
.muted {
  opacity: 0.75;
  font-size: 0.85rem;
  margin: 0 0 0.4rem;
}
.failed-text {
  color: #e84a5f;
}
.pickers {
  display: flex;
  gap: 1rem;
  align-items: center;
  font-size: 0.85rem;
  margin-bottom: 0.3rem;
}
.legend {
  display: inline-flex;
  gap: 0.35rem;
  align-items: center;
}
.dot {
  display: inline-block;
  width: 8px;
  height: 8px;
  border-radius: 50%;
}
.axis line {
  stroke: currentColor;
  stroke-opacity: 0.4;
}
.axis text {
  fill: currentColor;
  font-size: 10px;
  opacity: 0.8;
}
</style>
