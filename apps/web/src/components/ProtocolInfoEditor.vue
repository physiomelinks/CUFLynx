<script setup>
import { ref, computed } from 'vue'
import Button from 'primevue/button'
import ParamInputPlot, { AXIS_W, RIGHT_PAD } from './ParamInputPlot.vue'
import { controlledSeries } from '../lib/plot'
import {
  SHAPES,
  buildProtocolInfo,
  subexpBoundaries,
  experimentTotalSim,
  makeCell,
  addExperiment,
  removeExperiment,
  addSubexp,
  removeSubexp,
  addParam,
  removeParam,
} from '../lib/protocolInfo'

const props = defineProps({
  model: { type: Object, required: true }, // mutated in place (parent owns it)
  allNames: { type: Array, default: () => [] },
  activeExp: { type: Number, default: 0 },
})
const emit = defineEmits(['update:activeExp'])

const newParam = ref('')

const activeExperiment = computed(() => props.model.experiments[props.activeExp] ?? null)
const subexpCount = computed(() => activeExperiment.value?.subexps.length ?? 0)
const paramQnames = computed(() => Object.keys(props.model.params))
const availableNames = computed(() =>
  props.allNames.filter((n) => !(n in props.model.params)),
)

// Compile the working model to a real protocol_info and reuse controlledSeries so
// the plot shows exactly what will be saved (ramp/pulse compiled to traces).
const seriesByQname = computed(() => {
  const arr = controlledSeries(buildProtocolInfo(props.model, null), props.activeExp)
  const map = {}
  for (const s of arr) map[s.qname] = s
  return map
})
const boundaries = computed(() => subexpBoundaries(activeExperiment.value))
const preTime = computed(() => Number(activeExperiment.value?.preTime) || 0)
const totalSim = computed(() => experimentTotalSim(activeExperiment.value))

function setActive(i) {
  emit('update:activeExp', i)
}
function onAddExperiment() {
  addExperiment(props.model)
  setActive(props.model.experiments.length - 1)
}
function onRemoveExperiment(e) {
  removeExperiment(props.model, e)
  if (props.activeExp >= props.model.experiments.length) {
    setActive(Math.max(0, props.model.experiments.length - 1))
  }
}
function onAddParam() {
  if (!newParam.value) return
  addParam(props.model, newParam.value)
  newParam.value = ''
}
function onNum(obj, field, value) {
  obj[field] = value === '' ? null : Number(value)
}
function shapeOptions(cell) {
  // 'trace' is only offered while the cell still references a preserved trace.
  return cell.shape === 'trace' ? [...SHAPES, 'trace'] : SHAPES
}
function onShapeChange(qname, s, shape) {
  const dur = activeExperiment.value.subexps[s].duration
  props.model.params[qname][props.activeExp][s] = makeCell(shape, dur)
}
</script>

