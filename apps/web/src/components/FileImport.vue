<script setup>
import { ref, watch } from 'vue'
import Message from 'primevue/message'
import InputText from 'primevue/inputtext'
import Button from 'primevue/button'
import FileBrowserDialog from './FileBrowserDialog.vue'
import EditParamsDialog from './EditParamsDialog.vue'
import EditObsDataDialog from './EditObsDataDialog.vue'
import { uploadCellML, uploadObsData, uploadParamsForId } from '../lib/api'

const props = defineProps({
  modelId: { type: String, default: null },
  outputsDir: { type: String, default: '' },
  // For the params Edit dialog: the loaded CSV's params (with param_type), the
  // model's candidate params + initial values, and names for the new filename.
  currentParams: { type: Array, default: () => [] },
  modelVariables: { type: Object, default: () => ({}) },
  modelName: { type: String, default: null },
  loadedFilename: { type: String, default: null },
  // For the obs_data Edit dialog: the loaded obs_data items + protocol_info and
  // the loaded obs filename for versioning.
  currentDataItems: { type: Array, default: () => [] },
  currentPredictionItems: { type: Array, default: () => [] },
  obsProtocolInfo: { type: Object, default: null },
  experimentCount: { type: Number, default: 0 },
  loadedObsFilename: { type: String, default: null },
  // Enables the pipeline/plotting export buttons (a model must be loaded).
  canExport: { type: Boolean, default: false },
})
const emit = defineEmits([
  'model-loaded',
  'obs-data-loaded',
  'params-loaded',
  'update:outputsDir',
  'export-pipeline',
  'export-plotting',
])

const error = ref('')
const notice = ref('')
const outputsBrowserOpen = ref(false)
const editParamsOpen = ref(false)
const editObsOpen = ref(false)

// The Edit dialog produces a new params set the same shape as a CSV upload, so
// reuse the existing params-loaded flow to re-seed sliders and make it active.
function onEditSaved(payload) {
  emit('params-loaded', payload)
}

// The obs Edit dialog already re-uploaded; reuse the obs-data-loaded flow.
function onObsEditSaved(payload) {
  emit('obs-data-loaded', payload)
}

// obs_data / params depend on a model_id to attach server-side (and params is
// parsed against the model's initial_values). Remember the last dropped inputs
// so they can be (re)attached once a CellML model is present — making the drop
// order irrelevant. See issue #16.
const pendingObs = ref(null) // parsed obs_data object
const pendingParams = ref(null) // params_for_id File

function extOk(filename, exts) {
  return exts.some((e) => filename.toLowerCase().endsWith(e))
}

