<script setup>
import { ref, computed, watch, onMounted, onUnmounted } from 'vue'
import Message from 'primevue/message'
import InputText from 'primevue/inputtext'
import Button from 'primevue/button'
import Dialog from 'primevue/dialog'
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
  sendToPhlynx,
  peekInbox,
  acceptInbox,
  rejectInbox,
} from '../lib/api'
import { phlynxOpenUrl } from '../lib/examples'

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
  // Where "Edit" sends the study. Blank = the production PhLynx; set from the
  // developer setting so the exchange can be checked against a PhLynx branch.
  phlynxUrl: { type: String, default: '' },
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
  // The current slider values, {qname: value}. Sent to PhLynx as the study's
  // parameter values -- a drag never rewrites the stored model, so the values
  // only exist here until something asks for them (#290).
  paramValues: { type: Object, default: () => ({}) },
})
const emit = defineEmits([
  'model-loaded',
  'obs-data-loaded',
  'params-loaded',
  'update:outputsDir',
  // Read what is already in the outputs directory, rather than only what this
  // session ran (#255). A press, not a watcher: reading a directory is real work
  // and the results found may not match what is loaded.
  'load-outputs',
  'export-pipeline',
  'export-plotting',
])

const error = ref('')
const notice = ref('')
// Everything an import could not do while still succeeding: a part that was
// never found, a check that could not run, a file in a vocabulary on its way
// out. Its own banner, at its own severity -- these used to ride along on the
// end of the blue "Loaded X" line, where a study whose observations had been
// rejected looked exactly like one that had loaded perfectly.
const warnings = ref([])
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
// `.mmt` and EasyML's `.model` are spotted through `converted_from` rather than
// through the model format: the conversion is what makes the loaded model
// CellML, so by the time it is loaded nothing else remembers what the file was.
const sourceExt = computed(() => {
  if (props.modelFormat === 'external_python') return '.py'
  const from = String(props.convertedFrom || '')
  if (/\.mmt$/i.test(from)) return '.mmt'
  if (/\.model$/i.test(from)) return '.model'
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
  return 'Send the current study to PhLynx to edit it there'
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
  // A CellML model goes to PhLynx as the whole study, so the user first says
  // which parameter values should travel with it (#290).
  // Clear any previous result: its links point at the *previous* study's
  // bytes, and offering those again would silently send the wrong thing.
  sendResult.value = null
  sendOpen.value = true
}

// Which values are written into the model on the way out. "As imported" is the
// escape hatch: it sends the model exactly as CUFLynx holds it, which is what
// you want when the sliders are mid-experiment and the point is to edit the
// structure rather than to carry a fit across.
const SEND_SOURCES = [
  { value: 'current', label: 'Current slider values' },
  { value: 'best_fit', label: 'Last calibration best fit' },
  { value: 'as_imported', label: 'As imported (no substitution)' },
]
const sendOpen = ref(false)
const sendSource = ref('current')
const sending = ref(false)

// A best fit is read from the outputs directory, so without one there is
// nowhere to read it from. It can come from a *previous* session's run, which
// is why this is not gated on a calibration having finished in this one.
const bestFitAvailable = computed(() => !!props.outputsDir.trim())

function sendSourceDisabled(value) {
  return value === 'best_fit' && !bestFitAvailable.value
}

// Everything the server needs to build the archive. `values` only matters for
// the "current" source; sending it always keeps the payload one shape.
function sendOptions() {
  return {
    source: sendSource.value,
    values: sendSource.value === 'current' ? props.paramValues : {},
    outputDir: props.outputsDir.trim(),
  }
}

// What the send did, beyond opening the tab: a parameter that could not be
// written is a value PhLynx will not see, and one written outside
// `parameters` / `parameters_global` is one PhLynx will not read back (#287).
// Both are reported rather than left for the user to discover downstream.
function sendNotice(res) {
  const parts = [`Sent ${res.member_count} files to PhLynx.`]
  if (res.unresolved?.length) {
    parts.push(`Not written into the model: ${res.unresolved.join(', ')}.`)
  }
  if (res.outside_parameters?.length) {
    parts.push(
      `Written outside parameters/parameters_global, so PhLynx will not pick ` +
        `them up: ${res.outside_parameters.join(', ')}.`,
    )
  }
  return parts.join(' ')
}

// Above the size guard the archive cannot ride in a URL — browsers truncate a
// long one silently — so it is handed over as a file to drop into PhLynx.
// What a completed send offers the user. Held rather than acted on: the links
// below are real anchors the user clicks, never a scripted `window.open`.
//
// pywebview's macOS backend forwards a new-window request only when the
// navigation type is `WKNavigationTypeLinkActivated` -- a genuine link click.
// A scripted open is `WKNavigationTypeOther`, so it was dropped silently and the
// packaged Mac app reported a successful send while doing nothing (#340). Linux
// happened to work because the GTK backend keys off the `_blank` frame name
// instead. Do not "simplify" this back into a `window.open`.
const sendResult = ref(null)

async function onSendConfirm() {
  error.value = ''
  notice.value = ''
  sendResult.value = null
  sending.value = true
  try {
    const res = await sendToPhlynx(props.modelId, sendOptions())
    sendResult.value = {
      // Built once, here. Computing it on click would be a scripted navigation
      // again, or a mousedown that races the click.
      openUrl: res.too_large ? '' : phlynxOpenUrl(res.base64, props.phlynxUrl),
      downloadUrl: res.download_url,
      filename: res.filename,
      tooLarge: !!res.too_large,
    }
    notice.value = res.too_large
      ? `${sendNotice(res)} The archive is too large to travel in a link ` +
        `(${Math.round(res.bytes / 1024)} kB) — download it below and import it ` +
        `into PhLynx by hand.`
      : sendNotice(res)

    // Try to open it for them, and fall back to the panel when that does not
    // work. `window.open` returns null exactly where it must not be relied on:
    // pywebview's macOS backend answers a scripted open with nil (#340), and a
    // browser popup blocker does the same. Where it returns a window -- Linux's
    // GTK backend, and every browser that allows it -- the send is one click, as
    // it was before.
    //
    // The reason this is an *optimisation over* the links rather than the
    // mechanism is that a non-null return is not proof: it is what the embedder
    // chose to hand back. So the panel below is what makes the send work, and
    // this only spares a click where it demonstrably can. If a future embedder
    // lies about it, the failure is a dialog that closed too eagerly -- not a
    // send that silently did nothing, which is what #340 was.
    if (!res.too_large && window.open(sendResult.value.openUrl, '_blank', 'noopener')) {
      sendOpen.value = false
      return
    }
    // Otherwise the dialog stays open: it is where the links live. Putting them
    // in the notice banner would leave a stale href pointing at a previous
    // study's bytes, and would keep ~1.5 MB of base64 in the DOM after the fact.
  } catch (e) {
    error.value = e?.response?.data?.detail || String(e)
  } finally {
    sending.value = false
  }
}

// The Start dialog chose an example: fetch it and feed it through the normal
// upload flow, so a loaded example is indistinguishable from a drop.
async function onSelectExample(example) {
  error.value = ''
  warnings.value = []
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
  // Same channel as an archive's: a document CA could not be asked about loads
  // either way, and only the banner distinguishes that from a checked one.
  if (summary?.warnings?.length) warnings.value = [...summary.warnings]
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

// --- A study delivered by PhLynx (#287) ------------------------------------
//
// PhLynx posts the archive to `/api/inbox` on localhost, where it is *staged*.
// It is never imported on arrival: CORS stops a page reading our responses, it
// does not stop it sending them, so anything running in the user's browser can
// deliver a study. This dialog is the security control, which is why it names
// the origin the archive came from rather than just asking "load this?".
const pendingStudy = ref(null)
const inboxBusy = ref(false)
let inboxTimer = null

// Polled because the frontend has no push channel and deliberately assumes no
// local backend; giving it one for this would be a bigger change than the
// feature. Paused when the window is hidden -- nobody can answer the dialog then.
const INBOX_POLL_MS = 2000

async function pollInbox() {
  if (document.hidden || pendingStudy.value || inboxBusy.value) return
  try {
    pendingStudy.value = await peekInbox()
  } catch {
    // The server is not up yet, or this build is served without it. A delivery
    // the user never asked for must not produce an error banner.
  }
}

async function onAcceptStudy() {
  inboxBusy.value = true
  error.value = ''
  try {
    const data = await acceptInbox(props.outputsDir)
    applyImportedStudy(data, pendingStudy.value?.filename || 'the delivered study')
    pendingStudy.value = null
  } catch (e) {
    error.value = e?.response?.data?.detail || String(e)
  } finally {
    inboxBusy.value = false
  }
}

async function onRejectStudy() {
  inboxBusy.value = true
  try {
    await rejectInbox()
    pendingStudy.value = null
  } catch (e) {
    error.value = e?.response?.data?.detail || String(e)
  } finally {
    inboxBusy.value = false
  }
}

onMounted(() => {
  inboxTimer = setInterval(pollInbox, INBOX_POLL_MS)
  pollInbox()
})
onUnmounted(() => {
  if (inboxTimer) clearInterval(inboxTimer)
})

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
/**
 * Fan a loaded archive out to the stores, and say what happened.
 *
 * Shared by the drop path and the PhLynx inbox because both receive the *same*
 * response body -- `/api/inbox/accept` runs the same importer `/api/omex/upload`
 * does. A study delivered over localhost has to behave exactly like one that was
 * dropped, and the way to guarantee that is for there to be one function.
 */
function applyImportedStudy(data, label) {
  // The model first: obs_data and params attach to it.
  emit('model-loaded', { ...data, filename: data.model_filename || label })
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
  // archive with a bad obs_data still gave us a model worth having. What it does
  // not do is pass for success: the failure goes in the warning banner, and the
  // notice says only what actually loaded.
  const failed = [data.obs_data, data.params_for_id]
    .filter((p) => p?.error)
    .map((p) => `${p.filename || 'a part of the archive'} was not loaded: ${p.error}`)
  notice.value =
    `Loaded ${label}` + (data.module_config_path ? ' (PhLynx layout kept)' : '')
  warnings.value = [...failed, ...(data.warnings || [])]
}

async function handleOmex(files) {
  const omex = files.find((f) => isOmexName(f.name))
  if (!omex) return false
  error.value = ''
  warnings.value = []
  try {
    const data = await uploadOmex(omex, props.outputsDir)
    applyImportedStudy(data, omex.name)
  } catch (e) {
    error.value = e?.response?.data?.detail || String(e)
  }
  return true
}

// Exposed so a drop anywhere on the page reaches this same handler rather than a
// second copy of it: a study delivered by dropping on the page has to behave
// exactly like one dropped on a box, and the way to guarantee that is for there
// to be one function.
defineExpose({ handleOmex, isOmexName })

function isOmexName(name) {
  return /\.omex$/i.test(String(name || ''))
}

async function onCellmlDrop(event) {
  event.preventDefault?.()
  // Claimed, so the page-wide handler in App.vue leaves it alone. An explicit
  // mark rather than `defaultPrevented`, which anything on the way up may set --
  // and which, being shared, cannot say *who* handled the drop.
  if (event) event.cuflynxHandledByBox = true
  error.value = ''
  warnings.value = []
  // Accept a whole bundle: a non-flattened model plus the sister files it
  // imports. The server picks the main file, resolves imports and flattens to
  // one CellML 2.0 document. A single self-contained file is just a bundle of 1.
  const files = filesFrom(event)
  resetPicker(event)
  if (!files.length) return
  // An archive is taken whole and returns early; what follows is the loose-file
  // path. .mmt and openCARP's EasyML .model are accepted there and converted to
  // CellML server-side (#27) -- both are single files, so neither ever joins a
  // sister-file bundle.
  if (await handleOmex(files)) return
  // `.py` is an *external python* model: a file holding the solver class itself
  // rather than a model description CUFLynx generates a solver from. It travels
  // the same upload route, and the server answers with model_format:
  // "external_python" so the app can lock the backend to it.
  const bad = files.find((f) => !extOk(f.name, ['.cellml', '.xml', '.mmt', '.py', '.model']))
  if (bad) {
    error.value = `Expected a .cellml, .mmt, .model, .py or .omex file, got "${bad.name}"`
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
    // What the import had to decide for itself. An EasyML model always has some:
    // the format leaves the membrane equation out, so something was put in its
    // place, and that is the user's to check rather than the log's to keep.
    warnings.value = [...(data.warnings || [])]
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
  // Claimed, so the page-wide handler in App.vue leaves it alone. An explicit
  // mark rather than `defaultPrevented`, which anything on the way up may set --
  // and which, being shared, cannot say *who* handled the drop.
  if (event) event.cuflynxHandledByBox = true
  error.value = ''
  warnings.value = []
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
  // Claimed, so the page-wide handler in App.vue leaves it alone. An explicit
  // mark rather than `defaultPrevented`, which anything on the way up may set --
  // and which, being shared, cannot say *who* handled the drop.
  if (event) event.cuflynxHandledByBox = true
  error.value = ''
  warnings.value = []
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
          <strong>Myokit</strong> (.mmt), <strong>EasyML</strong> (.model) or
          <strong>External Python</strong> (.py)
          <small>one file, a non-flattened model with its sisters, or a .omex archive</small>
        </template>
        <input type="file" accept=".cellml,.xml,.mmt,.model,.py,.omex" multiple @change="onCellmlDrop" />
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
    <Message
      v-if="warnings.length && !error"
      severity="warn"
      data-testid="import-warning"
      :closable="false"
    >
      <div v-for="w in warnings" :key="w" class="import-warning-line">{{ w }}</div>
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
        <Button
          icon="pi pi-refresh"
          size="small"
          text
          :disabled="!outputsDir"
          title="Reopen the study in this directory: the model, obs_data and params_for_id the run was made from, its calibration, progress, sensitivity, UQ and emulator"
          data-testid="outputs-load"
          @click="emit('load-outputs')"
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

    <Dialog
      v-model:visible="sendOpen"
      modal
      header="Send to PhLynx"
      :style="{ width: '30rem' }"
      data-testid="phlynx-send-dialog"
    >
      <template v-if="!sendResult">
        <p class="send-intro">
          The whole study travels — model, obs_data, params_for_id, and every file
          the archive came with. Which parameter values should be written into the
          model?
        </p>
        <div v-for="opt in SEND_SOURCES" :key="opt.value" class="send-option">
          <label>
            <input
              v-model="sendSource"
              type="radio"
              name="phlynx-send-source"
              :value="opt.value"
              :disabled="sendSourceDisabled(opt.value)"
              :data-testid="`phlynx-source-${opt.value}`"
            />
            <span :class="{ disabled: sendSourceDisabled(opt.value) }">{{ opt.label }}</span>
          </label>
        </div>
        <p v-if="!bestFitAvailable" class="send-hint">
          Set an outputs directory to send a calibration best fit.
        </p>
      </template>

      <!-- The archive is ready. These are REAL anchors on purpose: the user's
           click is what makes the navigation a `WKNavigationTypeLinkActivated`
           one, which is the only kind pywebview's macOS backend forwards to the
           system browser. A scripted `window.open` here is silently dropped in
           the packaged Mac app (#340). -->
      <!-- Only reached when the automatic open did not happen: either the archive
           is too large for a link, or the embedder refused the scripted open. So
           this says what to do, without explaining a corner case on every send. -->
      <template v-else>
        <p class="send-intro">
          <template v-if="sendResult.tooLarge">
            The archive is ready, but too large to travel in a link — download it
            and import it into PhLynx.
          </template>
          <template v-else>The archive is ready.</template>
        </p>
      </template>

      <template #footer>
        <template v-if="!sendResult">
          <Button label="Cancel" severity="secondary" text @click="sendOpen = false" />
          <Button
            label="Send"
            :loading="sending"
            data-testid="phlynx-send-confirm"
            @click="onSendConfirm"
          />
        </template>
        <template v-else>
          <a
            v-if="!sendResult.tooLarge"
            class="send-link"
            :href="sendResult.openUrl"
            target="_blank"
            rel="noopener"
            data-testid="phlynx-open-link"
          >Open in PhLynx</a>
          <a
            class="send-link secondary"
            :href="sendResult.downloadUrl"
            :download="sendResult.filename"
            data-testid="phlynx-download-link"
          >Download the archive</a>
          <Button label="Close" severity="secondary" text @click="sendOpen = false" />
        </template>
      </template>
    </Dialog>

    <Dialog
      :visible="!!pendingStudy"
      modal
      header="A study was sent to CUFLynx"
      :closable="false"
      :style="{ width: '32rem' }"
      data-testid="inbox-dialog"
    >
      <p class="inbox-intro">
        <strong data-testid="inbox-origin">{{ pendingStudy?.origin }}</strong>
        sent <strong>{{ pendingStudy?.filename }}</strong>
        ({{ Math.max(1, Math.round((pendingStudy?.bytes || 0) / 1024)) }} kB).
        Loading it replaces the study you have open.
      </p>
      <ul class="inbox-members" data-testid="inbox-members">
        <li v-for="m in pendingStudy?.members || []" :key="m">{{ m }}</li>
      </ul>
      <template #footer>
        <Button
          label="Discard"
          severity="secondary"
          text
          :disabled="inboxBusy"
          data-testid="inbox-reject"
          @click="onRejectStudy"
        />
        <Button
          label="Load study"
          :loading="inboxBusy"
          data-testid="inbox-accept"
          @click="onAcceptStudy"
        />
      </template>
    </Dialog>

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
.send-intro {
  margin: 0 0 0.75rem;
  font-size: 0.85rem;
  color: var(--text-muted, #666);
}
.send-option {
  padding: 0.15rem 0;
}
.send-option label {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  cursor: pointer;
}
.send-option .disabled {
  opacity: 0.5;
}
.send-hint {
  margin: 0.5rem 0 0;
  font-size: 0.8rem;
  color: var(--text-muted, #666);
}
/* Anchors, not buttons -- see the template. Styled to sit with PrimeVue's
   footer buttons so the difference is invisible to the user. */
.send-link {
  display: inline-block;
  padding: 0.5rem 1rem;
  border-radius: 6px;
  background: var(--p-primary-color, #10b981);
  color: #fff;
  font-size: 0.875rem;
  text-decoration: none;
}
.send-link.secondary {
  background: transparent;
  color: var(--p-primary-color, #10b981);
}
.inbox-intro {
  margin: 0 0 0.5rem;
  font-size: 0.9rem;
}
.inbox-members {
  margin: 0;
  padding-left: 1.1rem;
  max-height: 9rem;
  overflow-y: auto;
  font-size: 0.8rem;
  color: var(--text-muted, #666);
}
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
.import-warning-line + .import-warning-line {
  margin-top: 0.4rem;
}
</style>
