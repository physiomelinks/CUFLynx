<script setup>
import { ref, reactive, computed, watch } from 'vue'
import Select from 'primevue/select'
import InputNumber from 'primevue/inputnumber'
import InputText from 'primevue/inputtext'
import Checkbox from 'primevue/checkbox'
import RadioButton from 'primevue/radiobutton'
import Button from 'primevue/button'
import { paramReductionHeatmapUrl } from '../lib/api'
import { fmtSigFigs } from '../lib/format'

/**
 * The Identifiability tab: which parameters the data can fix.
 *
 * Two sections, both built from circulatory_autogen's descriptors and never from
 * names written here:
 *
 * - **Parameter reduction** (`ANALYSIS_OPTIONS['param_reduction']`): reads a Sobol
 *   run -- an existing one, or one run first -- and keeps the parameters to
 *   calibrate. Each option the backend marked with `methods` is shown only for
 *   those methods, since CA refuses another method's option set to anything but
 *   its default. "Calibrate these" makes the reduced params_for_id the study's.
 * - **Identifiability analysis** (`ANALYSIS_OPTIONS['identifiability_analysis']`):
 *   CA's Laplace approximation or profile likelihood about a best fit, through the
 *   UQ runner's laplace method.
 */
const props = defineProps({
  // GET /api/identifiability/defaults: { param_reduction: {options}, ia_options }
  defaults: { type: Object, default: () => ({}) },
  sobolRuns: { type: Array, default: () => [] },
  canRun: { type: Boolean, default: false },
  // Parameter reduction job
  reductionState: { type: String, default: 'idle' },
  reductionLines: { type: Array, default: () => [] },
  reductionError: { type: String, default: '' },
  reduction: { type: Object, default: null },
  // Identifiability analysis job
  iaState: { type: String, default: 'idle' },
  iaLines: { type: Array, default: () => [] },
  iaError: { type: String, default: '' },
  iaParams: { type: Array, default: () => [] },
  iaProfiles: { type: Array, default: null },
})
const emit = defineEmits([
  'run-reduction',
  'cancel-reduction',
  'adopt',
  'refresh-runs',
  'run-ia',
  'cancel-ia',
])

// ---- shared option-form helpers ---------------------------------------------
function optionLabel(name) {
  const s = String(name).replace(/_/g, ' ')
  return s.charAt(0).toUpperCase() + s.slice(1)
}

/** Seed each descriptor's default, keeping any value the user already chose. */
function seed(values, descriptors) {
  for (const o of descriptors) {
    if (values[o.name] === undefined) values[o.name] = o.default ?? null
  }
}

/** Only what has a value: CA fills in the rest from its own defaults. */
function collect(values, descriptors) {
  const out = {}
  for (const o of descriptors) {
    const v = values[o.name]
    if (v !== null && v !== undefined && v !== '') out[o.name] = v
  }
  return out
}

// Forms render bool, enum, str, int and float options; a dict option (CA's
// profile-likelihood optimiser_options) has no field and is left to CA's default.
const renderable = (o) => ['bool', 'enum', 'str', 'int', 'float'].includes(o.type)

// ---- parameter reduction -----------------------------------------------------
const reductionDescriptors = computed(() => props.defaults.param_reduction?.options ?? [])
const reductionAvailable = computed(() => reductionDescriptors.value.length > 0)
const methodDescriptor = computed(() =>
  reductionDescriptors.value.find((o) => o.name === 'method'),
)
const reductionValues = reactive({})
watch(reductionDescriptors, (d) => seed(reductionValues, d), { immediate: true })

const source = ref('existing') // 'existing' | 'sobol_first'
const SOBOL_RUN_OPTION = 'sensitivity_dir'

/** The options the chosen method reads, plus the ones every method reads. */
const reductionOptions = computed(() =>
  reductionDescriptors.value
    .filter((o) => o !== methodDescriptor.value && renderable(o))
    .filter((o) => !o.methods || o.methods.includes(reductionValues.method))
    // Run-first reads the Sobol run it has just made.
    .filter((o) => !(source.value === 'sobol_first' && o.name === SOBOL_RUN_OPTION)),
)

const sobolRunChoices = computed(() =>
  props.sobolRuns.map((r) => ({
    value: r.dir,
    label: `${r.dir} (n=${r.num_samples ?? '?'}${r.has_samples ? '' : ', indices only'})`,
  })),
)

