<script setup>
/**
 * A parameter reduction's results, in the Analysis tab: the parameters kept (and "Calibrate
 * these", which makes the reduced params_for_id the study's), the PCA modes, the effect of
 * those not kept, what each mode is made of (a tab per mode: its feature loadings and the
 * parameters' total Sobol index for it), and the heatmap. The Identifiability tab runs it; this
 * shows what it found. Each part folds from its heading (v-fold).
 */
import { computed, ref, watch } from 'vue'
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
// CA's pca_modes.csv / pca_mode_sobol_indices.csv, per mode (ca_run_history.pca_mode_composition)
const composition = computed(() => props.reduction?.mode_composition ?? [])
const modeInfo = computed(() => Object.fromEntries((modes.value ?? []).map((m) => [m.mode, m])))
const activeMode = ref(null)
watch(
  composition,
  (list) => {
    if (!list.some((m) => m.mode === activeMode.value)) activeMode.value = list[0]?.mode ?? null
  },
  { immediate: true },
)
const active = computed(() => composition.value.find((m) => m.mode === activeMode.value) ?? null)
// Loadings are signed: each bar runs from the centre line, scaled to the largest |loading|.
const loadingBars = computed(() => {
  const rows = (active.value?.features ?? []).filter((f) => typeof f.loading === 'number')
  const top = Math.max(0, ...rows.map((f) => Math.abs(f.loading))) || 1
  return rows
    .slice()
    .sort((a, b) => Math.abs(b.loading) - Math.abs(a.loading))
    .map((f) => ({ ...f, width: (50 * Math.abs(f.loading)) / top, negative: f.loading < 0 }))
})
const stBars = computed(() =>
  (active.value?.parameters ?? [])
    .filter((p) => typeof p.ST === 'number')
    .slice()
    .sort((a, b) => b.ST - a.ST)
    .map((p) => ({ ...p, width: 100 * Math.min(Math.max(p.ST, 0), 1) })),
)
function modeLabel(m) {
  const info = modeInfo.value[m.mode]
  return info ? `Mode ${m.mode} (${pct(info.variance_share)})` : `Mode ${m.mode}`
}
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
    <section v-if="composition.length" v-fold data-testid="pr-composition">
      <h3 title="Each mode's loading on every feature (CA's pca_modes.csv), and the parameters' total Sobol index for that mode">
        What makes up each mode
      </h3>
      <div class="mode-tabs" role="tablist">
        <button v-for="m in composition" :key="m.mode" type="button" role="tab"
                class="mode-tab"
                :class="{ on: m.mode === activeMode, dim: modeInfo[m.mode]?.informative === false }"
                :aria-selected="m.mode === activeMode" :data-testid="`pr-mode-tab-${m.mode}`"
                @click="activeMode = m.mode">
          {{ modeLabel(m) }}
        </button>
      </div>
      <div v-if="active" role="tabpanel" class="mode-panel">
        <h4>Feature loadings</h4>
        <div class="bars signed" data-testid="pr-mode-loadings">
          <template v-for="f in loadingBars" :key="f.label">
            <span class="bar-label" :title="f.label">{{ f.label }}</span>
            <span class="bar-track">
              <span class="bar" :class="{ neg: f.negative }"
                    :style="f.negative ? { right: '50%', width: `${f.width}%` } : { left: '50%', width: `${f.width}%` }" />
            </span>
            <span class="bar-value">{{ fmt(f.loading) }}</span>
          </template>
        </div>
        <template v-if="stBars.length">
          <h4>Total Sobol index (ST) of each parameter for this mode</h4>
          <div class="bars" data-testid="pr-mode-st">
            <template v-for="p in stBars" :key="p.parameter">
              <span class="bar-label" :title="p.parameter">{{ p.parameter }}</span>
              <span class="bar-track">
                <span class="bar st" :style="{ left: 0, width: `${p.width}%` }" />
              </span>
              <span class="bar-value">{{ fmt(p.ST) }}</span>
            </template>
          </div>
        </template>
      </div>
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
.mode-tabs { display: flex; flex-wrap: wrap; gap: 0.25rem; margin-bottom: 0.4rem; }
.mode-tab { font-size: 0.78rem; padding: 0.15rem 0.5rem; border: 1px solid var(--p-content-border-color, #444); border-radius: 4px; background: transparent; color: inherit; cursor: pointer; }
.mode-tab.on { background: var(--p-primary-color, #3b82f6); color: var(--p-primary-contrast-color, #fff); border-color: transparent; }
.mode-tab.dim:not(.on) { opacity: 0.6; }
.mode-panel h4 { font-size: 0.8rem; margin: 0.3rem 0; font-weight: 600; }
.bars { display: grid; grid-template-columns: minmax(8rem, max-content) minmax(8rem, 1fr) 4rem; gap: 0.15rem 0.5rem; align-items: center; font-size: 0.78rem; max-width: 46rem; }
.bar-label { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; max-width: 20rem; }
.bar-track { position: relative; height: 0.8rem; background: color-mix(in srgb, currentColor 6%, transparent); }
.bars.signed .bar-track::after { content: ''; position: absolute; left: 50%; top: 0; bottom: 0; border-left: 1px solid color-mix(in srgb, currentColor 40%, transparent); }
.bar { position: absolute; top: 0; bottom: 0; background: var(--p-primary-color, #3b82f6); }
.bar.neg { background: #e8833a; }
.bar.st { background: #6b8fd6; }
.bar-value { text-align: right; font-variant-numeric: tabular-nums; }
.fold-head { cursor: pointer; user-select: none; }
.fold-head::before { content: '▾ '; opacity: 0.6; }
[data-folded] > .fold-head::before { content: '▸ '; }
[data-folded] > :not(.fold-head) { display: none !important; }
</style>
