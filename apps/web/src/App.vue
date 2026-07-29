<script setup>
import { ref, computed, watch, onMounted } from 'vue'
import ControlPanel from './components/ControlPanel.vue'
import VariableList from './components/VariableList.vue'
import PlotPanel from './components/PlotPanel.vue'
import FileImport from './components/FileImport.vue'
import StatusBar from './components/StatusBar.vue'
import CalibrationPanel from './components/CalibrationPanel.vue'
import ProgressPanel from './components/ProgressPanel.vue'
import SensitivityPanel from './components/SensitivityPanel.vue'
import UQPanel from './components/UQPanel.vue'
import AnalysisPanel from './components/AnalysisPanel.vue'
import InputNumber from 'primevue/inputnumber'
import Button from 'primevue/button'
import Message from 'primevue/message'
import Select from 'primevue/select'
import Checkbox from 'primevue/checkbox'
import Dialog from 'primevue/dialog'
import FileBrowserDialog from './components/FileBrowserDialog.vue'

import { useModel } from './stores/useModel'
import { useSliders, shouldUseLog } from './stores/useSliders'
import { useSimResult } from './stores/useSimResult'
import { useObsData } from './stores/useObsData'
import { useParamsForId } from './stores/useParamsForId'
import { useCalibration, applyBestParams } from './stores/useCalibration'
import { useSensitivity } from './stores/useSensitivity'
import { useUQ } from './stores/useUQ'
import {
  getVariables,
  simulate,
  runProtocol,
  getCalibrationDefaults,
  getCalibrationPythons,
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
  timeUnit,
} from './lib/plot'
import SaveParamsDialog from './components/SaveParamsDialog.vue'
import { requestNotificationPermission } from './lib/notify'
import { useRunNotifications } from './stores/useRunNotifications'
import { useSavedRuns } from './stores/useSavedRuns'
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
const uq = useUQ()

const simTime = ref(10)
const preTime = ref(0)

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
// /api/config. Defaults to the sibling clone; pick a different checkout to dev against.
const caDir = ref('')
const caExists = ref(true)
const caBrowserOpen = ref(false)

// Backend solver selection (Settings popup). generatedModelFormat is CA's
// model_type; solver + solverInfo are gated by it. solverOpts holds the
// capabilities/schema from /api/config (formats, solvers-per-format, solver_info
// fields, differentiability). adAvailable gates the AD/sp_minimize options.
const solverOpts = ref({})
const generatedModelFormat = ref('cellml_only')
const solver = ref('CVODE_myokit')
const solverInfo = ref({})

