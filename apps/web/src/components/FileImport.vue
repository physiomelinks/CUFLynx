<script setup>
import { ref } from 'vue'
import Message from 'primevue/message'
import { uploadCellML, uploadObsData, uploadParamsForId } from '../lib/api'

const props = defineProps({
  modelId: { type: String, default: null },
})
const emit = defineEmits([
  'model-loaded',
  'obs-data-loaded',
  'params-loaded',
])

const error = ref('')

function extOk(filename, exts) {
  return exts.some((e) => filename.toLowerCase().endsWith(e))
}

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
    emit('model-loaded', data)
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
    const summary = await uploadObsData(props.modelId, obsData)
    emit('obs-data-loaded', summary)
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
    const data = await uploadParamsForId(file, props.modelId)
    emit('params-loaded', { ...data, filename: file.name })
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
</style>
