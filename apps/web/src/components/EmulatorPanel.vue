<script setup>
import { computed, nextTick, reactive, ref, watch } from 'vue'
import Button from 'primevue/button'
import Checkbox from 'primevue/checkbox'
import InputNumber from 'primevue/inputnumber'
import InputText from 'primevue/inputtext'
import Select from 'primevue/select'

/**
 * Train a surrogate of the model's scalar observable features, then run the
 * other analyses on it (circulatory_autogen #333).
 *
 * Two steps, two controls, deliberately separate: **Train** fits an emulator
 * against the solver, and the **Use the emulator** tick box makes sensitivity,
 * calibration and UQ evaluate it instead. They are independent because training
 * once and using it across many runs is the normal shape — and because the
 * comparison the tick box enables (emulator beside model on the parameter plots)
 * is worth having without retraining.
 *
 * The settings form is built from CA's ANALYSIS_OPTIONS['emulation'] schema, so
 * a new emulator option in CA appears here without a change to this file.
 */
const props = defineProps({
  defaults: { type: Object, default: () => ({}) },
  canRun: { type: Boolean, default: false },
  lines: { type: Array, default: () => [] },
  state: { type: String, default: 'idle' },
  error: { type: String, default: '' },
  mpiexecAvailable: { type: Boolean, default: true },
  /** CA's emulator_metadata.json for the trained emulator, or null. */
  metadata: { type: Object, default: null },
  /** Per-feature {label, r2, rmse} rows derived from it. */
  features: { type: Array, default: () => [] },
  /** v-model for the "use the emulator" tick box. */
  modelValue: { type: Boolean, default: false },
})
const emit = defineEmits(['run', 'cancel', 'change', 'update:modelValue'])

// CUFLynx-level settings; every CA emulator option comes from the schema below.
const settings = reactive({
  num_cores: 1,
  dt: 0.01,
  DEBUG: false,
})

// Per-option values for CA's emulator settings, keyed by option name.
const optionValues = reactive({})

// CA's emulation option descriptors. `emulator_dir` is dropped: CUFLynx derives
// it from the outputs directory on both sides (train and use), and a second way
// to say where the bundle lives is a way for the two to disagree.
const emulatorOptions = computed(() =>
  (props.defaults.options ?? []).filter((o) => o.name !== 'emulator_dir'),
)

const supported = computed(() => props.defaults.supported !== false)

watch(
  emulatorOptions,
  (opts) => {
    for (const o of opts) {
      if (optionValues[o.name] === undefined) optionValues[o.name] = o.default
    }
  },
  { immediate: true },
)

function optionLabel(name) {
  const s = String(name).replace(/_/g, ' ')
  return s.charAt(0).toUpperCase() + s.slice(1)
}

function buildSettings() {
  const opts = {}
  for (const o of emulatorOptions.value) opts[o.name] = optionValues[o.name]
  return { ...settings, ...opts }
}

watch([settings, optionValues], () => emit('change', buildSettings()), {
  deep: true,
  immediate: true,
})

/**
 * `models` is a runtime registry (whatever autoemulate has registered), so it is
 * offered as a menu when the backend could read it and left as free text when it
 * could not — an empty menu would read as "there are none".
 */
const modelChoices = computed(() => {
  const names = props.defaults.models ?? []
  if (!names.length) return []
  return ['default', 'all', ...names]
})

const running = computed(() => props.state === 'running')
const coresInvalid = computed(
  () => !props.mpiexecAvailable && Number(settings.num_cores) > 1,
)

const worstR2 = computed(() => props.metadata?.worst_r2 ?? null)
/** The configured refusal threshold, so the panel judges by the same number CA will. */
const minR2 = computed(() => Number(optionValues.min_r2 ?? 0.9))
const belowThreshold = computed(
  () => worstR2.value != null && worstR2.value < minR2.value,
)

function fmt(value, digits = 4) {
  if (value == null || Number.isNaN(Number(value))) return '—'
  return Number(value).toFixed(digits)
}

const trainingBox = computed(() => {
  const meta = props.metadata
  if (!meta) return []
  return (meta.param_entry_labels ?? []).map((label, i) => ({
    label,
    min: meta.param_mins?.[i],
    max: meta.param_maxs?.[i],
  }))
})

