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
} from './lib/api'
import { overlayItemsFor, controlledSeries } from './lib/plot'

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

// Where calibration outputs are written; blank => backend uses a temp dir.
const outputsDir = ref('')

// Raw params_for_id entries (incl. param_type, which the slider store drops) and
// the loaded CSV filename — fed to the params Edit dialog so it can pre-fill rows
// and version the new filename.
const loadedParamsRaw = ref([])
const loadedParamsFilename = ref(null)

// Python interpreter chosen once in the top bar and shared by the Sensitivity,
// Calibration and UQ runs. Blank => backend uses its default interpreter.
const pythonPath = ref('')
const pythonBrowserOpen = ref(false)

// circulatory_autogen source directory (top-bar "CA dir"), shared server-side via
// /api/config. Defaults to the sibling clone; pick a different checkout to dev against.
const caDir = ref('')
const caExists = ref(true)
const caBrowserOpen = ref(false)

async function applyCaDir(dir) {
  try {
    const c = await setConfig(dir)
    caDir.value = c.ca_dir
    caExists.value = c.ca_exists
  } catch {
    /* leave previous value on error */
  }
}

// Settings popup (CA dir + theme).
const settingsOpen = ref(false)

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
    const c = await getConfig()
    caDir.value = c.ca_dir
    caExists.value = c.ca_exists
  } catch {
    /* backend not up yet */
  }
})

const pythonOptions = computed(() => {
  const opts = [
    { label: 'Server default', value: '' },
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
  calib.start(model.modelId.value, {
    ...settings,
    python_path: pythonPath.value,
    config_outputs_dir: outputsDir.value.trim() || undefined,
  })
}

// Sensitivity reuses the same prerequisites as calibration (model + obs + params).
function onRunSensitivity(settings) {
  sa.start(model.modelId.value, {
    ...settings,
    python_path: pythonPath.value,
    config_outputs_dir: outputsDir.value.trim() || undefined,
  })
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
      // Request only the obs-referenced variables, keep every experiment, and
      // render one plot per (experiment, variable).
      const outputs = obs.plotVariables.value.map((v) => v.qname)
      const data = await runProtocol(model.modelId.value, sliders.paramDict.value, {
        outputs,
      })
      sim.setExperiments(data.experiments, data.warnings, performance.now() - started)
    } else if (obs.hasObsData.value) {
      // Data-only obs_data: overlays only, no protocol. The manual t1/pre are
      // not used; run with backend defaults and plot the referenced variables.
      const outputs = obs.plotVariables.value.map((v) => v.qname)
      const data = await simulate(model.modelId.value, sliders.paramDict.value, {
        outputs,
      })
      sim.setResult(data, performance.now() - started)
    } else {
      // No obs_data: manual t1/pre drive the single run.
      const data = await simulate(model.modelId.value, sliders.paramDict.value, {
        simTime: simTime.value,
        preTime: preTime.value,
      })
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
      const label = labels[e]
        ? `Experiment ${e}: ${labels[e]}`
        : `Experiment ${e}`
      return { key: `exp${e}`, label, cells }
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
    return [{ key: 'data-only', label: '', cells }]
  }
  // Plain manual run.
  if (sim.result.value) {
    return [
      {
        key: 'single',
        label: '',
        cells: [
          {
            key: 'single',
            title: model.name.value ?? '',
            varLabel: '',
            controlled: false,
            simResult: sim.result.value,
            dataItems: [],
          },
        ],
      },
    ]
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
            :lines="sa.lines.value"
            :state="sa.state.value"
            :error="sa.error.value"
            @run="onRunSensitivity"
            @cancel="sa.cancel()"
          />
        </div>
        <div v-show="leftTab === 'calibration'" class="left-pane left-pane-scroll">
          <CalibrationPanel
            :defaults="calibDefaults"
            :can-run="canCalibrate"
            :lines="calib.lines.value"
            :state="calib.state.value"
            :cost="calib.cost.value"
            :error="calib.error.value"
            @run="onRunCalibration"
            @cancel="calib.cancel()"
          />
        </div>
        <div v-show="leftTab === 'uq'" class="left-pane left-pane-scroll">
          <UQPanel
            :defaults="uqDefaults"
            :can-run="canCalibrate"
            :lines="uq.lines.value"
            :state="uq.state.value"
            :error="uq.error.value"
            @run="onRunUQ"
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
          />
        </div>
        <div v-show="centerTab === 'analysis'" class="plot-groups">
          <AnalysisPanel
            :indices="sa.indices.value"
            :param-names="sa.paramNames.value"
            :output-names="sa.outputNames.value"
            :param-labels="paramLabels"
            :percent-error="calib.percentError.value"
            :std-error="calib.stdError.value"
            :error-labels="calib.errorLabels.value"
            :uq-params="uq.params.value"
            :uq-method="uq.method.value"
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
          @model-loaded="onModelLoaded"
          @obs-data-loaded="obs.setObsData"
          @params-loaded="onParamsLoaded"
        />
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
.col-right {
  border-left: 1px solid var(--p-content-border-color, #333);
  display: flex;
  flex-direction: column;
}
</style>
