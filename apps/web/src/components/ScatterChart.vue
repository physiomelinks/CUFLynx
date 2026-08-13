<script setup>
/**
 * A small scatter plot with real axes.
 *
 * Points arrive in *data* coordinates and are scaled here, so the guide line is
 * drawn in the same space as the points it is judged against. The parity plot
 * used to draw its 1:1 line as a CSS-rotated div, which is only a diagonal when
 * the box happens to be square -- at any other width the line and the points
 * disagreed, and the plot said the emulator was biased when it was not.
 */
import { computed } from 'vue'
import { niceTicks, fmtTick } from '../lib/plot'

const props = defineProps({
  points: { type: Array, default: () => [] }, // [{ x, y, title }]
  xDomain: { type: Array, required: true }, // [lo, hi] in data units
  yDomain: { type: Array, required: true },
  xLabel: { type: String, default: '' },
  yLabel: { type: String, default: '' },
  /** 'diagonal' draws y = x, 'zero' draws y = 0. Both in data coordinates. */
  guide: { type: String, default: '' },
  /** Square for parity plots, where a 45 degree line is the point of the plot. */
  square: { type: Boolean, default: false },
})

const VIEW_W = 320
const VIEW_H = computed(() => (props.square ? 320 : 210))
const MARGIN = { top: 10, right: 12, bottom: 34, left: 54 }

const plot = computed(() => ({
  x0: MARGIN.left,
  x1: VIEW_W - MARGIN.right,
  y0: VIEW_H.value - MARGIN.bottom, // bottom, in SVG's downward y
  y1: MARGIN.top,
}))

/** A domain that is never zero-width, so a constant column still plots. */
function safeDomain([lo, hi]) {
  const l = Number(lo)
  const h = Number(hi)
  if (!Number.isFinite(l) || !Number.isFinite(h)) return [0, 1]
  if (h > l) return [l, h]
  const pad = Math.abs(l) || 1
  return [l - pad / 2, l + pad / 2]
}

const xd = computed(() => safeDomain(props.xDomain))
const yd = computed(() => safeDomain(props.yDomain))

const sx = (v) =>
  plot.value.x0 + ((v - xd.value[0]) / (xd.value[1] - xd.value[0])) * (plot.value.x1 - plot.value.x0)
const sy = (v) =>
  plot.value.y0 - ((v - yd.value[0]) / (yd.value[1] - yd.value[0])) * (plot.value.y0 - plot.value.y1)

const xTicks = computed(() =>
  niceTicks(...xd.value).map((v) => ({ v, at: sx(v), text: fmtTick(v) })),
)
const yTicks = computed(() =>
  niceTicks(...yd.value).map((v) => ({ v, at: sy(v), text: fmtTick(v) })),
)

const scaled = computed(() =>
  props.points.map((p) => ({ cx: sx(Number(p.x)), cy: sy(Number(p.y)), title: p.title })),
)

/** The guide, in data coordinates, clipped to whatever both axes cover. */
const guideLine = computed(() => {
  if (props.guide === 'diagonal') {
    const lo = Math.max(xd.value[0], yd.value[0])
    const hi = Math.min(xd.value[1], yd.value[1])
    if (!(hi > lo)) return null
    return { x1: sx(lo), y1: sy(lo), x2: sx(hi), y2: sy(hi) }
  }
  if (props.guide === 'zero') {
    if (yd.value[0] > 0 || yd.value[1] < 0) return null
    return { x1: plot.value.x0, y1: sy(0), x2: plot.value.x1, y2: sy(0) }
  }
  return null
})
</script>

<template>
  <svg
    class="scatter-chart"
    :class="{ square }"
    :viewBox="`0 0 ${VIEW_W} ${VIEW_H}`"
    preserveAspectRatio="xMidYMid meet"
    role="img"
    :aria-label="`${yLabel} against ${xLabel}`"
  >
    <!-- Gridlines first, so nothing else is drawn under them. -->
    <g class="grid">
      <line
        v-for="t in xTicks"
        :key="'gx' + t.v"
        :x1="t.at"
        :y1="plot.y0"
        :x2="t.at"
        :y2="plot.y1"
      />
      <line
        v-for="t in yTicks"
        :key="'gy' + t.v"
        :x1="plot.x0"
        :y1="t.at"
        :x2="plot.x1"
        :y2="t.at"
      />
    </g>

    <line
      v-if="guideLine"
      class="guide"
      data-testid="chart-guide"
      :x1="guideLine.x1"
      :y1="guideLine.y1"
      :x2="guideLine.x2"
      :y2="guideLine.y2"
    />

    <circle
      v-for="(p, i) in scaled"
      :key="i"
      class="parity-point"
      :cx="p.cx"
      :cy="p.cy"
      r="3"
    >
      <title v-if="p.title">{{ p.title }}</title>
    </circle>

    <!-- Axes on top: points crowding the origin must not hide the scale. -->
    <g class="axis">
      <line :x1="plot.x0" :y1="plot.y0" :x2="plot.x1" :y2="plot.y0" />
      <line :x1="plot.x0" :y1="plot.y0" :x2="plot.x0" :y2="plot.y1" />
      <template v-for="t in xTicks" :key="'tx' + t.v">
        <line :x1="t.at" :y1="plot.y0" :x2="t.at" :y2="plot.y0 + 4" />
        <text class="tick" :x="t.at" :y="plot.y0 + 14" text-anchor="middle">{{ t.text }}</text>
      </template>
      <template v-for="t in yTicks" :key="'ty' + t.v">
        <line :x1="plot.x0 - 4" :y1="t.at" :x2="plot.x0" :y2="t.at" />
        <text class="tick" :x="plot.x0 - 7" :y="t.at + 3" text-anchor="end">{{ t.text }}</text>
      </template>
    </g>

    <text
      v-if="xLabel"
      class="axis-title"
      :x="(plot.x0 + plot.x1) / 2"
      :y="VIEW_H - 4"
      text-anchor="middle"
    >
      {{ xLabel }}
    </text>
    <text
      v-if="yLabel"
      class="axis-title"
      :transform="`translate(11 ${(plot.y0 + plot.y1) / 2}) rotate(-90)`"
      text-anchor="middle"
    >
      {{ yLabel }}
    </text>
  </svg>
</template>

<style scoped>
.scatter-chart {
  width: 100%;
  max-width: 34rem;
  display: block;
}
/* Square only for parity, where the eye reads the 45 degree line as "equal". */
.scatter-chart.square {
  max-width: 22rem;
}
.axis line {
  stroke: var(--p-content-border-color, #888);
  stroke-width: 1;
}
.grid line {
  stroke: var(--p-content-border-color, #ddd);
  stroke-width: 0.5;
  opacity: 0.5;
}
.guide {
  stroke: var(--p-content-border-color, #bbb);
  stroke-width: 1;
  stroke-dasharray: 4 3;
}
.parity-point {
  fill: var(--p-primary-color, #5b9bd5);
  opacity: 0.75;
}
.tick {
  font-size: 9px;
  fill: currentColor;
  opacity: 0.7;
}
.axis-title {
  font-size: 10px;
  fill: currentColor;
  opacity: 0.85;
}
</style>
