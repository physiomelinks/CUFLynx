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
  error: { type: String, default: '' },
  // False when no MPI launcher is available: cores>1 would silently run on one
  // core, so mark the Cores field invalid and block the run until it's set to 1.
  mpiexecAvailable: { type: Boolean, default: true },
})
const emit = defineEmits(['run', 'cancel', 'change'])

// pre_time / sim_time come from the obs_data protocol_info (mirrors calibration).
// The Python interpreter is chosen once in the top bar. The MCMC options
// (num_steps, num_walkers, …) are NOT here — they come from CA's ANALYSIS_OPTIONS
// schema (see mcmcOptions/optionValues below), so new CA MCMC options surface
// automatically.
const settings = reactive({
  method: 'mcmc',
  run_calibration_first: false,
  num_cores: 1,
  dt: 0.01,
  DEBUG: false,
})

// Per-option values for CA's MCMC settings, keyed by option name (seeded from
// each descriptor's default). Merged into the run/change payload flat.
const optionValues = reactive({})

const isMcmc = computed(() => settings.method === 'mcmc')

// CA's mcmc option descriptors (num_steps, num_walkers, …), never hardcoded.
const mcmcOptions = computed(() => props.defaults.mcmc_options ?? [])

// Seed each option's default when the schema arrives, keeping any user value.
watch(
  mcmcOptions,
  (opts) => {
    for (const o of opts) {
      if (optionValues[o.name] === undefined) optionValues[o.name] = o.default
    }
  },
  { immediate: true },
)

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

// A short field label from an option name (num_steps -> "Num steps").
function optionLabel(name) {
  const s = String(name).replace(/_/g, ' ')
  return s.charAt(0).toUpperCase() + s.slice(1)
}

// The run/change payload: CUFLynx-level settings + the MCMC option values.
function buildSettings() {
  const opts = {}
  for (const o of mcmcOptions.value) opts[o.name] = optionValues[o.name]
  return { ...settings, ...opts }
}

// Surface live settings upward so the pipeline export can capture them.
watch([settings, optionValues], () => emit('change', buildSettings()), {
  deep: true,
  immediate: true,
})

const methods = computed(() =>
  (props.defaults.methods ?? ['mcmc', 'laplace']).map((m) => ({
    label: m === 'mcmc' ? 'MCMC' : 'Laplace',
    value: m,
  })),
)

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
      <h2>UQ</h2>
      <span class="cal-state" :data-state="state">{{ state }}</span>
    </header>

    <div class="cal-form">
      <label class="field">
        <span title="MCMC (emcee) posterior, or Laplace Gaussian approximation">Method</span>
        <Select
          v-model="settings.method"
          :options="methods"
          option-label="label"
          option-value="value"
          size="small"
        />
      </label>
      <!-- MCMC options, from CA's ANALYSIS_OPTIONS[mcmc]. -->
      <template v-if="isMcmc">
        <template v-for="opt in mcmcOptions" :key="opt.name">
          <label v-if="opt.type === 'bool'" class="field checkbox">
            <Checkbox v-model="optionValues[opt.name]" :binary="true" :input-id="'mcmc-opt-' + opt.name" />
            <span :title="opt.description">{{ optionLabel(opt.name) }}</span>
          </label>
          <label v-else-if="opt.type === 'enum'" class="field">
            <span :title="opt.description">{{ optionLabel(opt.name) }}</span>
            <Select
              v-model="optionValues[opt.name]"
              :options="opt.choices"
              size="small"
              :data-testid="'mcmc-opt-' + opt.name"
            />
          </label>
          <!-- 'str' needs a text box: falling through to InputNumber coerces a
               string default (e.g. sub_method='parabola_fit') to NaN. -->
          <label v-else-if="opt.type === 'str'" class="field">
            <span :title="opt.description">{{ optionLabel(opt.name) }}</span>
            <InputText
              v-model="optionValues[opt.name]"
              size="small"
              :data-testid="'mcmc-opt-' + opt.name"
            />
          </label>
          <label v-else class="field">
            <span :title="opt.description">{{ optionLabel(opt.name) }}</span>
            <InputNumber
              v-model="optionValues[opt.name]"
              :min-fraction-digits="opt.type === 'float' ? 1 : undefined"
              :max-fraction-digits="opt.type === 'float' ? 10 : undefined"
              size="small"
              :data-testid="'mcmc-opt-' + opt.name"
            />
          </label>
        </template>
      </template>
      <label class="field">
        <span title="mpiexec -n N: parallel sampling / calibration">Cores</span>
        <InputNumber
          v-model="settings.num_cores"
          :min="1"
          :max="64"
          size="small"
          :invalid="coresInvalid"
          data-testid="uq-cores"
        />
        <small v-if="coresInvalid" class="cores-invalid" data-testid="uq-cores-invalid">
          Cores &gt; 1 not available (no MPI launcher). Set to 1 to run.
        </small>
      </label>
      <label class="field checkbox">
        <Checkbox
          v-model="settings.run_calibration_first"
          :binary="true"
          input-id="uq-fresh-calib"
        />
        <span title="Otherwise UQ reuses the latest completed calibration's best fit">
          Run a fresh calibration first
        </span>
      </label>
      <label class="field checkbox">
        <Checkbox v-model="settings.DEBUG" :binary="true" input-id="uq-debug" />
        <span>DEBUG (small/fast)</span>
      </label>
    </div>

    <div class="cal-actions">
      <Button
        label="Run UQ"
        icon="pi pi-play"
        size="small"
        data-testid="run-uq"
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
      Load a model, an obs_data.json and a params_for_id.csv to run UQ.
    </p>
    <p v-else-if="!settings.run_calibration_first" class="hint">
      Reuses the latest completed calibration's best fit — run a calibration first, or
      tick the box above.
    </p>
    <p v-if="error" class="cal-error">{{ error }}</p>

    <pre ref="term" class="terminal" data-testid="uq-terminal">{{ lines.join('\n') }}</pre>
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
