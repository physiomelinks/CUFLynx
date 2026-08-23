<script setup>
import { ref, computed, watch, onMounted } from 'vue'
import ControlPanel from './components/ControlPanel.vue'
import VariableList from './components/VariableList.vue'
import PlotPanel from './components/PlotPanel.vue'
import ImagePanel from './components/ImagePanel.vue'
import FileImport from './components/FileImport.vue'
import StatusBar from './components/StatusBar.vue'
import CalibrationPanel from './components/CalibrationPanel.vue'
import EmulatorPanel from './components/EmulatorPanel.vue'
import ProgressPanel from './components/ProgressPanel.vue'
import SensitivityPanel from './components/SensitivityPanel.vue'
import UQPanel from './components/UQPanel.vue'
import AnalysisPanel from './components/AnalysisPanel.vue'
import CostSensitivityBar from './components/CostSensitivityBar.vue'
import InputNumber from 'primevue/inputnumber'
import InputText from 'primevue/inputtext'
import Button from 'primevue/button'
import Message from 'primevue/message'
import Select from 'primevue/select'
import Checkbox from 'primevue/checkbox'
import Dialog from 'primevue/dialog'
import FileBrowserDialog from './components/FileBrowserDialog.vue'
import TourOverlay from './components/TourOverlay.vue'
import { TOUR_STEPS } from './lib/tourSteps'

import { useModel } from './stores/useModel'
import { useSliders, shouldUseLog } from './stores/useSliders'
import { useSimResult } from './stores/useSimResult'
import { useObsData } from './stores/useObsData'
import { useParamsForId } from './stores/useParamsForId'
import { useCalibration, applyBestParams, expandBestFitParams } from './stores/useCalibration'
import { useEmulator } from './stores/useEmulator'
import { useSensitivity } from './stores/useSensitivity'
import { useUQ } from './stores/useUQ'
import {
  getVariables,
  // No `simulate`: a run is a protocol run now, because the run window is the
  // protocol's and a single run has no other way to say how long it lasts.
  runProtocol,
  costSensitivity,
  costAtParams,
  getCalibrationDefaults,
  getCalibrationPythons,
  getEmulatorDefaults,
  predictEmulator,
  getSensitivityDefaults,
  getUQDefaults,
  getConfig,
  setConfig,
  exportPipeline,
  exportPlotting,
  saveParams,
  loadParams,
  errorMessage,
} from './lib/api'
import {
  overlayItemsFor,
  attachOutputSeries,
  controlledSeries,
  buildExtraPlotCells,
  unitForVars,
  hasMixedUnits,
  withOverlayVars,
  timeUnit,
} from './lib/plot'
import { fmtSigFigs } from './lib/format'
import SearchableSelect from './components/SearchableSelect.vue'
import SaveParamsDialog from './components/SaveParamsDialog.vue'
import { requestNotificationPermission } from './lib/notify'
import { useRunNotifications } from './stores/useRunNotifications'
import { useSavedRuns } from './stores/useSavedRuns'
import { useAxisAlign } from './stores/useAxisAlign'
import {
  solversForFormat,
  defaultSolverFor,
  solverFieldsForMethod,
  defaultSolverInfo,
  nonDifferentiableInUse,
} from './lib/solverConfig'

const model = useModel()
const sliders = useSliders()
const sim = useSimResult()
const obs = useObsData()
const paramsForId = useParamsForId(sliders)
const calib = useCalibration()
const sa = useSensitivity()
const emu = useEmulator()
const uq = useUQ()

// The run window is the obs_data's `protocol_info`, and nothing else can set it.
// It used to be settable in two places -- these two values as top-bar spinners,
// or the protocol -- and a calibration then ran over one window while the live
// cost ran over the other, so the same parameters scored two different costs.
// They are derived here, from the one source, for the payload builders that have
// to state a window explicitly (`runTimes()`, the pipeline export, cost_at_params).
//
// The fallbacks are the API's own defaults and apply only where there is no
// protocol -- a state in which nothing can be run at all (`canRun`), so they
// describe no run the user can see; they are here so an export of a
// protocol-less study is no worse than it was.
const DEFAULT_SIM_TIME = 10
const DEFAULT_PRE_TIME = 0
const simTime = computed(() => obs.protocolSimTime.value ?? DEFAULT_SIM_TIME)
const preTime = computed(() => obs.protocolPreTime.value ?? DEFAULT_PRE_TIME)

/**
 * Whether the model can be run at all: something to run, and a window to run it
 * over. Without a `protocol_info` there is no window, so a request would be a
 * guess presented as a result -- the top bar says what is missing instead.
 */
const canRun = computed(() => model.hasModel.value && obs.hasProtocol.value)

// Where outputs are written. Chosen at startup (see the outputs-dir prompt) and
// remembered across sessions; blank => backend uses a temp dir.
const outputsDir = ref(localStorage.getItem('cuflynx-outputs-dir') || '')
watch(outputsDir, (v) => localStorage.setItem('cuflynx-outputs-dir', v || ''))
// On open, ask the user where outputs should go (the first thing they see).
const outputsSetupOpen = ref(false)

// Raw params_for_id entries (incl. param_type, which the slider store drops) and
// the loaded CSV filename — fed to the params Edit dialog so it can pre-fill rows
// and version the new filename.
const loadedParamsRaw = ref([])
const loadedParamsFilename = ref(null)
// Loaded obs_data filename, for versioning the obs Edit dialog's output. The obs
// content itself already lives in obs.obsData.value.
const loadedObsFilename = ref(null)

// Python interpreter chosen once in the top bar and shared by the Sensitivity,
// Calibration and UQ runs. Blank => backend uses its default interpreter — but
// the packaged desktop app *has* no default (its own executable is the frozen
// bundle, not a Python), so there the choice is required. Hydrated from, and
// persisted back to, /api/config so it survives a restart.
const pythonPath = ref('')
// What the server's "" (default) choice resolves to, so the picker can name it
// and report its MPI support like any other interpreter.
const pythonDefault = ref('')
const pythonBrowserOpen = ref(false)
// True in the packaged desktop app; drives the "Bundled (CUFLynx)" default label.
const packaged = ref(false)

// Whether an MPI launcher is available for the current interpreter. When false,
// a num_cores>1 run would silently drop to a single core server-side, so the
// analysis panels mark the Cores field invalid and block the run until it's set
// back to 1. Tracks the selected interpreter.
const mpiexecAvailable = ref(true)

// circulatory_autogen source directory (top-bar "CA dir"), shared server-side via
// /api/config. Optional since #18 -- the app bundles libCUFLynx -- and set only to
// develop libCUFLynx itself, against the checkout that copy lives in.
const caDir = ref('')
const caExists = ref(true)
const caBrowserOpen = ref(false)

// Backend solver selection (Settings popup). generatedModelFormat is CA's
// model_type; solver + solverInfo are gated by it. solverOpts holds the
// capabilities/schema from /api/config (formats, solvers-per-format, solver_info
// fields, differentiability). adAvailable gates the AD/sp_minimize options.
const solverOpts = ref({})
const generatedModelFormat = ref('cellml')
const solver = ref('CVODE_myokit')
const solverInfo = ref({})

// Myokit JIT-compiles each model, so without a C toolchain every simulation
// fails with an opaque 500. The backend detects this (compiler_check.py) and we
// warn up front — it's the most likely first-run stumble in the packaged app,
// which has no compiler of its own to fall back on.
const cppCompiler = ref({ present: true, hint: '' })
// AADC (Matlogica) availability, so Settings can explain a missing aadc_python
// format rather than leaving a silent gap in the menu (#122).
const aadc = ref(null)
const aadcNotice = computed(() => {
  const a = aadc.value
  if (!a) return '' // older API said nothing; don't invent a status
  if (!a.available) {
    return `aadc_python is not listed: AADC is not installed. ${a.hint || ''}`.trim()
  }
  // The two tiers use different interpreters, so AADC in one and not the other
  // half-works — and which half breaks depends on which one is missing it.
  // Saying "available" flatly is what let a user pick the format and hit
  // "aadc is not installed" on the very next simulation.
  if (!a.in_app) {
    return (
      'aadc_python: AADC is installed in the interpreter used for calibration / ' +
      'sensitivity / UQ, so those runs will work — but not in the app’s own ' +
      'Python, which runs the live plots, so simulating with this format will ' +
      'fail. Install aadc alongside the app to plot with it.'
    )
  }
  if (a.in_analysis_python === false) {
    return (
      'aadc_python: AADC is installed for the live plots, but not in the ' +
      'interpreter chosen for analysis runs — calibration / sensitivity / UQ ' +
      'with this format will fail until it is installed there too.'
    )
  }
  return 'aadc_python is available (AADC found in both interpreters).'
})

// "Python (scipy solve_ivp) or CasADi" — the compiler-free backends, named by the
// server so the UI can't drift from CA's solver schema.
const compilerAlternatives = computed(() =>
  (cppCompiler.value.alternatives ?? []).map((a) => a.label).join(' or '),
)

// Global random seed for analysis runs (Settings popup). null = no seed
// (non-deterministic, the default); an integer makes calibration / sensitivity /
// UQ reproducible. Persisted server-side via /api/config, so it survives a restart.
const seed = ref(null)

// Last value the server told us about. Hydrating pythonPath from /api/config
// triggers the watch below, and without this it would POST the value straight
// back on every load.
let serverPythonPath = ''
// Same guard for the seed: hydrating it must not immediately POST it back.
let serverSeed = null

function applyConfigPayload(c) {
  caDir.value = c.ca_dir
  caExists.value = c.ca_exists
  solverOpts.value = c
  generatedModelFormat.value = c.generated_model_format ?? 'cellml'
  solver.value = c.solver ?? ''
  solverInfo.value = { ...(c.solver_info ?? {}) }
  cppCompiler.value = c.cpp_compiler ?? { present: true, hint: '' }
  aadc.value = c.aadc ?? null
  pythonDefault.value = c.python_default ?? ''
  // The server resolves "" to a concrete interpreter and reports *that*, so
  // taking python_path verbatim silently moved the picker off "Server default"
  // onto the path it resolves to (from source, the serving interpreter). Map it
  // back: an interpreter that *is* the default is the default choice.
  const p = c.python_path ?? ''
  pythonPath.value = p && p === pythonDefault.value ? '' : p
  serverPythonPath = pythonPath.value
  seed.value = c.seed ?? null
  serverSeed = seed.value
  packaged.value = c.packaged ?? false
  mpiexecAvailable.value = c.mpiexec_available ?? true
}

// Persist the interpreter choice server-side (it's what spawns the runners).
// An empty value is a real choice — "reset to the bundled/default interpreter" —
// so it must POST too, not be skipped; the backend treats "" as reset.
watch(pythonPath, async (p) => {
  if (p === serverPythonPath) return
  try {
    serverPythonPath = p
    // Apply the response: it carries the interpreter the server actually
    // settled on and whether a launcher resolves for it, so the MPI/Cores
    // chips reflect the new pick instead of the one loaded at startup.
    applyConfigPayload(await setConfig({ pythonPath: p }))
    // Re-read the interpreters too: the list includes the configured one, so a
    // browsed venv only gains its version / readiness / MPI status once it has
    // been probed as the current choice (#122 follow-up).
    try {
      calibPythons.value = (await getCalibrationPythons()).pythons ?? []
    } catch {
      /* keep the list we have; the chips just stay as they were */
    }
    // Emulation is judged by importing autoemulate *in the chosen interpreter*,
    // so the Emulator tab's availability changes with this pick (#261).
    await refreshEmulatorDefaults()
  } catch {
    /* keep the in-session choice even if persisting fails */
  }
})

// Persist the global random seed server-side. Clearing it (null) is a real choice
// — "no seed, non-deterministic" — so it POSTs '' (the backend's clear signal),
// while a number sets it.
watch(seed, async (s) => {
  if (s === serverSeed) return
  try {
    serverSeed = s
    await setConfig({ seed: s == null ? '' : s })
  } catch {
    /* keep the in-session choice even if persisting fails */
  }
})

async function applyCaDir(dir) {
  try {
    applyConfigPayload(await setConfig(dir))
    // A different circulatory_autogen can have a different emulation schema, or
    // none at all — re-read it rather than leaving the startup answer up.
    await refreshEmulatorDefaults()
  } catch {
    /* leave previous value on error */
  }
}

// Set when the user changes the backend solver selection, so closing Settings
// regenerates + re-runs the model for the new backend (see the settingsOpen watch).
const solverConfigDirty = ref(false)

// Persist the current backend-solver selection and re-read the payload (so
// ad_available + any re-gated options refresh).
async function applyBackendSolver() {
  try {
    applyConfigPayload(
      await setConfig({
        generatedModelFormat: generatedModelFormat.value,
        solver: solver.value,
        solverInfo: solverInfo.value,
      }),
    )
    solverConfigDirty.value = true
  } catch {
    /* leave previous value on error */
  }
}

const solverChoices = computed(() => solversForFormat(solverOpts.value, generatedModelFormat.value))
const solverInfoFields = computed(() =>
  solverFieldsForMethod(solverOpts.value, solver.value, solverInfo.value.method),
)

// The operations actually used by the current obs_data that aren't
// @differentiable — surfaced when casadi_python is selected so the user knows
// exactly which in-use operations block AD (the unused CA registry is ignored).
const nonDifferentiableOps = computed(() =>
  nonDifferentiableInUse(obs.obsData.value, solverOpts.value.differentiable_operations),
)

// AD is valid for casadi_python only, and only when every operation the loaded
// obs_data uses is @differentiable. With no obs_data there's nothing to block it.
const adAvailable = computed(
  () => generatedModelFormat.value === 'casadi_python' && nonDifferentiableOps.value.length === 0,
)

// Gradient sources (FD / AD / FSA) for the current model, from /api/config; the
// calibration panel's gradient menu is populated from this, not hardcoded.
const gradientSources = computed(() => solverOpts.value.gradient_sources ?? [])
// Local sensitivity implements its own gradients — its AD path is CasADi-specific
// — so it gets a list narrowed to what that path can actually do, rather than
// the calibration list, which legitimately includes backends (AADC) whose AD
// only calibration can use.
const localGradientSources = computed(
  () => solverOpts.value.local_gradient_sources ?? gradientSources.value,
)

// Some integrators can't produce their backend's analytic gradient — CasADi AD
// fails with the SUNDIALS adjoint integrators (cvodes/idas); Myokit FSA needs the
// CVODE integrator (CA #298). When the selected integrator is unsuitable, warn and
// name the suitable ones (the analytic source is already dropped from the menus).
const gradientIntegratorWarning = computed(() => {
  const method = solverInfo.value.method
  if (!method) return ''
  const fmt = generatedModelFormat.value
  if (fmt === 'casadi_python') {
    const ok = solverOpts.value.ad_suitable_methods?.[solver.value]
    if (ok && !ok.includes(method))
      return `Automatic differentiation (AD) is not available with the '${method}' integrator ` +
        `(it uses SUNDIALS adjoint sensitivity). For AD, choose one of: ${ok.join(', ')}.`
  } else if (fmt === 'cellml') {
    const ok = solverOpts.value.fsa_suitable_methods?.[solver.value]
    if (ok && !ok.includes(method))
      return `Forward sensitivity (FSA) is not available with the '${method}' integrator. ` +
        `For FSA, choose one of: ${ok.join(', ')}.`
  }
  return ''
})

// Changing the format picks that format's default solver + default solver_info,
// then persists. Changing the solver reseeds solver_info for the new solver. The
// model is (re)generated, cached and run when Settings is closed (see below),
// so the user sees the new backend's outputs immediately on exit.
function onFormatChange(fmt) {
  generatedModelFormat.value = fmt
  solver.value = defaultSolverFor(solverOpts.value, fmt)
  solverInfo.value = defaultSolverInfo(solverOpts.value, solver.value)
  applyBackendSolver()
}
function onSolverChange(s) {
  solver.value = s
  solverInfo.value = defaultSolverInfo(solverOpts.value, s)
  applyBackendSolver()
}

// --- free-form solver_info fields, edited as JSON ---------------------------
// CA types a solver_info field `json` when it is a free-form object it hands to
// the backend untouched — external_python's `user_config`, which is the *whole*
// of an external solver's configuration. There is no schema to build a form
// from, so it is text: the text being typed is kept separately from the parsed
// value, and only a parse writes back, so a half-typed `{"nx":` does not clear
// the setting that is currently in force.
const jsonDrafts = ref({})
const jsonFieldErrors = ref({})

function jsonFieldText(key) {
  if (key in jsonDrafts.value) return jsonDrafts.value[key]
  const v = solverInfo.value[key]
  return v == null ? '' : JSON.stringify(v)
}

