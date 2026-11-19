<script setup>
/**
 * A parameter reduction's results, in the Analysis tab: the parameters kept (and "Calibrate
 * these", which makes the reduced params_for_id the study's), the PCA modes, the effect of
 * those not kept, and the heatmap. The Identifiability tab runs it; this shows what it found.
 * Each part folds from its heading (v-fold).
 */
import { computed } from 'vue'
import Button from 'primevue/button'
import { paramReductionHeatmapUrl } from '../lib/api'
import { fmtSigFigs } from '../lib/format'
import { vFold } from '../lib/vFold'

const props = defineProps({
  // the reduction's result (GET /api/param_reduction/... status), or null
  reduction: { type: Object, default: null },
})
const emit = defineEmits(['adopt'])

const kept = computed(() => props.reduction?.selected ?? [])
const modes = computed(() => props.reduction?.modes ?? null)
const unexplained = computed(() =>
  Object.entries(props.reduction?.unexplained_effects ?? {}).map(([parameter, effect]) => ({
    parameter,
    effect,
  })),
)
const heatmapUrl = computed(() =>
  props.reduction?.heatmap ? paramReductionHeatmapUrl(props.reduction.report_dir) : null,
)
const canAdopt = computed(() => !!props.reduction?.reduced_params_for_id)

function fmt(v, digits = 3) {
  return typeof v === 'number' && Number.isFinite(v) ? fmtSigFigs(v, digits) : '—'
}
function pct(v) {
  return typeof v === 'number' ? `${(100 * v).toFixed(1)}%` : '—'
}
</script>

<template>
  <div v-if="reduction" class="results" data-testid="pr-results">
    <section v-fold>
      <h3>Kept ({{ reduction.reduction?.method }})</h3>
      <table class="tbl" data-testid="pr-kept">
        <thead><tr><th>Parameter</th><th>Score</th><th>Mode</th></tr></thead>
        <tbody>
          <tr v-for="row in kept" :key="row.parameter">
            <td>{{ row.parameter }}</td>
            <td>{{ fmt(row.score) }}</td>
            <td>{{ row.mode ?? '—' }}</td>
          </tr>
        </tbody>
      </table>
      <Button label="Calibrate these" icon="pi pi-arrow-right" size="small"
              data-testid="pr-adopt" :disabled="!canAdopt"
              @click="emit('adopt', reduction.report_dir)" />
    </section>
    <section v-if="modes" v-fold>
      <h3 title="Each mode's spread over the Sobol samples, in obs_data noise stds">
        PCA modes ({{ reduction.n_informative_modes ?? '?' }} informative)
      </h3>
      <table class="tbl" data-testid="pr-modes">
        <thead>
          <tr><th>Mode</th><th>Std (noise stds)</th><th>Share</th><th>Informative</th></tr>
        </thead>
        <tbody>
          <tr v-for="m in modes" :key="m.mode">
            <td>{{ m.mode }}</td>
            <td>{{ fmt(m.std) }}</td>
            <td>{{ pct(m.variance_share) }}</td>
            <td>{{ m.informative ? 'yes' : 'no' }}</td>
          </tr>
        </tbody>
      </table>
    </section>
    <section v-if="unexplained.length" v-fold>
      <h3 title="What each parameter not kept moves the data by, once the kept ones are accounted for">
        Not kept: unexplained effect
      </h3>
      <table class="tbl" data-testid="pr-unexplained">
        <thead><tr><th>Parameter</th><th>Effect (noise stds)</th></tr></thead>
        <tbody>
          <tr v-for="row in unexplained" :key="row.parameter">
            <td>{{ row.parameter }}</td>
            <td>{{ fmt(row.effect) }}</td>
          </tr>
        </tbody>
      </table>
    </section>
    <section v-if="heatmapUrl" v-fold>
      <h3>Total Sobol index per PCA mode</h3>
      <img :src="heatmapUrl" alt="ST per PCA mode" class="heatmap" data-testid="pr-heatmap" />
    </section>
  </div>
</template>

<style scoped>
.results { display: flex; flex-direction: column; gap: 0.6rem; }
.results h3 { font-size: 0.9rem; margin: 0.2rem 0; }
.tbl { border-collapse: collapse; font-size: 0.8rem; margin-bottom: 0.4rem; }
.tbl th, .tbl td { padding: 0.15rem 0.5rem; border-bottom: 1px solid var(--p-content-border-color, #444); text-align: left; }
.heatmap { max-width: 100%; }
.fold-head { cursor: pointer; user-select: none; }
.fold-head::before { content: '▾ '; opacity: 0.6; }
[data-folded] > .fold-head::before { content: '▸ '; }
[data-folded] > :not(.fold-head) { display: none !important; }
</style>
