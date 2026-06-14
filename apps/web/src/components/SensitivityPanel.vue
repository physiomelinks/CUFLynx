<script setup>
import { ref, reactive, watch, nextTick, computed } from 'vue'
import Select from 'primevue/select'
import InputNumber from 'primevue/inputnumber'
import Checkbox from 'primevue/checkbox'
import Button from 'primevue/button'
import FileBrowserDialog from './FileBrowserDialog.vue'

const props = defineProps({
  defaults: { type: Object, default: () => ({}) },
  pythons: { type: Array, default: () => [] },
  canRun: { type: Boolean, default: false },
  lines: { type: Array, default: () => [] },
  state: { type: String, default: 'idle' },
  error: { type: String, default: '' },
  // Shared with the Calibration panel: picking an interpreter in one updates both.
  pythonPath: { type: String, default: '' },
})
const emit = defineEmits(['run', 'cancel', 'update:pythonPath'])

// Note: pre_time / sim_time are intentionally NOT here — SA timing comes from
// the obs_data.json protocol_info (mirrors calibration, see #13).
const settings = reactive({
  method: 'sobol',
  sample_type: 'saltelli',
  num_samples: 256,
  num_cores: 1,
  python_path: '',
  dt: 0.01,
  DEBUG: false,
})

const pythonBrowserOpen = ref(false)

const pythonOptions = computed(() => {
  const opts = [
    { label: 'Server default', value: '' },
    ...props.pythons.map((p) => ({
      label:
        `Python ${p.version} — ${p.path}` +
        (p.ready ? '' : ` (missing: ${(p.missing || []).join(', ')})`),
      value: p.path,
      ready: p.ready,
    })),
  ]
  // Show a browsed interpreter that isn't among the auto-discovered ones.
  if (settings.python_path && !opts.some((o) => o.value === settings.python_path)) {
    opts.push({ label: `Custom — ${settings.python_path}`, value: settings.python_path })
  }
  return opts
})

// Update local state and notify the parent so the other panel stays in sync.
function setPython(p) {
  settings.python_path = p
  emit('update:pythonPath', p)
}

function onPythonSelected(p) {
  setPython(p)
}

// Adopt an interpreter chosen in the sibling panel.
watch(
  () => props.pythonPath,
  (p) => {
    if (p !== settings.python_path) settings.python_path = p
  },
)

// Whether the chosen interpreter is known to be missing required deps.
const selectedNotReady = computed(() => {
  const p = props.pythons.find((x) => x.path === settings.python_path)
  return p && !p.ready ? p.missing : null
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
  (props.defaults.methods ?? ['sobol']).map((m) => ({ label: m, value: m })),
)
const sampleTypes = computed(() =>
  (props.defaults.sample_types ?? ['saltelli', 'sobol']).map((m) => ({
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
      <!-- Not a <label>: this row has two controls (Select + browse Button); a
           label would forward chevron clicks to the native button (the browse
           dialog) instead of opening the dropdown. -->
      <div class="field">
        <span title="Interpreter/env used to run the analysis">Python</span>
        <span class="field-input">
          <Select
            :model-value="settings.python_path"
            :options="pythonOptions"
            option-label="label"
            option-value="value"
            size="small"
            data-testid="sa-python-select"
            @update:model-value="setPython"
          />
          <Button
            icon="pi pi-folder-open"
            size="small"
            text
            title="Browse for a Python interpreter"
            data-testid="sa-python-browse"
            @click="pythonBrowserOpen = true"
          />
        </span>
      </div>
      <p v-if="selectedNotReady" class="cal-error" data-testid="sa-python-warning">
        Selected interpreter is missing: {{ selectedNotReady.join(', ') }}
      </p>
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

    <FileBrowserDialog
      v-model:visible="pythonBrowserOpen"
      mode="file"
      title="Select a Python interpreter"
      @select="onPythonSelected"
    />
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