function onJsonFieldInput(key, text) {
  jsonDrafts.value = { ...jsonDrafts.value, [key]: text }
  const trimmed = String(text ?? '').trim()
  if (!trimmed) {
    // Empty is a real choice: no user_config at all.
    jsonFieldErrors.value = { ...jsonFieldErrors.value, [key]: '' }
    solverInfo.value[key] = null
    applyBackendSolver()
    return
  }
  try {
    solverInfo.value[key] = JSON.parse(trimmed)
    jsonFieldErrors.value = { ...jsonFieldErrors.value, [key]: '' }
    applyBackendSolver()
  } catch {
    jsonFieldErrors.value = {
      ...jsonFieldErrors.value,
      [key]: 'Not valid JSON yet — the last value that parsed is still in force.',
    }
  }
}

// --- external python models own their backend -------------------------------
// An external python model *is* the solver: there is nothing to generate and no
// choice to make, so the format follows the loaded model rather than the user.
const EXTERNAL_PYTHON = 'external_python'
const isExternalPythonModel = computed(() => model.modelFormat.value === EXTERNAL_PYTHON)

// The formats offered in Settings. `external_python` cannot be generated from a
// CellML model — it only exists because the user dropped a .py — so it is hidden
// unless that is what is loaded, the same rule that keeps unavailable backends
// (OpenCOR, a missing AADC) out of the menu rather than in it and failing.
const formatChoices = computed(() => {
  const all = solverOpts.value.model_formats ?? ['cellml']
  const kept = all.filter((f) =>
    isExternalPythonModel.value ? f === EXTERNAL_PYTHON : f !== EXTERNAL_PYTHON,
  )
  // A backend that predates external python models doesn't list the format; the
  // selector still has to name what the loaded model runs as.
  if (!kept.length && isExternalPythonModel.value) return [EXTERNAL_PYTHON]
  return kept
})

// Bind the backend to whatever model was just loaded: a .py forces
// external_python, and any other model has to leave it, since external_python
// has no model to run once the .py is gone.
function syncFormatToModel() {
  const wanted = isExternalPythonModel.value ? EXTERNAL_PYTHON : null
  if (wanted) {
    if (generatedModelFormat.value !== wanted) onFormatChange(wanted)
  } else if (generatedModelFormat.value === EXTERNAL_PYTHON) {
    onFormatChange(formatChoices.value[0] ?? 'cellml')
  }
}

// Settings popup (CA dir + backend solver + theme).
const settingsOpen = ref(false)

// Closing Settings after a backend-solver change (re)generates + caches the
// model and runs it, so the new backend's outputs show immediately — and any
// later sensitivity/calibration run reuses the cached build instead of
// regenerating. scheduleRun no-ops without a loaded model.
watch(settingsOpen, (open) => {
  if (!open && solverConfigDirty.value) {
    solverConfigDirty.value = false
    scheduleRun()
  }
})

// Colour scheme: toggles the `.cellml-dark` class PrimeVue keys off. Persisted.
const themeOptions = [
  { label: 'Dark', value: 'dark' },
  { label: 'Light', value: 'light' },
]
const theme = ref(localStorage.getItem('cuflynx-theme') || 'dark')
watch(
  theme,
  (t) => {
    document.documentElement.classList.toggle('cellml-dark', t === 'dark')
    localStorage.setItem('cuflynx-theme', t)
  },
  { immediate: true },
)

// Browser notification when a long run (calibration / sensitivity / UQ) finishes
// (#105). Opt-in and default OFF per the issue discussion — it only pays off for
// runs long enough that the user walks away. Client-side preference, so it lives
// in localStorage like the theme rather than in the backend settings store
// (whose PERSISTED_KEYS are machine-level: ca_dir, python_path, …).
const notifyOnFinish = ref(localStorage.getItem('cuflynx-notify-on-finish') === '1')
// Non-empty when the toggle is on but can't actually deliver, so the Settings row
// says why instead of leaving a switch that silently does nothing.
const notifyWarning = ref('')
watch(notifyOnFinish, async (on) => {
  localStorage.setItem('cuflynx-notify-on-finish', on ? '1' : '0')
  notifyWarning.value = ''
  if (!on) return
  // Browsers only honour a permission request from a user gesture — this switch is it.
  const perm = await requestNotificationPermission()
  if (perm === 'unsupported') notifyWarning.value = 'This browser does not support notifications.'
  else if (perm !== 'granted')
    notifyWarning.value = 'Notifications are blocked — allow them for this site in your browser settings.'
})
useRunNotifications(
  [
    { kind: 'calibration', state: calib.state, detail: () => ({ cost: calib.cost.value }) },
    { kind: 'sensitivity', state: sa.state },
    { kind: 'uq', state: uq.state },
  ],
  notifyOnFinish,
)

// Left column tab: 'params' | 'sensitivity' | 'calibration' | 'uq'
const leftTab = ref('params')

// Emulator (CA #333). `emuDefaults` is CA's emulation settings schema;
// `emuSettings` mirrors the panel's live values so a run can carry them.
const emuDefaults = ref({})
const emuSettings = ref({})
// The emulator's predicted features at the current slider values, keyed by CA's
// own feature label. Null when no emulator is in use — which is what makes the
// third reference line appear and disappear with the tick box.
const emulatorFeatureMap = ref(null)
// What those predicted features cost, from the same request and scored by the
// same CA path the displayed cost is (#333). With the tick box on, this is the
// cost a calibration actually minimises, so the gap between it and the solver's
// is the surrogate's error — which is the question "why doesn't the calibration
// cost match the panel?" turns out to be.
const emulatorCost = ref(null)
// The parameters each figure was measured at, serialised. Two costs are only
// comparable at one point, and the prediction and the run are separate requests:
// mid-drag one can land before the other, and a number from the previous slider
// position beside one from the current is exactly the confusion this feature
// exists to remove. Unequal keys mean the em cost simply is not shown yet.
const emulatorCostAt = ref('')
// Why the em cost is missing, when it is. Rendered beside the cost line so the
// absence is diagnosable rather than silent.
const emulatorCostWhy = ref('')
const lastRunAt = ref('')
function paramSignature() {
  return JSON.stringify(sliders.analysisDict.value)
}
// The right-hand import column is resizable via a draggable vertical divider:
// drag it left/right to resize, drag it fully to the right edge to hide the column
// (giving the plots/analysis more room). When hidden, a tab sits on the right edge
// and can be dragged back out (or double-clicked to restore the default width). The
// chosen width persists across reloads.
const RHS_DEFAULT_WIDTH = 300
const RHS_MIN_WIDTH = 200 // narrowest expanded width
const RHS_MAX_WIDTH = 640
const RHS_SNAP_WIDTH = 120 // dragged narrower than this -> collapse to 0
const rhsWidth = ref(Math.max(0, Number(localStorage.getItem('cuflynx-rhs-width') ?? RHS_DEFAULT_WIDTH)))
const rhsCollapsed = computed(() => rhsWidth.value <= 0)
const rhsDragging = ref(false)
watch(rhsWidth, (w) => localStorage.setItem('cuflynx-rhs-width', String(w)))

function _rhsWidthFromEvent(e) {
  // Distance from the pointer to the right edge of the viewport = the column width.
  const w = window.innerWidth - e.clientX
  if (w < RHS_SNAP_WIDTH) return 0 // snap closed
  return Math.min(Math.max(w, RHS_MIN_WIDTH), RHS_MAX_WIDTH)
}
function onRhsDrag(e) {
  rhsWidth.value = _rhsWidthFromEvent(e)
}
function endRhsDrag() {
  rhsDragging.value = false
  window.removeEventListener('mousemove', onRhsDrag)
  window.removeEventListener('mouseup', endRhsDrag)
  document.body.style.userSelect = ''
  document.body.style.cursor = ''
}
function startRhsDrag(e) {
  e.preventDefault()
  rhsDragging.value = true
  window.addEventListener('mousemove', onRhsDrag)
  window.addEventListener('mouseup', endRhsDrag)
  document.body.style.userSelect = 'none'
  document.body.style.cursor = 'col-resize'
}
function restoreRhs() {
  rhsWidth.value = RHS_DEFAULT_WIDTH
}

// The left column (params / sensitivity / calibration / uq) is resizable the same
// way: a draggable divider on its right edge; drag fully left to hide it, drag the
// tab (or double-click) to bring it back. Width persists across reloads.
const LHS_DEFAULT_WIDTH = 320
const LHS_MIN_WIDTH = 240
const LHS_MAX_WIDTH = 680
const LHS_SNAP_WIDTH = 130 // dragged narrower than this -> collapse to 0
const lhsWidth = ref(Math.max(0, Number(localStorage.getItem('cuflynx-lhs-width') ?? LHS_DEFAULT_WIDTH)))
const lhsCollapsed = computed(() => lhsWidth.value <= 0)
const lhsDragging = ref(false)
watch(lhsWidth, (w) => localStorage.setItem('cuflynx-lhs-width', String(w)))

function _lhsWidthFromEvent(e) {
  // The left column is flush to the left edge, so its width is the pointer's x.
  const w = e.clientX
  if (w < LHS_SNAP_WIDTH) return 0
  return Math.min(Math.max(w, LHS_MIN_WIDTH), LHS_MAX_WIDTH)
}
function onLhsDrag(e) {
  lhsWidth.value = _lhsWidthFromEvent(e)
}
function endLhsDrag() {
  lhsDragging.value = false
  window.removeEventListener('mousemove', onLhsDrag)
  window.removeEventListener('mouseup', endLhsDrag)
  document.body.style.userSelect = ''
  document.body.style.cursor = ''
}
function startLhsDrag(e) {
  e.preventDefault()
  lhsDragging.value = true
  window.addEventListener('mousemove', onLhsDrag)
  window.addEventListener('mouseup', endLhsDrag)
  document.body.style.userSelect = 'none'
  document.body.style.cursor = 'col-resize'
}
function restoreLhs() {
  lhsWidth.value = LHS_DEFAULT_WIDTH
}

// Center column tab: 'plots' | 'progress' | 'analysis'
const centerTab = ref('plots')

/* ------------------------------------------------------------------ *
 * Guided tour
 * ------------------------------------------------------------------ */

// TourOverlay only ever *reads* the app: it waits for the user to click the
// real control, so a wrong click can never strand them, and nothing here can
// be driven from a step definition.
const tourOpen = ref(false)
const tourStep = ref(0)
// Only "have they met it", so the button stops asking. Same `cuflynx-` key
// convention as the theme, and read the same way (no try/catch, because
// nothing else here guards localStorage either).
const tourSeen = ref(localStorage.getItem('cuflynx-tour-seen') === '1')

// A stable plain object of *getters*, not a computed and not watchers: the
// overlay reads it on a 200 ms tick, and a reactive wrapper would turn every
// tick into a dependency and a re-render.
//
// What is deliberately NOT here: whether the obs_data / params_for_id /
// custom-funcs dialogs are open. Those are local refs inside FileImport and
// EditObsDataDialog with no exposed method, and lifting them into App.vue to
// serve a tour would invert the ownership this codebase has settled on — for a
// 200 ms-granularity signal the DOM already carries. Those steps read the DOM
// instead (`gone('[data-testid="edit-obs"]')`).
const tourCtx = {
  hasModel: () => model.hasModel.value,
  settingsOpen: () => settingsOpen.value,
  leftTab: () => leftTab.value,
  centerTab: () => centerTab.value,
  hasObsData: () => obs.hasObsData.value,
  hasEmulator: () => emu.trained.value,
  // The one writer, for the "close Settings to carry on" step's Next button
  // (TourOverlay's `onNext`). Setting the ref is exactly what closing the
  // dialog does -- the regenerate-and-re-run is driven by the `settingsOpen`
  // watch, not by the close handler -- so this is the real close, not a
  // second path that skips half of it.
  closeSettings: () => {
    settingsOpen.value = false
  },
  // Closing a dialog whose open flag belongs to a child component (the
  // operation-funcs editor's lives in EditObsDataDialog). Rather than lifting
  // that flag into App just so a tour step can reach it, press the dialog's own
  // close button: the same element the user would click, running the same
  // handler, so there is no second close path to keep in step with the first.
  closeDialog: (selector) => {
    const dialog = document.querySelector(selector)
    if (!dialog) return
    // Several selectors because PrimeVue has spelled this differently across
    // versions; the header's own button is the last-resort match. If none hit,
    // nothing happens and the step's waitFor still ends it when the user
    // closes the dialog themselves.
    const close = dialog.querySelector(
      '[data-pc-name="pcclosebutton"], [data-pc-section="closebutton"],' +
        '.p-dialog-close-button, .p-dialog-header button',
    )
    if (close) close.click()
  },
}

function markTourSeen() {
  tourSeen.value = true
  localStorage.setItem('cuflynx-tour-seen', '1')
}
function startTour() {
  // Resumes where it was left. Skipping is usually "not now" rather than "never
  // again", and restarting at 1 then replayed the whole run -- and, with a model
  // already loaded, silently skipped forward past every step about getting one,
  // landing somewhere in the middle with no way back to what had just been read.
  //
  // Deliberately session state and not localStorage: a *reload* starts over,
  // because resuming at step 19 into an empty app describes controls that are no
  // longer there. `onTourClose` resets it when the run actually finished, so
  // pressing Tutorial again after the end starts from the beginning.
  tourOpen.value = true
  markTourSeen()
}
function onTourClose(reason) {
  tourOpen.value = false
  if (reason === 'finish') tourStep.value = 0
  markTourSeen()
}

// Individual-plot maximize (issue #115): the key of the output plot expanded to
// fill the middle window, or null for the normal grid. Toggled per plot.
const maximizedPlot = ref(null)
function toggleMaximizePlot(key) {
  maximizedPlot.value = maximizedPlot.value === key ? null : key
}
// Self-correcting: if the maximized plot disappears (removed, or the sim
// regenerates cells) fall back to the normal grid instead of showing nothing.
// (Getter is lazy, so referencing plotGroups declared later is safe.)
const effectiveMaximized = computed(() =>
  maximizedPlot.value &&
  plotGroups.value.some((g) => g.cells.some((c) => c.key === maximizedPlot.value))
    ? maximizedPlot.value
    : null,
)

// User-added output plots. Each plot is scoped to one experiment group via
// `groupKey` (e.g. 'exp0', 'data-only', 'single') so the "+ Add plot" button at
// the bottom of an experiment creates a plot for that experiment's run only.
// { id, groupKey, expIdx, qname, xqname, label }
// `xqname` (issue #124) is the variable on the x axis; null/absent means time,
// which is the plain time-series plot.
const extraPlots = ref([])
let nextPlotId = 1

// Model variables that can be plotted as time series (states + algebraic);
// params are constants set via sliders, so they're excluded.
const plottableVariables = computed(() => {
  const v = model.variables.value
  return [...(v.odes ?? []), ...(v.algebraic ?? [])]
})

// CellML units per variable (qname -> units identifier), used to label plot
// axes (#125). The x-axis unit is the model's time variable's unit.
const modelUnits = computed(() => model.variables.value.units ?? {})
// The unit the model declares for its time variable, when it declares one. A
// Myokit .mmt with a bare `time = 0 bind time` declares none, and a CellML
// converted from it reports `dimensionless` — so this is often empty (#27).
// Issue #159: the cost of whatever the sliders currently say, computed by the
// backend from the run it already did. null when it cannot be known -- no
// obs_data, no CA -- which must not read as a perfect fit of zero.
const currentCost = computed(() => sim.cost.value ?? null)
// The same parameters as seen by the emulator (#333). Shown only with the tick
// box on -- with the emulator off there is nothing the calibration would have
// minimised instead -- and only while both figures are of the same point.
const emCostWhy = computed(() => {
  if (!emu.useEmulator.value) return ''
  if (emCost.value !== null) return ''
  return emulatorCostWhy.value
})
const emCost = computed(() => {
  if (!emu.useEmulator.value) return null
  if (emulatorCostAt.value !== lastRunAt.value) return null
  return emulatorCost.value ?? null
})
// Whether the last calibration was run on the emulator. The cost it reports is
// then an *em cost*, which is the whole reason it can disagree with the number
// above the plots -- so the tooltip that explains the gap says so.
const lastCalibrationUsedEmulator = ref(false)
const emCostTitle = computed(() => {
  const base =
    'cost is the solver\'s, em cost the emulator\'s — same parameters, same cost function, ' +
    'different features. With "use the emulator" ticked a calibration minimises the em cost, ' +
    'so a gap between the two is the surrogate\'s error rather than a bug.'
  return lastCalibrationUsedEmulator.value
    ? `${base} The last calibration ran on the emulator, so its reported best cost is an em cost.`
    : base
})
// A snapshot to compare against. The comparison is the point -- a cost alone
// says little, a cost next to the one you started from says whether you are
// winning.
const compareCosts = ref(false)