async function attachObs(obsData, filename) {
  const summary = await uploadObsData(props.modelId, obsData)
  emit('obs-data-loaded', { ...summary, filename })
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
      if (pendingObs.value) await attachObs(pendingObs.value.obsData, pendingObs.value.filename)
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

// After picking via the <input>, clear its value so selecting the SAME file
// again still fires `change` (needed to retry after an error). Harmless for the
// drag path, where the event target has no `value`.
function resetPicker(event) {
  if (event.target && 'value' in event.target) event.target.value = ''
}

// Some Linux desktop setups (e.g. multiple X display sessions, Snap-confined
// apps) hand the browser a dragged file it can't actually read — it arrives as
// 0 bytes and the upload fails with an opaque network error. Detect that and
// point the user at the reliable file picker. Returns a message if unreadable.
function unreadableDrop(file) {
  if (file.size > 0) return ''
  return (
    `"${file.name}" came through as 0 bytes — your desktop didn't hand the ` +
    `browser a readable file (a known Linux drag-and-drop limitation). Use the ` +
    `"click to browse" button instead.`
  )
}

async function onCellmlDrop(event) {
  event.preventDefault?.()
  error.value = ''
  const [file] = filesFrom(event)
  resetPicker(event)
  if (!file) return
  if (!extOk(file.name, ['.cellml', '.xml'])) {
    error.value = `Expected a .cellml file, got "${file.name}"`
    return
  }
  const unreadable = unreadableDrop(file)
  if (unreadable) {
    error.value = unreadable
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
  resetPicker(event)
  if (!file) return
  if (!extOk(file.name, ['.json'])) {
    error.value = `Expected a .json file, got "${file.name}"`
    return
  }
  const unreadable = unreadableDrop(file)
  if (unreadable) {
    error.value = unreadable
    return
  }
  try {
    const obsData = JSON.parse(await file.text())
    pendingObs.value = { obsData, filename: file.name }
    if (props.modelId) {
      await attachObs(obsData, file.name)
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
  resetPicker(event)
  if (!file) return
  if (!extOk(file.name, ['.csv'])) {
    error.value = `Expected a .csv file, got "${file.name}"`
    return
  }
  const unreadable = unreadableDrop(file)
  if (unreadable) {
    error.value = unreadable
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

    <label
      class="dropzone"
      data-testid="cellml-drop"
      @dragover.prevent
      @drop="onCellmlDrop"
    >
      <i class="pi pi-file" /> Drop <strong>CellML</strong> (.cellml)
      <small>or click to browse</small>
      <input type="file" accept=".cellml,.xml" @change="onCellmlDrop" />
    </label>

    <div class="params-row">
      <label
        class="dropzone"
        data-testid="obs-drop"
        @dragover.prevent
        @drop="onObsDrop"
      >
        <i class="pi pi-chart-line" /> Drop <strong>obs_data.json</strong>
        <small>or click to browse</small>
        <input type="file" accept=".json" @change="onObsDrop" />
      </label>
      <Button
        label="Edit"
        icon="pi pi-pencil"
        size="small"
        class="params-edit-btn"
        data-testid="obs-edit"
        title="Edit obs_data items, save a new dated JSON"
        :disabled="!modelId"
        @click="editObsOpen = true"
      />
    </div>

    <div class="params-row">
      <label
        class="dropzone"
        data-testid="params-drop"
        @dragover.prevent
        @drop="onParamsDrop"
      >
        <i class="pi pi-sliders-h" /> Drop <strong>params_for_id.csv</strong>
        <small>or click to browse</small>
        <input type="file" accept=".csv" @change="onParamsDrop" />
      </label>
      <Button
        label="Edit"
        icon="pi pi-pencil"
        size="small"
        class="params-edit-btn"
        data-testid="params-edit"
        title="Edit included parameters and ranges, save a new dated CSV"
        :disabled="!modelId"
        @click="editParamsOpen = true"
      />
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
    <div class="export-buttons">
      <Button
        label="Export pipeline to python"
        icon="pi pi-file-export"
        size="small"
        text
        :disabled="!canExport"
        data-testid="export-pipeline"
        @click="emit('export-pipeline')"
      />
      <Button
        label="Export python plotting script"
        icon="pi pi-chart-line"
        size="small"
        text
        :disabled="!canExport"
        data-testid="export-plotting"
        @click="emit('export-plotting')"
      />
    </div>

    <FileBrowserDialog
      v-model:visible="outputsBrowserOpen"
      mode="dir"
      title="Select an outputs directory"
      @select="emit('update:outputsDir', $event)"
    />

    <EditParamsDialog
      v-model:visible="editParamsOpen"
      :model-id="modelId"
      :current-params="currentParams"
      :model-variables="modelVariables"
      :model-name="modelName"
      :loaded-filename="loadedFilename"
      @saved="onEditSaved"
    />

    <EditObsDataDialog
      v-model:visible="editObsOpen"
      :model-id="modelId"
      :current-data-items="currentDataItems"
      :current-prediction-items="currentPredictionItems"
      :protocol-info="obsProtocolInfo"
      :experiment-count="experimentCount"
      :model-variables="modelVariables"
      :model-name="modelName"
      :loaded-filename="loadedObsFilename"
      @saved="onObsEditSaved"
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
.params-row {
  display: flex;
  align-items: stretch;
  gap: 0.5rem;
}
.params-row .dropzone {
  flex: 1;
}
/* Match the Edit button to the dropzone's color scheme: dashed border,
   transparent fill, inherited text, primary-color border on hover. */
.params-row .params-edit-btn {
  border: 1px dashed var(--p-content-border-color, #555);
  border-radius: 6px;
  background: transparent;
  color: inherit;
}
.params-row .params-edit-btn:enabled:hover {
  border-color: var(--p-primary-color, #5b9bd5);
  background: transparent;
  color: inherit;
}
.dropzone input[type='file'] {
  display: none;
}
.dropzone small {
  display: block;
  opacity: 0.55;
  font-size: 0.7rem;
  margin-top: 0.15rem;
}
.exports-heading {
  margin: 0.5rem 0 0;
}
.export-buttons {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 0.35rem;
  margin: 0.5rem 0;
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
