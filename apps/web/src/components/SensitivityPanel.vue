<script setup>
import { ref, reactive, watch, nextTick, computed } from 'vue'
import Select from 'primevue/select'
import InputNumber from 'primevue/inputnumber'
import Checkbox from 'primevue/checkbox'
import Button from 'primevue/button'

const props = defineProps({
  defaults: { type: Object, default: () => ({}) },
  canRun: { type: Boolean, default: false },
  lines: { type: Array, default: () => [] },
  state: { type: String, default: 'idle' },
  error: { type: String, default: '' },
})
const emit = defineEmits(['run', 'cancel'])

// pre_time / sim_time come from the obs_data.json protocol_info (mirrors
// calibration, see #13). The Python interpreter is chosen once in the top bar.
const settings = reactive({
  method: 'sobol',
  // Sobol (global) options:
  sample_type: 'saltelli',
  num_samples: 256,
  num_cores: 1,
  // Local (derivative-based) options:
  gradient_method: 'FD',
  rel_step: 0.01,
  nominal: 'current',
  // When true, run a fresh calibration first and linearise about its best fit.
  // The calibration uses whatever is configured in the Calibration panel
  // (folded in by App.vue), so there are no GA controls duplicated here.
  run_calibration_first: false,
  dt: 0.01,
  DEBUG: false,
})

// Seed from server defaults once they arrive.
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

const METHOD_LABELS = { sobol: 'Sobol (global)', local: 'Local (finite difference)' }
const methods = computed(() =>
  (props.defaults.methods ?? ['sobol']).map((m) => ({
    label: METHOD_LABELS[m] ?? m,
    value: m,
  })),
)
const sampleTypes = computed(() =>
  (props.defaults.sample_types ?? ['saltelli', 'sobol']).map((m) => ({
    label: m,
    value: m,
  })),
)
// Gradient sources for local SA: [{value, label, disabled}]. Only FD is enabled
// today; AD / CVODES are listed (disabled) so the UI shows where they'll slot in.
const gradientMethods = computed(
  () =>
    props.defaults.gradient_methods ?? [{ value: 'FD', label: 'Finite difference' }],
)
const nominals = computed(() =>
  (props.defaults.nominals ?? ['midpoint', 'geometric']).map((m) => ({
    label: m,
    value: m,
  })),
)

const isLocal = computed(() => settings.method === 'local')
const running = computed(() => props.state === 'running')

const term = ref(null)
watch(
  () => props.lines.length,
  async () => {
    await nextTick()
    if (term.value) term.value.scrollTop = term.value.scrollHeight
  },
)

function onRun() {
  emit('run', { ...settings })
}
</script>

<template>
  <section class="calibration-panel">
    <header class="cal-header">
      <h2>Sensitivity</h2>
      <span class="cal-state" :data-state="state">{{ state }}</span>
    </header>

    <div class="cal-form">
      <label class="field">
        <span>Method</span>
        <Select
          v-model="settings.method"
          :options="methods"
          option-label="label"
          option-value="value"
          size="small"
        />
      </label>
      <!-- Sobol (global) options -->
      <template v-if="!isLocal">
        <label class="field">
          <span>Sample type</span>
          <Select
            v-model="settings.sample_type"
            :options="sampleTypes"
            option-label="label"
            option-value="value"
            size="small"
          />
        </label>
        <label class="field">
          <span title="Base sample count N; total sims ~ N·(2·num_params+2)">Samples</span>
          <InputNumber v-model="settings.num_samples" :min="1" size="small" />
        </label>
        <label class="field">
          <span title="mpiexec -n N: parallel sample evaluation">Cores</span>
          <InputNumber v-model="settings.num_cores" :min="1" :max="64" size="small" />
        </label>
      </template>

      <!-- Local (derivative-based) options -->
      <template v-else>
        <label class="field">
          <span title="Gradient source. Only finite difference works for CellML today; AD needs casadi_python, CVODES needs upstream Myokit sensitivities.">Gradient source</span>
          <Select
            v-model="settings.gradient_method"
            :options="gradientMethods"
            option-label="label"
            option-value="value"
            :option-disabled="(o) => o.disabled"
            size="small"
            data-testid="gradient-method"
          />
        </label>
        <label class="field">
          <span title="Relative central-difference step about the nominal parameter value">Rel. step</span>
          <InputNumber
            v-model="settings.rel_step"
            :min="1e-6"
            :max="0.5"
            :min-fraction-digits="2"
            :max-fraction-digits="6"
            size="small"
          />
        </label>
        <label class="field">
          <span title="Parameter point to linearise about. 'current' uses the model's current values; 'best_fit' reuses a completed calibration.">Nominal point</span>
          <Select
            v-model="settings.nominal"
            :options="nominals"
            option-label="label"
            option-value="value"
            :disabled="settings.run_calibration_first"
            size="small"
          />
        </label>
        <label class="field checkbox">
          <Checkbox
            v-model="settings.run_calibration_first"
            :binary="true"
            input-id="sa-run-calib-first"
            data-testid="sa-run-calib-first"
          />
          <span title="Run a fresh calibration (using the Calibration tab's settings), then take the local sensitivity about that best fit">
            Run calibration first (about best fit)
          </span>
        </label>
      </template>

      <label class="field checkbox">
        <Checkbox v-model="settings.DEBUG" :binary="true" input-id="sa-debug" />
        <span>DEBUG (fewer samples, fast)</span>
      </label>
    </div>

    <div class="cal-actions">
      <Button
        label="Run sensitivity"
        icon="pi pi-play"
        size="small"
        data-testid="run-sensitivity"
        :disabled="!canRun || running"
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
      Load a model, an obs_data.json and a params_for_id.csv to run a sensitivity
      analysis.
    </p>
    <p v-if="error" class="cal-error">{{ error }}</p>

    <pre ref="term" class="terminal" data-testid="sa-terminal">{{ lines.join('\n') }}</pre>
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
