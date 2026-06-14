<script setup>
import { ref, watch } from 'vue'
import Message from 'primevue/message'
import InputText from 'primevue/inputtext'
import Button from 'primevue/button'
import FileBrowserDialog from './FileBrowserDialog.vue'
import { uploadCellML, uploadObsData, uploadParamsForId } from '../lib/api'

const props = defineProps({
  modelId: { type: String, default: null },
  outputsDir: { type: String, default: '' },
})
const emit = defineEmits([
  'model-loaded',
  'obs-data-loaded',
  'params-loaded',
  'update:outputsDir',
])

const error = ref('')
const notice = ref('')
const outputsBrowserOpen = ref(false)

// obs_data / params depend on a model_id to attach server-side (and params is
// parsed against the model's initial_values). Remember the last dropped inputs
// so they can be (re)attached once a CellML model is present — making the drop
// order irrelevant. See issue #16.
const pendingObs = ref(null) // parsed obs_data object
const pendingParams = ref(null) // params_for_id File

function extOk(filename, exts) {
  return exts.some((e) => filename.toLowerCase().endsWith(e))
}

async function attachObs(obsData) {
  const summary = await uploadObsData(props.modelId, obsData)
  emit('obs-data-loaded', summary)
}

async function attachParams(file) {
  const data = await uploadParamsForId(file, props.modelId)
  emit('params-loaded', { ...data, filename: file.name })
}

// When a model is (re)loaded, flush any remembered obs/params onto it. The
// parent clears its obs/params stores on model load, so this repopulates them.
watch(
  () => props.modelId,
  async (id, prev) => {
    if (!id || id === prev) return
    error.value = ''
    try {
      if (pendingObs.value) await attachObs(pendingObs.value)
      if (pendingParams.value) await attachParams(pendingParams.value)
      notice.value = ''
    } catch (e) {
      error.value = e?.response?.data?.detail || String(e)
    }
  },
)

function filesFrom(event) {
  if (event.dataTransfer?.files?.length) return Array.from(event.dataTransfer.files)
  if (event.target?.files?.length) return Array.from(event.target.files)
  return []
}

async function onCellmlDrop(event) {
  event.preventDefault?.()
  error.value = ''
  const [file] = filesFrom(event)
  if (!file) return
  if (!extOk(file.name, ['.cellml', '.xml'])) {
    error.value = `Expected a .cellml file, got "${file.name}"`
    return
  }
  try {
    const data = await uploadCellML(file)
    emit('model-loaded', { ...data, filename: file.name })
  } catch (e) {
    error.value = e?.response?.data?.detail || String(e)
  }
}

async function onObsDrop(event) {
  event.preventDefault?.()
  error.value = ''
  const [file] = filesFrom(event)
  if (!file) return
  if (!extOk(file.name, ['.json'])) {
    error.value = `Expected a .json file, got "${file.name}"`
    return
  }
  try {
    const obsData = JSON.parse(await file.text())
    pendingObs.value = obsData
    if (props.modelId) {
      await attachObs(obsData)
    } else {
      notice.value = 'obs_data queued — it will attach once a CellML model is loaded.'
    }
  } catch (e) {
    error.value = e?.response?.data?.detail || String(e)
  }
}

async function onParamsDrop(event) {
  event.preventDefault?.()
  error.value = ''
  const [file] = filesFrom(event)
  if (!file) return
  if (!extOk(file.name, ['.csv'])) {
    error.value = `Expected a .csv file, got "${file.name}"`
    return
  }
  try {
    pendingParams.value = file
    if (props.modelId) {
      await attachParams(file)
    } else {
      notice.value =
        'params_for_id queued — it will attach once a CellML model is loaded.'
    }
  } catch (e) {
    error.value = e?.response?.data?.detail || String(e)
  }
}
</script>

<template>
  <section class="file-import">
    <h2>Imports</h2>

    <div
      class="dropzone"
      data-testid="cellml-drop"
      @dragover.prevent
      @drop="onCellmlDrop"
    >
      <i class="pi pi-file" /> Drop <strong>CellML</strong> (.cellml)
    </div>

    <div
      class="dropzone"
      data-testid="obs-drop"
      @dragover.prevent
      @drop="onObsDrop"
    >
      <i class="pi pi-chart-line" /> Drop <strong>obs_data.json</strong>
    </div>

    <div
      class="dropzone"
      data-testid="params-drop"
      @dragover.prevent
      @drop="onParamsDrop"
    >
      <i class="pi pi-sliders-h" /> Drop <strong>params_for_id.csv</strong>
    </div>

    <Message
      v-if="error"
      severity="error"
      data-testid="import-error"
      :closable="false"
    >
      {{ error }}
    </Message>
    <Message
      v-if="notice && !error"
      severity="info"
      data-testid="import-notice"
      :closable="false"
    >
      {{ notice }}
    </Message>

    <h2 class="exports-heading">Exports</h2>
    <label class="outputs-dir">
      <span>Outputs directory</span>
      <span class="outputs-input">
        <InputText
          :model-value="outputsDir"
          data-testid="config-outputs-dir"
          placeholder="default: system temp dir"
          size="small"
          @update:model-value="emit('update:outputsDir', $event)"
        />
        <Button
          icon="pi pi-folder-open"
          size="small"
          text
          title="Browse for an outputs directory"
          data-testid="outputs-browse"
          @click="outputsBrowserOpen = true"
        />
      </span>
    </label>
    <small class="hint">
      Absolute path where calibration outputs are written. Leave blank for a
      temporary directory.
    </small>

    <FileBrowserDialog
      v-model:visible="outputsBrowserOpen"
      mode="dir"
      title="Select an outputs directory"
      @select="emit('update:outputsDir', $event)"
    />
  </section>
</template>

<style scoped>
.file-import {
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
  padding: 0.75rem;
}
.dropzone {
  border: 1px dashed var(--p-content-border-color, #555);
  border-radius: 6px;
  padding: 1rem 0.75rem;
  text-align: center;
  font-size: 0.85rem;
  cursor: pointer;
}
.dropzone:hover {
  border-color: var(--p-primary-color, #5b9bd5);
}
.exports-heading {
  margin: 0.5rem 0 0;
}
.outputs-dir {
  display: flex;
  flex-direction: column;
  gap: 0.25rem;
  font-size: 0.85rem;
}
.outputs-input {
  display: flex;
  align-items: center;
  gap: 0.25rem;
}
.outputs-dir :deep(input) {
  width: 100%;
}
.hint {
  opacity: 0.6;
  font-size: 0.75rem;
}
</style>
