<script setup>
import { ref, computed } from 'vue'
import Button from 'primevue/button'
import ParamInputPlot from './ParamInputPlot.vue'
import { controlledSeries } from '../lib/plot'
import {
  SHAPES,
  buildProtocolInfo,
  subexpBoundaries,
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
        <label>pre_time
          <input
            type="number"
            step="any"
            :value="activeExperiment.preTime"
            @input="onNum(activeExperiment, 'preTime', $event.target.value)"
          />
        </label>
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

      <!-- Subexperiment durations -->
      <div class="pie-subs">
        <span class="pie-label">subexperiments (durations):</span>
        <span v-for="(sub, s) in activeExperiment.subexps" :key="s" class="pie-sub">
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
        <Button icon="pi pi-plus" label="subexp" text size="small" data-testid="add-subexp" @click="addSubexp(model, activeExp)" />
      </div>

      <!-- params_to_change -->
      <div class="pie-add-param">
        <select v-model="newParam" data-testid="param-select">
          <option value="">add controlled parameter…</option>
          <option v-for="n in availableNames" :key="n" :value="n">{{ n }}</option>
        </select>
        <Button label="Add" size="small" data-testid="add-param" :disabled="!newParam" @click="onAddParam" />
      </div>

      <div v-for="qname in paramQnames" :key="qname" class="pc-param" data-testid="pc-param">
        <div class="pc-head">
          <span class="pc-qname" :title="qname">{{ qname }}</span>
          <Button icon="pi pi-times" text rounded size="small" aria-label="remove param" @click="removeParam(model, qname)" />
        </div>
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
        <ParamInputPlot :series="seriesByQname[qname]" :boundaries="boundaries" :title="qname" />
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
.pie-subs,
.pie-add-param {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 0.5rem;
}
.pie-fields label,
.pie-add-param {
  display: flex;
  flex-direction: column;
  font-size: 0.72rem;
  gap: 0.15rem;
}
.pie-add-param {
  flex-direction: row;
  align-items: flex-end;
}
.pie-label {
  font-size: 0.72rem;
  opacity: 0.7;
}
.pie-sub {
  display: inline-flex;
  align-items: center;
}
.pie-sub input {
  width: 4.5rem;
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
