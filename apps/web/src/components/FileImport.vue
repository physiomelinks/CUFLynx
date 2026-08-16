<script setup>
import { ref, computed, watch } from 'vue'
import Message from 'primevue/message'
import InputText from 'primevue/inputtext'
import Button from 'primevue/button'
import FileBrowserDialog from './FileBrowserDialog.vue'
import EditParamsDialog from './EditParamsDialog.vue'
import EditObsDataDialog from './EditObsDataDialog.vue'
import StartDialog from './StartDialog.vue'
import {
  uploadCellML,
  uploadObsData,
  uploadParamsForId,
  fetchExampleModel,
  editModelSource,
  uploadOmex,
} from '../lib/api'
import { PHLYNX_URL } from '../lib/examples'

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
  // Gates the group/modifier buttons in the params editor: the CasADi backend
  // refuses grouped and modifier rows at run time (#208).
  generatedModelFormat: { type: String, default: '' },
  // What kind of model is loaded, as the server reported it: 'external_python'
  // for a dropped .py, empty for CellML. Decides what Edit does.
  modelFormat: { type: String, default: '' },
  // The filename a converted model came from -- set for a .mmt, which becomes
  // CellML at import (#27). Also decides what Edit does.
  convertedFrom: { type: String, default: null },
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
const startOpen = ref(false)

// Drag feedback (#137): a dropzone highlights while a file is over it, so the
// user can see the drop will land somewhere rather than guessing.
const dragOver = ref('')
function onDragEnter(zone) {
  dragOver.value = zone
}
function onDragLeave(zone) {
  if (dragOver.value === zone) dragOver.value = ''
}

// Once a file is in, the dropzone stops needing to explain itself: it shrinks to
// a single line naming what is loaded, and the Edit button beside it -- now the
// thing the user actually wants -- takes the space (#137).
const cellmlLoaded = computed(() => !!props.modelId)
const obsLoaded = computed(() => !!props.loadedObsFilename)
const paramsLoaded = computed(() => !!props.loadedFilename)

// PhLynx is a *CellML* model builder, so it is only ever the right thing to open
// for a CellML model. An external python model's source is its .py, and a Myokit
// model's is the .mmt it was converted from — opening a CellML builder on either
// would show the user a model they did not write and cannot edit there.
//
// `.mmt` is spotted through `converted_from` rather than through the model
// format: the conversion is what makes the loaded model CellML, so by the time
// it is loaded nothing else remembers the file was Myokit.
const sourceExt = computed(() => {
  if (props.modelFormat === 'external_python') return '.py'
  if (/\.mmt$/i.test(String(props.convertedFrom || ''))) return '.mmt'
  return ''
})

// The box beside the CellML dropzone: "Create" (no model yet) opens the Start
// dialog to pick an example or link to PhLynx; with a model loaded it either
// opens the user's own source file in their editor or opens PhLynx, per
// `sourceExt`.
const startEditLabel = computed(() => {
  if (!props.modelId) return 'Create'
  return sourceExt.value ? 'Edit source' : 'Edit'
})
const startEditTitle = computed(() => {
  if (!props.modelId) return 'Create a model: start from an example or build one in PhLynx'
  if (sourceExt.value) {
    return `Open the model source (${sourceExt.value}) in your editor, saved in the outputs directory`
  }
  return 'Edit the current model in PhLynx'
})

// Where the edited copy went, and what it means — the whole point of the button
// being "Edit source" rather than "View source" is that the user knows which
// file they are now working on.
function editedSourceMessage(res) {
  const where = res.runs
    ? `Editing ${res.path} — that copy is the model CUFLynx runs from now on.`
    : `Editing ${res.path} — CUFLynx runs the CellML converted from it, so drop the edited file back in to apply your changes.`
  return res.opened ? where : `No editor could be opened here. ${where}`
}

async function onStartEdit() {
  if (!props.modelId) {
    startOpen.value = true
    return
  }
  if (sourceExt.value) {
    error.value = ''
    notice.value = ''
    try {
      // The backend copies the source into the outputs directory and opens it
      // there. A browser cannot start a local editor, so this is a server
      // action on the same localhost assumption the file pickers already make.
      const res = await editModelSource(props.modelId, props.outputsDir)
      notice.value = editedSourceMessage(res)
    } catch (e) {
      // Only a real refusal (no outputs directory set) is an error banner. A
      // launch that could not happen comes back 200 with `opened: false`, and
      // is reported above as the path it wrote — which is still what was wanted.
      error.value = e?.response?.data?.detail || String(e)
    }
    return
  }
  // A link that opens PhLynx is enough for now; deeper integration is future
  // work (issue #91).
  window.open(PHLYNX_URL, '_blank', 'noopener')
}