const term = ref(null)
watch(
  () => props.lines.length,
  async () => {
    await nextTick()
    if (term.value) term.value.scrollTop = term.value.scrollHeight
  },
)

function onRun() {
  if (coresInvalid.value) return
  emit('run', buildSettings())
}
</script>

<template>
  <section class="calibration-panel">
    <header class="cal-header">
      <h2>Emulator</h2>
      <span class="cal-state" :data-state="state">{{ state }}</span>
    </header>

    <p v-if="!supported" class="hint" data-testid="emu-unsupported">
      This circulatory_autogen has no emulator support. Update it, or point the CA
      directory (gear icon) at a version with emulators.
    </p>

    <template v-else>
      <!-- The use switch. First, because it is what the other tabs read. -->
      <div class="use-row" :class="{ disabled: !metadata }">
        <label class="field checkbox">
          <Checkbox
            :model-value="modelValue"
            :binary="true"
            :disabled="!metadata"
            input-id="use-emulator"
            data-testid="use-emulator"
            @update:model-value="(v) => emit('update:modelValue', v)"
          />
          <span
            title="Sensitivity, calibration and UQ evaluate the trained emulator instead of the solver. The parameter sliders keep using the solver, and draw the emulator's prediction beside it."
          >
            Use the emulator for sensitivity / calibration / UQ
          </span>
        </label>
        <p v-if="!metadata" class="hint" data-testid="emu-none">
          No emulator trained for this study yet.
        </p>
        <p v-else-if="belowThreshold" class="cal-error" data-testid="emu-below-threshold">
          Worst held-out R² is {{ fmt(worstR2) }}, below the min_r2 of {{ minR2 }} —
          circulatory_autogen will refuse to use this emulator. Train it with more
          samples, or lower the threshold if you accept the error.
        </p>
      </div>

      <!-- What was trained: the numbers that decide whether to trust it. -->
      <div v-if="metadata" class="emu-summary" data-testid="emu-summary">
        <table class="emu-table">
          <thead>
            <tr><th>Feature</th><th>held-out R²</th><th>RMSE</th></tr>
          </thead>
          <tbody>
            <tr v-for="f in features" :key="f.label">
              <td class="emu-feature">{{ f.label }}</td>
              <td :class="{ bad: f.r2 != null && f.r2 < minR2 }">{{ fmt(f.r2) }}</td>
              <td>{{ fmt(f.rmse, 3) }}</td>
            </tr>
          </tbody>
        </table>
        <small class="emu-meta">
          {{ metadata.model_name ?? 'emulator' }} ·
          {{ metadata.design?.num_used ?? metadata.design?.num_train_samples ?? '?' }} samples ·
          {{ metadata.design?.sample_type ?? '?' }} design
        </small>
        <details class="emu-box">
          <summary>Valid parameter range</summary>
          <ul>
            <li v-for="b in trainingBox" :key="b.label">
              {{ b.label }}: {{ fmt(b.min, 4) }} … {{ fmt(b.max, 4) }}
            </li>
          </ul>
          <small>
            Outside this box the emulator is extrapolating with no error estimate;
            circulatory_autogen refuses such a run unless out_of_bounds says otherwise.
          </small>
        </details>
      </div>

      <div class="cal-form">
        <!-- CA's emulation options, from ANALYSIS_OPTIONS['emulation']. -->
        <template v-for="opt in emulatorOptions" :key="opt.name">
          <label v-if="opt.type === 'bool'" class="field checkbox">
            <Checkbox
              v-model="optionValues[opt.name]"
              :binary="true"
              :input-id="'emu-opt-' + opt.name"
            />
            <span :title="opt.description">{{ optionLabel(opt.name) }}</span>
          </label>
          <label v-else-if="opt.type === 'enum'" class="field">
            <span :title="opt.description">{{ optionLabel(opt.name) }}</span>
            <Select
              v-model="optionValues[opt.name]"
              :options="opt.choices"
              size="small"
              :data-testid="'emu-opt-' + opt.name"
            />
          </label>
          <!-- `models` is a runtime registry: a menu when the backend could read
               it, free text when it could not. -->
          <label v-else-if="opt.name === 'models' && modelChoices.length" class="field">
            <span :title="opt.description">{{ optionLabel(opt.name) }}</span>
            <Select
              v-model="optionValues[opt.name]"
              :options="modelChoices"
              :editable="true"
              size="small"
              :data-testid="'emu-opt-' + opt.name"
            />
          </label>
          <label v-else-if="opt.type === 'str'" class="field">
            <span :title="opt.description">{{ optionLabel(opt.name) }}</span>
            <InputText
              v-model="optionValues[opt.name]"
              size="small"
              :data-testid="'emu-opt-' + opt.name"
            />
          </label>
          <label v-else class="field">
            <span :title="opt.description">{{ optionLabel(opt.name) }}</span>
            <InputNumber
              v-model="optionValues[opt.name]"
              :min-fraction-digits="opt.type === 'float' ? 1 : undefined"
              :max-fraction-digits="opt.type === 'float' ? 10 : undefined"
              size="small"
              :data-testid="'emu-opt-' + opt.name"
            />
          </label>
        </template>

        <label class="field">
          <span title="mpiexec -n N: the training simulations are split across ranks">Cores</span>
          <InputNumber
            v-model="settings.num_cores"
            :min="1"
            :max="64"
            size="small"
            :invalid="coresInvalid"
            data-testid="emu-cores"
          />
          <small v-if="coresInvalid" class="cores-invalid" data-testid="emu-cores-invalid">
            Cores &gt; 1 not available (no MPI launcher). Set to 1, or pick a Python
            interpreter marked MPI ✓ in the top bar.
          </small>
        </label>

        <label class="field checkbox">
          <Checkbox v-model="settings.DEBUG" :binary="true" input-id="emu-debug" />
          <span>DEBUG (more output info)</span>
        </label>
      </div>

      <div class="cal-actions">
        <Button
          :label="metadata ? 'Retrain emulator' : 'Train emulator'"
          icon="pi pi-play"
          size="small"
          data-testid="train-emulator"
          :disabled="!canRun || running || coresInvalid"
          @click="onRun"
        />
        <Button
          v-if="running"
          label="Cancel"
          icon="pi pi-times"
          severity="danger"
          size="small"
          text
          @click="emit('cancel')"
        />
      </div>
      <p v-if="!canRun" class="hint">
        Load a model, an obs_data.json and a params_for_id.csv to train an emulator.
      </p>
      <p class="hint">
        Training runs the solver once per sample and is paid up front — worth it for
        Sobol, UQ and repeated calibrations, not for a single run.
      </p>
      <p v-if="error" class="cal-error">{{ error }}</p>

      <pre ref="term" class="terminal" data-testid="emu-terminal">{{ lines.join('\n') }}</pre>
    </template>
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
.cal-state {
  font-size: 0.7rem;
  text-transform: uppercase;
  opacity: 0.7;
}
.cal-form {
  display: flex;
  flex-direction: column;
  gap: 0.35rem;
}
.field {
  display: flex;
  flex-direction: column;
  gap: 0.15rem;
  font-size: 0.8rem;
}
.field.checkbox {
  flex-direction: row;
  align-items: center;
  gap: 0.4rem;
}
.use-row {
  border: 1px solid var(--p-content-border-color, #ddd);
  border-radius: 4px;
  padding: 0.4rem 0.5rem;
}
.use-row.disabled {
  opacity: 0.7;
}
.emu-summary {
  font-size: 0.75rem;
}
.emu-table {
  width: 100%;
  border-collapse: collapse;
}
.emu-table th,
.emu-table td {
  text-align: left;
  padding: 0.1rem 0.25rem;
  border-bottom: 1px solid var(--p-content-border-color, #eee);
}
.emu-table td.bad {
  color: var(--p-red-500, #d33);
  font-weight: 600;
}
.emu-feature {
  max-width: 12rem;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.emu-meta {
  opacity: 0.75;
}
.emu-box ul {
  margin: 0.2rem 0;
  padding-left: 1rem;
}
.cal-actions {
  display: flex;
  gap: 0.4rem;
}
.hint,
.cores-invalid {
  font-size: 0.72rem;
  opacity: 0.8;
}
.cal-error {
  font-size: 0.75rem;
  color: var(--p-red-500, #d33);
}
.terminal {
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 0.7rem;
  background: var(--p-content-background, #111);
  color: var(--p-text-color, #ddd);
  padding: 0.4rem;
  border-radius: 4px;
  max-height: 14rem;
  overflow: auto;
  white-space: pre-wrap;
}
</style>
