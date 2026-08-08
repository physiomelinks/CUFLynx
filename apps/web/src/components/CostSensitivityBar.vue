<script setup>
/**
 * Which parameter is driving the cost, beside the cost itself (issue #188).
 *
 * The cost line says the parameters cost 36.8. This says it is `alpha` that the
 * 36.8 is about, and which way to drag it.
 *
 * One row per parameter, in the order the sliders are in. Ranking by magnitude
 * reads well in isolation but makes the panel a different list from the one
 * beside it, so finding a parameter means re-scanning instead of looking across
 * -- and the bar lengths already carry the ranking without moving anything.
 *
 * The number is `d ln(cost)/d ln(p)`: the percentage the cost moves per percent
 * the parameter moves. Raw dJ/dp cannot be ranked -- across parameters measured
 * in mmHg, seconds and litres per second, the biggest derivative is whichever
 * parameter happens to be smallest in its own units.
 */
import Button from 'primevue/button'
import ProgressSpinner from 'primevue/progressspinner'
import { computed } from 'vue'
import { renderMath } from '../lib/math'

const props = defineProps({
  // The /api/cost_sensitivity payload, or null before the first run.
  result: { type: Object, default: null },
  // 'running' | 'stale' | 'ready' | 'error'. `stale` means the sliders have
  // moved since these were measured, so they describe a point that is no longer
  // on screen -- shown, but dimmed and labelled, because the last known ranking
  // is usually still the useful one and silently wrong numbers are not.
  status: { type: String, default: 'ready' },
  error: { type: String, default: '' },
  // qname -> name_for_plotting, so a row reads like the slider it belongs to.
  labels: { type: Object, default: () => ({}) },
  // Whether analytic (AD) gradients are available *for this study* — casadi_python
  // with every operation the loaded obs_data actually uses @differentiable. Passed
  // in rather than read from the payload because that judgement is per-obs_data,
  // not per-backend: the global "are all registered operations differentiable"
  // flag is false for everyone (16 of CA's 38 ops are not), so deciding from it
  // would warn on every study including the ones where AD demonstrably works.
  // Defaults to true so a caller that does not wire it makes no claim of slowness.
  adAvailable: { type: Boolean, default: true },
})

defineEmits(['recompute'])

const rows = computed(() => {
  const params = props.result?.params ?? []
  const scored = params.filter((p) => p.elasticity != null)
  // The largest magnitude sets the bar scale: these are relative to each other,
  // and there is no absolute scale a coefficient could be drawn against.
  const peak = Math.max(...scored.map((p) => Math.abs(p.elasticity)), 0)
  // Not sorted: the backend returns them in the order they were sent, which is
  // the order of the parameter column, so the two lists line up row for row.
  return [...params]
    .map((p) => ({
      ...p,
      label: props.labels?.[p.name] ?? p.name,
      width: peak > 0 && p.elasticity != null ? (Math.abs(p.elasticity) / peak) * 100 : 0,
    }))
})

// The FD step as a percentage. Named rather than inlined because 1e-4 * 100 is
// 0.010000000000000002 in binary floating point, and a stray tail of digits in a
// caption reads as a bug in the number beside it.
const stepLabel = computed(() =>
  props.result?.rel_step ? `${+(props.result.rel_step * 100).toPrecision(3)}%` : '',
)

/** Coefficients span orders of magnitude; significant figures read either way. */
function format(value) {
  if (value == null || !Number.isFinite(value)) return '—'
  const abs = Math.abs(value)
  if (abs !== 0 && (abs < 1e-2 || abs >= 1e4)) return value.toExponential(1)
  return value.toPrecision(2).replace(/\.?0+$/, '')
}

/**
 * Which way to drag. The sign is the actionable half of the number -- a user
 * with a cost and a ranking still has to guess the direction otherwise -- so it
 * is spelled out rather than left implicit in a minus sign.
 */
function direction(value) {
  if (value == null || !Number.isFinite(value) || value === 0) return ''
  return value < 0 ? 'increase to improve' : 'decrease to improve'
}
</script>

