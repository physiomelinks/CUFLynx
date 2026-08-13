<script setup>
/**
 * The MCMC chain, autocorrelation and windowed mean, drawn while the run is still going (#244).
 *
 * All three are many-lines-one-axes plots, so they share one renderer rather than three
 * near-identical ones. Every series is already thinned by the API -- the browser cannot draw
 * 100k points per parameter and would show nothing more for having them.
 */
import { computed, ref } from 'vue'
import { niceTicks, fmtTick, PALETTE } from '../lib/plot'
import { renderMath } from '../lib/math'

const props = defineProps({
  // The /api/uq/{job}/progress payload; null before the first poll returns.
  progress: { type: Object, default: null },
  running: { type: Boolean, default: false },
  // qname -> LaTeX/plotting name from params_for_id, so a panel is titled the way the output
  // plots title it rather than with the raw model qname.
  paramLabels: { type: Object, default: () => ({}) },
  // True once a run has been through this panel, so the section stays put afterwards rather
  // than disappearing the moment `running` goes false -- the calibration charts persist and
  // this should too.
  finished: { type: Boolean, default: false },
})

const VIEW_W = 340
const VIEW_H = 120
const MARGIN = { top: 8, right: 10, bottom: 26, left: 52 }

const VIEWS = [
  {
    key: 'trace',
    title: 'Chain',
    xLabel: 'step',
    hint: 'Walkers that have settled into the same band have found the posterior; one wandering'
      + ' off on its own has not.',
  },
  {
    key: 'cumulative',
    title: 'Cumulative mean',
    xLabel: 'step',
    hint: 'Each point averages everything up to it, so the line flattens once the estimate'
      + ' stops moving. The two lines converging means the burn-in no longer matters; a'
      + ' persistent gap means it does.',
  },
  {
    key: 'autocorrelation',
    title: 'Autocorrelation',
    xLabel: 'lag',
    hint: 'Inside ±0.1 by the end of the trace means the chain is producing near-independent'
      + ' draws.',
  },
]

const view = ref('trace')
const activeView = computed(() => VIEWS.find((v) => v.key === view.value) ?? VIEWS[0])

const hasChain = computed(() => (props.progress?.steps ?? 0) > 0)

/** The x values and per-parameter line sets for whichever view is selected. */
const plotted = computed(() => {
  const p = props.progress
  if (!p || !p.steps) return null
  if (view.value === 'cumulative') {
    if (!p.cumulative_mean) return null
    // Two lines per parameter, in a fixed order so the legend below means something.
    return {
      x: p.cumulative_mean.steps,
      series: p.cumulative_mean.series.map((s) => [s.from_start, s.from_burn_in]),
      guides: [],
      legend: ['from step 0', `from burn-in (step ${p.cumulative_mean.burn_in})`],
    }
  }
  if (view.value === 'autocorrelation') {
    if (!p.autocorrelation) return null
    // The ±0.1 band is what makes this plot readable — it is the threshold being judged.
    return { x: p.autocorrelation.lags, series: p.autocorrelation.series, guides: [0.1, 0, -0.1] }
  }
  return { x: p.trace_steps, series: p.traces, guides: [] }
})

/** Why there is nothing to draw — never a blank panel with no explanation. */
const emptyReason = computed(() => {
  if (!hasChain.value) {
    if (props.running) return 'Waiting for the first chain checkpoint…'
    // A finished run with no chain is a real outcome worth naming: a Laplace run writes none,
    // and an MCMC run that failed early leaves none either. Saying "run an MCMC analysis" to
    // someone who just did would send them looking in the wrong place.
    if (props.finished) {
      return 'That run finished without writing a chain. A Laplace run has none to write; an'
        + ' MCMC run should — check the run log for what it did instead.'
    }
    return 'Run an MCMC analysis to watch the chain here.'
  }
  if (!plotted.value && view.value === 'cumulative') {
    return 'Not enough steps yet to average.'
  }
  return plotted.value ? '' : 'Not enough steps yet for this view.'
})