// The Start dialog chose an example: fetch it and feed it through the normal
// upload flow, so a loaded example is indistinguishable from a drop.
async function onSelectExample(example) {
  error.value = ''
  try {
    const file = await fetchExampleModel(example.name, example.filename)
    // Examples ship as archives so one click loads the whole study — model,
    // obs_data and params_for_id (#180). A plain .cellml example would still
    // work, hence the fallback rather than a hard assumption.
    if (await handleOmex([file])) return
    const data = await uploadCellML([file])
    emit('model-loaded', { ...data, filename: example.filename })
  } catch (e) {
    error.value = e?.response?.data?.detail || String(e)
  }
}

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

// A dropped .mmt carries a [[protocol]] the model import leaves behind, since
// the protocol belongs in obs_data. The server converts it and hands it back
// here; it is adopted only when the user has no obs_data of their own, so a
// hand-written one is never overwritten by a derived one. See #27.
const derivedObs = ref(null)

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
      // After the pending flush, so an obs_data the user dropped always wins
      // over one derived from the .mmt's protocol.
      await adoptDerivedObs()
    } catch (e) {
      error.value = e?.response?.data?.detail || String(e)
    }
  },
)

// Create an obs_data from the .mmt's protocol, but only if there isn't one
// already: the point is to save retyping a protocol, not to replace anything.
async function adoptDerivedObs() {
  const derived = derivedObs.value
  derivedObs.value = null
  if (!derived) return
  if (!derived.obs_data) {
    // It could not be converted. Say why rather than leaving the user to wonder
    // where their protocol went — the reason is usually a fact about the file.
    if (derived.reason) notice.value = `No protocol taken from the .mmt: ${derived.reason}`
    return
  }
  if (pendingObs.value) return // the user dropped their own
  await attachObs(derived.obs_data, derived.filename)
  const notes = (derived.notes || []).join(' ')
  notice.value =
    `Created ${derived.filename} from the .mmt's protocol` +
    (derived.path ? ` (saved to ${derived.path})` : '') +
    `. It has no data_items yet — add what to measure via Edit.` +
    (notes ? ` ${notes}` : '')
}

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

/**
 * A COMBINE archive is the whole study, not any one of its files, so it is
 * accepted on *every* dropzone (#149) -- making the user unzip it and drop three
 * files in the right order is the thing this removes.
 *
 * Returns true when the drop was an archive and has been handled.
 */
async function handleOmex(files) {
  const omex = files.find((f) => isOmexName(f.name))
  if (!omex) return false
  error.value = ''
  try {
    const data = await uploadOmex(omex, props.outputsDir)
    // The model first: obs_data and params attach to it.
    emit('model-loaded', { ...data, filename: data.model_filename || omex.name })
    if (data.obs_data && !data.obs_data.error) {
      emit('obs-data-loaded', { ...data.obs_data, model_id: data.model_id })
    }
    if (data.params_for_id && !data.params_for_id.error) {
      emit('params-loaded', {
        params: data.params_for_id.params,
        filename: data.params_for_id.filename,
      })
    }
    // A part that failed to parse is reported without losing the rest -- an
    // archive with a bad obs_data still gave us a model worth having.
    const failed = [data.obs_data, data.params_for_id]
      .filter((p) => p?.error)
      .map((p) => `${p.filename}: ${p.error}`)
    notice.value = failed.length
      ? `Loaded ${omex.name}, but ${failed.join('; ')}`
      : `Loaded ${omex.name}` +
        (data.module_config_path ? ' (PhLynx layout kept)' : '')
  } catch (e) {
    error.value = e?.response?.data?.detail || String(e)
  }
  return true
}

function isOmexName(name) {
  return /\.omex$/i.test(String(name || ''))
}

