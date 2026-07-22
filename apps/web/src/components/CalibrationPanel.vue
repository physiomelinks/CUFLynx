<script setup>
import { ref, reactive, watch, nextTick, computed } from 'vue'
import Select from 'primevue/select'
import InputNumber from 'primevue/inputnumber'
import InputText from 'primevue/inputtext'
import Checkbox from 'primevue/checkbox'
import Button from 'primevue/button'

const props = defineProps({
  defaults: { type: Object, default: () => ({}) },
  canRun: { type: Boolean, default: false },
  lines: { type: Array, default: () => [] },
  state: { type: String, default: 'idle' },
  cost: { type: Number, default: null },
  error: { type: String, default: '' },
  adAvailable: { type: Boolean, default: false },
  // Gradient sources available for the current model (FD / AD / FSA), from the
  // backend's /api/config -> gradient_sources.
  gradientSources: { type: Array, default: () => [] },
  // False when no MPI launcher is available: cores>1 would silently run on one
  // core, so mark the Cores field invalid and block the run until it's set to 1.
  mpiexecAvailable: { type: Boolean, default: true },
})
const emit = defineEmits(['run', 'cancel', 'change'])

// Note: pre_time / sim_time are intentionally NOT here — calibration timing
// comes from the obs_data.json protocol_info (see #13). The Python interpreter
// is chosen once in the top bar (shared across calibration/sensitivity/UQ).
// CUFLynx-level settings that apply to every method (the per-method settings come
// from CA's schema — see optionValues below). num_cores drives mpiexec parallelism;
// gradient_method is the gradient source for gradient-based methods.
const settings = reactive({
  param_id_method: 'genetic_algorithm',
  gradient_method: 'FD',
  num_cores: 1,
  dt: 0.01,
  DEBUG: false,
})

// Per-method setting values, keyed by option name (from CA's schema). Seeded from
// each option's default; the run/change payload includes only the current method's.
const optionValues = reactive({})

// Seed the CUFLynx-level settings from server defaults once they arrive.
watch(
  () => props.defaults,
  (d) => {
    if (!d) return
    for (const k of Object.keys(settings)) {
      if (d[k] != null) settings[k] = d[k]
    }
  },
  { immediate: true },
)

// Methods come from CA's PARAM_ID_METHODS schema (GET /api/calibration/defaults),
// each { value, label, gradient_based, options } — never hardcoded, so new CA
// methods and their settings surface automatically. Tolerate the old string-array
// shape (older backend / built-in fallback).
const GRADIENT_FALLBACK = ['sp_minimize', 'multi_start_sp_minimize']
const methods = computed(() => {
  const raw = props.defaults.methods ?? ['genetic_algorithm', 'CMA-ES']
  return raw.map((m) =>
    typeof m === 'string'
      ? { value: m, label: m, gradient_based: GRADIENT_FALLBACK.includes(m), options: [] }
      : {
          value: m.value,
          label: m.label ?? m.value,
          gradient_based: !!m.gradient_based,
          options: m.options ?? [],
        },
  )
})

const selectedMethod = computed(() =>
  methods.value.find((m) => m.value === settings.param_id_method),
)
// The settings CA says this method actually consumes — so gradient-descent methods
// don't show max_patience, etc.
const methodOptions = computed(() => selectedMethod.value?.options ?? [])
const isGradientMethod = computed(() => selectedMethod.value?.gradient_based ?? false)

// Seed each option's default when the selected method's options change, keeping any
// value the user already set for a like-named option.
watch(
  methodOptions,
  (opts) => {
    for (const o of opts) {
      if (optionValues[o.name] === undefined) optionValues[o.name] = o.default
    }
  },
  { immediate: true },
)

// Gradient sources (FD / AD / FSA) come from the backend, derived from the current
// model (GET /api/config -> gradient_sources); never hardcoded, so FSA shows for
// cellml_only + CVODE_myokit.
//
// Sources CA flags `requires_all_differentiable` (CasADi AD) only apply when every
// operation the loaded obs_data uses is @differentiable. That's a per-model
// property the model-agnostic /api/config can't evaluate, so it's gated here
// against `adAvailable` (App.vue's in-use differentiability check).
const gradientOptions = computed(() => {
  const base = props.gradientSources?.length
    ? props.gradientSources
    : [{ value: 'FD', label: 'Finite difference' }]
  return base.filter((o) => !o.requires_all_differentiable || props.adAvailable)
})

// If the current gradient source isn't offered for this model, fall back to FD;
// the gradient method itself still runs (CA uses finite differences).
watch(
  gradientOptions,
  (opts) => {
    if (!opts.some((o) => o.value === settings.gradient_method)) settings.gradient_method = 'FD'
  },
  { immediate: true },
)

// A short field label from an option name (num_calls_to_function -> "Num calls to function").
function optionLabel(name) {
  const s = String(name).replace(/_/g, ' ')
  return s.charAt(0).toUpperCase() + s.slice(1)
}