// Myokit JIT-compiles each model, so without a C toolchain every simulation
// fails with an opaque 500. The backend detects this (compiler_check.py) and we
// warn up front — it's the most likely first-run stumble in the packaged app,
// which has no compiler of its own to fall back on.
const cppCompiler = ref({ present: true, hint: '' })

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
  generatedModelFormat.value = c.generated_model_format ?? 'cellml_only'
  solver.value = c.solver ?? ''
  solverInfo.value = { ...(c.solver_info ?? {}) }
  cppCompiler.value = c.cpp_compiler ?? { present: true, hint: '' }
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
  } else if (fmt === 'cellml_only') {
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
const timeUnitLabel = computed(() => timeUnit(modelUnits.value))

// Extra-plot qnames to append to a run's requested outputs so the chosen
// variables come back from the engine — the x variable of a phase-plane plot
// included, else the engine never returns that series.
const extraOutputNames = computed(() => [
  ...new Set(
    extraPlots.value.flatMap((p) => (p.xqname ? [p.qname, p.xqname] : [p.qname])),
  ),
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

// Calibration / sensitivity
const calibDefaults = ref({})
const calibPythons = ref([])
const saDefaults = ref({})
const uqDefaults = ref({})
onMounted(async () => {
  try {
    calibDefaults.value = await getCalibrationDefaults()
  } catch {
    /* backend not up yet; panel falls back to built-in defaults */
  }
  try {
    saDefaults.value = await getSensitivityDefaults()
  } catch {
    /* backend not up yet; panel falls back to built-in defaults */
  }
  try {
    uqDefaults.value = await getUQDefaults()
  } catch {
    /* backend not up yet; panel falls back to built-in defaults */
  }
  try {
    calibPythons.value = (await getCalibrationPythons()).pythons ?? []
  } catch {
    /* interpreter discovery optional */
  }
  try {
    applyConfigPayload(await getConfig())
  } catch {
    /* backend not up yet */
  }
  // First thing on open: ask where outputs should go (sets outputsDir).
  outputsSetupOpen.value = true
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

function onRunCalibration(settings) {
  calib.start(
    model.modelId.value,
    {
      ...settings,
      python_path: pythonPath.value,
      config_outputs_dir: outputsDir.value.trim() || undefined,
    },
    // Live slider values, so gradient descent can start from the user's current
    // parameter values when "start from current" is enabled (#65).
    { ...sliders.paramDict.value },
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
      python_path: pythonPath.value,
      config_outputs_dir: outputsDir.value.trim() || undefined,
    },
    // Live slider values, so local SA with nominal="current" linearises about the
    // user's current parameter values rather than the model defaults (#65).
    { ...sliders.paramDict.value },
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
    python_path: pythonPath.value,
    config_outputs_dir: outputsDir.value.trim() || undefined,
  })
}

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
  sliders.clear()
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
  if (!model.hasModel.value) return onScreen
  try {
    return await runWithParams(sliders.paramDict.value, { allOutputs: true })
  } catch {
    return onScreen
  }
}

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
  if (!best || !model.hasModel.value) return null
  // The fit only names the calibrated parameters; everything else stays where
  // the sliders are, so the comparison isolates what calibration changed.
  const params = { ...sliders.paramDict.value, ...best }
  const data = await runWithParams(params)
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
  // plots no longer have a stable home.
  extraPlots.value = []
}

let timer = null
function scheduleRun() {
  if (!model.hasModel.value) return
  clearTimeout(timer)
  timer = setTimeout(runSimulation, 300)
}

/**
 * Run the model at `params` and return the raw result, without touching the
 * displayed run. Shared with runSimulation so an overlay (the best fit, #126)
 * is produced exactly the way the live trace is — same outputs, same protocol
 * vs single-run choice — instead of by a second, drifting copy of these rules.
 */
async function runWithParams(params, { allOutputs = false } = {}) {
  // Asking for every plottable variable means asking for some the solver cannot
  // resolve -- the CellML parser classifies variables the engine has no output
  // for (3compartment's pvn_module.R_v among them). Strict validation then
  // failed the whole request, so the wider save silently fell back to the
  // on-screen run and only one saved run ever covered an added plot (#150).
  const best = allOutputs ? { bestEffortOutputs: true } : {}
  // `allOutputs` widens the request to every plottable variable. A live run asks
  // only for what is on screen, because every slider drag pays for it — but a
  // saved run has to answer plots that do not exist yet (#148).
  const everything = allOutputs ? plottableVariables.value : []
  if (obs.hasProtocol.value) {
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
  if (obs.hasObsData.value) {
    const outputs = [
      ...new Set([
        ...obs.plotVariables.value.map((v) => v.qname),
        ...extraOutputNames.value,
        ...everything,
      ]),
    ]
    const data = await simulate(model.modelId.value, params, {
      outputs,
      outputsDir: outputsDir.value.trim() || undefined,
      ...best,
    })
    return { time: data.time, outputs: data.outputs }
  }
  const opts = { simTime: simTime.value, preTime: preTime.value, ...best }
  if (extraOutputNames.value.length || everything.length) {
    opts.outputs = [
      ...new Set([
        ...model.defaultOutputs.value,
        ...extraOutputNames.value,
        ...everything,
      ]),
    ]
  }
  const data = await simulate(model.modelId.value, params, opts)
  return { time: data.time, outputs: data.outputs }
}

async function runSimulation() {
  if (!model.hasModel.value) return
  sim.setRunning()
  const started = performance.now()
  try {
    if (obs.hasProtocol.value) {
      // Protocol run: pre_times/sim_times come from the obs_data protocol_info.
      // Request the obs-referenced variables plus any user-added plots, keep
      // every experiment, and render one plot per (experiment, variable).
      const outputs = [
        ...new Set([
          ...obs.plotVariables.value.map((v) => v.qname),
          ...extraOutputNames.value,
        ]),
      ]
      const data = await runProtocol(model.modelId.value, sliders.paramDict.value, {
        outputs,
        outputsDir: outputsDir.value.trim() || undefined,
      })
      sim.setExperiments(data.experiments, data.warnings, performance.now() - started)
    } else if (obs.hasObsData.value) {
      // Data-only obs_data: overlays only, no protocol. The manual t1/pre are
      // not used; run with backend defaults and plot the referenced variables
      // plus any user-added plots.
      const outputs = [
        ...new Set([
          ...obs.plotVariables.value.map((v) => v.qname),
          ...extraOutputNames.value,
        ]),
      ]
      const data = await simulate(model.modelId.value, sliders.paramDict.value, {
        outputs,
        outputsDir: outputsDir.value.trim() || undefined,
      })
      sim.setResult(data, performance.now() - started)
    } else {
      // No obs_data: manual t1/pre drive the single run. Default outputs are the
      // states; add any user-added plot variables so they're fetched too.
      const opts = { simTime: simTime.value, preTime: preTime.value }
      if (extraOutputNames.value.length) {
        opts.outputs = [
          ...new Set([...model.defaultOutputs.value, ...extraOutputNames.value]),
        ]
      }
      const data = await simulate(model.modelId.value, sliders.paramDict.value, opts)
      sim.setResult(data, performance.now() - started)
    }
  } catch (e) {
    sim.setError(errorMessage(e))
  }
}

// Plots grouped by experiment: each group has a heading and its plot cells.
// A protocol run shows every experiment, prefixing each with the controlled
// (params_to_change) inputs, then one plot per (experiment, variable).
const plotGroups = computed(() => {
  if (obs.hasProtocol.value && sim.experiments.value.length) {
    const vars = obs.plotVariables.value
    const labels = obs.experimentLabels.value
    const pi = obs.obsData.value?.protocol_info
    return sim.experiments.value.map((exp, e) => {
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
      return { key: `exp${e}`, expIdx: e, label, cells }
    })
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
    return [{ key: 'data-only', expIdx: 0, label: '', cells }]
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
    return [{ key: 'single', expIdx: 0, label: '', cells }]
  }
  return []
})

watch(
  () => ({ ...sliders.paramDict.value, _t: simTime.value, _p: preTime.value }),
  scheduleRun,
  { deep: true },
)
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
      <div v-if="!obs.hasObsData.value" class="time-controls" data-testid="time-controls">
        <label>t₁ <InputNumber v-model="simTime" :min="0" show-buttons size="small" /></label>
        <label>pre <InputNumber v-model="preTime" :min="0" show-buttons size="small" /></label>
      </div>
      <div
        v-else-if="obs.hasProtocol.value"
        class="protocol-summary"
        data-testid="protocol-summary"
      >
        Protocol: {{ obs.experimentCount.value }} experiment(s)
        <Button label="Clear obs data" size="small" text @click="obs.clearObsData()" />
      </div>
      <div v-else class="protocol-summary" data-testid="obs-overlay-summary">
        Obs overlays: {{ obs.dataItems.value.length }} item(s)
        <Button label="Clear obs data" size="small" text @click="obs.clearObsData()" />
      </div>
      <Button label="Run" icon="pi pi-play" size="small" @click="runSimulation" />
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
        <div v-show="leftTab === 'sensitivity'" class="left-pane left-pane-scroll">
          <SensitivityPanel
            :defaults="saDefaults"
            :can-run="canCalibrate"
            :mpiexec-available="mpiexecAvailable"
            :ad-available="adAvailable"
            :gradient-sources="gradientSources"
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
        <div
          v-show="centerTab === 'plots'"
          class="plot-groups"
          :class="{ 'has-maximized': effectiveMaximized }"
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
              <PlotPanel
                v-for="cell in g.cells"
                v-show="!effectiveMaximized || effectiveMaximized === cell.key"
                :key="cell.key"
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
                :saved-series="cell.savedSeries ?? []"
                :removable="!!cell.removeId"
                :switchable="!!cell.xLabel"
                maximizable
                :maximized="effectiveMaximized === cell.key"
                @toggle-maximize="toggleMaximizePlot(cell.key)"
                @remove="removeExtraPlot(cell.removeId)"
                @switch-axes="switchExtraPlotAxes(cell.removeId)"
              />
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
            :uq-params="uq.params.value"
            :uq-method="uq.method.value"
            @select-result="sa.selectResult"
            @remove-result="sa.removeResult"
            @clear-results="sa.clearResults"
          />
        </div>
        <StatusBar
          :status="sim.status.value"
          :message="sim.message.value"
          :last-run-ms="sim.lastRunMs.value"
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
          Defaults to the sibling <code>circulatory_autogen</code> clone. Pick a
          different checkout to develop against — runs use it on their next launch.
        </p>

        <hr class="settings-sep" />

        <label class="settings-row">
          <span
            class="settings-label"
            title="circulatory_autogen model_type: the backend the dropped CellML runs through. python / casadi_python generate a Python model from the CellML."
          >
            Generated model format
          </span>
          <Select
            :model-value="generatedModelFormat"
            :options="solverOpts.model_formats ?? ['cellml_only']"
            size="small"
            data-testid="model-format-select"
            @update:model-value="onFormatChange"
          />
        </label>
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
        <div
          v-for="f in solverInfoFields"
          :key="f.key"
          class="settings-row"
        >
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
.time-controls {
  display: flex;
  gap: 0.75rem;
  align-items: center;
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
.tab-dot {
  display: inline-block;
  width: 7px;
  height: 7px;
  margin-left: 0.35rem;
  border-radius: 50%;
  background: #ffc000;
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
</style>