// A cost spans orders of magnitude between models, so a fixed number of decimal
// places is either noise or nothing. Significant figures read the same either way.
function formatCost(value) {
  if (value == null || !Number.isFinite(value)) return '—'
  const abs = Math.abs(value)
  if (abs !== 0 && (abs < 1e-3 || abs >= 1e5)) return value.toExponential(3)
  return value.toPrecision(4).replace(/\.?0+$/, '')
}

// How many observables actually contributed. An unscored one -- an operand the
// run did not record, an operation that returns a series -- is not a zero, and
// saying "6 of 8" is the difference between a cost and a misleading one.
function scoredCount(cost) {
  return (cost?.items ?? []).filter((i) => i.cost != null).length
}

// The observables the cost is a mean over: weight-0 items are switched off, and
// counting them here would read as a failure to score rather than a choice
// (#181). Falls back to the item count for a payload from an older backend.
function weightedCount(cost) {
  return cost?.n_weighted ?? (cost?.items ?? []).length
}

// "1 of 2 observables", or both coverages where the emulator's cost is shown
// beside the model's: each figure is a mean over the observables it could
// score, and they need not be the same ones -- so the note has to say which
// count belongs to which number rather than appearing to describe both (#333).
const costNote = computed(() => {
  const cost = currentCost.value
  if (!cost) return ''
  const model = `${scoredCount(cost)} of ${weightedCount(cost)} observables`
  const em = emCost.value
  if (!em) return model
  return `cost: ${model}, em cost: ${scoredCount(em)} of ${weightedCount(em)}`
})

// The calibration's own error vectors, offered as the baseline once a run has
// produced them, so "current vs best fit" needs no extra simulation. Reads the
// composable's refs rather than a result object -- that is the shape the
// Analysis panel is already given.
const bestFitBaseline = computed(() => {
  const labels = calib.errorLabels.value ?? []
  const percent = calib.percentError.value
  if (!percent?.length) return null
  const history = calib.costHistory.value ?? []
  const last = history[history.length - 1]
  return {
    label: 'calibration best fit',
    // The best cost of the final generation. Left null, both figures read "—"
    // and the comparison said nothing at all.
    cost: Array.isArray(last) ? last[0] : null,
    items: labels.map((label, i) => ({
      label,
      percent_error: percent[i] ?? null,
      std_error: calib.stdError.value?.[i] ?? null,
    })),
  }
})

// The baseline the Analysis tab compares against: the best fit, once a
// calibration has produced one.
const activeBaseline = computed(() => bestFitBaseline.value)

// ---------------------------------------------------------------------------
// Cost sensitivities (#188)
// ---------------------------------------------------------------------------
// The cost says the parameters are worth 36.8; this says which of them the 36.8
// is about. Off by default and remembered, because it is not free: 2M+1
// simulations for M parameters, every time the parameters settle. A user who
// never asks for it pays nothing, which is the only way this can sit on the
// panel a slider drag redraws.
const costSensOn = ref(localStorage.getItem('cuflynx-cost-sensitivity') === '1')
const costSens = ref(null)
// 'ready' | 'running' | 'error'; staleness is derived, not stored.
const costSensStatus = ref('ready')
const costSensError = ref('')
// The parameters the shown numbers were measured at, serialised for comparison.
const costSensAt = ref('')
let costSensTimer = null
let costSensAbort = null
// Discards a reply whose request has been superseded: the run that answers last
// is not necessarily the run that was asked for last.
let costSensToken = 0

// A gradient of the displayed cost is a run like any other -- 2M+1 of them --
// so it needs the same window the run needs (`canRun`), not merely an obs_data.
const costSensAvailable = computed(
  () => canRun.value && sliders.count.value > 0,
)
const costSensState = computed(() => {
  if (costSensStatus.value !== 'ready') return costSensStatus.value
  if (!costSens.value) return 'ready'
  return costSensAt.value === paramKey() ? 'ready' : 'stale'
})

function paramKey() {
  return JSON.stringify(sliders.paramDict.value)
}

/** The slider ranges, which matter only where a parameter sits at exactly 0.
 *  Free sliders only: a modifier's bounds are θ's and travel in the modifiers
 *  block, not here. */
function costSensBounds() {
  const out = {}
  for (const [qname, s] of Object.entries(sliders.sliders)) {
    if (s.kind !== 'modifier') out[qname] = [s.min, s.max]
  }
  return out
}

/** The parameters the cost-sensitivity bars difference individually: free
 *  sliders. Modifier sliders are differenced in θ via the modifiers block. */
function costSensParamNames() {
  return sliders.order.value.filter((q) => sliders.sliders[q]?.kind !== 'modifier')
}

/**
 * The outputs a live run asks for, in one place: the sensitivity runs must
 * request exactly these or they would score a different set of observables, and
 * the gradient would belong to a number other than the one on the panel.
 */
function liveOutputs() {
  return [
    ...new Set([
      ...obs.plotVariables.value.map((v) => v.qname),
      ...extraOutputNames.value,
    ]),
  ]
}

/**
 * Re-measure once the parameters have settled — never during the drag itself.
 *
 * Dragging is a pixel-rate event, and even one solve per frame would queue work
 * behind the plot the user is watching (and far more than one where the gradient
 * has to be differenced). So the plot run is debounced as before and this waits
 * again after it, with any in-flight computation aborted the moment a new drag
 * starts. Between the two the last measured ranking stays on screen, dimmed and
 * labelled stale — briefly, since this runs after every completed live run.
 */
function scheduleCostSensitivity() {
  clearTimeout(costSensTimer)
  if (!costSensOn.value || !model.hasModel.value || !costSensAvailable.value) return
  costSensTimer = setTimeout(runCostSensitivity, 600)
}

function cancelCostSensitivity() {
  clearTimeout(costSensTimer)
  costSensToken += 1
  costSensAbort?.abort()
  costSensAbort = null
  if (costSensStatus.value === 'running') costSensStatus.value = 'ready'
}

async function runCostSensitivity() {
  if (!costSensOn.value || !model.hasModel.value || !costSensAvailable.value) return
  const params = { ...sliders.paramDict.value }
  const key = paramKey()
  const token = ++costSensToken
  const controller = new AbortController()
  costSensAbort = controller
  costSensStatus.value = 'running'
  costSensError.value = ''
  try {
    // No sim_time/pre_time: the obs_data paths do not send them either, and a
    // different run length would be a different cost.
    const data = await costSensitivity(model.modelId.value, params, {
      paramNames: costSensParamNames(),
      bounds: costSensBounds(),
      modifiers: sliders.modifierSpecs.value,
      outputs: liveOutputs(),
      outputsDir: outputsDir.value.trim() || undefined,
      signal: controller.signal,
    })
    if (token !== costSensToken) return
    costSens.value = data
    costSensAt.value = key
    costSensStatus.value = 'ready'
  } catch (e) {
    // An abort is this app superseding itself, not a failure to report.
    if (token !== costSensToken || controller.signal.aborted) return
    costSensStatus.value = 'error'
    costSensError.value = errorMessage(e)
  } finally {
    if (costSensAbort === controller) costSensAbort = null
  }
}

watch(costSensOn, (on) => {
  localStorage.setItem('cuflynx-cost-sensitivity', on ? '1' : '0')
  if (on) {
    scheduleCostSensitivity()
  } else {
    cancelCostSensitivity()
    costSens.value = null
    costSensError.value = ''
  }
})

// A new model or new obs_data is a different cost; the old ranking is about
// neither of them.
watch([() => model.modelId.value, () => obs.obsData.value], () => {
  cancelCostSensitivity()
  costSens.value = null
  costSensError.value = ''
})

const modelTimeUnit = computed(() => timeUnit(modelUnits.value))
// User-supplied time unit, for a model that does not state its own. Persisted,
// because it is a property of the model the user is working on rather than a
// per-session choice. Guessing (ms for electrophysiology, s for haemodynamics)
// would be putting a unit on the user's model that the model never claimed.
const timeUnitOverride = ref(localStorage.getItem('cuflynx-time-unit') || '')
watch(timeUnitOverride, (v) => localStorage.setItem('cuflynx-time-unit', (v || '').trim()))
// What the model says, else what the user told us.
const timeUnitLabel = computed(
  () => modelTimeUnit.value || timeUnitOverride.value.trim(),
)

// The run window, said out loud beside the experiment count: how long the study
// simulates for, and how much of that is warm-up. The count alone described the
// protocol's shape but never its length, which is the number the removed t₁/pre
// spinners used to show — so it is stated here instead of nowhere.
const runWindowLabel = computed(() => {
  const unit = timeUnitLabel.value ? ` ${timeUnitLabel.value}` : ''
  const pre = preTime.value
    ? ` (+ ${fmtSigFigs(preTime.value, 4)}${unit} pre)`
    : ''
  return `${fmtSigFigs(simTime.value, 4)}${unit}${pre}`
})

const runWindowTitle =
  'The run window comes from the obs_data protocol_info: the total of its ' +
  'sim_times, after the total of its pre_times. Edit the protocol to change it.'

// Variables the user has overlaid onto an existing plot (issue #196), as
// cellKey -> [qname]. Keyed by the *cell* rather than by an id of its own
// because every kind of plot can take an overlay — an obs-derived cell, the
// combined manual run, an added "Add plot" cell — and their keys are already
// stable across runs (`exp0:heart/P_lv`, `single`, `extra:3`). That stability
// is the point: a re-run must not quietly drop what the user chose to compare,
// and only a change of model or obs_data (below) invalidates the keys.
const plotVars = ref({})

// Extra-plot qnames to append to a run's requested outputs so the chosen
// variables come back from the engine — the x variable of a phase-plane plot
// included, else the engine never returns that series. Overlaid variables
// (#196) are requested the same way, for the same reason.
const extraOutputNames = computed(() => [
  ...new Set([
    ...extraPlots.value.flatMap((p) => (p.xqname ? [p.qname, p.xqname] : [p.qname])),
    ...Object.values(plotVars.value).flat(),
  ]),
])

// Add-plot dialog state. `addPlotXVar` is the x axis: 'time' (the default) for a
// time series, or another variable for a phase-plane plot (issue #124).
const addPlotOpen = ref(false)
const addPlotTarget = ref({ groupKey: null, expIdx: 0, label: '' })
const addPlotVar = ref(null)
const addPlotXVar = ref('time')

// x-axis choices: time plus every plottable variable (a variable already on a
// y axis is still a valid x axis).
const addPlotXChoices = computed(() => [
  { label: 'time', value: 'time' },
  ...plottableVariables.value.map((q) => ({ label: q, value: q })),
])

// Variables offered in the dialog: plottable vars not already shown in the
// target group (neither an obs-derived nor an already-added plot). "Already
// shown" is per x axis — the same y against a different x is a different plot.
const addPlotChoices = computed(() => {
  const key = addPlotTarget.value.groupKey
  const xq = addPlotXVar.value || 'time'
  const taken = new Set(
    extraPlots.value
      .filter((p) => p.groupKey === key && (p.xqname || 'time') === xq)
      .map((p) => p.qname),
  )
  if (xq === 'time' && (key === 'data-only' || (key && key.startsWith('exp')))) {
    // Obs-derived cells are time series, so they only collide on the time axis.
    for (const v of obs.plotVariables.value) taken.add(v.qname)
  }
  return plottableVariables.value
    .filter((q) => !taken.has(q))
    .map((q) => ({ label: q, value: q }))
})

function openAddPlot(group) {
  addPlotTarget.value = {
    groupKey: group.key,
    expIdx: group.expIdx ?? 0,
    label: group.label || '',
  }
  addPlotVar.value = null
  addPlotXVar.value = 'time'
  addPlotOpen.value = true
}

function confirmAddPlot() {
  const qname = addPlotVar.value
  if (!qname) return
  const xqname = addPlotXVar.value && addPlotXVar.value !== 'time' ? addPlotXVar.value : null
  extraPlots.value.push({
    id: nextPlotId++,
    groupKey: addPlotTarget.value.groupKey,
    expIdx: addPlotTarget.value.expIdx,
    qname,
    xqname,
    // The title names the y variable only — it reads as the y-axis label, and
    // the x variable is named under the x axis (#124/#125).
    label: qname,
  })
  addPlotOpen.value = false
  // Re-run so the newly requested variable is fetched for this experiment.
  runSimulation()
}

function removeExtraPlot(id) {
  extraPlots.value = extraPlots.value.filter((p) => p.id !== id)
}

// Swap a phase-plane plot's axes (issue #124): the y variable goes on the x
// axis and vice versa. Both series are already requested from the engine, so
// this is a relabelling — no re-run.
function switchExtraPlotAxes(id) {
  const p = extraPlots.value.find((e) => e.id === id)
  if (!p?.xqname) return
  const qname = p.xqname
  p.xqname = p.qname
  p.qname = qname
  p.label = qname
}

// Extra-plot cells for a group, each a single-variable plot built from that
// group's own simulation outputs.
function extraCellsFor(groupKey, time, outputs, expIdx = null) {
  return buildExtraPlotCells(
    extraPlots.value, groupKey, time, outputs, modelUnits.value,
  ).map((cell) => ({
    // Single-variable cells, so each takes the shown runs' trace for its own
    // variable (#126). A phase-plane cell also asks for the run's own x series,
    // so it is drawn against that rather than dropped (#150).
    ...cell,
    savedSeries: savedRuns.seriesFor(cell.qname, expIdx, cell.xqname),
  }))
}

/**
 * Apply the user's overlaid variables to a plot cell (issue #196).
 *
 * Every cell goes through this on its way out of `plotGroups`, so the "+"
 * affordance means the same thing on an obs-derived plot as on an added one.
 * The one exception is a `controlled` cell: it draws a params_to_change input
 * on its own synthesised time base, not the run's, so a model variable dropped
 * onto it would be plotted against the wrong x.
 *
 * An overlaid variable brings its saved-run traces (#126) with it — otherwise
 * ticking a saved run would redraw only half of a two-variable comparison.
 */
function withUserVars(cell, outputs, expIdx = null) {
  if (cell.controlled) return cell
  const added = plotVars.value[cell.key]
  const merged = withOverlayVars(cell, added, outputs, modelUnits.value)
  if (merged === cell) return { ...cell, addable: true }
  return {
    ...merged,
    addable: true,
    savedSeries: [
      ...(cell.savedSeries ?? []),
      ...merged.overlayVars.flatMap((q) => savedRuns.seriesFor(q, expIdx, cell.xqname)),
    ],
  }
}

// --- add/remove a variable on a plot (#196) --------------------------------
// One dialog does both: a picker adds, and the list of what is already drawn
// removes. Splitting them would leave "how do I take that line off again?"
// answered only by deleting the whole plot.
const plotVarsOpen = ref(false)
const plotVarsKey = ref(null)
const plotVarsPick = ref('')

// Read back from `plotGroups` rather than captured at open time, so the dialog
// keeps up with what it is itself changing. (Lazy getter, so referencing
// plotGroups — declared later — is safe.)
const plotVarsCell = computed(
  () =>
    plotGroups.value.flatMap((g) => g.cells).find((c) => c.key === plotVarsKey.value) ??
    null,
)
// Everything drawn on the plot, the cell's own variables first.
const plotVarsDrawn = computed(() =>
  Object.keys(plotVarsCell.value?.simResult?.outputs ?? {}),
)
const plotVarsAdded = computed(() => plotVars.value[plotVarsKey.value] ?? [])
const plotVarsChoices = computed(() =>
  plottableVariables.value.filter((q) => !plotVarsDrawn.value.includes(q)),
)
// Warn *before* the overlay is drawn, naming both units: after the fact the
// axis simply loses its label, which is a much quieter signal than it deserves.
const plotVarsUnitWarning = computed(() => {
  const q = plotVarsPick.value
  if (!q) return ''
  if (!hasMixedUnits(modelUnits.value, [...plotVarsDrawn.value, q])) return ''
  const theirs = modelUnits.value[q] || 'unknown'
  const ours = unitForVars(modelUnits.value, plotVarsDrawn.value) || 'mixed'
  return `${q} is in ${theirs}, this plot is in ${ours}. They will share one axis and the unit label is dropped.`
})

function openPlotVars(cell) {
  plotVarsKey.value = cell.key
  plotVarsPick.value = ''
  plotVarsOpen.value = true
}

function confirmPlotVar() {
  const qname = plotVarsPick.value
  if (!qname || !plotVarsKey.value) return
  plotVars.value = {
    ...plotVars.value,
    [plotVarsKey.value]: [...(plotVars.value[plotVarsKey.value] ?? []), qname],
  }
  plotVarsPick.value = ''
  // Re-run so the newly requested variable is fetched, exactly as adding a plot
  // does — until then the cell would draw an empty series.
  runSimulation()
}

// Removing needs no re-run: the series is already in hand, it just stops being
// drawn. The next run stops asking for it.
function removePlotVar(qname) {
  const key = plotVarsKey.value
  if (!key) return
  const rest = (plotVars.value[key] ?? []).filter((q) => q !== qname)
  const next = { ...plotVars.value }
  if (rest.length) next[key] = rest
  else delete next[key]
  plotVars.value = next
}