// The run/change payload: CUFLynx-level settings + the selected method's option
// values (only those), plus the gradient source for gradient methods.
function buildSettings() {
  const opts = {}
  for (const o of methodOptions.value) opts[o.name] = optionValues[o.name]
  return {
    param_id_method: settings.param_id_method,
    num_cores: settings.num_cores,
    dt: settings.dt,
    DEBUG: settings.DEBUG,
    ...(isGradientMethod.value ? { gradient_method: settings.gradient_method } : {}),
    ...opts,
  }
}

// Surface live settings upward so other panels (e.g. sensitivity "run calibration
// first") reuse the user's calibration configuration.
watch([settings, optionValues], () => emit('change', buildSettings()), {
  deep: true,
  immediate: true,
})

const running = computed(() => props.state === 'running')

// cores>1 only runs with an MPI launcher available; otherwise invalid + blocked.
const coresInvalid = computed(
  () => !props.mpiexecAvailable && Number(settings.num_cores) > 1,
)

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
      <h2>Calibration</h2>
      <span class="cal-state" :data-state="state">{{ state }}</span>
    </header>

    <div class="cal-form">
      <label class="field">
        <span>Method</span>
        <Select
          v-model="settings.param_id_method"
          :options="methods"
          option-label="label"
          option-value="value"
          size="small"
          data-testid="calib-method"
        />
      </label>
      <label v-if="isGradientMethod" class="field">
        <span title="Gradient source: finite difference, or automatic differentiation / forward sensitivity where the model supports it">Gradient</span>
        <Select
          v-model="settings.gradient_method"
          :options="gradientOptions"
          option-label="label"
          option-value="value"
          size="small"
          data-testid="calib-gradient-method"
        />
      </label>

      <!-- Per-method settings, from CA's PARAM_ID_METHODS[method].options schema. -->
      <template v-for="opt in methodOptions" :key="opt.name">
        <label v-if="opt.type === 'bool'" class="field checkbox">
          <Checkbox v-model="optionValues[opt.name]" :binary="true" :input-id="'opt-' + opt.name" />
          <span :title="opt.description">{{ optionLabel(opt.name) }}</span>
        </label>
        <label v-else-if="opt.type === 'enum'" class="field">
          <span :title="opt.description">{{ optionLabel(opt.name) }}</span>
          <Select
            v-model="optionValues[opt.name]"
            :options="opt.choices"
            size="small"
            :data-testid="'calib-opt-' + opt.name"
          />
        </label>
        <!-- 'str' needs a text box: falling through to InputNumber coerces a
             string default to NaN. -->
        <label v-else-if="opt.type === 'str'" class="field">
          <span :title="opt.description">{{ optionLabel(opt.name) }}</span>
          <InputText
            v-model="optionValues[opt.name]"
            size="small"
            :data-testid="'calib-opt-' + opt.name"
          />
        </label>
        <label v-else class="field">
          <span :title="opt.description">{{ optionLabel(opt.name) }}</span>
          <InputNumber
            v-model="optionValues[opt.name]"
            :min-fraction-digits="opt.type === 'float' ? 1 : undefined"
            :max-fraction-digits="opt.type === 'float' ? 10 : undefined"
            size="small"
            :data-testid="'calib-opt-' + opt.name"
          />
        </label>
      </template>

      <label class="field">
        <span title="mpiexec -n N: parallel population evaluation">Cores</span>
        <InputNumber
          v-model="settings.num_cores"
          :min="1"
          :max="64"
          size="small"
          :invalid="coresInvalid"
          data-testid="calib-cores"
        />
        <small v-if="coresInvalid" class="cores-invalid" data-testid="calib-cores-invalid">
          Cores &gt; 1 not available (no MPI launcher). Set to 1 to run.
        </small>
      </label>
      <label class="field checkbox">
        <Checkbox v-model="settings.DEBUG" :binary="true" input-id="cal-debug" />
        <span>DEBUG (small population, fast)</span>
      </label>
    </div>

    <div class="cal-actions">
      <Button
        label="Run calibration"
        icon="pi pi-play"
        size="small"
        data-testid="run-calibration"
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
      <span v-if="cost != null" class="cal-cost">cost: {{ cost.toPrecision(4) }}</span>
    </div>
    <p v-if="!canRun" class="hint">
      Load a model, an obs_data.json and a params_for_id.csv to calibrate.
    </p>
    <p v-if="error" class="cal-error">{{ error }}</p>

    <pre ref="term" class="terminal" data-testid="cal-terminal">{{ lines.join('\n') }}</pre>
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
}
.cal-actions {
  display: flex;
  align-items: center;
  gap: 0.5rem;
}
.cal-cost {
  font-size: 0.8rem;
  color: #70ad47;
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
.cores-invalid {
  display: block;
  margin-top: 0.15rem;
  color: #e84a5f;
  font-size: 0.72rem;
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
