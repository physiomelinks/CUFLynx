<script setup>
import { ref, computed } from 'vue'
import Button from 'primevue/button'
import ParamInputPlot, { AXIS_W, RIGHT_PAD, PRE_FRAC } from './ParamInputPlot.vue'
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
  // 0-based subexp index to lightly highlight in the active experiment's timeline
  // (e.g. the subexperiment a selected data_item targets). null = no highlight.
  highlightSubexp: { type: Number, default: null },
  // The experiment the highlight belongs to: the subexp is only highlighted while
  // that experiment is the active one (a data_item lives in a single experiment).
  highlightExp: { type: Number, default: null },
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

// Highlight a subexp only when one is requested AND (no experiment is pinned, or
// the pinned experiment is the one currently shown) — so a data_item's highlight
// appears only in its own experiment's timeline, not on every experiment tab.
const showHighlight = computed(
  () =>
    props.highlightSubexp != null &&
    (props.highlightExp == null || props.activeExp === props.highlightExp),
)
// Pads a "time track" so its segments line up with the plot's drawing area
// (left = chart y-axis width, right = chart padding).
const trackStyle = computed(() => ({
  paddingLeft: AXIS_W + 'px',
  paddingRight: RIGHT_PAD + 'px',
}))
// Same fixed-fraction pre slot the plot uses, so tracks align with its x-axis.
const preFlex = computed(() => Math.max(totalSim.value * PRE_FRAC, 0.001))

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
// Horizontal bounds the edit popup must stay within: the window, tightened by any
// clipping/scrolling ancestor (e.g. the obs_data dialog body) so the popup is never
// hidden behind a clipped edge.
function editorBounds(el) {
  let left = 0
  let right = window.innerWidth
  let node = el.parentElement
  while (node) {
    const s = getComputedStyle(node)
    if (/(auto|scroll|hidden|clip)/.test(s.overflow + s.overflowX + s.overflowY)) {
      const r = node.getBoundingClientRect()
      left = Math.max(left, r.left)
      right = Math.min(right, r.right)
    }
    node = node.parentElement
  }
  return { left, right }
}
// When a cell's edit popup opens, if it would run off the right edge (of the window
// or its clipping container), shift it left so every input stays reachable on screen
// instead of stretching off and being clipped.
function fitEditor(e) {
  const edit = e.currentTarget.querySelector('.tt-edit')
  if (!edit) return
  // clear any prior shift so the CSS-default position is measured first
  edit.style.left = ''
  edit.style.right = ''
  edit.style.transform = ''
  requestAnimationFrame(() => {
    const rect = edit.getBoundingClientRect()
    const margin = 8
    const { left: bl, right: br } = editorBounds(edit)
    if (rect.left >= bl + margin && rect.right <= br - margin) return // already fits
    const parent = edit.offsetParent
    if (!parent) return
    const parentLeft = parent.getBoundingClientRect().left
    let rendered = Math.min(rect.left, br - margin - rect.width)
    rendered = Math.max(rendered, bl + margin)
    edit.style.left = `${rendered - parentLeft}px`
    edit.style.right = 'auto'
    edit.style.transform = 'none'
  })
}
// Time fields (step/pulse start & end) are clamped to [0, dur] so a perturbation
// can only move within its own subexperiment, never past its boundary.
function onTimeNum(obj, field, value, dur) {
  if (value === '') {
    obj[field] = null
    return
  }
  const v = Number(value)
  obj[field] = Number.isFinite(v) ? Math.min(Math.max(v, 0), Number(dur) || 0) : null
}
function shapeOptions(cell) {
  // 'trace' is only offered while the cell still references a preserved trace.
  return cell.shape === 'trace' ? [...SHAPES, 'trace'] : SHAPES
}
function onShapeChange(qname, s, shape) {
  const dur = activeExperiment.value.subexps[s].duration
  props.model.params[qname][props.activeExp][s] = makeCell(shape, dur)
}
// Polyline points (0..12 x 0..10 box) for a cell's shape icon (constant shows its
// number, trace shows a file icon — handled in the template).
function shapeIcon(cell) {
  switch (cell?.shape) {
    case 'constant':
      return '0,9 0,1 12,1' // upside-down L: a held level
    case 'ramp':
      return '0,9 3,9 9,1 12,1'
    case 'step':
      return '0,9 6,9 6,1 12,1'
    case 'pulse':
      return '0,9 3,9 3,1 7,1 7,9 12,9'
    default:
      return null
  }
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

      <div class="pie-plotwrap">
        <Button
          class="pie-addsub"
          label="+ subexp"
          size="small"
          text
          data-testid="add-subexp"
          @click="addSubexp(model, activeExp)"
        />

        <!-- Time-aligned timeline: pre_time + each subexp duration over the plot's x-axis. -->
        <div class="tt-track" :style="trackStyle">
        <div class="tt-pre dim" :style="{ flexGrow: preFlex }" @mouseenter="fitEditor" @focusin="fitEditor">
          <span class="dim-val">{{ activeExperiment.preTime }}</span>
          <span class="dim-line" />
          <div class="tt-edit">
            <span class="tt-lbl">pre</span>
            <input
              type="number"
              step="any"
              :value="activeExperiment.preTime"
              data-testid="pre-time"
              @input="onNum(activeExperiment, 'preTime', $event.target.value)"
            />
          </div>
        </div>
        <div
          v-for="(sub, s) in activeExperiment.subexps"
          :key="s"
          class="tt-seg dim"
          :class="{ 'tt-highlight': showHighlight && s === highlightSubexp }"
          :style="{ flexGrow: Math.max(sub.duration, 0.001) }"
          @mouseenter="fitEditor"
          @focusin="fitEditor"
        >
          <span class="dim-val">{{ sub.duration }}</span>
          <span class="dim-line" />
          <div class="tt-edit">
            <span class="tt-lbl">sub {{ s }}</span>
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
          </div>
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
          <Button icon="pi pi-times" text rounded size="small" severity="danger" aria-label="remove param" @click="removeParam(model, qname)" />
        </div>
        <ParamInputPlot
          :series="seriesByQname[qname]"
          :pre-time="preTime"
          :total-sim="totalSim"
          :boundaries="boundaries"
          :title="qname"
        />
        <!-- Per-subexp value cells, time-aligned with the plot's subexp lines. -->
        <div class="tt-track" :style="trackStyle">
          <div class="tt-pre" :style="{ flexGrow: preFlex }" />
          <div
            v-for="s in subexpCount"
            :key="s - 1"
            class="tt-seg"
            :class="{ 'tt-highlight': showHighlight && s - 1 === highlightSubexp }"
            :style="{ flexGrow: Math.max(activeExperiment.subexps[s - 1].duration, 0.001) }"
            @mouseenter="fitEditor"
            @focusin="fitEditor"
          >
            <span class="tt-mark">
              <i v-if="model.params[qname][activeExp][s - 1].shape === 'trace'" class="pi pi-file tt-ico" />
              <svg v-else class="tt-ico" viewBox="0 0 12 10">
                <polyline :points="shapeIcon(model.params[qname][activeExp][s - 1])" />
              </svg>
            </span>
            <div class="tt-edit">
              <select
                :value="model.params[qname][activeExp][s - 1].shape"
                data-testid="cell-shape"
                @change="onShapeChange(qname, s - 1, $event.target.value)"
              >
                <option v-for="sh in shapeOptions(model.params[qname][activeExp][s - 1])" :key="sh" :value="sh">{{ sh }}</option>
              </select>
              <template v-if="model.params[qname][activeExp][s - 1].shape === 'constant'">
                <label class="tt-field"><span class="tt-cap">value</span>
                  <input type="number" step="any" title="constant value, held over this sub-experiment" :value="model.params[qname][activeExp][s - 1].value" @input="onNum(model.params[qname][activeExp][s - 1], 'value', $event.target.value)" />
                </label>
              </template>
              <template v-else-if="model.params[qname][activeExp][s - 1].shape === 'ramp'">
                <label class="tt-field"><span class="tt-cap">from</span>
                  <input type="number" step="any" title="value at the start of this sub-experiment" :value="model.params[qname][activeExp][s - 1].from" @input="onNum(model.params[qname][activeExp][s - 1], 'from', $event.target.value)" />
                </label>
                <label class="tt-field"><span class="tt-cap">to</span>
                  <input type="number" step="any" title="value at the end of this sub-experiment" :value="model.params[qname][activeExp][s - 1].to" @input="onNum(model.params[qname][activeExp][s - 1], 'to', $event.target.value)" />
                </label>
              </template>
              <template v-else-if="model.params[qname][activeExp][s - 1].shape === 'step'">
                <label class="tt-field"><span class="tt-cap">baseline</span>
                  <input type="number" step="any" title="value before the step" :value="model.params[qname][activeExp][s - 1].baseline" @input="onNum(model.params[qname][activeExp][s - 1], 'baseline', $event.target.value)" />
                </label>
                <label class="tt-field"><span class="tt-cap">level</span>
                  <input type="number" step="any" title="value after the step, held to the end of the sub-experiment" :value="model.params[qname][activeExp][s - 1].level" @input="onNum(model.params[qname][activeExp][s - 1], 'level', $event.target.value)" />
                </label>
                <label class="tt-field"><span class="tt-cap">t step</span>
                  <input type="number" step="0.1" min="0" :max="activeExperiment.subexps[s - 1].duration" title="time within the sub-experiment when the step occurs" :value="model.params[qname][activeExp][s - 1].ts" @input="onTimeNum(model.params[qname][activeExp][s - 1], 'ts', $event.target.value, activeExperiment.subexps[s - 1].duration)" />
                </label>
              </template>
              <template v-else-if="model.params[qname][activeExp][s - 1].shape === 'pulse'">
                <label class="tt-field"><span class="tt-cap">baseline</span>
                  <input type="number" step="any" title="value outside the pulse" :value="model.params[qname][activeExp][s - 1].baseline" @input="onNum(model.params[qname][activeExp][s - 1], 'baseline', $event.target.value)" />
                </label>
                <label class="tt-field"><span class="tt-cap">peak</span>
                  <input type="number" step="any" title="value during the pulse" :value="model.params[qname][activeExp][s - 1].peak" @input="onNum(model.params[qname][activeExp][s - 1], 'peak', $event.target.value)" />
                </label>
                <label class="tt-field"><span class="tt-cap">t start</span>
                  <input type="number" step="0.1" min="0" :max="activeExperiment.subexps[s - 1].duration" title="pulse start time within the sub-experiment" :value="model.params[qname][activeExp][s - 1].ts" @input="onTimeNum(model.params[qname][activeExp][s - 1], 'ts', $event.target.value, activeExperiment.subexps[s - 1].duration)" />
                </label>
                <label class="tt-field"><span class="tt-cap">t end</span>
                  <input type="number" step="0.1" min="0" :max="activeExperiment.subexps[s - 1].duration" title="pulse end time within the sub-experiment" :value="model.params[qname][activeExp][s - 1].te" @input="onTimeNum(model.params[qname][activeExp][s - 1], 'te', $event.target.value, activeExperiment.subexps[s - 1].duration)" />
                </label>
              </template>
              <template v-else>
                <span class="pc-traceref" :title="model.params[qname][activeExp][s - 1].key">trace: {{ model.params[qname][activeExp][s - 1].key }}</span>
              </template>
            </div>
          </div>
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
/* "+ subexp" floats at the top-right of the plot area. */
.pie-plotwrap {
  position: relative;
  padding-top: 1.5rem;
}
.pie-addsub {
  position: absolute;
  top: 0;
  right: 4px;
  z-index: 4;
}

/* A "time track": segments grow exactly proportionally to time (no gaps /
   borders / min-widths) and are padded to the plot's drawing area, so each
   segment's LEFT edge lines up with that subexp's vertical line in the plot.
   The value is shown as a small mark; the full editor opens on hover so a narrow
   subexp never forces its segment wider than its time share. */
.tt-track {
  display: flex;
  align-items: flex-start;
  gap: 0;
  margin: 0.15rem 0;
}
.tt-pre,
.tt-seg {
  position: relative;
  flex-basis: 0;
  min-width: 0;
  display: flex;
  flex-direction: column;
  align-items: flex-start;
}
/* Light tint marking the subexperiment targeted by a selected data_item. */
.tt-seg.tt-highlight {
  background: var(--p-highlight-background, rgba(91, 155, 213, 0.12));
  border-radius: 3px;
}
.tt-mark {
  font-size: 0.72rem;
  line-height: 1.1;
  overflow: hidden;
  max-width: 100%;
  white-space: nowrap;
  opacity: 0.85;
}
/* Shape icons: ramp / step / pulse drawn as polylines, trace as a file icon. */
.tt-ico {
  width: 15px;
  height: 12px;
  vertical-align: middle;
}
svg.tt-ico {
  stroke: currentColor;
  fill: none;
  stroke-width: 1.3;
}
i.tt-ico {
  font-size: 11px;
}
/* Duration shown as a dimension line: |————5————| with the value centred above
   and end-bars at the subexp boundaries (which line up with the plot's lines). */
.tt-seg.dim,
.tt-pre.dim {
  align-items: stretch;
}
.dim-val {
  text-align: center;
  font-size: 0.7rem;
  line-height: 1.1;
  opacity: 0.9;
}
.dim-line {
  height: 7px;
  border-left: 1.5px solid rgba(150, 150, 150, 0.85);
  border-right: 1.5px solid rgba(150, 150, 150, 0.85);
  position: relative;
}
.dim-line::before {
  content: '';
  position: absolute;
  left: 0;
  right: 0;
  top: 50%;
  border-top: 1.5px solid rgba(150, 150, 150, 0.85);
}
.tt-edit {
  display: none;
}
.tt-pre:hover .tt-edit,
.tt-pre:focus-within .tt-edit,
.tt-seg:hover .tt-edit,
.tt-seg:focus-within .tt-edit {
  display: flex;
  align-items: flex-end;
  flex-wrap: nowrap;
  gap: 3px;
  position: absolute;
  left: 0;
  top: -2px;
  z-index: 10;
  padding: 2px 3px;
  background: var(--p-content-background, #1f1f1f);
  border: 1px solid var(--p-content-border-color, #555);
  border-radius: 4px;
  box-shadow: 0 2px 6px rgba(0, 0, 0, 0.45);
}
/* Duration (dimension-line) cells open the editor centred — where the value sits
   — rather than at the boundary line. */
.tt-pre.dim:hover .tt-edit,
.tt-pre.dim:focus-within .tt-edit,
.tt-seg.dim:hover .tt-edit,
.tt-seg.dim:focus-within .tt-edit {
  left: 50%;
  transform: translateX(-50%);
}
.tt-lbl {
  font-size: 0.62rem;
  opacity: 0.55;
}
.tt-edit input,
.tt-edit select {
  width: 3rem;
  font-size: 0.74rem;
}
/* Each shape input gets an always-visible caption so its purpose is clear. */
.tt-field {
  display: flex;
  flex-direction: column;
  gap: 1px;
}
.tt-cap {
  font-size: 0.58rem;
  opacity: 0.6;
  line-height: 1;
  white-space: nowrap;
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
.pc-traceref {
  font-size: 0.72rem;
  opacity: 0.7;
}
</style>
