<script setup>
import { ref, computed, watch } from 'vue'
import ControlPanel from './components/ControlPanel.vue'
import VariableList from './components/VariableList.vue'
import PlotPanel from './components/PlotPanel.vue'
import FileImport from './components/FileImport.vue'
import StatusBar from './components/StatusBar.vue'
import InputNumber from 'primevue/inputnumber'
import Button from 'primevue/button'
import Message from 'primevue/message'

import { useModel } from './stores/useModel'
import { useSliders, shouldUseLog } from './stores/useSliders'
import { useSimResult } from './stores/useSimResult'
import { useObsData } from './stores/useObsData'
import { useParamsForId } from './stores/useParamsForId'
import { getVariables, simulate, runProtocol } from './lib/api'
import { overlayItemsFor } from './lib/plot'

const model = useModel()
const sliders = useSliders()
const sim = useSimResult()
const obs = useObsData()
const paramsForId = useParamsForId(sliders)

const simTime = ref(10)
const preTime = ref(0)

async function onModelLoaded(data) {
  model.setModel(data)
  obs.clearObsData()
  paramsForId.clear()
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

function onParamsLoaded(data) {
  paramsForId.importParams(data.params, data.filename)
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

// One plot cell per (experiment, variable) when obs_data drives the run;
// otherwise a single plot of the manual simulation.
const plotCells = computed(() => {
  // Protocol run: grid of (experiment, variable).
  if (obs.hasProtocol.value && sim.experiments.value.length) {
    const vars = obs.plotVariables.value
    const labels = obs.experimentLabels.value
    const cells = []
    sim.experiments.value.forEach((exp, e) => {
      const expLabel = labels[e] ?? `exp ${e}`
      for (const v of vars) {
        cells.push({
          key: `${e}:${v.qname}`,
          title: `${expLabel} · ${v.label}`,
          simResult: { time: exp.time, outputs: { [v.qname]: exp.outputs?.[v.qname] ?? [] } },
          dataItems: overlayItemsFor(obs.obsData.value, e, v.qname),
        })
      }
    })
    return cells
  }
  // Data-only obs_data (no protocol): one plot per referenced variable, with
  // overlays, from the single manual run.
  if (obs.hasObsData.value && obs.plotVariables.value.length && sim.result.value) {
    const out = sim.result.value.outputs ?? {}
    return obs.plotVariables.value.map((v) => ({
      key: v.qname,
      title: v.label,
      simResult: { time: sim.result.value.time, outputs: { [v.qname]: out[v.qname] ?? [] } },
      dataItems: overlayItemsFor(obs.obsData.value, 0, v.qname),
    }))
  }
  // Plain manual run.
  if (sim.result.value) {
    return [
      {
        key: 'single',
        title: model.name.value ?? '',
        simResult: sim.result.value,
        dataItems: [],
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
      <h1>CellML Explorer</h1>
      <span v-if="model.name.value" class="model-name">{{ model.name.value }}</span>
      <div class="spacer" />
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
        <ControlPanel
          :sliders="sliders.sliders"
          @update="onSliderUpdate"
          @remove="({ qname }) => sliders.removeSlider(qname)"
        />
      </aside>

      <section class="col col-center">
        <Message
          v-if="sim.warnings.value.length"
          severity="warn"
          :closable="false"
          class="warn-banner"
          data-testid="sim-warning"
        >
          {{ sim.warnings.value.join(' ') }}
        </Message>
        <div class="plot-grid" :class="{ single: plotCells.length <= 1 }">
          <PlotPanel
            v-for="cell in plotCells"
            :key="cell.key"
            class="plot-cell"
            :title="cell.title"
            :sim-result="cell.simResult"
            :data-items="cell.dataItems"
          />
          <p v-if="plotCells.length === 0" class="empty-hint">
            Upload a CellML model and run a simulation.
          </p>
        </div>
        <StatusBar
          :status="sim.status.value"
          :message="sim.message.value"
          :last-run-ms="sim.lastRunMs.value"
        />
      </section>

      <aside class="col col-right">
        <FileImport
          :model-id="model.modelId.value"
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
}
.col-center {
  display: flex;
  flex-direction: column;
}
.warn-banner {
  margin: 0.5rem;
}
.plot-grid {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(320px, 1fr));
  gap: 0.5rem;
  padding: 0.5rem;
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