const reductionRunning = computed(() => props.reductionState === 'running')
const canReduce = computed(
  () =>
    props.canRun &&
    reductionAvailable.value &&
    !reductionRunning.value &&
    (source.value === 'sobol_first' || !!reductionValues[SOBOL_RUN_OPTION]),
)

function onRunReduction() {
  if (!canReduce.value) return
  const options = {
    ...(methodDescriptor.value ? { method: reductionValues.method } : {}),
    ...collect(reductionValues, reductionOptions.value),
  }
  emit('run-reduction', { source: source.value, param_reduction_options: options })
}

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

// ---- identifiability analysis ------------------------------------------------
const iaDescriptors = computed(() => (props.defaults.ia_options ?? []).filter(renderable))
const iaValues = reactive({})
watch(iaDescriptors, (d) => seed(iaValues, d), { immediate: true })

// When each IA option is read, as {option: [values]}. A descriptor's own `when`
// or `methods` wins; CA's ia_options state these only in their descriptions, so
// this table restates them for the options CA marks with neither.
const IA_APPLIES = {
  gradient_source: { method: ['Laplace'] },
  sub_method: { method: ['Laplace'], gradient_source: ['FD'] },
  num_points: { method: ['profile_likelihood'] },
  range_factor: { method: ['profile_likelihood'] },
  optimiser_options: { method: ['profile_likelihood'] },
}
function applies(o, values) {
  const when = o.when ?? (o.methods ? { method: o.methods } : IA_APPLIES[o.name])
  if (!when) return true
  return Object.entries(when).every(
    ([key, allowed]) => values[key] == null || allowed.includes(values[key]),
  )
}
/** The IA options the current choices read: shown, and the only ones sent. */
const iaShown = computed(() => iaDescriptors.value.filter((o) => applies(o, iaValues)))
const runCalibrationFirst = ref(false)
const iaRunning = computed(() => props.iaState === 'running')

function onRunIA() {
  if (!props.canRun || iaRunning.value) return
  emit('run-ia', {
    // The UQ runner's method that runs CA's IdentifiabilityAnalysis.
    method: 'laplace',
    run_calibration_first: runCalibrationFirst.value,
    ia_options: collect(iaValues, iaShown.value),
  })
}

function ciBound(profile, side) {
  const v = profile.ci_95?.[side]
  return v === null || v === undefined ? 'unbounded' : fmt(v)
}
</script>

