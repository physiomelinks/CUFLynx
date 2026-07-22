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
} from './lib/api'
import { overlayItemsFor, controlledSeries, buildExtraPlotCells } from './lib/plot'
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

// Last value the server told us about. Hydrating pythonPath from /api/config
// triggers the watch below, and without this it would POST the value straight
// back on every load.
let serverPythonPath = ''

function applyConfigPayload(c) {
  caDir.value = c.ca_dir
  caExists.value = c.ca_exists
  solverOpts.value = c
  generatedModelFormat.value = c.generated_model_format ?? 'cellml_only'
  solver.value = c.solver ?? ''
  solverInfo.value = { ...(c.solver_info ?? {}) }
  cppCompiler.value = c.cpp_compiler ?? { present: true, hint: '' }
  pythonPath.value = c.python_path ?? ''
  serverPythonPath = pythonPath.value
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
    await setConfig({ pythonPath: p })
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

// Left column tab: 'params' | 'sensitivity' | 'calibration' | 'uq'
const leftTab = ref('params')
// Center column tab: 'plots' | 'progress' | 'analysis'
const centerTab = ref('plots')

// User-added output plots. Each plot is scoped to one experiment group via
// `groupKey` (e.g. 'exp0', 'data-only', 'single') so the "+ Add plot" button at
// the bottom of an experiment creates a plot for that experiment's run only.
// { id, groupKey, expIdx, qname, label }
const extraPlots = ref([])
let nextPlotId = 1

// Model variables that can be plotted as time series (states + algebraic);
// params are constants set via sliders, so they're excluded.
const plottableVariables = computed(() => {
  const v = model.variables.value
  return [...(v.odes ?? []), ...(v.algebraic ?? [])]
})

// Extra-plot qnames to append to a run's requested outputs so the chosen
// variables come back from the engine.
const extraOutputNames = computed(() => [
  ...new Set(extraPlots.value.map((p) => p.qname)),
])

// Add-plot dialog state.
const addPlotOpen = ref(false)
const addPlotTarget = ref({ groupKey: null, expIdx: 0, label: '' })
const addPlotVar = ref(null)

// Variables offered in the dialog: plottable vars not already shown in the
// target group (neither an obs-derived nor an already-added plot).
const addPlotChoices = computed(() => {
  const key = addPlotTarget.value.groupKey
  const taken = new Set(
    extraPlots.value.filter((p) => p.groupKey === key).map((p) => p.qname),
  )
  if (key && key.startsWith('exp')) {
    for (const v of obs.plotVariables.value) taken.add(v.qname)
  } else if (key === 'data-only') {
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
  addPlotOpen.value = true
}

function confirmAddPlot() {
  const qname = addPlotVar.value
  if (!qname) return
  extraPlots.value.push({
    id: nextPlotId++,
    groupKey: addPlotTarget.value.groupKey,
    expIdx: addPlotTarget.value.expIdx,
    qname,
    label: qname,
  })
  addPlotOpen.value = false
  // Re-run so the newly requested variable is fetched for this experiment.
  runSimulation()
}

function removeExtraPlot(id) {
  extraPlots.value = extraPlots.value.filter((p) => p.id !== id)
}

// Extra-plot cells for a group, each a single-variable plot built from that
// group's own simulation outputs.
function extraCellsFor(groupKey, time, outputs) {
  return buildExtraPlotCells(extraPlots.value, groupKey, time, outputs)
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
  const defaultLabel = packaged.value
    ? 'Bundled (CUFLynx) — runs analysis in-app'
    : 'Server default'
  const opts = [
    { label: defaultLabel, value: '' },
    ...calibPythons.value.map((p) => ({
      label:
        `Python ${p.version} — ${p.path}` +
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

// Missing required deps for the chosen interpreter (shown as a warning chip).
const pythonNotReady = computed(() => {
  const p = calibPythons.value.find((x) => x.path === pythonPath.value)
  return p && !p.ready ? p.missing : null
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
    exportNotice.value = `Export failed: ${e?.response?.data?.detail || String(e)}`
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
    exportNotice.value = `Export failed: ${e?.response?.data?.detail || String(e)}`
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
    sim.setError(e?.response?.data?.detail || String(e))
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
          controlled: true,
          simResult: { time: c.time, outputs: { [c.qname]: c.values } },
          dataItems: [],
        })
      }
      for (const v of vars) {
        cells.push({
          key: `${e}:${v.qname}`,
          title: v.label,
          varLabel: v.label,
          controlled: false,
          simResult: { time: exp.time, outputs: { [v.qname]: exp.outputs?.[v.qname] ?? [] } },
          dataItems: overlayItemsFor(obs.obsData.value, e, v.qname),
        })
      }
      cells.push(...extraCellsFor(`exp${e}`, exp.time, exp.outputs))
      const label = labels[e]
        ? `Experiment ${e}: ${labels[e]}`
        : `Experiment ${e}`
      return { key: `exp${e}`, expIdx: e, label, cells }
    })
  }
  // Data-only obs_data: one group, no heading, one plot per referenced variable.
  if (obs.hasObsData.value && obs.plotVariables.value.length && sim.result.value) {
    const out = sim.result.value.outputs ?? {}
    const cells = obs.plotVariables.value.map((v) => ({
      key: v.qname,
      title: v.label,
      varLabel: v.label,
      controlled: false,
      simResult: { time: sim.result.value.time, outputs: { [v.qname]: out[v.qname] ?? [] } },
      dataItems: overlayItemsFor(obs.obsData.value, 0, v.qname),
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
        controlled: false,
        simResult: { time: sim.result.value.time, outputs: mainOutputs },
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

    <main class="columns">
      <aside class="col col-left">
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
        <div v-show="centerTab === 'plots'" class="plot-groups">
          <section
            v-for="g in plotGroups"
            :key="g.key"
            class="exp-group"
            data-testid="exp-group"
          >
            <h2 v-if="g.label" class="exp-heading">{{ g.label }}</h2>
            <div class="plot-grid" :class="{ single: g.cells.length <= 1 }">
              <PlotPanel
                v-for="cell in g.cells"
                :key="cell.key"
                class="plot-cell"
                :title="cell.title"
                :var-label="cell.varLabel"
                :tag="cell.controlled ? 'controlled' : ''"
                :stepped="cell.controlled"
                :sim-result="cell.simResult"
                :data-items="cell.dataItems"
                :removable="!!cell.removeId"
                @remove="removeExtraPlot(cell.removeId)"
              />
            </div>
            <div v-if="plottableVariables.length" class="add-plot-row">
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

      <aside class="col col-right">
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
          v-if="solverInfo.method === 'semi_implicit_euler'"
          class="settings-warn"
          data-testid="semi-implicit-warning"
        >
          ⚠ semi_implicit_euler is a first-order, fixed-step damped solver — it enables
          AD on stiff models but is <strong>less accurate than CVODES</strong>. Reduce
          dt and run a convergence study (confirm results stop changing) before trusting them.
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

    <Dialog
      v-model:visible="addPlotOpen"
      modal
      :header="addPlotTarget.label ? `Add plot — ${addPlotTarget.label}` : 'Add plot'"
      :style="{ width: '24rem' }"
    >
      <div class="add-plot-dialog">
        <label class="add-plot-label" for="add-plot-var">Variable</label>
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
  grid-template-columns: 320px 1fr 300px;
  flex: 1;
  min-height: 0;
}
.col {
  min-height: 0;
  overflow: hidden;
}
.col-left {
  border-right: 1px solid var(--p-content-border-color, #333);
  display: flex;
  flex-direction: column;
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
.plot-cell {
  min-height: 240px;
  border: 1px solid var(--p-content-border-color, #333);
  border-radius: 6px;
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
}
</style>