async function onCellmlDrop(event) {
  event.preventDefault?.()
  error.value = ''
  // Accept a whole bundle: a non-flattened model plus the sister files it
  // imports. The server picks the main file, resolves imports and flattens to
  // one CellML 2.0 document. A single self-contained file is just a bundle of 1.
  const files = filesFrom(event)
  resetPicker(event)
  if (!files.length) return
  // An archive is taken whole and returns early; what follows is the loose-file
  // path. .mmt is accepted there and converted to CellML server-side (#27) -- a
  // Myokit model is a single file, so it never joins a sister-file bundle.
  if (await handleOmex(files)) return
  // `.py` is an *external python* model: a file holding the solver class itself
  // rather than a model description CUFLynx generates a solver from. It travels
  // the same upload route, and the server answers with model_format:
  // "external_python" so the app can lock the backend to it.
  const bad = files.find((f) => !extOk(f.name, ['.cellml', '.xml', '.mmt', '.py']))
  if (bad) {
    error.value = `Expected a .cellml, .mmt, .py or .omex file, got "${bad.name}"`
    return
  }
  const unreadable = files.map(unreadableDrop).find(Boolean)
  if (unreadable) {
    error.value = unreadable
    return
  }
  // The main file (for the display name) is the one importing sisters, if any;
  // a dropped .mmt is on its own, so it is simply the file itself.
  const main = files.find((f) => f.name.toLowerCase().endsWith('.cellml')) ?? files[0]
  try {
    const data = await uploadCellML(files, props.outputsDir)
    // Picked up by the modelId watcher, which runs after the parent has cleared
    // its obs store and after any pending obs_data has been re-attached.
    derivedObs.value = data.protocol_obs_data || null
    emit('model-loaded', { ...data, filename: main.name })
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
  if (await handleOmex([file])) return
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
  if (await handleOmex([file])) return
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

    <div class="params-row">
      <label
        class="dropzone"
        :class="{ compact: cellmlLoaded, 'drag-over': dragOver === 'cellml' }"
        data-testid="cellml-drop"
        @dragover.prevent="onDragEnter('cellml')"
        @dragleave="onDragLeave('cellml')"
        @drop="onDragLeave('cellml'); onCellmlDrop($event)"
      >
        <template v-if="cellmlLoaded">
          <i class="pi pi-check" />
          <span class="dz-loaded" data-testid="cellml-loaded">{{ modelName || 'CellML loaded' }}</span>
          <small>drop another to replace</small>
        </template>
        <template v-else>
          <i class="pi pi-file" /> Drop <strong>CellML</strong> (.cellml),
          <strong>Myokit</strong> (.mmt) or <strong>External Python</strong> (.py)
          <small>one file, a non-flattened model with its sisters, or a .omex archive</small>
        </template>
        <input type="file" accept=".cellml,.xml,.mmt,.py,.omex" multiple @change="onCellmlDrop" />
      </label>
      <Button
        :label="startEditLabel"
        :icon="modelId ? 'pi pi-pencil' : 'pi pi-plus'"
        size="small"
        class="params-edit-btn"
        data-testid="start-edit"
        :title="startEditTitle"
        @click="onStartEdit"
      />
    </div>

    <div class="params-row">
      <label
        class="dropzone"
        :class="{ compact: obsLoaded, 'drag-over': dragOver === 'obs' }"
        data-testid="obs-drop"
        @dragover.prevent="onDragEnter('obs')"
        @dragleave="onDragLeave('obs')"
        @drop="onDragLeave('obs'); onObsDrop($event)"
      >
        <template v-if="obsLoaded">
          <i class="pi pi-check" />
          <span class="dz-loaded" data-testid="obs-loaded">{{ loadedObsFilename }}</span>
          <small>drop another to replace</small>
        </template>
        <template v-else>
          <i class="pi pi-chart-line" /> Drop <strong>obs_data.json</strong>
          <small>or click to browse</small>
        </template>
        <input type="file" accept=".json,.omex" @change="onObsDrop" />
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
        :class="{ compact: paramsLoaded, 'drag-over': dragOver === 'params' }"
        data-testid="params-drop"
        @dragover.prevent="onDragEnter('params')"
        @dragleave="onDragLeave('params')"
        @drop="onDragLeave('params'); onParamsDrop($event)"
      >
        <template v-if="paramsLoaded">
          <i class="pi pi-check" />
          <span class="dz-loaded" data-testid="params-loaded">{{ loadedFilename }}</span>
          <small>drop another to replace</small>
        </template>
        <template v-else>
          <i class="pi pi-sliders-h" /> Drop <strong>params_for_id.csv</strong>
          <small>or click to browse</small>
        </template>
        <input type="file" accept=".csv,.omex" @change="onParamsDrop" />
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

    <StartDialog
      v-model:visible="startOpen"
      @select-example="onSelectExample"
    />

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
      :generated-model-format="generatedModelFormat"
      :outputs-dir="outputsDir"
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
      :outputs-dir="outputsDir"
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
/* A file is over the zone: say so, so the drop is visibly going to land (#137). */
.dropzone.drag-over {
  border-color: var(--p-primary-color, #5b9bd5);
  border-style: solid;
  background: color-mix(in srgb, var(--p-primary-color, #5b9bd5) 12%, transparent);
}
/* Loaded: the zone has said what it needed to say, so it shrinks to one line
   naming the file (#137). The Edit button beside it keeps its own size -- the
   shrunken box is the improvement, and stretching the button to fill the row
   just made it a different odd shape. */
.dropzone.compact {
  flex: 0 1 auto;
  padding: 0.35rem 0.6rem;
  text-align: left;
  display: flex;
  align-items: baseline;
  gap: 0.35rem;
  min-width: 0;
  overflow: hidden;
}
.dropzone.compact small {
  margin-top: 0;
}
.dz-loaded {
  font-weight: 600;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
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