// Calibration / sensitivity
const calibDefaults = ref({})
const calibPythons = ref([])
const saDefaults = ref({})
const uqDefaults = ref({})
onMounted(() => {
  // Ask where outputs should go, and mean "first": this line used to sit at the
  // *end* of six sequential round trips below, one of which walks the filesystem
  // looking for Python interpreters. So the one thing the user is actually
  // waiting on arrived last, after everything they could not see. Nothing below
  // feeds this dialog -- it opens at $HOME and only ever *writes* outputsDir --
  // so it does not have to wait for any of it.
  outputsSetupOpen.value = true

  // ...and the six are independent of each other too, so they go out together
  // rather than in a chain: the wait is now the slowest one, not the sum. Each
  // keeps its own failure handling, because a backend that is not up yet should
  // leave one panel on its built-in defaults rather than abandoning the rest.
  const settle = (promise, apply) => promise.then(apply).catch(() => {})

  settle(getCalibrationDefaults(), (v) => (calibDefaults.value = v))
  settle(getSensitivityDefaults(), (v) => (saDefaults.value = v))
  settle(getUQDefaults(), (v) => (uqDefaults.value = v))
  settle(getCalibrationPythons(), (v) => (calibPythons.value = v.pythons ?? []))
  settle(getConfig(), applyConfigPayload)
  refreshEmulatorDefaults()
})

const pythonOptions = computed(() => {
  // Blank = the server default. In the packaged app that's the bundled
  // interpreter (analysis runs in-app, no external Python needed); from source
  // it's the serving interpreter. Switching to a discovered/browsed Python is for
  // pointing at a local circulatory_autogen checkout during CA development.
  // Name the interpreter the default resolves to, and mark its MPI support the
  // same way as the rest: "Server default" alone said nothing about either, so
  // choosing it looked like losing MPI even when it is the same interpreter.
  const defaultProbe = calibPythons.value.find((x) => x.path === pythonDefault.value)
  const defaultLabel = packaged.value
    ? 'Bundled (CUFLynx) — runs analysis in-app'
    : 'Server default' +
      (pythonDefault.value ? ` — ${pythonDefault.value}` : '') +
      (defaultProbe?.mpi ? ' — MPI ✓' : '')
  const opts = [
    { label: defaultLabel, value: '' },
    // "MPI ✓" marks the interpreters whose own environment ships an MPI
    // launcher, i.e. the ones that un-gate Cores > 1 — otherwise nothing tells
    // the user which interpreter to pick for a multi-core run.
    ...calibPythons.value.map((p) => ({
      label:
        `Python ${p.version} — ${p.path}` +
        (p.mpi ? ' — MPI ✓' : '') +
        (p.ready ? '' : ` (missing: ${(p.missing || []).join(', ')})`),
      value: p.path,
    })),
  ]
  // Show a browsed interpreter that isn't among the auto-discovered ones.
  if (pythonPath.value && !opts.some((o) => o.value === pythonPath.value)) {
    opts.push({ label: `Custom — ${pythonPath.value}`, value: pythonPath.value })
  }
  return opts
})

// The interpreter the runners will actually use: an explicit pick, else
// whatever the server's default resolves to. The default is a real interpreter,
// so it gets the same probe-backed chips as any other choice.
const selectedPython = computed(() =>
  calibPythons.value.find((x) => x.path === (pythonPath.value || pythonDefault.value)),
)

// Missing required deps for the chosen interpreter (shown as a warning chip).
const pythonNotReady = computed(() => {
  const p = selectedPython.value
  return p && !p.ready ? p.missing : null
})

// The chosen interpreter's MPI launcher status, so the bar says whether this
// pick enables Cores > 1 (and, in the tooltip, which launcher it would use).
// Null only for a browsed path we never probed — say nothing rather than imply
// "no MPI".
//
// Three states, not two: a launcher found on PATH rather than in the
// interpreter's own environment still runs (resolve_mpiexec falls back to it),
// so calling that "MPI ✗ / unavailable" contradicts a machine where multi-core
// demonstrably works. It is flagged separately instead, because it is the
// configuration that can mismatch mpi4py's runtime.
const pythonMpi = computed(() => {
  const p = selectedPython.value
  if (!p) return null
  if (p.mpi) {
    return {
      mpi: true,
      label: 'MPI ✓',
      title: `MPI launcher in this environment (${p.mpiexec}): Cores > 1 available`,
    }
  }
  if (p.mpiexec) {
    return {
      mpi: true,
      label: 'MPI (system)',
      title:
        `No launcher in this interpreter's own environment; runs would use ${p.mpiexec} ` +
        'from PATH. Cores > 1 works when that MPI matches the mpi4py this ' +
        'interpreter imports — install mpi4py + an MPI into the environment itself ' +
        'to be sure.',
    }
  }
  return {
    mpi: false,
    label: 'MPI ✗',
    title: 'No MPI launcher in this environment or on PATH: Cores > 1 unavailable',
  }
})

// Keep the top bar compact by showing only the tail of a long path.
function pathTail(value) {
  const s = String(value || '')
  return s.length > 20 ? '…' + s.slice(-20) : s
}

// Collapsed Python display (the full label still shows in the dropdown).
function shortPython(value) {
  return value ? pathTail(value) : 'Server default'
}

const canCalibrate = computed(
  () =>
    model.hasModel.value &&
    obs.hasObsData.value &&
    paramsForId.importedKeys.value.length > 0,
)

// qname -> plotting/LaTeX name, for the Analysis-tab heatmap row labels.
const paramLabels = computed(() => {
  const out = {}
  for (const [qname, spec] of Object.entries(paramsForId.paramSpecs.value || {})) {
    out[qname] = spec.name_for_plotting ?? qname
  }
  return out
})

// The protocol's window travels with every analysis run. The runner takes its
// timing from the obs_data's protocol_info (#13), so these are the same numbers
// stated explicitly — sent because a runner handed neither silently fell back to
// sim_time=2.0 while the Output-plots cost ran at the top bar's t₁, and the same
// best-fit parameters then scored two different costs. Now that both come from
// protocol_info the two cannot disagree.
function runTimes() {
  return { sim_time: simTime.value, pre_time: preTime.value }
}

function onRunCalibration(settings) {
  // Remembered because the cost it reports is only interpretable with it: a
  // calibration on the emulator minimises the em cost, and the Output plots'
  // cost is the solver's (#333).
  lastCalibrationUsedEmulator.value = emu.useEmulator.value
  calib.start(
    model.modelId.value,
    {
      ...settings,
      ...runTimes(),
      python_path: pythonPath.value,
      config_outputs_dir: outputsDir.value.trim() || undefined,
      // Evaluate the trained emulator instead of the solver (Emulator tab).
      use_emulator: emu.useEmulator.value,
    },
    // Live slider values, so gradient descent can start from the user's current
    // parameter values when "start from current" is enabled (#65). The θ-aware
    // analysisDict, not paramDict: a modifier's start point is θ at its anchor
    // (CA samples θ there), never an expanded physical value (#208).
    { ...sliders.analysisDict.value },
  )
}

// Live calibration settings, mirrored from the Calibration panel so the
// sensitivity tab's "run calibration first" can reuse the same configuration.
const calibSettings = ref({})
// Live sensitivity / UQ settings, mirrored so the pipeline export can capture
// the current configuration without needing a run first.
const saSettings = ref({})
const uqSettings = ref({})

// ----- Pipeline export ----------------------------------------------------
const exportPromptOpen = ref(false)
const exportNotice = ref('')
const CUFLYNX_ISSUES_URL = 'https://github.com/physiomelinks/CUFLynx/issues/new'

// Which stages the exported pipeline should run, from the current UI state.
const exportEnabled = computed(() => ({
  do_simulation: true,
  do_calibration: canCalibrate.value,
  do_sensitivity: canCalibrate.value,
  do_mcmc: canCalibrate.value && uqSettings.value.method === 'mcmc',
  do_ia: canCalibrate.value && uqSettings.value.method === 'laplace',
}))

function exportPayload() {
  return {
    model_id: model.modelId.value,
    file_prefix: model.filePrefix.value || undefined,
    sim_time: simTime.value,
    pre_time: preTime.value,
    calibration: { ...calibSettings.value },
    sensitivity: { ...saSettings.value },
    uq: { ...uqSettings.value },
    enabled: exportEnabled.value,
    config_outputs_dir: outputsDir.value.trim() || undefined,
  }
}

// Clicking "export pipeline" first prompts the user to file an issue for gaps.
function onExportPipeline() {
  if (!model.hasModel.value) return
  exportPromptOpen.value = true
}

async function confirmExportPipeline() {
  exportPromptOpen.value = false
  try {
    const res = await exportPipeline(exportPayload())
    exportNotice.value = `Exported pipeline to ${res.export_dir}`
  } catch (e) {
    exportNotice.value = `Export failed: ${errorMessage(e)}`
  }
}

async function onExportPlotting() {
  if (!model.hasModel.value) return
  try {
    const res = await exportPlotting({
      config_outputs_dir: outputsDir.value.trim() || undefined,
      // Names the panels after the loaded obs_data, so the script is something
      // you edit rather than something you read around (#144).
      model_id: model.modelId.value || undefined,
    })
    exportNotice.value = `Exported plotting script to ${res.path}`
  } catch (e) {
    exportNotice.value = `Export failed: ${errorMessage(e)}`
  }
}

// Sensitivity reuses the same prerequisites as calibration (model + obs + params).
// When 'run calibration first' is set, fold in the calibration panel's GA
// settings rather than duplicating those controls in the sensitivity panel.
function onRunSensitivity(settings) {
  const calibFirst = settings.run_calibration_first
    ? {
        param_id_method: calibSettings.value.param_id_method,
        num_calls_to_function: calibSettings.value.num_calls_to_function,
        max_patience: calibSettings.value.max_patience,
        cost_convergence: calibSettings.value.cost_convergence,
      }
    : {}
  sa.start(
    model.modelId.value,
    {
      ...settings,
      ...calibFirst,
      ...runTimes(),
      python_path: pythonPath.value,
      config_outputs_dir: outputsDir.value.trim() || undefined,
      // Evaluate the trained emulator instead of the solver (Emulator tab).
      use_emulator: emu.useEmulator.value,
    },
    // Live slider values, so local SA with nominal="current" linearises about the
    // user's current parameter values rather than the model defaults (#65). The
    // θ-aware analysisDict: a modifier's nominal is θ at its anchor (#208).
    { ...sliders.analysisDict.value },
  )
}

// When a sensitivity run finishes, surface the heatmap automatically.
watch(
  () => sa.state.value,
  (state) => {
    if (state === 'done') centerTab.value = 'analysis'
  },
)

// UQ reuses the same prerequisites as calibration (model + obs + params).
function onRunUQ(settings) {
  uq.start(model.modelId.value, {
    ...settings,
    ...runTimes(),
    python_path: pythonPath.value,
    config_outputs_dir: outputsDir.value.trim() || undefined,
    use_emulator: emu.useEmulator.value,
  })
}

function onTrainEmulator(settings) {
  emu.train(model.modelId.value, {
    ...settings,
    // The SAME window every other analysis uses. CA's staleness fingerprint covers
    // protocol_info's sim_times, so an emulator trained on the runner's fallback and
    // then used by a calibration running over a different window is rejected as
    // stale -- "the model, parameter bounds, obs_data operations or protocol
    // differ". One source for the window is what keeps every side equal.
    ...runTimes(),
    python_path: pythonPath.value,
    config_outputs_dir: outputsDir.value.trim() || undefined,
  })
}

/**
 * Refresh the emulator's predicted features for the current slider values.
 *
 * Only while the tick box is on: the prediction costs a round trip to the
 * simulation worker, and with no emulator in use there is nothing to compare.
 * A failure clears the overlay rather than surfacing an error — the model's own
 * trace is still correct, and a missing dotted line is the honest way to say the
 * surrogate could not answer here.
 */
async function refreshEmulatorFeatures() {
  if (!emu.useEmulator.value || !model.modelId.value) {
    emulatorFeatureMap.value = null
    emulatorCost.value = null
    emulatorCostWhy.value = ''
    emulatorCostAt.value = ''
    return
  }
  // Stamped before the request, not after: what came back describes the
  // parameters it was asked about, whatever they are by the time it arrives.
  const at = paramSignature()
  try {
    const res = await predictEmulator(
      model.modelId.value,
      { ...sliders.analysisDict.value },
      { config_outputs_dir: outputsDir.value.trim() || undefined },
    )
    const map = {}
    ;(res.labels ?? []).forEach((label, i) => {
      map[label] = res.values?.[i]
    })
    emulatorFeatureMap.value = map
    // Comes back with the prediction, so no second round trip on a slider
    // settle. Null on a backend that predates it, or an obs_data CA cannot
    // score — the line then shows the one cost it always showed.
    emulatorCost.value = res.cost ?? null
    // Why there is no number, when there is none. The dotted overlay still draws from
    // the same response, so a silent null left lines on the plot with nothing beside
    // them and nothing to act on.
    emulatorCostWhy.value = res.cost ? '' : (res.cost_unavailable ?? '')
    emulatorCostAt.value = at
  } catch (e) {
    emulatorFeatureMap.value = null
    emulatorCost.value = null
    emulatorCostWhy.value = errorMessage(e)
    emulatorCostAt.value = ''
  }
}

// Follow the sliders, and the tick box itself, so the comparison is live while a
// parameter is dragged — which is the only way to see where the surrogate starts
// disagreeing with the model.
watch(
  () => [emu.useEmulator.value, JSON.stringify(sliders.analysisDict.value)],
  () => {
    refreshEmulatorFeatures()
  },
)

/**
 * Re-read CA's emulation schema, and with it whether emulation is possible at
 * all. It is not a constant of the session: `available` is answered by probing
 * the interpreter chosen in Settings, so switching interpreter or CA directory
 * can turn emulation on or off and the tab has to follow without a restart.
 */
async function refreshEmulatorDefaults() {
  try {
    emuDefaults.value = await getEmulatorDefaults()
  } catch {
    /* backend not up yet; panel falls back to built-in defaults */
  }
}

/**
 * Whether emulation can be done at all with the current interpreter and CA.
 *
 * `available: false` is the backend saying the interpreter that would train
 * cannot `import autoemulate` (it also sends `unavailable_reason`); `supported:
 * false` is the older "this circulatory_autogen has no emulators" case. Either
 * way there is nothing to configure, so the tab says so instead of degrading its
 * form into controls that cannot work (#261). A CA that sends neither key reads
 * as available, which is the behaviour that predates this.
 */
const emuUnavailable = computed(
  () => emuDefaults.value?.available === false || emuDefaults.value?.supported === false,
)
const emuUnavailableTitle = computed(() =>
  emuDefaults.value?.unavailable_reason ||
  'Emulation is unavailable — open the Emulator tab for how to enable it.',
)

// ...and nothing downstream may keep evaluating a surrogate that cannot be
// loaded. The tick box is hidden while unavailable, so leaving the flag on would
// be a setting with no visible control: sensitivity, calibration and UQ would
// keep asking for an emulator and fail inside CA. Force it off; the panel says
// that the analyses are back on the solver.
watch(
  emuUnavailable,
  (unavailable) => {
    if (unavailable) emu.useEmulator.value = false
  },
  { immediate: true },
)

// A newly trained emulator becomes the one the overlay uses.
watch(
  () => emu.state.value,
  (state) => {
    if (state === 'done') refreshEmulatorFeatures()
  },
)

// ---------------------------------------------------------------------------
// The calibration best fit, scored by the model and by the emulator (#333)
// ---------------------------------------------------------------------------
// The Analysis tab's per-observable errors come from the calibration's own
// vectors, and a calibration on the emulator wrote the *emulator's*. Which of
// the two is on screen is now the user's choice, so both have to exist —
// measured once, at the best fit, and kept: the forward-model side is a solver
// run, and a tick box that re-ran it on every click would be unusable.
const bestFitScores = ref(null) // { model, emulator }
// The best fit they were measured at, so a *new* calibration refetches and the
// same one never does.
const bestFitScoresAt = ref('')