function panelFor(paramIdx) {
  const data = plotted.value
  const lines = data.series[paramIdx] ?? []
  const flat = lines.flat().filter((v) => v != null && Number.isFinite(v))
  if (!flat.length || !data.x.length) return null

  let lo = Math.min(...flat, ...data.guides)
  let hi = Math.max(...flat, ...data.guides)
  if (hi === lo) {
    const pad = Math.abs(hi) || 1
    lo -= pad / 2
    hi += pad / 2
  }
  const x0 = MARGIN.left
  const x1 = VIEW_W - MARGIN.right
  const y0 = VIEW_H - MARGIN.bottom
  const y1 = MARGIN.top
  const xMin = data.x[0]
  const xMax = data.x[data.x.length - 1]
  const sx = (v) => x0 + ((v - xMin) / (xMax - xMin || 1)) * (x1 - x0)
  const sy = (v) => y0 - ((v - lo) / (hi - lo)) * (y0 - y1)

  return {
    x0,
    x1,
    y0,
    y1,
    paths: lines.map((line, i) => ({
      // `null` is a gap, not a zero: the burn-in line does not exist before the burn-in, and
      // joining across it would draw a slope that was never sampled.
      d: line.reduce((acc, v, j) => {
        if (v == null || !Number.isFinite(v)) return acc + ''
        const cmd = acc === '' || line[j - 1] == null ? 'M' : 'L'
        return `${acc}${cmd}${sx(data.x[j]).toFixed(1)} ${sy(v).toFixed(1)} `
      }, ''),
      colour: PALETTE[i % PALETTE.length],
    })),
    guides: data.guides.map((g) => ({ y: sy(g), zero: g === 0 })),
    xTicks: niceTicks(xMin, xMax).map((v) => ({ v, at: sx(v), text: fmtTick(v) })),
    yTicks: niceTicks(lo, hi).map((v) => ({ v, at: sy(v), text: fmtTick(v) })),
  }
}

const panels = computed(() => {
  if (!plotted.value) return []
  return (props.progress.param_labels ?? []).map((label, idx) => ({
    label,
    // Same lookup the UQ posteriors use: name_for_plotting when there is one, the qname
    // otherwise. renderMath turns \alpha into the symbol.
    display: props.paramLabels?.[label] ?? label,
    geom: panelFor(idx),
  })).filter((p) => p.geom)
})
</script>

<template>
  <section class="mcmc-progress" data-testid="mcmc-progress">
    <header class="mcmc-head">
      <h3>MCMC</h3>
      <div class="view-toggle" data-testid="mcmc-view-toggle">
        <button
          v-for="v in VIEWS"
          :key="v.key"
          :class="{ active: view === v.key }"
          :data-testid="`mcmc-view-${v.key}`"
          @click="view = v.key"
        >
          {{ v.title }}
        </button>
      </div>
      <span v-if="hasChain" class="mcmc-count" data-testid="mcmc-steps">
        {{ progress.steps }} steps ·
        {{ progress.walkers_shown }}<template v-if="progress.walkers_shown < progress.walkers">
          of {{ progress.walkers }}</template> walkers
      </span>
      <span
        v-if="view === 'autocorrelation' && progress?.autocorrelation"
        class="mcmc-verdict"
        :data-bounded="progress.autocorrelation.bounded"
        data-testid="mcmc-bounded"
      >
        {{ progress.autocorrelation.bounded ? 'within ±0.1' : 'still correlated' }}
      </span>
    </header>

    <p v-if="emptyReason" class="empty-hint" data-testid="mcmc-empty">{{ emptyReason }}</p>

    <template v-else>
      <p class="mcmc-hint">{{ activeView.hint }}</p>
      <div v-if="plotted?.legend" class="mcmc-legend" data-testid="mcmc-legend">
        <span v-for="(label, i) in plotted.legend" :key="label">
          <i :style="{ background: PALETTE[i % PALETTE.length] }" />{{ label }}
        </span>
      </div>
      <div
        v-for="panel in panels"
        :key="panel.label"
        class="mcmc-panel"
        data-testid="mcmc-panel"
      >
        <span class="mcmc-label" v-html="renderMath(panel.display)" />
        <svg
          :viewBox="`0 0 ${VIEW_W} ${VIEW_H}`"
          preserveAspectRatio="xMidYMid meet"
          class="mcmc-plot"
          role="img"
          :aria-label="`${activeView.title} for ${panel.display}`"
        >
          <line
            v-for="g in panel.geom.guides"
            :key="'g' + g.y"
            class="guide"
            :class="{ zero: g.zero }"
            :x1="panel.geom.x0"
            :y1="g.y"
            :x2="panel.geom.x1"
            :y2="g.y"
          />
          <path
            v-for="(p, i) in panel.geom.paths"
            :key="i"
            :d="p.d"
            :stroke="p.colour"
            class="walker"
          />
          <g class="axis">
            <line
              :x1="panel.geom.x0"
              :y1="panel.geom.y0"
              :x2="panel.geom.x1"
              :y2="panel.geom.y0"
            />
            <line
              :x1="panel.geom.x0"
              :y1="panel.geom.y0"
              :x2="panel.geom.x0"
              :y2="panel.geom.y1"
            />
            <template v-for="t in panel.geom.xTicks" :key="'x' + t.v">
              <line :x1="t.at" :y1="panel.geom.y0" :x2="t.at" :y2="panel.geom.y0 + 3" />
              <text class="tick" :x="t.at" :y="panel.geom.y0 + 12" text-anchor="middle">
                {{ t.text }}
              </text>
            </template>
            <template v-for="t in panel.geom.yTicks" :key="'y' + t.v">
              <line :x1="panel.geom.x0 - 3" :y1="t.at" :x2="panel.geom.x0" :y2="t.at" />
              <text class="tick" :x="panel.geom.x0 - 6" :y="t.at + 3" text-anchor="end">
                {{ t.text }}
              </text>
            </template>
          </g>
          <text
            class="axis-title"
            :x="(panel.geom.x0 + panel.geom.x1) / 2"
            :y="VIEW_H - 2"
            text-anchor="middle"
          >
            {{ activeView.xLabel }}
          </text>
        </svg>
      </div>
    </template>
  </section>