<template>
  <section class="calibration-panel">
    <header class="cal-header">
      <h2>Identifiability</h2>
      <span class="cal-state" :data-state="reductionState">{{ reductionState }}</span>
    </header>

    <!-- ---------------- Parameter reduction ---------------- -->
    <h3 class="section-title">Parameter reduction</h3>
    <p v-if="!reductionAvailable" class="hint" data-testid="pr-unavailable">
      This circulatory_autogen has no parameter reduction.
    </p>
    <div v-else class="cal-form" data-testid="pr-settings">
      <div class="field checkbox">
        <RadioButton v-model="source" value="existing" input-id="pr-source-existing"
                     data-testid="pr-source-existing" />
        <label for="pr-source-existing">Reduce an existing Sobol run</label>
      </div>
      <div class="field checkbox">
        <RadioButton v-model="source" value="sobol_first" input-id="pr-source-sobol"
                     data-testid="pr-source-sobol" />
        <label for="pr-source-sobol"
               title="Runs a Sobol analysis with the Sensitivity tab's settings, then reduces it">
          Run a Sobol analysis first
        </label>
      </div>
      <label v-if="source === 'existing'" class="field">
        <span>Sobol run</span>
        <span class="field-input">
          <Select
            v-model="reductionValues[SOBOL_RUN_OPTION]"
            :options="sobolRunChoices"
            option-label="label"
            option-value="value"
            placeholder="choose a run"
            size="small"
            data-testid="pr-sobol-run"
          />
          <Button icon="pi pi-refresh" size="small" text title="Look for Sobol runs again"
                  data-testid="pr-refresh-runs" @click="emit('refresh-runs')" />
        </span>
      </label>
      <label v-if="methodDescriptor" class="field">
        <span :title="methodDescriptor.description">Method</span>
        <Select
          v-model="reductionValues.method"
          :options="methodDescriptor.choices ?? []"
          size="small"
          data-testid="pr-opt-method"
        />
      </label>
      <template v-for="opt in reductionOptions" :key="opt.name">
        <label v-if="opt.type === 'bool'" class="field checkbox">
          <Checkbox v-model="reductionValues[opt.name]" :binary="true"
                    :input-id="'pr-opt-' + opt.name" :data-testid="'pr-opt-' + opt.name" />
          <span :title="opt.description">{{ optionLabel(opt.name) }}</span>
        </label>
        <label v-else-if="opt.type === 'enum'" class="field">
          <span :title="opt.description">{{ optionLabel(opt.name) }}</span>
          <Select v-model="reductionValues[opt.name]" :options="opt.choices ?? []" size="small"
                  :data-testid="'pr-opt-' + opt.name" />
        </label>
        <label v-else-if="opt.type === 'str'" class="field">
          <span :title="opt.description">{{ optionLabel(opt.name) }}</span>
          <InputText v-model="reductionValues[opt.name]" size="small"
                     :data-testid="'pr-opt-' + opt.name" />
        </label>
        <label v-else class="field">
          <span :title="opt.description">{{ optionLabel(opt.name) }}</span>
          <InputNumber
            v-model="reductionValues[opt.name]"
            :min-fraction-digits="opt.type === 'float' ? 1 : undefined"
            :max-fraction-digits="opt.type === 'float' ? 10 : undefined"
            size="small"
            :data-testid="'pr-opt-' + opt.name"
          />
        </label>
      </template>
      <div class="cal-actions">
        <Button label="Reduce" icon="pi pi-play" size="small" data-testid="pr-run"
                :disabled="!canReduce" @click="onRunReduction" />
        <Button v-if="reductionRunning" label="Cancel" icon="pi pi-times" severity="danger"
                size="small" text @click="emit('cancel-reduction')" />
      </div>
      <p v-if="!canRun" class="hint">
        Load a model, an obs_data.json and a params_for_id.json first.
      </p>
      <p v-if="reductionError" class="cal-error" data-testid="pr-error">{{ reductionError }}</p>
      <pre v-if="reductionLines.length" class="terminal" data-testid="pr-terminal">{{
        reductionLines.join('\n') }}</pre>
    </div>

    <div v-if="reduction" class="results" data-testid="pr-results">
      <h4>Kept ({{ reduction.reduction?.method }})</h4>
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

      <template v-if="modes">
        <h4 title="Each mode's spread over the Sobol samples, in obs_data noise stds">
          PCA modes ({{ reduction.n_informative_modes ?? '?' }} informative)
        </h4>
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
      </template>
      <template v-if="unexplained.length">
        <h4 title="What each parameter not kept moves the data by, once the kept ones are accounted for">
          Not kept: unexplained effect
        </h4>
        <table class="tbl" data-testid="pr-unexplained">
          <thead><tr><th>Parameter</th><th>Effect (noise stds)</th></tr></thead>
          <tbody>
            <tr v-for="row in unexplained" :key="row.parameter">
              <td>{{ row.parameter }}</td>
              <td>{{ fmt(row.effect) }}</td>
            </tr>
          </tbody>
        </table>
      </template>
      <img v-if="heatmapUrl" :src="heatmapUrl" alt="ST per PCA mode" class="heatmap"
           data-testid="pr-heatmap" />
    </div>

    <!-- ---------------- Identifiability analysis ---------------- -->
    <h3 class="section-title">Identifiability analysis</h3>
    <div class="cal-form" data-testid="ia-settings">
      <template v-for="opt in iaShown" :key="opt.name">
        <label v-if="opt.type === 'bool'" class="field checkbox">
          <Checkbox v-model="iaValues[opt.name]" :binary="true"
                    :input-id="'ia-opt-' + opt.name" :data-testid="'ia-opt-' + opt.name" />
          <span :title="opt.description">{{ optionLabel(opt.name) }}</span>
        </label>
        <label v-else-if="opt.type === 'enum'" class="field">
          <span :title="opt.description">{{ optionLabel(opt.name) }}</span>
          <Select v-model="iaValues[opt.name]" :options="opt.choices ?? []" size="small"
                  :data-testid="'ia-opt-' + opt.name" />
        </label>
        <label v-else-if="opt.type === 'str'" class="field">
          <span :title="opt.description">{{ optionLabel(opt.name) }}</span>
          <InputText v-model="iaValues[opt.name]" size="small"
                     :data-testid="'ia-opt-' + opt.name" />
        </label>
        <label v-else class="field">
          <span :title="opt.description">{{ optionLabel(opt.name) }}</span>
          <InputNumber
            v-model="iaValues[opt.name]"
            :min-fraction-digits="opt.type === 'float' ? 1 : undefined"
            :max-fraction-digits="opt.type === 'float' ? 10 : undefined"
            size="small"
            :data-testid="'ia-opt-' + opt.name"
          />
        </label>
      </template>
      <label class="field checkbox">
        <Checkbox v-model="runCalibrationFirst" :binary="true" input-id="ia-fresh-calib" />
        <span title="Otherwise the analysis is about the latest completed calibration's best fit">
          Run a fresh calibration first
        </span>
      </label>
      <div class="cal-actions">
        <Button label="Run" icon="pi pi-play" size="small" data-testid="ia-run"
                :disabled="!canRun || iaRunning" @click="onRunIA" />
        <Button v-if="iaRunning" label="Cancel" icon="pi pi-times" severity="danger"
                size="small" text @click="emit('cancel-ia')" />
        <span class="cal-state" :data-state="iaState">{{ iaState }}</span>
      </div>
      <p v-if="iaError" class="cal-error" data-testid="ia-error">{{ iaError }}</p>
      <pre v-if="iaLines.length" class="terminal" data-testid="ia-terminal">{{
        iaLines.join('\n') }}</pre>
    </div>

    <div v-if="iaProfiles" class="results" data-testid="ia-profiles">
      <h4>Profile likelihood: 95% intervals</h4>
      <table class="tbl">
        <thead><tr><th>Parameter</th><th>Best</th><th>Lower</th><th>Upper</th></tr></thead>
        <tbody>
          <tr v-for="p in iaProfiles" :key="p.param_name">
            <td>{{ p.param_name }}</td>
            <td>{{ fmt(p.best_val) }}</td>
            <td>{{ ciBound(p, 'lower') }}</td>
            <td>{{ ciBound(p, 'upper') }}</td>
          </tr>
        </tbody>
      </table>
    </div>
    <div v-else-if="iaParams.length" class="results" data-testid="ia-laplace">
      <h4 title="Gaussian approximation about the best fit">Laplace: posterior</h4>
      <table class="tbl">
        <thead>
          <tr><th>Parameter</th><th>Mean</th><th>Std</th><th>Std / |mean|</th></tr>
        </thead>
        <tbody>
          <tr v-for="p in iaParams" :key="p.qname">
            <td>{{ p.qname }}</td>
            <td>{{ fmt(p.mean) }}</td>
            <td>{{ fmt(p.std) }}</td>
            <td>{{ p.mean ? fmt(p.std / Math.abs(p.mean)) : '—' }}</td>
          </tr>
        </tbody>
      </table>
    </div>
  </section>