async function refreshBestFitScores() {
  const best = calib.bestParams.value
  // Only where the choice can mean something: an emulator that exists and can
  // be loaded, and a calibration to describe. Otherwise the section stays
  // exactly as it was, with nothing to tick.
  if (!best || !model.hasModel.value || !obs.hasObsData.value ||
      !emu.trained.value || emuUnavailable.value) {
    bestFitScores.value = null
    bestFitScoresAt.value = ''
    return
  }
  const at = JSON.stringify(best)
  if (at === bestFitScoresAt.value) return
  try {
    const res = await costAtParams(
      model.modelId.value,
      // Physical values for the solver, θ for the emulator — one best fit
      // written the two ways its two consumers need, so both are scored at the
      // same point (#208).
      { ...expandBestFitParams(best, calib.bestModifiers.value) },
      {
        analysisParams: { ...best },
        outputs: liveOutputs(),
        simTime: simTime.value,
        preTime: preTime.value,
        outputsDir: outputsDir.value.trim() || undefined,
      },
    )
    bestFitScores.value = { model: res.cost ?? null, emulator: res.emulator_cost ?? null }
    bestFitScoresAt.value = at
  } catch {
    // A failed run leaves the section as it was: the calibration's own vectors
    // are still there, and an error banner over an optional comparison would be
    // noise about something the user did not ask for.
    bestFitScores.value = null
    bestFitScoresAt.value = ''
  }
}

// A new best fit, or an emulator that has only just appeared, is the only thing
// that can change the answer — so this is what it follows, and nothing else.
watch(
  () => [calib.bestParams.value, emu.trained.value, emuUnavailable.value],
  () => {
    refreshBestFitScores()
  },
)

// Look for an already-trained emulator whenever the study or its outputs
// directory changes: an emulator outlives the session that trained it, and one
// trained by circulatory_autogen's own script counts too.
watch(
  () => [model.modelId.value, outputsDir.value],
  () => {
    emu.refresh(model.modelId.value, outputsDir.value.trim() || '')
  },
  { immediate: true },
)

// When a UQ run finishes, surface the posterior distributions automatically.
watch(
  () => uq.state.value,
  (state) => {
    if (state === 'done') centerTab.value = 'analysis'
  },
)

// When calibration finishes, write best-fit params into the sliders and re-run.
watch(
  () => calib.state.value,
  (state) => {
    if (state === 'done' && calib.bestParams.value) {
      applyBestParams(sliders, paramsForId.paramSpecs.value, calib.bestParams.value)
      runSimulation()
    }
  },
)

async function onModelLoaded(data) {
  model.setModel(data)
  obs.clearObsData()
  paramsForId.clear()
  loadedParamsRaw.value = []
  loadedParamsFilename.value = null
  loadedObsFilename.value = null
  extraPlots.value = []
  // The last run's solver figures belong to the model that produced them, and
  // their URLs are keyed to that run.
  sim.clearSolverPlots()
  // A new model has new variables, so overlays keyed to the old one's plots
  // (#196) name variables this model may not even have.
  plotVars.value = {}
  sliders.clear()
  // The backend is a property of the model for an external python one, so it is
  // set here rather than left to the user (and left locked in Settings).
  syncFormatToModel()
  try {
    const vars = await getVariables(data.model_id)
    model.setVariables(vars)
  } catch (e) {
    sim.setError(String(e))
  }
}

function onAddSlider({ qname }) {
  const initial = model.variables.value.initial_values?.[qname]
  // Without min/max metadata, seed a symmetric range around the default. Keep
  // it symmetric (don't clamp min to 0) so negative defaults still get a usable
  // range instead of collapsing to min == max == 0.
  const base = initial != null && initial !== 0 ? Math.abs(initial) : 1
  const min = initial != null ? initial - base : 0
  const max = initial != null ? initial + base : 1
  sliders.addSlider(qname, {
    min,
    max,
    value: initial ?? (min + max) / 2,
    log: shouldUseLog(min, max),
  })
}

function onSliderUpdate({ qname, value }) {
  sliders.setValue(qname, value)
}

// Whether a calibration best-fit exists, to gate the "Reset to best fit" button.
const hasBestFit = computed(() => calib.bestParams.value != null)

// Reset all parameter values back to their initial values (after manual edits).
function onResetInit() {
  sliders.resetToInit()
  runSimulation()
}

// Reset all parameter values to the latest calibration best-fit.
function onResetBest() {
  if (!calib.bestParams.value) return
  applyBestParams(sliders, paramsForId.paramSpecs.value, calib.bestParams.value)
  runSimulation()
}

// --- Save / load slider values to a file (.npy default, .csv) — issue #106 ---
const saveParamsOpen = ref(false)
const savedParamsBrowserOpen = ref(false)
// The last file "Save current" wrote, so "Reset to saved" defaults to it. Persisted
// so it survives a reload.
const lastSavedParamsPath = ref(localStorage.getItem('cuflynx-last-saved-params') || '')
// Where the "Reset to saved" browser opens + pre-selects: the last saved file, or
// the default manual_params.npy in the output dir.
const savedParamsStart = computed(
  () =>
    lastSavedParamsPath.value ||
    (outputsDir.value.trim() ? `${outputsDir.value.trim()}/manual_params.npy` : ''),
)

// Saved runs available to overlay on the plots (issue #126).
const savedRuns = useSavedRuns()

/**
 * The traces to store with a saved parameter set (#148).
 *
 * The displayed run only holds the variables that were on screen when it ran, so
 * a saved run built from it has nothing to show on a plot added later — which is
 * exactly what the user hits: overlays appear on the original plots and not on
 * new ones. Re-run once at save time asking for every plottable variable, so the
 * saved run can answer any plot.
 *
 * Falls back to the displayed run if that fails: a solver failure while widening
 * the outputs must not cost the user the save they asked for.
 */
async function savedRunResult() {
  const onScreen = sim.experiments.value.length
    ? { experiments: sim.experiments.value }
    : sim.result.value
  if (!canRun.value) return onScreen
  try {
    return (await runWithParams(sliders.paramDict.value, { allOutputs: true })) ?? onScreen
  } catch {
    return onScreen
  }
}

// When the configured model format cannot run in this process (its library is
// only in the analysis interpreter), live plots fall back to one that can. The
// analysis runs still use the chosen format, so the difference has to be
// visible rather than silently shown as if it were the configured backend.
const backendFallback = ref(null)
const backendFallbackNotice = computed(() => {
  const f = backendFallback.value
  if (!f) return ''
  return (
    `Live plots are using ${f.used} (${f.solver}): ${f.requested} needs a library ` +
    `that is not installed in the app’s own Python. Calibration / sensitivity / UQ ` +
    `still use ${f.requested}.`
  )
})

// Shared y-axis width so the plots in the window line up (#145). Only cells with
// a time x axis take part: a phase-plane cell's x is another variable, so
// aligning it against time plots would line up unrelated axes.
const axisAlign = useAxisAlign()
const alignsWithTime = (cell) => !cell.xLabel
function onAxisWidth(cell, width) {
  if (alignsWithTime(cell)) axisAlign.report(cell.key, width)
}
// A maximized plot is alone in the window, so there is nothing to line it up
// with — and forcing the shared width on it would waste margin.
const sharedAxisWidth = computed(() =>
  effectiveMaximized.value ? 0 : axisAlign.maxWidth.value,
)

// "Save current" -> name+format dialog -> write the file (npy in the slider order,
// or a self-describing csv) under the output directory, plus the traces those
// values produced under the same prefix (#126).
async function onSaveParams({ filename }) {
  try {
    const { path, outputs_error: outputsError } = await saveParams(
      sliders.paramDict.value,
      sliders.order.value,
      filename,
      outputsDir.value.trim(),
      await savedRunResult(),
    )
    if (path) {
      lastSavedParamsPath.value = path
      localStorage.setItem('cuflynx-last-saved-params', path)
    }
    // The parameters saved; only the traces alongside them failed, so say so
    // rather than letting the run silently not be there to tick later.
    if (outputsError) sim.setError(`Parameters saved, but ${outputsError}`)
    await savedRuns.refresh(outputsDir.value.trim())
  } catch (e) {
    sim.setError(errorMessage(e))
  }
}

// The saved runs live in the output directory, so the list follows it — and is
// read once at startup for whatever was saved in a previous session.
watch(outputsDir, (v) => savedRuns.refresh((v || '').trim()), { immediate: true })

// The calibration best fit as a tickable overlay (#126). Its parameter values
// exist the moment a calibration finishes, but its *traces* do not — nothing has
// run the model at them — so they are produced on demand when it is ticked,
// rather than costing a run nobody asked for.
const BEST_FIT_PREFIX = 'best fit'

async function loadBestFitRun() {
  const best = calib.bestParams.value
  if (!best || !canRun.value) return null
  // The fit only names the calibrated parameters; everything else stays where
  // the sliders are, so the comparison isolates what calibration changed. A
  // modifier's slots carry θ and must be expanded to θ·baseline before they
  // are handed to a simulation (#208).
  const params = {
    ...sliders.paramDict.value,
    ...expandBestFitParams(best, calib.bestModifiers.value),
  }
  const data = await runWithParams(params)
  if (!data) return null
  return { prefix: BEST_FIT_PREFIX, params, ...data }
}

// Keep the entry in step with the latest fit. setVirtualRun drops any cached
// traces, so a re-tick re-runs at the new values instead of showing the old.
watch(
  () => calib.bestParams.value,
  (best) => {
    if (best && Object.keys(best).length) {
      savedRuns.setVirtualRun({
        prefix: BEST_FIT_PREFIX,
        title: 'Latest calibration best fit',
        params: best,
        load: loadBestFitRun,
      })
    } else {
      savedRuns.removeVirtualRun(BEST_FIT_PREFIX)
    }
  },
  { immediate: true },
)

// Tick / untick a saved run: loads its traces on first show.
async function onToggleSavedRun(prefix) {
  await savedRuns.toggle(prefix)
  if (savedRuns.error.value) {
    sim.setError(savedRuns.error.value)
    savedRuns.error.value = ''
  }
}

// "Reset to saved" -> browse for a .npy/.csv -> apply onto the sliders.
async function onPickSavedParams(path) {
  savedParamsBrowserOpen.value = false
  if (!path) return
  try {
    const { values } = await loadParams(path, sliders.order.value)
    sliders.applyValues(values)
    runSimulation()
  } catch (e) {
    sim.setError(errorMessage(e))
  }
}

function onParamsLoaded(data) {
  paramsForId.importParams(data.params, data.filename)
  // Keep the raw entries (with param_type) + filename for the Edit dialog.
  loadedParamsRaw.value = data.params
  loadedParamsFilename.value = data.filename
}

function onObsDataLoaded(payload) {
  obs.setObsData(payload)
  if (payload?.filename) loadedObsFilename.value = payload.filename
  // The experiment grouping changes with the obs_data, so per-experiment added
  // plots no longer have a stable home — nor do the cell keys that overlaid
  // variables (#196) hang off.
  extraPlots.value = []
  plotVars.value = {}
  // The groups are about to be rebuilt, and a solver figure hangs off one of them.
  sim.clearSolverPlots()
}

let timer = null
function scheduleRun() {
  if (!canRun.value) return
  clearTimeout(timer)
  // Drop any pending or in-flight gradient first: the parameters it is about
  // have just changed, and left running it would hold the engine while the user
  // waits for the plot (#188).
  cancelCostSensitivity()
  timer = setTimeout(runSimulation, 300)
}

/**
 * Run the model at `params` and return the raw result, without touching the
 * displayed run. Shared with runSimulation so an overlay (the best fit, #126)
 * is produced exactly the way the live trace is — same outputs, same protocol —
 * instead of by a second, drifting copy of these rules.
 *
 * Returns null when there is nothing runnable (no model, or no protocol to run
 * it over): the caller keeps whatever it already had rather than showing the
 * result of a run that could not describe anything.
 */
async function runWithParams(params, { allOutputs = false } = {}) {
  if (!canRun.value) return null
  // `allOutputs` widens the request to every plottable variable. A live run asks
  // only for what is on screen, because every slider drag pays for it — but a
  // saved run has to answer plots that do not exist yet (#148).
  const everything = allOutputs ? plottableVariables.value : []
  const outputs = [
    ...new Set([
      ...obs.plotVariables.value.map((v) => v.qname),
      ...extraOutputNames.value,
      ...everything,
    ]),
  ]
  const data = await runProtocol(model.modelId.value, params, {
    outputs,
    outputsDir: outputsDir.value.trim() || undefined,
  })
  return { experiments: data.experiments }
}

async function runSimulation() {
  // Nothing to run, or no window to run it over — see `canRun`. Silent on
  // purpose: the top bar already says what is missing, and a failed request or a
  // spinner left turning would present it as a fault instead.
  if (!canRun.value) return
  sim.setRunning()
  const started = performance.now()
  // The parameters this run is about, for the em cost beside it to be checked
  // against (#333): two costs of two different points are not a comparison.
  const at = paramSignature()
  try {
    // Protocol run: pre_times/sim_times come from the obs_data protocol_info,
    // the one place the run window is stated. Request the obs-referenced
    // variables plus any user-added plots, keep every experiment, and render one
    // plot per (experiment, variable).
    const outputs = liveOutputs()
    const data = await runProtocol(model.modelId.value, sliders.paramDict.value, {
      outputs,
      outputsDir: outputsDir.value.trim() || undefined,
    })
    backendFallback.value = data.backend_fallback ?? null
    sim.setExperiments(
      data.experiments,
      data.warnings,
      performance.now() - started,
      data.cost ?? null,
      // Figures the solver drew for this run, if it draws any (an external
      // python model's extra_plots()). A protocol run's payload is not handed
      // to the store wholesale, so they travel as an argument like the cost.
      data.solver_plots ?? [],
    )
    lastRunAt.value = at
    // Only once the plot the user is watching is on screen, and only if they
    // asked for it (#188).
    scheduleCostSensitivity()
  } catch (e) {
    sim.setError(errorMessage(e))
  }
}

/**
 * Image cells for the figures the solver drew for the last run (#Workstream D).
 *
 * These are not built from a series and have no axes, units or overlays of their
 * own, so they carry `kind: 'image'` and the cell `v-for` renders them with
 * ImagePanel instead of PlotPanel. They are appended after the model's own plots
 * — and, on a protocol run, to the last experiment group — because a figure is
 * about the whole run rather than about any one experiment, so it reads last
 * wherever the groups came from.
 */
const solverPlotCells = computed(() =>
  (sim.solverPlots.value ?? []).map((p, i) => ({
    key: `solver:${p.index ?? i}`,
    kind: 'image',
    title: p.title || `Solver plot ${p.index ?? i}`,
    url: p.url,
  })),
)

// Plots grouped by experiment: each group has a heading and its plot cells.
// A protocol run shows every experiment, prefixing each with the controlled
// (params_to_change) inputs, then one plot per (experiment, variable).
const plotGroups = computed(() => {
  if (obs.hasProtocol.value && sim.experiments.value.length) {
    const vars = obs.plotVariables.value
    const labels = obs.experimentLabels.value
    const pi = obs.obsData.value?.protocol_info
    const groups = sim.experiments.value.map((exp, e) => {
      const cells = []
      // Controlled inputs first, flagged so they get a "controlled" label.
      for (const c of controlledSeries(pi, e)) {
        cells.push({
          key: `${e}:ctrl:${c.qname}`,
          title: c.label,
          varLabel: c.label,
          yUnit: unitForVars(modelUnits.value, [c.qname]),
          controlled: true,
          simResult: { time: c.time, outputs: { [c.qname]: c.values } },
          dataItems: [],
        })
      }
      const allItems = obs.obsData.value?.data_items ?? []
      for (const v of vars) {
        cells.push({
          key: `${e}:${v.qname}`,
          title: v.label,
          varLabel: v.label,
          yUnit: unitForVars(modelUnits.value, [v.qname]),
          controlled: false,
          simResult: { time: exp.time, outputs: { [v.qname]: exp.outputs?.[v.qname] ?? [] } },
          savedSeries: savedRuns.seriesFor(v.qname, e),
          dataItems: attachOutputSeries(
            overlayItemsFor(obs.obsData.value, e, v.qname),
            exp.output_series,
            allItems,
          ),
        })
      }
      cells.push(...extraCellsFor(`exp${e}`, exp.time, exp.outputs, e))
      const label = labels[e]
        ? `Experiment ${e}: ${labels[e]}`
        : `Experiment ${e}`
      return {
        key: `exp${e}`,
        expIdx: e,
        label,
        cells: cells.map((c) => withUserVars(c, exp.outputs, e)),
      }
    })
    // One run, one set of figures: they go on the last group so they appear once,
    // at the end, rather than repeated under every experiment.
    if (groups.length && solverPlotCells.value.length) {
      groups[groups.length - 1].cells.push(...solverPlotCells.value)
    }
    return groups
  }
  // Data-only obs_data: one group, no heading, one plot per referenced variable.
  if (obs.hasObsData.value && obs.plotVariables.value.length && sim.result.value) {
    const out = sim.result.value.outputs ?? {}
    const allItems = obs.obsData.value?.data_items ?? []
    const cells = obs.plotVariables.value.map((v) => ({
      key: v.qname,
      title: v.label,
      varLabel: v.label,
      yUnit: unitForVars(modelUnits.value, [v.qname]),
      controlled: false,
      simResult: { time: sim.result.value.time, outputs: { [v.qname]: out[v.qname] ?? [] } },
      savedSeries: savedRuns.seriesFor(v.qname),
      dataItems: attachOutputSeries(
        overlayItemsFor(obs.obsData.value, 0, v.qname),
        sim.result.value.output_series,
        allItems,
      ),
    }))
    cells.push(...extraCellsFor('data-only', sim.result.value.time, out))
    return [
      {
        key: 'data-only',
        expIdx: 0,
        label: '',
        cells: [...cells.map((c) => withUserVars(c, out)), ...solverPlotCells.value],
      },
    ]
  }
  // Plain manual run: one combined plot of all returned outputs, with any
  // user-added variables split out into their own plots (and excluded here).
  if (sim.result.value) {
    const out = sim.result.value.outputs ?? {}
    const extraNames = new Set(
      extraPlots.value.filter((p) => p.groupKey === 'single').map((p) => p.qname),
    )
    const mainOutputs = Object.fromEntries(
      Object.entries(out).filter(([k]) => !extraNames.has(k)),
    )
    const cells = [
      {
        key: 'single',
        title: model.name.value ?? '',
        varLabel: '',
        // Combined plot: only annotated when every trace shares one unit.
        yUnit: unitForVars(modelUnits.value, Object.keys(mainOutputs)),
        controlled: false,
        simResult: { time: sim.result.value.time, outputs: mainOutputs },
        // The combined cell draws several variables, so it takes each shown
        // run's trace for every one of them.
        savedSeries: Object.keys(mainOutputs).flatMap((q) => savedRuns.seriesFor(q)),
        dataItems: [],
      },
    ]
    cells.push(...extraCellsFor('single', sim.result.value.time, out))
    return [
      {
        key: 'single',
        expIdx: 0,
        label: '',
        cells: [...cells.map((c) => withUserVars(c, out)), ...solverPlotCells.value],
      },
    ]
  }
  return []
})

