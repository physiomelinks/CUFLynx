<script setup>
import { ref, watch } from 'vue'
import ControlPanel from './components/ControlPanel.vue'
import VariableList from './components/VariableList.vue'
import PlotPanel from './components/PlotPanel.vue'
import FileImport from './components/FileImport.vue'
import StatusBar from './components/StatusBar.vue'
import InputNumber from 'primevue/inputnumber'
import Button from 'primevue/button'

import { useModel } from './stores/useModel'
import { useSliders, shouldUseLog } from './stores/useSliders'
import { useSimResult } from './stores/useSimResult'
import { useObsData } from './stores/useObsData'
import { useParamsForId } from './stores/useParamsForId'
import { getVariables, simulate, runProtocol } from './lib/api'

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
  // Without min/max metadata, seed a +/- one-decade range around the default.
  const base = initial != null && initial !== 0 ? Math.abs(initial) : 1
  const min = initial != null ? Math.max(0, initial - base) : 0
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
    let data
    if (obs.hasObsData.value) {
      data = await runProtocol(model.modelId.value, sliders.paramDict.value)
      // Flatten first experiment for the chart.
      data = data.experiments?.[0] ?? { time: [], outputs: {} }
    } else {
      data = await simulate(model.modelId.value, sliders.paramDict.value, {
        simTime: simTime.value,
        preTime: preTime.value,
      })
    }
    sim.setResult(data, performance.now() - started)
  } catch (e) {
    sim.setError(e?.response?.data?.detail || String(e))
  }
}

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
      <div v-if="obs.useManualTime.value" class="time-controls">
        <label>t₁ <InputNumber v-model="simTime" :min="0" show-buttons size="small" /></label>
        <label>pre <InputNumber v-model="preTime" :min="0" show-buttons size="small" /></label>
      </div>
      <div v-else class="protocol-summary" data-testid="protocol-summary">
        Protocol: {{ obs.experimentCount.value }} experiment(s)
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
        <PlotPanel :sim-result="sim.result.value" :data-items="obs.dataItems.value" />
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
.col-right {
  border-left: 1px solid var(--p-content-border-color, #333);
  display: flex;
  flex-direction: column;
}
</style>