</template>

<style scoped>
.mcmc-head {
  display: flex;
  align-items: center;
  gap: 0.6rem;
  flex-wrap: wrap;
}
.mcmc-head h3 {
  margin: 0;
  font-size: 0.95rem;
}
.view-toggle {
  display: flex;
  gap: 0.15rem;
}
.view-toggle button {
  font-size: 0.7rem;
  padding: 0.1rem 0.45rem;
  border: 1px solid var(--p-content-border-color, #ccc);
  background: transparent;
  color: inherit;
  cursor: pointer;
  border-radius: 3px;
}
.view-toggle button.active {
  background: var(--p-primary-color, #5b9bd5);
  color: #fff;
  border-color: var(--p-primary-color, #5b9bd5);
}
.mcmc-count,
.mcmc-verdict {
  font-size: 0.7rem;
  opacity: 0.75;
}
.mcmc-verdict[data-bounded='false'] {
  color: #e84a5f;
  opacity: 1;
}
.mcmc-legend {
  display: flex;
  gap: 0.9rem;
  font-size: 0.7rem;
  opacity: 0.85;
  margin-bottom: 0.3rem;
}
.mcmc-legend i {
  display: inline-block;
  width: 0.7rem;
  height: 0.15rem;
  margin-right: 0.3rem;
  vertical-align: middle;
}
.mcmc-hint {
  font-size: 0.7rem;
  opacity: 0.75;
  margin: 0.2rem 0 0.4rem;
  max-width: 44rem;
}
.mcmc-panel {
  margin-bottom: 0.3rem;
}
.mcmc-label {
  font-size: 0.7rem;
  opacity: 0.85;
}
.mcmc-plot {
  width: 100%;
  max-width: 40rem;
  display: block;
}
.walker {
  fill: none;
  stroke-width: 0.8;
  opacity: 0.65;
  vector-effect: non-scaling-stroke;
}
.guide {
  stroke: #e84a5f;
  stroke-width: 1;
  stroke-dasharray: 4 3;
  vector-effect: non-scaling-stroke;
}
.guide.zero {
  stroke: var(--p-content-border-color, #888);
  stroke-dasharray: none;
}
.axis line {
  stroke: var(--p-content-border-color, #888);
  stroke-width: 1;
  vector-effect: non-scaling-stroke;
}
.tick {
  font-size: 8px;
  fill: currentColor;
  opacity: 0.7;
}
.axis-title {
  font-size: 9px;
  fill: currentColor;
  opacity: 0.85;
}
.empty-hint {
  opacity: 0.65;
  padding: 0.6rem 0;
  font-size: 0.8rem;
}
</style>