</template>

<style scoped>
.calibration-panel {
  display: flex;
  flex-direction: column;
  gap: 0.4rem;
  padding: 0.6rem 0.75rem;
}
.cal-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
}
.section-title {
  margin: 0.4rem 0 0;
  font-size: 0.9rem;
}
.results h4 {
  margin: 0.4rem 0 0.2rem;
  font-size: 0.8rem;
}
.cal-state {
  font-size: 0.7rem;
  text-transform: uppercase;
  opacity: 0.7;
}
.cal-state[data-state='running'] {
  color: #ffc000;
}
.cal-state[data-state='done'] {
  color: #70ad47;
}
.cal-state[data-state='error'] {
  color: #e84a5f;
}
.cal-form {
  display: flex;
  flex-direction: column;
  gap: 0.3rem;
}
.field {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 0.5rem;
  font-size: 0.8rem;
}
.field.checkbox {
  justify-content: flex-start;
}
.field-input {
  display: flex;
  align-items: center;
  gap: 0.25rem;
  min-width: 0;
}
.cal-actions {
  display: flex;
  align-items: center;
  gap: 0.5rem;
}
.hint,
.cal-error {
  font-size: 0.75rem;
  opacity: 0.7;
  margin: 0;
}
.cal-error {
  color: #e84a5f;
  opacity: 1;
}
.tbl {
  width: 100%;
  border-collapse: collapse;
  font-size: 0.75rem;
}
.tbl th,
.tbl td {
  text-align: left;
  padding: 0.1rem 0.3rem;
  border-bottom: 1px solid rgba(128, 128, 128, 0.25);
}
.heatmap {
  max-width: 100%;
  margin-top: 0.4rem;
}
.terminal {
  margin: 0.25rem 0 0;
  background: #0c0c0c;
  color: #cfcfcf;
  font-family: ui-monospace, monospace;
  font-size: 0.72rem;
  line-height: 1.25;
  padding: 0.4rem 0.5rem;
  border-radius: 4px;
  height: 140px;
  overflow-y: auto;
  white-space: pre-wrap;
}
</style>