<template>
  <div class="pie">
    <!-- Experiment tabs -->
    <div class="pie-exps">
      <button
        v-for="(exp, e) in model.experiments"
        :key="e"
        type="button"
        class="pie-tab"
        :class="{ active: e === activeExp }"
        data-testid="exp-tab"
        @click="setActive(e)"
      >
        {{ exp.label || `experiment_${e}` }}
      </button>
      <Button label="+ experiment" size="small" text data-testid="add-exp" @click="onAddExperiment" />
    </div>

    <div v-if="activeExperiment" class="pie-exp">
      <div class="pie-fields">
        <label>label
          <input
            type="text"
            :value="activeExperiment.label"
            @input="activeExperiment.label = $event.target.value"
          />
        </label>
        <Button
          label="+ subexp"
          icon="pi pi-plus"
          size="small"
          text
          data-testid="add-subexp"
          @click="addSubexp(model, activeExp)"
        />
        <Button
          label="Remove experiment"
          icon="pi pi-times"
          size="small"
          text
          severity="danger"
          :disabled="model.experiments.length <= 1"
          data-testid="remove-exp"
          @click="onRemoveExperiment(activeExp)"
        />
      </div>

      <!-- params_to_change -->
      <div class="pie-add-param">
        <select v-model="newParam" data-testid="param-select">
          <option value="">add controlled parameter…</option>
          <option v-for="n in availableNames" :key="n" :value="n">{{ n }}</option>
        </select>
        <Button label="Add" size="small" data-testid="add-param" :disabled="!newParam" @click="onAddParam" />
      </div>

      <!-- Time-aligned timeline: pre_time + each subexp duration sit over the plot's x-axis. -->
      <div
        class="pie-timeline"
        :style="{ paddingLeft: AXIS_W + 'px', paddingRight: RIGHT_PAD + 'px' }"
      >
        <div class="tl-seg tl-pre" :style="{ flexGrow: Math.max(preTime, 0.001) }">
          <span class="tl-h">pre</span>
          <input
            type="number"
            step="any"
            :value="activeExperiment.preTime"
            data-testid="pre-time"
            @input="onNum(activeExperiment, 'preTime', $event.target.value)"
          />
        </div>
        <div
          v-for="(sub, s) in activeExperiment.subexps"
          :key="s"
          class="tl-seg"
          :style="{ flexGrow: Math.max(sub.duration, 0.001) }"
        >
          <span class="tl-h">sub {{ s }}</span>
          <span class="tl-srow">
            <input
              type="number"
              step="any"
              :value="sub.duration"
              data-testid="subexp-dur"
              @input="onNum(sub, 'duration', $event.target.value)"
            />
            <Button
              icon="pi pi-minus"
              text
              size="small"
              :disabled="activeExperiment.subexps.length <= 1"
              data-testid="remove-subexp"
              @click="removeSubexp(model, activeExp, s)"
            />
          </span>
        </div>
      </div>

      <!-- No controlled params → a single empty timeline plot (axes + subexp lines). -->
      <ParamInputPlot
        v-if="paramQnames.length === 0"
        :series="null"
        :pre-time="preTime"
        :total-sim="totalSim"
        :boundaries="boundaries"
      />

      <div v-for="qname in paramQnames" :key="qname" class="pc-param" data-testid="pc-param">
        <div class="pc-head">
          <span class="pc-qname" :title="qname">{{ qname }}</span>
          <Button icon="pi pi-times" text rounded size="small" aria-label="remove param" @click="removeParam(model, qname)" />
        </div>
        <ParamInputPlot
          :series="seriesByQname[qname]"
          :pre-time="preTime"
          :total-sim="totalSim"
          :boundaries="boundaries"
          :title="qname"
        />
        <div class="pc-cells">
          <div v-for="s in subexpCount" :key="s - 1" class="pc-cell">
            <span class="pc-cell-h">sub {{ s - 1 }}</span>
            <select
              :value="model.params[qname][activeExp][s - 1].shape"
              data-testid="cell-shape"
              @change="onShapeChange(qname, s - 1, $event.target.value)"
            >
              <option v-for="sh in shapeOptions(model.params[qname][activeExp][s - 1])" :key="sh" :value="sh">{{ sh }}</option>
            </select>
            <template v-if="model.params[qname][activeExp][s - 1].shape === 'constant'">
              <input type="number" step="any" placeholder="value" :value="model.params[qname][activeExp][s - 1].value" @input="onNum(model.params[qname][activeExp][s - 1], 'value', $event.target.value)" />
            </template>
            <template v-else-if="model.params[qname][activeExp][s - 1].shape === 'ramp'">
              <input type="number" step="any" placeholder="from" :value="model.params[qname][activeExp][s - 1].from" @input="onNum(model.params[qname][activeExp][s - 1], 'from', $event.target.value)" />
              <input type="number" step="any" placeholder="to" :value="model.params[qname][activeExp][s - 1].to" @input="onNum(model.params[qname][activeExp][s - 1], 'to', $event.target.value)" />
            </template>
            <template v-else-if="model.params[qname][activeExp][s - 1].shape === 'pulse'">
              <input type="number" step="any" placeholder="baseline" :value="model.params[qname][activeExp][s - 1].baseline" @input="onNum(model.params[qname][activeExp][s - 1], 'baseline', $event.target.value)" />
              <input type="number" step="any" placeholder="peak" :value="model.params[qname][activeExp][s - 1].peak" @input="onNum(model.params[qname][activeExp][s - 1], 'peak', $event.target.value)" />
              <input type="number" step="any" placeholder="t start" :value="model.params[qname][activeExp][s - 1].ts" @input="onNum(model.params[qname][activeExp][s - 1], 'ts', $event.target.value)" />
              <input type="number" step="any" placeholder="t end" :value="model.params[qname][activeExp][s - 1].te" @input="onNum(model.params[qname][activeExp][s - 1], 'te', $event.target.value)" />
            </template>
            <template v-else>
              <span class="pc-traceref" :title="model.params[qname][activeExp][s - 1].key">trace: {{ model.params[qname][activeExp][s - 1].key }}</span>
            </template>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.pie {
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
}
.pie-exps {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 0.25rem;
}
.pie-tab {
  font-size: 0.78rem;
  padding: 0.2rem 0.5rem;
  border: 1px solid var(--p-content-border-color, #444);
  border-radius: 5px;
  background: transparent;
  color: inherit;
  cursor: pointer;
}
.pie-tab.active {
  border-color: var(--p-primary-color, #5b9bd5);
  background: var(--p-highlight-background, rgba(91, 155, 213, 0.2));
}
.pie-fields,
.pie-add-param {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 0.5rem;
}
.pie-fields label {
  display: flex;
  flex-direction: column;
  font-size: 0.72rem;
  gap: 0.15rem;
}
.pie-add-param {
  align-items: flex-end;
}
/* Timeline: segments grow proportionally to time and align with the plot x-axis
   (padding matches the chart's fixed y-axis width + right padding). */
.pie-timeline {
  display: flex;
  align-items: stretch;
  gap: 2px;
}
.tl-seg {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 0.1rem;
  min-width: 3.4rem;
  flex-basis: 0;
  border-left: 1px solid var(--p-content-border-color, #333);
  padding: 0.1rem 0.15rem;
}
.tl-pre {
  opacity: 0.8;
}
.tl-h {
  font-size: 0.62rem;
  opacity: 0.55;
}
.tl-srow {
  display: flex;
  align-items: center;
}
.tl-seg input {
  width: 100%;
  min-width: 2.4rem;
  font-size: 0.74rem;
}
.pc-param {
  border: 1px solid var(--p-content-border-color, #333);
  border-radius: 6px;
  padding: 0.4rem;
}
.pc-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
}
.pc-qname {
  font-size: 0.8rem;
  font-weight: 600;
}
.pc-cells {
  display: flex;
  flex-wrap: wrap;
  gap: 0.4rem;
  margin: 0.3rem 0;
}
.pc-cell {
  display: flex;
  flex-direction: column;
  gap: 0.15rem;
  border: 1px solid var(--p-content-border-color, #2a2a2a);
  border-radius: 5px;
  padding: 0.3rem;
}
.pc-cell-h {
  font-size: 0.66rem;
  opacity: 0.6;
}
.pc-cell input {
  width: 5rem;
  font-size: 0.76rem;
}
.pc-traceref {
  font-size: 0.72rem;
  opacity: 0.7;
}
</style>