// Drop widths for cells that are gone: a removed plot must not go on holding the
// maximum and padding everything else out to a width nothing needs any more.
watch(
  () => plotGroups.value.flatMap((g) => g.cells.map((c) => c.key)),
  (keys) => {
    const alive = new Set(keys)
    for (const key of Object.keys(axisAlign.widths)) {
      if (!alive.has(key)) axisAlign.forget(key)
    }
  },
)

// Auto-run is the only way to run: moving a slider re-runs, and so does changing
// the study it is a slider of. There is no Run button and no t₁/pre to key on —
// the window is the protocol's, so a new (or re-edited, which re-uploads and
// replaces) obs_data is what changes it.
watch(() => ({ ...sliders.paramDict.value }), scheduleRun, { deep: true })
watch(() => obs.obsData.value, scheduleRun)
</script>

<template>
  <div class="layout">
    <header class="topbar">
      <h1>CUFLynx</h1>
      <span v-if="model.filePrefix.value" class="model-name">{{ model.filePrefix.value }}</span>
      <div class="spacer" />
      <!-- Shared Python interpreter for calibration / sensitivity / UQ runs.
           Not a <label>: two controls (Select + browse Button) would make label
           clicks forward to the button and hijack the dropdown. -->
      <div class="python-bar" data-testid="python-bar">
        <span class="py-label" title="Interpreter/env used for calibration, sensitivity and UQ">
          Python
        </span>
        <Select
          :model-value="pythonPath"
          :options="pythonOptions"
          option-label="label"
          option-value="value"
          size="small"
          data-testid="python-select"
          @update:model-value="pythonPath = $event"
        >
          <template #value="{ value }">
            <span :title="value || 'Server default'">{{ shortPython(value) }}</span>
          </template>
        </Select>
        <Button
          icon="pi pi-folder-open"
          size="small"
          text
          title="Browse for a Python interpreter"
          data-testid="python-browse"
          @click="pythonBrowserOpen = true"
        />
        <span
          v-if="pythonNotReady"
          class="py-warn"
          data-testid="python-warning"
          :title="'Selected interpreter is missing: ' + pythonNotReady.join(', ')"
        >
          ⚠
        </span>
        <span
          v-if="pythonMpi"
          class="py-mpi"
          :class="{ off: !pythonMpi.mpi }"
          data-testid="python-mpi"
          :title="pythonMpi.title"
        >
          {{ pythonMpi.label }}
        </span>
      </div>
      <Button
        icon="pi pi-cog"
        size="small"
        text
        title="Settings"
        data-testid="settings-open"
        @click="settingsOpen = true"
      />
      <!--
        The run window, in the one place it is stated. There are no t₁/pre
        spinners to disagree with it and no Run button: the protocol says how
        long the run is, and every parameter change re-runs it.
      -->
      <div
        v-if="obs.hasProtocol.value"
        class="protocol-summary"
        data-testid="protocol-summary"
        :title="runWindowTitle"
      >
        Protocol: {{ obs.experimentCount.value }} experiment(s) · {{ runWindowLabel }}
      </div>
      <div
        v-else-if="model.hasModel.value"
        class="protocol-summary no-protocol"
        data-testid="no-protocol"
      >
        Load an obs_data with a protocol_info to set the run window
      </div>
      <!--
        No auto-start: a 37-step overlay thrown over an empty app is a hijack.
        Until the tour has been seen once, the button pulses (three cycles, and
        nothing at all under prefers-reduced-motion) and says more in its
        tooltip. `tourSeen` is set on first start as well as on skip/finish, so
        it asks exactly once.
      -->
      <Button
        icon="pi pi-question-circle"
        label="Tutorial"
        size="small"
        text
        :class="{ 'tour-pulse': !tourSeen }"
        :title="
          tourSeen
            ? 'Guided tour'
            : 'Guided tour: how to get from nothing to a calibrated model'
        "
        data-testid="tour-start"
        @click="startTour"
      />
    </header>

    <main
      class="columns"
      :class="{ 'rhs-dragging': rhsDragging, 'lhs-dragging': lhsDragging }"
      :style="{ gridTemplateColumns: `${lhsWidth}px 1fr ${rhsWidth}px` }"
    >
      <aside class="col col-left" :class="{ collapsed: lhsCollapsed }" data-testid="lhs-column">
        <div
          class="lhs-divider"
          :class="{ collapsed: lhsCollapsed }"
          data-testid="lhs-handle"
          role="separator"
          aria-orientation="vertical"
          :aria-label="lhsCollapsed ? 'Drag to show the left panel' : 'Drag to resize or hide the left panel'"
          :title="lhsCollapsed ? 'Drag right to show the left panel (double-click to restore)' : 'Drag to resize; drag fully left to hide'"
          @mousedown="startLhsDrag"
          @dblclick="restoreLhs"
        >
          <span class="rhs-grip" />
        </div>
        <div class="lhs-content">
        <div class="left-tabs">
          <button
            class="left-tab"
            :class="{ active: leftTab === 'params' }"
            data-testid="tab-params"
            @click="leftTab = 'params'"
          >
            Parameters
          </button>
          <button
            class="left-tab"
            :class="{ active: leftTab === 'emulator' }"
            data-testid="tab-emulator"
            :title="emuUnavailable ? emuUnavailableTitle : null"
            @click="leftTab = 'emulator'"
          >
            Emulator
            <!-- Deliberately unmarked. An emulator is optional, so not having one is
                 a feature you are not using rather than something wrong, and an amber
                 tab with a warning glyph made a healthy app look faulty. The tooltip
                 still names what to install, and the panel says it in full. -->
            <span
              v-if="emu.running.value"
              class="tab-dot"
              title="emulator training"
            />
            <!-- A dot the other tabs do not have: the emulator is the one tab
                 whose setting changes what the *other* tabs do, so whether it is
                 in use has to be visible from any of them. -->
            <span
              v-else-if="emu.useEmulator.value"
              class="tab-dot tab-dot-on"
              title="analyses are using the emulator"
            />
          </button>
          <button
            class="left-tab"
            :class="{ active: leftTab === 'sensitivity' }"
            data-testid="tab-sensitivity"
            @click="leftTab = 'sensitivity'"
          >
            Sensitivity
            <span
              v-if="sa.running.value"
              class="tab-dot"
              title="sensitivity running"
            />
          </button>
          <button
            class="left-tab"
            :class="{ active: leftTab === 'calibration' }"
            data-testid="tab-calibration"
            @click="leftTab = 'calibration'"
          >
            Calibration
            <span
              v-if="calib.running.value"
              class="tab-dot"
              title="calibration running"
            />
          </button>
          <button
            class="left-tab"
            :class="{ active: leftTab === 'uq' }"
            data-testid="tab-uq"
            @click="leftTab = 'uq'"
          >
            UQ
            <span v-if="uq.running.value" class="tab-dot" title="UQ running" />
          </button>
        </div>

        <div v-show="leftTab === 'params'" class="left-pane left-pane-scroll">
          <ControlPanel
            :sliders="sliders.sliders"
            :has-best-fit="hasBestFit"
            @update="onSliderUpdate"
            @remove="({ qname }) => sliders.removeSlider(qname)"
            @reset-init="onResetInit"
            @reset-best="onResetBest"
            :saved-runs="savedRuns.items.value"
            @save-current="saveParamsOpen = true"
            @reset-saved="savedParamsBrowserOpen = true"
            @toggle-saved="onToggleSavedRun"
          />
        </div>
        <div v-show="leftTab === 'emulator'" class="left-pane left-pane-scroll">
          <EmulatorPanel
            v-model="emu.useEmulator.value"
            :defaults="emuDefaults"
            :can-run="canCalibrate"
            :mpiexec-available="mpiexecAvailable"
            :lines="emu.lines.value"
            :state="emu.state.value"
            :error="emu.error.value"
            :metadata="emu.metadata.value"
            :features="emu.features.value"
            :reusable="emu.reusable.value"
            @run="onTrainEmulator"
            @change="(s) => (emuSettings = s)"
            @cancel="emu.cancel()"
          />
        </div>
        <div v-show="leftTab === 'sensitivity'" class="left-pane left-pane-scroll">
          <SensitivityPanel
            :defaults="saDefaults"
            :can-run="canCalibrate"
            :mpiexec-available="mpiexecAvailable"
            :ad-available="adAvailable"
            :gradient-sources="localGradientSources"
            :lines="sa.lines.value"
            :state="sa.state.value"
            :error="sa.error.value"
            @run="onRunSensitivity"
            @change="(s) => (saSettings = s)"
            @cancel="sa.cancel()"
          />
        </div>
        <div v-show="leftTab === 'calibration'" class="left-pane left-pane-scroll">
          <CalibrationPanel
            :defaults="calibDefaults"
            :can-run="canCalibrate"
            :mpiexec-available="mpiexecAvailable"
            :ad-available="adAvailable"
            :gradient-sources="gradientSources"
            :lines="calib.lines.value"
            :state="calib.state.value"
            :cost="calib.cost.value"
            :error="calib.error.value"
            :calibrated-model-url="calib.calibratedModelUrl.value"
            @run="onRunCalibration"
            @change="(s) => (calibSettings = s)"
            @cancel="calib.cancel()"
          />
        </div>
        <div v-show="leftTab === 'uq'" class="left-pane left-pane-scroll">
          <UQPanel
            :defaults="uqDefaults"
            :can-run="canCalibrate"
            :mpiexec-available="mpiexecAvailable"
            :lines="uq.lines.value"
            :state="uq.state.value"
            :error="uq.error.value"
            @run="onRunUQ"
            @change="(s) => (uqSettings = s)"
            @cancel="uq.cancel()"
          />
        </div>
        </div>
      </aside>

      <section class="col col-center">
        <div class="left-tabs">
          <button
            class="left-tab"
            :class="{ active: centerTab === 'plots' }"
            data-testid="tab-plots"
            @click="centerTab = 'plots'"
          >
            Output plots
          </button>
          <button
            class="left-tab"
            :class="{ active: centerTab === 'progress' }"
            data-testid="tab-progress"
            @click="centerTab = 'progress'"
          >
            Progress
            <span
              v-if="calib.running.value"
              class="tab-dot"
              title="calibration running"
            />
          </button>
          <button
            class="left-tab"
            :class="{ active: centerTab === 'analysis' }"
            data-testid="tab-analysis"
            @click="centerTab = 'analysis'"
          >
            Analysis
            <span
              v-if="sa.running.value"
              class="tab-dot"
              title="sensitivity running"
            />
          </button>
        </div>

        <Message
          v-if="!cppCompiler.present"
          severity="warn"
          :closable="false"
          class="warn-banner"
          data-testid="no-compiler-warning"
        >
          <strong>No C compiler found — the Myokit CVODE solver is unavailable.</strong>
          Myokit compiles each model to a native extension when it runs. Everything
          else still works: switch the backend in <strong>Settings</strong> to
          <em>{{ compilerAlternatives }}</em> — neither needs a compiler.
          <details v-if="cppCompiler.hint">
            <summary>To enable CVODE_myokit, install a C compiler</summary>
            <pre class="compiler-hint">{{ cppCompiler.hint }}</pre>
          </details>
        </Message>

        <Message
          v-if="centerTab === 'plots' && sim.warnings.value.length"
          severity="warn"
          :closable="false"
          class="warn-banner"
          data-testid="sim-warning"
        >
          {{ sim.warnings.value.join(' ') }}
        </Message>
        <!--
          The cost of the current parameters, where the parameters are being
          changed (#159). Manual exploration had a picture and no number: you
          moved a slider, the trace moved, and whether it moved *towards* the
          data was left to the eye.
        -->
        <div
          v-if="centerTab === 'plots' && currentCost"
          class="cost-line"
          data-testid="cost-line"
        >
          <span class="cost-label">cost</span>
          <span class="cost-value" data-testid="cost-value">{{ formatCost(currentCost.cost) }}</span>
          <!--
            The emulator's cost of the same parameters (#333). A calibration with
            the tick box on minimises *this* number while the plots showed the
            solver's, so the two disagreed with nothing on screen to say why. The
            gap is the surrogate's error, and it belongs where the question is
            asked.
          -->
          <template v-if="emCost">
            <span class="cost-label em-cost" data-testid="em-cost-label" :title="emCostTitle">
              em cost
            </span>
            <span class="cost-value em-cost" data-testid="em-cost-value" :title="emCostTitle">
              {{ formatCost(emCost.cost) }}
            </span>
          </template>
          <!--
            The emulator is in use but could not be scored. Its predicted features
            still draw their dotted overlay, so saying nothing here leaves lines on
            the plot with no number beside them and no way to tell why.
          -->
          <span
            v-else-if="emCostWhy"
            class="cost-note em-cost-missing"
            data-testid="em-cost-unavailable"
            :title="emCostWhy"
          >
            no em cost — {{ emCostWhy }}
          </span>
          <span class="cost-note" data-testid="cost-note" :title="emCost ? emCostTitle : null">
            {{ costNote }}
            <template v-if="currentCost.incomplete">
              — not comparable with the calibration cost
            </template>
          </span>
          <!--
            Opt-in, and priced in the tooltip: a gradient is 2M+1 simulations
            every time the parameters settle, which is why it is not simply on.
          -->
          <button
            v-if="costSensAvailable"
            type="button"
            class="cost-pin"
            :class="{ on: costSensOn }"
            :aria-pressed="costSensOn"
            data-testid="cost-sens-toggle"
            :title="
              `show how sensitive the cost is to each parameter — costs ${2 * sliders.count.value + 1} extra simulations each time the parameters settle`
            "
            @click="costSensOn = !costSensOn"
          >
            cost sensitivities
          </button>
        </div>
        <CostSensitivityBar
          v-if="centerTab === 'plots' && costSensOn && costSensAvailable"
          :result="costSens"
          :status="costSensState"
          :error="costSensError"
          :labels="paramLabels"
        />
        <div
          v-show="centerTab === 'plots'"
          class="plot-groups"
          :class="{ 'has-maximized': effectiveMaximized }"
          data-testid="plot-groups"
        >
          <section
            v-for="g in plotGroups"
            v-show="!effectiveMaximized || g.cells.some((c) => c.key === effectiveMaximized)"
            :key="g.key"
            class="exp-group"
            data-testid="exp-group"
          >
            <h2 v-if="g.label && !effectiveMaximized" class="exp-heading">{{ g.label }}</h2>
            <div
              class="plot-grid"
              :class="{ single: g.cells.length <= 1, maximized: !!effectiveMaximized }"
            >
              <template v-for="cell in g.cells" :key="cell.key">
              <!--
                A solver-drawn figure (external python `extra_plots()`) is a
                picture, not a series: same chrome, different body.
              -->
              <ImagePanel
                v-if="cell.kind === 'image'"
                v-show="!effectiveMaximized || effectiveMaximized === cell.key"
                class="plot-cell"
                data-testid="solver-plot-cell"
                :title="cell.title"
                :url="cell.url"
                maximizable
                :maximized="effectiveMaximized === cell.key"
                @toggle-maximize="toggleMaximizePlot(cell.key)"
              />
              <PlotPanel
                v-else
                v-show="!effectiveMaximized || effectiveMaximized === cell.key"
                class="plot-cell"
                :title="cell.title"
                :var-label="cell.varLabel"
                :y-unit="cell.yUnit ?? ''"
                :x-label="cell.xLabel || 'time'"
                :x-unit="cell.xUnit ?? timeUnitLabel"
                :tag="cell.controlled ? 'controlled' : ''"
                :stepped="cell.controlled"
                :sim-result="cell.simResult"
                :data-items="cell.dataItems"
                :emulator-features="emulatorFeatureMap"
                :saved-series="cell.savedSeries ?? []"
                :removable="!!cell.removeId"
                :switchable="!!cell.xLabel"
                :addable="!!cell.addable && plottableVariables.length > 0"
                :mixed-units="!!cell.mixedUnits"
                :align-width="alignsWithTime(cell) ? sharedAxisWidth : 0"
                @axis-width="(w) => onAxisWidth(cell, w)"
                maximizable
                :maximized="effectiveMaximized === cell.key"
                @toggle-maximize="toggleMaximizePlot(cell.key)"
                @remove="removeExtraPlot(cell.removeId)"
                @switch-axes="switchExtraPlotAxes(cell.removeId)"
                @add-variable="openPlotVars(cell)"
              />
              </template>
            </div>
            <div v-if="plottableVariables.length && !effectiveMaximized" class="add-plot-row">
              <Button
                label="Add plot"
                icon="pi pi-plus"
                text
                size="small"
                data-testid="add-plot-btn"
                @click="openAddPlot(g)"
              />
            </div>
          </section>
          <p v-if="plotGroups.length === 0" class="empty-hint">
            Upload a CellML model and run a simulation.
          </p>
        </div>
        <div v-show="centerTab === 'progress'" class="plot-groups">
          <ProgressPanel
            :cost-history="calib.costHistory.value"
            :param-names="calib.paramHistory.value.paramNames"
            :param-history="calib.paramHistory.value.generations"
            :start-costs="calib.startCosts.value"
            :start-params="calib.startParams.value"
            :grad-history="calib.gradHistory.value"
            :start-grads="calib.startGrads.value"
            :param-specs="paramsForId.paramSpecs.value"
            :uq-progress="uq.progress.value"
            :uq-running="uq.running.value"
            :uq-state="uq.state.value"
            :param-labels="paramLabels"
          />
        </div>
        <div v-show="centerTab === 'analysis'" class="plot-groups">
          <AnalysisPanel
            :indices="sa.indices.value"
            :param-names="sa.paramNames.value"
            :output-names="sa.outputNames.value"
            :param-labels="paramLabels"
            :nominal="sa.nominal.value"
            :nominal-source="sa.nominalSource.value"
            :saved-results="sa.results.value"
            :selected-result-id="sa.selectedId.value"
            :percent-error="calib.percentError.value"
            :std-error="calib.stdError.value"
            :error-labels="calib.errorLabels.value"
            :current-cost="currentCost"
            :baseline-cost="activeBaseline"
            :uq-params="uq.params.value"
            :uq-method="uq.method.value"
            :coverage="uq.coverage.value"
            :posterior-predictive="uq.posteriorPredictive.value"
            :emulator-metadata="emu.metadata.value"
            :emulator-error-points="emu.errorPoints.value"
            :emulator-in-use="emu.useEmulator.value"
            :best-fit-model-cost="bestFitScores?.model ?? null"
            :best-fit-emulator-cost="bestFitScores?.emulator ?? null"
            :calibration-used-emulator="lastCalibrationUsedEmulator"
            @select-result="sa.selectResult"
            @remove-result="sa.removeResult"
            @clear-results="sa.clearResults"
          />
        </div>
        <StatusBar
          :status="sim.status.value"
          :message="sim.message.value"
          :last-run-ms="sim.lastRunMs.value"
          :notice="backendFallbackNotice"
        />
      </section>

      <aside
        class="col col-right"
        :class="{ collapsed: rhsCollapsed }"
        data-testid="rhs-column"
      >
        <div
          class="rhs-divider"
          :class="{ collapsed: rhsCollapsed }"
          data-testid="rhs-handle"
          role="separator"
          aria-orientation="vertical"
          :aria-label="rhsCollapsed ? 'Drag to show the import panel' : 'Drag to resize or hide the import panel'"
          :title="rhsCollapsed ? 'Drag left to show the import panel (double-click to restore)' : 'Drag to resize; drag fully right to hide'"
          @mousedown="startRhsDrag"
          @dblclick="restoreRhs"
        >
          <span class="rhs-grip" />
        </div>
        <div class="rhs-content">
        <FileImport
          v-model:outputs-dir="outputsDir"
          :model-id="model.modelId.value"
          :current-params="loadedParamsRaw"
          :model-variables="model.variables.value"
          :model-name="model.name.value"
          :loaded-filename="loadedParamsFilename"
          :current-data-items="obs.dataItems.value"
          :current-prediction-items="obs.predictionItems.value"
          :obs-protocol-info="obs.obsData.value?.protocol_info ?? null"
          :experiment-count="obs.experimentCount.value"
          :loaded-obs-filename="loadedObsFilename"
          :can-export="model.hasModel.value"
          :generated-model-format="generatedModelFormat"
          :model-format="model.modelFormat.value"
          :converted-from="model.convertedFrom.value"
          :param-values="sliders.paramDict.value"
          @model-loaded="onModelLoaded"
          @obs-data-loaded="onObsDataLoaded"
          @params-loaded="onParamsLoaded"
          @export-pipeline="onExportPipeline"
          @export-plotting="onExportPlotting"
        />
        <p v-if="exportNotice" class="export-notice" data-testid="export-notice">
          {{ exportNotice }}
        </p>
        <VariableList
          :variables="model.variables.value"
          :active-keys="Object.keys(sliders.sliders)"
          @add-slider="onAddSlider"
        />
        </div>
      </aside>
    </main>

    <Dialog
      v-model:visible="settingsOpen"
      modal
      header="Settings"
      :style="{ width: '34rem' }"
      data-testid="settings-dialog"
    >
      <div class="settings-form">
        <label class="settings-row">
          <span class="settings-label">Colour scheme</span>
          <Select
            v-model="theme"
            :options="themeOptions"
            option-label="label"
            option-value="value"
            size="small"
            data-testid="theme-select"
          />
        </label>
        <div class="settings-row">
          <span
            class="settings-label"
            title="circulatory_autogen directory used for simulation / calibration / sensitivity / UQ"
          >
            CA dir
          </span>
          <span class="settings-input">
            <code class="ca-path" :title="caDir || '(default)'">{{ caDir || '(default)' }}</code>
            <Button
              icon="pi pi-folder-open"
              size="small"
              text
              title="Browse for the circulatory_autogen directory"
              data-testid="ca-browse"
              @click="caBrowserOpen = true"
            />
            <span
              v-if="!caExists"
              class="py-warn"
              data-testid="ca-warning"
              :title="'circulatory_autogen not found at: ' + caDir"
            >
              ⚠
            </span>
          </span>
        </div>
        <p class="settings-hint">
          Not needed: <code>libCUFLynx</code> ships with the app. Set this only to
          develop <code>libCUFLynx</code> itself — point it at the
          <code>circulatory_autogen</code> checkout the copy you are working on lives
          in, and runs will use that on their next launch.
        </p>

        <hr class="settings-sep" />

        <!--
          dt, pre-time and sim-time are all in the model's time units, so the
          unit belongs here beside them. Highlighted when the model does not
          declare one — a Myokit .mmt with a bare `time = 0 bind time` says
          nothing, and an unlabelled axis is the visible symptom (#27).
        -->
        <label class="settings-row" :class="{ 'needs-attention': !timeUnitLabel }">
          <span
            class="settings-label"
            title="The unit of the model's time variable. dt, pre-time and sim-time are all in these units, and it labels the plots' time axis."
          >
            Time unit
          </span>
          <InputText
            v-if="!modelTimeUnit"
            v-model="timeUnitOverride"
            size="small"
            placeholder="e.g. ms"
            data-testid="time-unit-input"
          />
          <code v-else class="settings-derived" data-testid="time-unit-derived">
            {{ modelTimeUnit }}
          </code>
        </label>
        <p class="settings-hint" data-testid="time-unit-hint">
          <template v-if="modelTimeUnit">
            From the model's own time variable. dt, pre-time and sim-time are in
            these units.
          </template>
          <template v-else-if="timeUnitOverride.trim()">
            This model does not declare a time unit, so this one is yours — it
            labels the time axis and is what dt, pre-time and sim-time are in.
          </template>
          <template v-else>
            <strong>This model does not declare a time unit</strong>, so the time
            axis is unlabelled and dt / pre-time / sim-time have no stated units.
            Set it here. Nothing is guessed for you: a wrong unit would be worse
            than none.
          </template>
        </p>

        <label class="settings-row">
          <span
            class="settings-label"
            title="circulatory_autogen model_type: the backend the dropped CellML runs through. python / casadi_python generate a Python model from the CellML."
          >
            Generated model format
          </span>
          <Select
            :model-value="generatedModelFormat"
            :options="formatChoices"
            size="small"
            :disabled="isExternalPythonModel"
            data-testid="model-format-select"
            @update:model-value="onFormatChange"
          />
        </label>
        <!--
          The loaded model is the solver, so there is no format to choose: say
          that, rather than leaving a greyed-out control to be puzzled over.
        -->
        <p
          v-if="isExternalPythonModel"
          class="settings-hint"
          data-testid="external-python-format-hint"
        >
          This model is an external Python solver, so it runs as
          <code>external_python</code> — there is nothing to generate and no other
          backend can run it. Drop a CellML or Myokit model to choose a format
          again.
        </p>
        <!--
          aadc_python is only in that list when Matlogica's AADC is importable
          (#122) — offering a format that cannot run is what the OpenCOR
          exclusion exists to avoid. When it is missing, say so and how to get
          it, rather than leaving a silent gap in the menu.
        -->
        <p v-if="aadcNotice" class="settings-hint" data-testid="aadc-hint">
          {{ aadcNotice }}
        </p>
        <label class="settings-row">
          <span class="settings-label" title="Solver wrapper, gated by the model format">Solver</span>
          <Select
            :model-value="solver"
            :options="solverChoices"
            size="small"
            data-testid="solver-select"
            @update:model-value="onSolverChange"
          />
        </label>
        <template v-for="f in solverInfoFields" :key="f.key">
        <div class="settings-row">
          <span class="settings-label">{{ f.label }}</span>
          <Select
            v-if="f.type === 'select'"
            v-model="solverInfo[f.key]"
            :options="f.options"
            size="small"
            :data-testid="`solver-info-${f.key}`"
            @update:model-value="applyBackendSolver"
          />
          <Checkbox
            v-else-if="f.type === 'bool'"
            v-model="solverInfo[f.key]"
            :binary="true"
            :data-testid="`solver-info-${f.key}`"
            @update:model-value="applyBackendSolver"
          />
          <!--
            A free-form object (external_python's `user_config`, which CA hands
            to the user's class untouched). There is no schema to build a form
            from, so it is edited as JSON text.
          -->
          <InputText
            v-else-if="f.type === 'json'"
            :model-value="jsonFieldText(f.key)"
            size="small"
            placeholder="{}"
            :data-testid="`solver-info-${f.key}`"
            @update:model-value="(v) => onJsonFieldInput(f.key, v)"
          />
          <InputNumber
            v-else
            v-model="solverInfo[f.key]"
            :min-fraction-digits="1"
            :max-fraction-digits="12"
            size="small"
            :data-testid="`solver-info-${f.key}`"
            @update:model-value="applyBackendSolver"
          />
        </div>
        <p
          v-if="f.type === 'json' && jsonFieldErrors[f.key]"
          class="settings-warn"
          :data-testid="`solver-info-${f.key}-error`"
        >
          ⚠ {{ jsonFieldErrors[f.key] }}
        </p>
        </template>
        <p v-if="generatedModelFormat === 'casadi_python'" class="settings-hint">
          casadi_python enables automatic differentiation:
          <span data-testid="ad-status">{{
            adAvailable
              ? 'AD available'
              : `AD unavailable — these obs_data operations in use are not @differentiable: ${nonDifferentiableOps.join(', ')}`
          }}</span>.
        </p>
        <p
          v-if="gradientIntegratorWarning"
          class="settings-warn"
          data-testid="gradient-integrator-warning"
        >
          ⚠ {{ gradientIntegratorWarning }}
        </p>
        <p
          v-if="solverInfo.method === 'semi_implicit_euler'"
          class="settings-warn"
          data-testid="semi-implicit-warning"
        >
          ⚠ semi_implicit_euler is a first-order, fixed-step damped solver — it enables
          AD on stiff models but is <strong>less accurate than CVODES</strong>. Reduce
          dt and run a convergence study (confirm results stop changing) before trusting them.
        </p>

        <hr class="settings-sep" />

        <label class="settings-row">
          <span
            class="settings-label"
            title="Seed CA's random processes (GA, multi-start sampling, Sobol sampling, MCMC) so calibration / sensitivity / UQ runs are reproducible. Leave blank for non-deterministic runs."
          >
            Random seed (optional)
          </span>
          <InputNumber
            v-model="seed"
            :use-grouping="false"
            :min="0"
            :step="1"
            show-buttons
            placeholder="none"
            size="small"
            data-testid="seed-input"
          />
        </label>
        <p class="settings-hint">
          Set a seed to make calibration / sensitivity / UQ runs repeatable. Leave
          blank (clear it) for non-deterministic runs.
        </p>

        <hr class="settings-sep" />

        <!-- Optional desktop notification when a long run ends (#105). Off by default. -->
        <label class="settings-row">
          <span
            class="settings-label"
            title="Show a browser notification when a calibration / sensitivity / UQ run finishes, fails or is cancelled"
          >
            Notify when long runs finish
          </span>
          <Checkbox
            v-model="notifyOnFinish"
            :binary="true"
            data-testid="notify-on-finish"
          />
        </label>
        <p class="settings-hint">
          Off by default. When on, a browser notification appears as soon as a
          calibration, sensitivity or UQ run ends — useful for runs long enough to
          walk away from. It asks to stay on screen until dismissed, and the tab
          title is flagged too, since some desktops hide notifications after a few
          seconds regardless.
        </p>
        <p v-if="notifyWarning" class="settings-warn" data-testid="notify-warning">
          ⚠ {{ notifyWarning }}
        </p>
      </div>
    </Dialog>

    <FileBrowserDialog
      v-model:visible="pythonBrowserOpen"
      mode="file"
      title="Select a Python interpreter"
      @select="(p) => (pythonPath = p)"
    />
    <FileBrowserDialog
      v-model:visible="caBrowserOpen"
      mode="dir"
      title="Select the circulatory_autogen directory"
      @select="applyCaDir"
    />
    <FileBrowserDialog
      v-model:visible="outputsSetupOpen"
      mode="dir"
      title="Where should outputs be saved?"
      @select="(d) => (outputsDir = d)"
    />

    <!-- Save / load slider values to a file (issue #106). -->
    <SaveParamsDialog
      v-model:visible="saveParamsOpen"
      :output-dir="outputsDir"
      @save="onSaveParams"
    />
    <FileBrowserDialog
      v-model:visible="savedParamsBrowserOpen"
      mode="file"
      title="Load saved parameter values (.npy or .csv)"
      :start-path="savedParamsStart"
      :start-dir="outputsDir"
      @select="onPickSavedParams"
    />

    <Dialog
      v-model:visible="addPlotOpen"
      modal
      :header="addPlotTarget.label ? `Add plot — ${addPlotTarget.label}` : 'Add plot'"
      :style="{ width: '24rem' }"
    >
      <div class="add-plot-dialog">
        <label class="add-plot-label" for="add-plot-var">Variable (y)</label>
        <Select
          id="add-plot-var"
          v-model="addPlotVar"
          :options="addPlotChoices"
          option-label="label"
          option-value="value"
          placeholder="Select a variable"
          filter
          data-testid="add-plot-select"
          class="add-plot-select"
        />
        <label class="add-plot-label" for="add-plot-x-var">Against (x)</label>
        <Select
          id="add-plot-x-var"
          v-model="addPlotXVar"
          :options="addPlotXChoices"
          option-label="label"
          option-value="value"
          placeholder="time"
          filter
          data-testid="add-plot-x-select"
          class="add-plot-select"
        />
        <p v-if="!addPlotChoices.length" class="empty-hint">
          All available variables are already plotted here.
        </p>
      </div>
      <template #footer>
        <Button label="Cancel" text @click="addPlotOpen = false" />
        <Button
          label="Add"
          icon="pi pi-plus"
          :disabled="!addPlotVar"
          data-testid="add-plot-confirm"
          @click="confirmAddPlot"
        />
      </template>
    </Dialog>

    <!--
      Overlay further variables on one plot (#196). The picker is a
      SearchableSelect, not a Select: the list is the model's whole plottable
      variable set, which runs to hundreds on a circulation model (#160).
    -->
    <Dialog
      v-model:visible="plotVarsOpen"
      modal
      header="Variables on this plot"
      :style="{ width: '26rem' }"
      data-testid="plot-vars-dialog"
    >
      <div class="add-plot-dialog">
        <ul class="plot-vars-list" data-testid="plot-vars-list">
          <li v-for="q in plotVarsDrawn" :key="q" class="plot-vars-item">
            <code>{{ q }}</code>
            <span v-if="modelUnits[q]" class="plot-vars-unit">[{{ modelUnits[q] }}]</span>
            <!--
              Only what the user added here can be taken off again: the cell's
              own variable is what the plot *is*, and removing it would leave an
              empty axis where "remove the plot" was meant.
            -->
            <button
              v-if="plotVarsAdded.includes(q)"
              type="button"
              class="plot-vars-remove"
              :title="`Remove ${q} from this plot`"
              :aria-label="`Remove ${q} from this plot`"
              data-testid="plot-vars-remove"
              @click="removePlotVar(q)"
            >
              ✕
            </button>
          </li>
        </ul>
        <!-- A plain span, not a <label for>: SearchableSelect's root is a span,
             which is not a labelable element. The control names itself. -->
        <span class="add-plot-label">Add a variable</span>
        <SearchableSelect
          v-model="plotVarsPick"
          :options="plotVarsChoices"
          placeholder="Select a variable"
          testid="plot-vars-select"
          class="add-plot-select"
        />
        <p v-if="plotVarsUnitWarning" class="plot-vars-warn" data-testid="plot-vars-unit-warning">
          {{ plotVarsUnitWarning }}
        </p>
        <p v-if="!plotVarsChoices.length" class="empty-hint">
          Every plottable variable is already on this plot.
        </p>
      </div>
      <template #footer>
        <Button label="Done" text data-testid="plot-vars-done" @click="plotVarsOpen = false" />
        <Button
          label="Add"
          icon="pi pi-plus"
          :disabled="!plotVarsPick"
          data-testid="plot-vars-confirm"
          @click="confirmPlotVar"
        />
      </template>
    </Dialog>

    <Dialog
      v-model:visible="exportPromptOpen"
      modal
      header="Export pipeline script"
      :style="{ width: '32rem' }"
      data-testid="export-prompt"
    >
      <p>
        This exports a reproducible Python pipeline driven by the dated
        <code>user_inputs.yaml</code>. If there's something you need to do that
        CUFLynx can't, please
        <a :href="CUFLYNX_ISSUES_URL" target="_blank" rel="noopener" data-testid="export-issue-link">
          create a GitHub issue</a>
        so we can improve functionality and keep your work in a reproducible pipeline.
      </p>
      <template #footer>
        <Button label="Cancel" text @click="exportPromptOpen = false" />
        <Button
          label="Continue export"
          icon="pi pi-file-export"
          data-testid="export-confirm"
          @click="confirmExportPipeline"
        />
      </template>
    </Dialog>

    <!--
      `v-if`, not `v-show`: with the tour closed nothing runs at all — no
      interval, no delegated listeners, no ResizeObserver.
    -->
    <TourOverlay
      v-if="tourOpen"
      v-model:step="tourStep"
      :steps="TOUR_STEPS"
      :ctx="tourCtx"
      @close="onTourClose"
    />

  </div>
</template>

<style scoped>
.layout {
  display: flex;
  flex-direction: column;
  height: 100vh;
}
.topbar {
  display: flex;
  align-items: center;
  gap: 1rem;
  padding: 0.5rem 1rem;
  border-bottom: 1px solid var(--p-content-border-color, #333);
}
.topbar h1 {
  font-size: 1.1rem;
  margin: 0;
}
.model-name {
  font-family: monospace;
  opacity: 0.8;
}
.spacer {
  flex: 1;
}
.python-bar {
  display: flex;
  align-items: center;
  gap: 0.3rem;
  font-size: 0.8rem;
}
.python-bar .py-label {
  opacity: 0.7;
}
.python-bar .py-warn {
  color: #ffc000;
  cursor: help;
}
.python-bar .py-mpi {
  cursor: help;
  white-space: nowrap;
}
.python-bar .py-mpi.off {
  opacity: 0.5;
}
/* A setting the model could not supply and the user has not either: worth
   pointing at, since its absence shows up as an unlabelled axis rather than as
   an error (#27). */
.settings-row.needs-attention .settings-label::after {
  content: ' ●';
  color: var(--p-primary-color, #5b9bd5);
}
.settings-derived {
  opacity: 0.8;
  font-size: 0.8rem;
}
.settings-form {
  display: flex;
  flex-direction: column;
  gap: 0.9rem;
}
.settings-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 1rem;
}
.settings-label {
  font-size: 0.9rem;
  opacity: 0.85;
}
.settings-input {
  display: flex;
  align-items: center;
  gap: 0.35rem;
  min-width: 0;
}
.settings-input .ca-path {
  font-family: monospace;
  font-size: 0.78rem;
  opacity: 0.85;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  max-width: 18rem;
}
.settings-input .py-warn {
  color: #ffc000;
  cursor: help;
}
.settings-hint {
  font-size: 0.78rem;
  opacity: 0.65;
  margin: 0;
}
.settings-sep {
  border: none;
  border-top: 1px solid var(--p-content-border-color, #333);
  margin: 0.5rem 0 0.25rem;
  width: 100%;
}
.settings-warn {
  font-size: 0.78rem;
  margin: 0;
  color: #d08700;
}
.export-notice {
  font-size: 0.75rem;
  opacity: 0.8;
  margin: 0.25rem 0 0;
  word-break: break-all;
}
.protocol-summary {
  display: flex;
  gap: 0.5rem;
  align-items: center;
  font-size: 0.8rem;
  white-space: nowrap;
}
/* Not an error — nothing has gone wrong, a study is simply not complete yet. */
.no-protocol {
  opacity: 0.85;
  font-style: italic;
}

/* The Tutorial button's pulse is the whole of the "auto-offer": three cycles,
   once, while the tour has never been opened — instead of throwing a 37-step
   overlay over an app the user has not filled in yet. */
.tour-pulse {
  animation: tour-pulse 1.6s ease-out 3;
  border-radius: 4px;
}
@keyframes tour-pulse {
  0%,
  100% {
    box-shadow: 0 0 0 0 transparent;
  }
  40% {
    box-shadow: 0 0 0 3px var(--p-primary-color, #3b82f6);
  }
}
/* Asked for no motion: give none. The tooltip still says more than the
   seen-it-already one, so nothing is lost but the animation. */
@media (prefers-reduced-motion: reduce) {
  .tour-pulse {
    animation: none;
  }
}
.columns {
  display: grid;
  /* grid-template-columns is set inline from rhsWidth (the draggable RHS width). */
  flex: 1;
  min-height: 0;
  overflow: hidden;
}
/* No width transition while actively dragging, so the resize tracks the pointer. */
.columns:not(.rhs-dragging):not(.lhs-dragging) {
  transition: grid-template-columns 0.18s ease;
}
.col {
  min-height: 0;
  overflow: hidden;
}
.col-left {
  border-right: 1px solid var(--p-content-border-color, #333);
  display: flex;
  flex-direction: column;
  position: relative;
  /* Override .col's overflow:hidden so the drag divider / tab can jut out. */
  overflow: visible;
}
.lhs-content {
  flex: 1;
  display: flex;
  flex-direction: column;
  min-width: 0;
  min-height: 0;
  overflow: hidden;
}
.col-left.collapsed .lhs-content {
  display: none;
}
/* Draggable divider on the left column's right edge; mirrors .rhs-divider. */
.lhs-divider {
  position: absolute;
  top: 0;
  bottom: 0;
  right: 0;
  transform: translateX(50%);
  width: 9px;
  z-index: 6;
  display: flex;
  align-items: center;
  justify-content: center;
  cursor: col-resize;
  background: transparent;
}
.lhs-divider:hover,
.columns.lhs-dragging .lhs-divider {
  background: rgba(91, 155, 213, 0.25);
}
.lhs-divider:hover .rhs-grip,
.columns.lhs-dragging .rhs-grip {
  background: var(--p-primary-color, #5b9bd5);
}
/* Collapsed: a grabbable tab pinned to the left edge, jutting right. */
.lhs-divider.collapsed {
  top: 50%;
  bottom: auto;
  right: 0;
  transform: translate(100%, -50%);
  width: 15px;
  height: 56px;
  border: 1px solid var(--p-content-border-color, #333);
  border-left: none;
  border-radius: 0 6px 6px 0;
  background: var(--p-content-background, #1e1e1e);
  opacity: 0.85;
}
.lhs-divider.collapsed:hover {
  opacity: 1;
}
.lhs-divider.collapsed .rhs-grip {
  height: 26px;
}
.left-tabs {
  display: flex;
  border-bottom: 1px solid var(--p-content-border-color, #333);
}
.left-tab {
  flex: 1;
  background: transparent;
  border: none;
  border-bottom: 2px solid transparent;
  color: inherit;
  opacity: 0.6;
  padding: 0.5rem;
  cursor: pointer;
  font-size: 0.85rem;
}
.left-tab.active {
  opacity: 1;
  border-bottom-color: var(--p-primary-color, #5b9bd5);
}
/* "This tab works, but this part of the app does not" — the same amber as the
   compiler warning banner and the running dot, not a second orange. Full
   opacity, or an inactive tab's 0.6 would mute the very thing being flagged. */
.left-tab.warn {
  color: var(--p-message-warn-color, #ffc000);
  opacity: 1;
}
.left-tab.warn.active {
  border-bottom-color: var(--p-message-warn-color, #ffc000);
}
.tab-warn-mark {
  margin-left: 0.3rem;
  font-size: 0.8rem;
}
.tab-dot {
  display: inline-block;
  width: 7px;
  height: 7px;
  margin-left: 0.35rem;
  border-radius: 50%;
  background: #ffc000;
}
/* Amber means "running"; green means "the emulator is what the other tabs will
   evaluate". A different state deserves a different colour, or the one dot that
   is not about a running job would read as one. */
.tab-dot-on {
  background: #3fb950;
}
.left-pane {
  flex: 1;
  min-height: 0;
}
.left-pane-scroll {
  overflow-y: auto;
}
.col-center {
  display: flex;
  flex-direction: column;
}
.warn-banner {
  margin: 0.5rem;
}
/* The install hint is copy-pasteable shell commands, so keep its line breaks. */
.compiler-hint {
  margin: 0.4rem 0 0;
  font-size: 0.8rem;
  white-space: pre-wrap;
  font-family: monospace;
}
.plot-groups {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  padding: 0.5rem;
}
.exp-group + .exp-group {
  margin-top: 0.75rem;
}
.exp-heading {
  margin: 0.25rem 0 0.5rem;
  font-size: 0.95rem;
  font-weight: 600;
  border-bottom: 1px solid var(--p-content-border-color, #333);
  padding-bottom: 0.25rem;
}
.plot-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(320px, 1fr));
  gap: 0.5rem;
}
.plot-grid.single {
  grid-template-columns: 1fr;
}
/* No fixed height: the cell is its plot area (pinned in PlotPanel) plus header
   and legend, so a legend row moves only the legend (#146). */
.plot-cell {
  border: 1px solid var(--p-content-border-color, #333);
  border-radius: 6px;
}
/* Individual-plot maximize (#115): the one visible plot fills the middle window. */
.plot-groups.has-maximized {
  overflow: hidden;
}
.plot-groups.has-maximized .exp-group,
.plot-grid.maximized {
  height: 100%;
}
.plot-grid.maximized {
  display: block;
}
.plot-grid.maximized .plot-cell {
  height: 100%;
  min-height: 0;
}
.empty-hint {
  opacity: 0.6;
  padding: 1rem;
}
.add-plot-row {
  display: flex;
  justify-content: center;
  margin-top: 0.4rem;
}
.add-plot-dialog {
  display: flex;
  flex-direction: column;
  gap: 0.4rem;
}
.add-plot-label {
  font-size: 0.8rem;
  opacity: 0.8;
}
.add-plot-select {
  width: 100%;
}
.plot-vars-list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 0.15rem;
}
.plot-vars-item {
  display: flex;
  align-items: center;
  gap: 0.4rem;
  font-size: 0.85rem;
}
.plot-vars-unit {
  opacity: 0.6;
  font-size: 0.78rem;
}
.plot-vars-remove {
  margin-left: auto;
  border: none;
  background: none;
  color: inherit;
  cursor: pointer;
  opacity: 0.55;
  line-height: 1;
  padding: 0.1rem 0.25rem;
}
.plot-vars-remove:hover {
  opacity: 1;
}
/* A caveat, not an error: the overlay is exactly what was asked for. */
.plot-vars-warn {
  margin: 0;
  font-size: 0.78rem;
  opacity: 0.8;
}
.col-right {
  border-left: 1px solid var(--p-content-border-color, #333);
  display: flex;
  flex-direction: column;
  position: relative;
  /* Override .col's overflow:hidden so the drag divider / tab can jut out over the
     border (and, when collapsed, sit on the viewport's right edge). */
  overflow: visible;
}
.rhs-content {
  flex: 1;
  min-width: 0;
  min-height: 0;
  overflow-y: auto;
  overflow-x: hidden;
}
/* Collapsed (width 0): hide the content outright so nothing spills past the edge. */
.col-right.collapsed .rhs-content {
  display: none;
}
/* The draggable vertical divider, straddling the border between center and RHS. */
.rhs-divider {
  position: absolute;
  top: 0;
  bottom: 0;
  left: 0;
  transform: translateX(-50%);
  width: 9px;
  z-index: 6;
  display: flex;
  align-items: center;
  justify-content: center;
  cursor: col-resize;
  background: transparent;
}
.rhs-divider:hover,
.columns.rhs-dragging .rhs-divider {
  background: rgba(91, 155, 213, 0.25);
}
/* The little vertical grip line in the middle of the divider. */
.rhs-grip {
  width: 2px;
  height: 42px;
  border-radius: 2px;
  background: var(--p-content-border-color, #666);
}
.rhs-divider:hover .rhs-grip,
.columns.rhs-dragging .rhs-grip {
  background: var(--p-primary-color, #5b9bd5);
}
/* Collapsed: the divider becomes a grabbable tab pinned to the right edge. */
.rhs-divider.collapsed {
  top: 50%;
  bottom: auto;
  transform: translate(-100%, -50%);
  width: 15px;
  height: 56px;
  border: 1px solid var(--p-content-border-color, #333);
  border-right: none;
  border-radius: 6px 0 0 6px;
  background: var(--p-content-background, #1e1e1e);
  opacity: 0.85;
}
.rhs-divider.collapsed:hover {
  opacity: 1;
}
.rhs-divider.collapsed .rhs-grip {
  height: 26px;
}

/* The cost line above the plots (#159): quiet, but the first thing on the panel
   where parameters are being changed. */
.em-cost-missing {
  color: var(--p-message-warn-color, #ffc000);
}
.cost-line {
  display: flex;
  align-items: baseline;
  gap: 0.6rem;
  padding: 0.3rem 0.55rem;
  margin-bottom: 0.4rem;
  border: 1px solid var(--p-content-border-color, #d5d5d5);
  border-radius: 4px;
  background: var(--p-content-background, #fff);
  font-size: 0.85rem;
}
.cost-label {
  text-transform: uppercase;
  letter-spacing: 0.08em;
  font-size: 0.68rem;
  opacity: 0.7;
}
.cost-value {
  font-variant-numeric: tabular-nums;
  font-weight: 700;
  font-size: 1rem;
}
/* The emulator's figure: the same shape as the model's so they read as one
   pair, in its own colour so they are never mistaken for each other (#333). */
.cost-label.em-cost,
.cost-value.em-cost {
  color: var(--p-primary-color, #5b9bd5);
  cursor: help;
}
.cost-note {
  opacity: 0.65;
  font-size: 0.75rem;
}
.cost-pin {
  margin-left: auto;
  font: inherit;
  font-size: 0.75rem;
  padding: 0.05rem 0.45rem;
  cursor: pointer;
  border: 1px solid var(--p-content-border-color, #d5d5d5);
  border-radius: 3px;
  background: transparent;
  color: inherit;
}
.cost-pin:hover {
  background: var(--p-highlight-background, #eef3fb);
}
/* A toggle, not an action: the pressed state has to be visible, or "is it on?"
   is answered only by whether the rows below happen to be there yet. */
.cost-pin.on {
  background: var(--p-highlight-background, #eef3fb);
  border-color: var(--p-primary-color, #6f9fd8);
}
</style>
