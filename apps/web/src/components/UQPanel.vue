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

// pre_time / sim_time come from the obs_data protocol_info (mirrors calibration).
// The Python interpreter is chosen once in the top bar.
const settings = reactive({
  method: 'mcmc',
  run_calibration_first: false,
  num_steps: 1000,
  num_walkers: 64,
  num_cores: 1,
  dt: 0.01,
  DEBUG: false,
})

const isMcmc = computed(() => settings.method === 'mcmc')

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
  (props.defaults.methods ?? ['mcmc', 'laplace']).map((m) => ({
    label: m === 'mcmc' ? 'MCMC' : 'Laplace',
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
      <template v-if="isMcmc">
        <label class="field">
          <span>Steps</span>
          <InputNumber v-model="settings.num_steps" :min="1" size="small" />
        </label>
        <label class="field">
          <span>Walkers</span>
          <InputNumber v-model="settings.num_walkers" :min="2" size="small" />
        </label>
      </template>
      <label class="field">
        <span title="mpiexec -n N: parallel sampling / calibration">Cores</span>
        <InputNumber v-model="settings.num_cores" :min="1" :max="64" size="small" />
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