<template>
  <div class="cost-sens" data-testid="cost-sensitivity">
    <div class="cost-sens-head">
      <span class="cost-sens-title">cost sensitivity</span>
      <span class="cost-sens-sub" data-testid="cost-sens-method">
        d ln(cost)/d ln(p)<template v-if="stepLabel">
          — central differences, step {{ stepLabel }}</template
        ><template v-if="result?.n_simulations">
          ({{ result.n_simulations }} simulations)</template>
      </span>
      <!--
        Issue #188: differencing costs the same 2M+1 solves on every backend, but
        only a backend without analytic (AD) gradients has no cheaper route in
        principle — so that is where the price is worth flagging rather than
        letting a drag quietly turn sluggish.
      -->
      <span
        v-if="result && !adAvailable"
        class="cost-sens-slow"
        data-testid="cost-sens-no-ad"
        :title="
          `No analytic (AD) gradients for this study, so each update runs ` +
          `${result.n_simulations ?? '2M+1'} simulations. AD needs the ` +
          `casadi_python model format with every operation this obs_data uses ` +
          `marked @differentiable.`
        "
      >
        slow: no AD
      </span>
      <ProgressSpinner
        v-if="status === 'running'"
        style="width: 0.9rem; height: 0.9rem"
        stroke-width="7"
        data-testid="cost-sens-running"
      />
      <!--
        Stale is offered as a choice, not fixed automatically: recomputing on
        every pixel of a drag would queue 2M+1 simulations behind the plot the
        user is actually watching.
      -->
      <Button
        v-else-if="status === 'stale'"
        label="recompute"
        text
        size="small"
        data-testid="cost-sens-recompute"
        @click="$emit('recompute')"
      />
    </div>

    <p v-if="status === 'error'" class="cost-sens-note" data-testid="cost-sens-error">
      {{ error || 'the sensitivities could not be computed' }}
    </p>
    <p
      v-else-if="result?.unavailable"
      class="cost-sens-note"
      data-testid="cost-sens-unavailable"
    >
      {{ result.unavailable }}
    </p>
    <p
      v-else-if="!rows.length"
      class="cost-sens-note"
      data-testid="cost-sens-empty"
    >
      {{ status === 'running' ? 'measuring…' : 'no parameters to measure' }}
    </p>

    <ul v-else class="cost-sens-rows" :class="{ stale: status === 'stale' }">
      <li v-for="row in rows" :key="row.name" data-testid="cost-sens-row">
        <span class="cost-sens-name" :title="row.name" v-html="renderMath(row.label)" />
        <span class="cost-sens-track">
          <span
            class="cost-sens-fill"
            :class="{ down: row.elasticity < 0 }"
            :style="{ width: `${row.width}%` }"
          />
        </span>
        <span class="cost-sens-value" data-testid="cost-sens-value">
          {{ format(row.elasticity) }}
        </span>
        <span v-if="row.reason" class="cost-sens-why" :title="row.reason">
          {{ row.reason }}
        </span>
        <span v-else class="cost-sens-why">{{ direction(row.elasticity) }}</span>
      </li>
    </ul>
    <p v-if="status === 'stale' && rows.length" class="cost-sens-note">
      measured at the previous parameters
    </p>
  </div>
</template>

<style scoped>
.cost-sens {
  padding: 0.25rem 0.75rem 0.5rem;
  font-size: 0.78rem;
}
.cost-sens-slow {
  font-size: 0.7rem;
  padding: 0.05rem 0.35rem;
  border-radius: 3px;
  cursor: help;
  color: var(--p-amber-400, #ffb74d);
  border: 1px solid var(--p-amber-400, #ffb74d);
  opacity: 0.85;
}
.cost-sens-head {
  display: flex;
  align-items: center;
  gap: 0.5rem;
}
.cost-sens-title {
  text-transform: uppercase;
  letter-spacing: 0.06em;
  opacity: 0.7;
}
.cost-sens-sub {
  opacity: 0.5;
  font-size: 0.72rem;
}
.cost-sens-note {
  margin: 0.2rem 0 0;
  opacity: 0.6;
}
.cost-sens-rows {
  list-style: none;
  margin: 0.25rem 0 0;
  padding: 0;
  display: grid;
  gap: 0.15rem;
}
.cost-sens-rows.stale {
  opacity: 0.45;
}
.cost-sens-rows li {
  display: grid;
  grid-template-columns: minmax(4rem, 10rem) minmax(3rem, 12rem) 3.5rem 1fr;
  align-items: center;
  gap: 0.5rem;
}
.cost-sens-name {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.cost-sens-track {
  height: 0.45rem;
  background: var(--p-content-border-color, #333);
  border-radius: 2px;
  overflow: hidden;
}
.cost-sens-fill {
  display: block;
  height: 100%;
  background: #e8a33d;
}
/* A parameter that *lowers* the cost as it rises is the one worth increasing;
   colour carries that so the table can be read without parsing every sign. */
.cost-sens-fill.down {
  background: #70ad47;
}
.cost-sens-value {
  font-variant-numeric: tabular-nums;
  text-align: right;
}
.cost-sens-why {
  opacity: 0.55;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
</style>
