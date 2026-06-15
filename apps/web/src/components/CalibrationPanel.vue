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
  cost: { type: Number, default: null },
  error: { type: String, default: '' },
})
const emit = defineEmits(['run', 'cancel'])

// Note: pre_time / sim_time are intentionally NOT here — calibration timing
// comes from the obs_data.json protocol_info (see #13). The Python interpreter
// is chosen once in the top bar (shared across calibration/sensitivity/UQ).
const settings = reactive({
  param_id_method: 'genetic_algorithm',
  num_calls_to_function: 100,
  cost_convergence: 0.001,
  max_patience: 10,
  num_cores: 1,
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

const methods = computed(() =>
  (props.defaults.methods ?? ['genetic_algorithm', 'CMA-ES']).map((m) => ({
    label: m,
    value: m,
  })),
)

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
        />
      </label>
      <label class="field">
        <span>Max evals</span>
        <InputNumber v-model="settings.num_calls_to_function" :min="1" size="small" />
      </label>
      <label class="field">
        <span>Convergence</span>
        <InputNumber
          v-model="settings.cost_convergence"
          :min-fraction-digits="1"
          :max-fraction-digits="8"
          size="small"
        />
      </label>
      <label class="field">
        <span>Max patience</span>
        <InputNumber v-model="settings.max_patience" :min="1" size="small" />
      </label>
      <label class="field">
        <span title="mpiexec -n N: parallel GA population evaluation">Cores</span>
        <InputNumber v-model="settings.num_cores" :min="1" :max="64" size="small" />
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
