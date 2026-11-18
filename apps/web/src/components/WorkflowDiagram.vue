<script setup>
/**
 * The workflow as a chart: the model on the left, one box per step in columns by what it
 * takes from (so every arrow points right), elbow arrows for result_from, start_from,
 * fixed_from, ..., and the target the calibrated values are merged into on the right. Each
 * box is coloured by what set its values, as CA's provenance diagram is: calibrated (an
 * optimiser fitted it), set by hand, an analysis, a surrogate.
 *
 * Click a box to change that step's settings in its panel; double-click it to inspect it;
 * drag it onto another box to put it in that one's place in the file (the server refuses a
 * place above a step it takes from); the bin deletes it. The step the next run changes is
 * outlined, and the steps a delete would take with it (`doomed`) are marked until it is
 * confirmed or cancelled.
 */
import { computed, ref } from 'vue'
import { SOURCE_WORDS, cellmlFile, externalFile, modelLabel } from '../stores/useWorkflow'

import { layoutSteps, referencesOf } from '../lib/workflowTarget'

const props = defineProps({
  // The workflow file's steps ({id, kind, result_from, settings, ...}), in order.
  steps: { type: Array, default: () => [] },
  // The tabs' words for each step (workflowTabs): id -> {detail, calibrates, ...}.
  details: { type: Object, default: () => ({}) },
  // id -> 'done' | 'stale' | 'not_run' | 'queued' | 'running' | 'failed' | 'cancelled'
  states: { type: Object, default: () => ({}) },
  // CA's status of each step ({best_cost, duration_s, ...}), by id.
  results: { type: Object, default: () => ({}) },
  // The workflow's target model (the file's `target`), for the model and target boxes.
  target: { type: Object, default: null },
  // The step the next run changes, or null.
  editing: { type: String, default: null },
  // Steps a delete would take with it, waiting for a confirmation.
  doomed: { type: Array, default: () => [] },
  busy: { type: Boolean, default: false },
})
const emit = defineEmits(['select', 'inspect', 'delete', 'move', 'open-target'])

const W = 250
const H = 78
const COL_GAP = 96
const ROW_GAP = 16
const PAD = 8

// An arrow's label: short, as it sits in the gap between two columns (the full words are in
// its tooltip and the step's detail).
const KEY_WORDS = { result_from: 'at fit', start_from: 'start', fixed_from: 'fixed',
  priors_from: 'prior', scale_from: 'scale', emulator_from: 'emulator', emulator_dir: 'emulator',
  sensitivity_from: 'ranks', params_for_id_from: 'params' }
const LONG_WORDS = { ...SOURCE_WORDS, start_from: 'starts at', fixed_from: 'fixed from',
  priors_from: 'priors from', scale_from: 'scales', emulator_dir: 'on the emulator of' }

function kindOf(step) {
  return step?.kind ?? 'calibration'
}

/** What set a box's values, for its colour and tag: as CA's provenance names them. */
function classOf(step) {
  const kind = kindOf(step)
  if (kind === 'calibration') return 'cal'
  if (kind === 'set') return 'hand'
  if (kind === 'emulation') return 'surrogate'
  if (kind === 'sweep') return 'sim'
  return 'analysis'
}

function method(step) {
  const s = step.settings ?? {}
  const kind = kindOf(step)
  if (kind === 'calibration') return s.param_id_method ?? ''
  if (kind === 'sensitivity') return s.sa_options?.method ?? ''
  if (kind === 'uq') return s.UQ_options?.method ?? ''
  if (kind === 'identifiability') return s.ia_options?.method ?? ''
  if (kind === 'emulation') {
    const models = s.emulator_settings?.models
    return Array.isArray(models) ? models.join(', ') : (models ?? '')
  }
  if (kind === 'reduction') return s.param_reduction_options?.method ?? step.reduction?.method ?? ''
  return ''
}

const TAGS = { calibration: 'calibrated', set: 'set by hand', sensitivity: 'sensitivity',
  uq: 'uq', identifiability: 'identifiability', emulation: 'surrogate', reduction: 'reduction',
  sweep: 'simulation' }
const STATE_WORDS = { stale: 'stale', not_run: 'not run', queued: 'queued', running: 'running',
  failed: 'failed', cancelled: 'cancelled' }

function tag(step) {
  const kind = kindOf(step)
  const n = (props.details[step.id]?.calibrates ?? []).length
  const parts = [TAGS[kind] ?? kind]
  if (kind === 'set') parts.push(step.point ? 'point only' : `${(step.set ?? []).length} values`)
  else if (n) parts.push(`${n} parameter${n === 1 ? '' : 's'}`)
  const m = method(step)
  if (m && kind !== 'calibration') parts.push(m)
  return parts.join(' · ')
}

/** A box's second line: its model when it is not the workflow's own, its method, its data. */
function line2(step) {
  const kind = kindOf(step)
  if (kind === 'set') {
    return (step.set ?? []).map((e) => `${e.parameter?.split('/').pop()} = ${e.value}`).join(', ')
  }
  const model = props.details[step.id]?.detail
  const own = !step.model || model === modelName.value
  const m = kind === 'calibration' ? method(step) : ''
  const obs = typeof step.obs_data === 'string' ? fileName(step.obs_data).replace(/\.json$/, '') : ''
  return [own ? '' : model, m, obs].filter(Boolean).join(' · ')
}

function cost(step) {
  const c = props.results[step.id]?.best_cost
  return typeof c === 'number' ? `cost ${c.toPrecision(3)}` : ''
}

function time(step) {
  const s = props.results[step.id]?.duration_s
  if (typeof s !== 'number') return ''
  return s >= 90 ? `${(s / 60).toPrecision(2)} min` : `${s.toPrecision(2)} s`
}

function fileName(path) {
  return typeof path === 'string' ? path.split(/[\\/]/).pop() : ''
}

/** What a box is fitted to or run with, for its tooltip. */
function data(step) {
  const out = [`${step.id} (${kindOf(step)})`]
  if (step.obs_data) out.push(`obs_data: ${typeof step.obs_data === 'string' ? fileName(step.obs_data) : 'inline'}`)
  if (step.params_for_id) out.push(`params_for_id: ${typeof step.params_for_id === 'string' ? fileName(step.params_for_id) : 'inline'}`)
  if (step.description) out.push(step.description)
  out.push('Click: change its settings. Double-click: inspect it. Drag onto another step: move it there.')
  return out.join('\n')
}

// -- layout ----------------------------------------------------------------------------
const placed = computed(() => layoutSteps(props.steps))
const hasModel = computed(() => props.steps.length > 0)
const colOffset = computed(() => (hasModel.value ? 1 : 0))
const stepCols = computed(() => Math.max(0, ...Object.values(placed.value).map((p) => p.col + 1)))
// the calibrated and hand-set values that are merged into the target
const merged = computed(() =>
  props.steps.filter((s) => kindOf(s) === 'calibration' || (kindOf(s) === 'set' && !s.point)),
)
const rowsIn = computed(() => {
  const out = {}
  for (const p of Object.values(placed.value)) out[p.col] = Math.max(out[p.col] ?? 0, p.row + 1)
  return out
})
const height = computed(() => {
  const most = Math.max(1, ...Object.values(rowsIn.value))
  return PAD * 2 + most * H + (most - 1) * ROW_GAP
})

function colX(col) {
  return PAD + col * (W + COL_GAP)
}

/** A column's boxes are centred on the chart's middle, as the deck's charts are. */
function colY(col, row) {
  const n = rowsIn.value[col] ?? 1
  const block = n * H + (n - 1) * ROW_GAP
  return (height.value - block) / 2 + row * (H + ROW_GAP)
}

const boxes = computed(() =>
  props.steps.map((s) => {
    const p = placed.value[s.id]
    return { step: s, x: colX(p.col + colOffset.value), y: colY(p.col, p.row) }
  }),
)
const byId = computed(() => Object.fromEntries(boxes.value.map((b) => [b.step.id, b])))
const modelBox = computed(() =>
  hasModel.value ? { x: colX(0), y: (height.value - H) / 2 } : null,
)
const targetBox = computed(() =>
  merged.value.length
    ? { x: colX(stepCols.value + colOffset.value), y: (height.value - H) / 2 }
    : null,
)
const width = computed(() =>
  colX(stepCols.value + colOffset.value + (targetBox.value ? 1 : 0)) - COL_GAP + PAD,
)

function elbow(x1, y1, x2, y2) {
  const xm = x2 - COL_GAP / 2
  return `M ${x1} ${y1} H ${xm} V ${y2} H ${x2 - 3}`
}

const edges = computed(() => {
  const out = []
  const labelled = new Set()
  for (const b of boxes.value) {
    const refs = referencesOf(b.step, props.steps).filter(([, id]) => byId.value[id])
    for (const [key, id] of refs) {
      const from = byId.value[id]
      const y2 = b.y + H / 2
      const label = labelled.has(`${b.step.id}:${key}`) ? '' : (KEY_WORDS[key] ?? key)
      labelled.add(`${b.step.id}:${key}`)
      out.push({ d: elbow(from.x + W, from.y + H / 2, b.x, y2), key, from: id, to: b.step.id,
                 label, lx: b.x - COL_GAP / 2 + 4, ly: y2 - 5, kind: 'step' })
    }
    if (!refs.length && modelBox.value) {
      out.push({ d: elbow(modelBox.value.x + W, modelBox.value.y + H / 2, b.x, b.y + H / 2),
                 key: 'model', from: 'model', to: b.step.id, label: '', kind: 'model' })
    }
  }
  if (targetBox.value) {
    for (const s of merged.value) {
      const from = byId.value[s.id]
      out.push({ d: elbow(from.x + W, from.y + H / 2, targetBox.value.x, targetBox.value.y + H / 2),
                 key: 'merged', from: s.id, to: 'target', label: '', kind: 'target' })
    }
  }
  return out
})

const modelName = computed(() =>
  cellmlFile(props.target) || externalFile(props.target) || modelLabel(props.target) || 'the model',
)

// -- dragging a box onto another: it takes that one's place in the file ------------------
const dragging = ref(null)
const over = ref(null)

function onDragStart(event, id) {
  dragging.value = id
  event.dataTransfer.effectAllowed = 'move'
  event.dataTransfer.setData('text/plain', id)
}
function onDragOver(id) {
  if (dragging.value !== null) over.value = id
}
function onDrop(id) {
  const from = dragging.value
  dragging.value = null
  over.value = null
  if (from === null || from === id) return
  emit('move', { step: from, index: props.steps.findIndex((s) => s.id === id) })
}
function onDragEnd() {
  dragging.value = null
  over.value = null
}

// -- panning: drag the chart by anything but a step box (a box drags to reorder) -------------
const pan = ref(null)
const panning = ref(null) // {x, y, left, top, id} while the chart is held

function onPanStart(event) {
  if (event.button !== 0 || event.target.closest('.wf-node')) return
  const el = pan.value
  panning.value = { x: event.clientX, y: event.clientY, left: el.scrollLeft, top: el.scrollTop,
                    id: event.pointerId }
  el.setPointerCapture?.(event.pointerId)
  event.preventDefault()
}
function onPanMove(event) {
  const p = panning.value
  if (!p || event.pointerId !== p.id) return
  pan.value.scrollLeft = p.left - (event.clientX - p.x)
  pan.value.scrollTop = p.top - (event.clientY - p.y)
}
function onPanEnd(event) {
  if (panning.value) pan.value.releasePointerCapture?.(event.pointerId)
  panning.value = null
}
</script>

<template>
  <div class="wf-chart-wrap" data-testid="workflow-diagram">
    <div
      ref="pan"
      class="wf-pan"
      :class="{ panning: !!panning }"
      title="Drag to move along the chart"
      data-testid="workflow-pan"
      @pointerdown="onPanStart"
      @pointermove="onPanMove"
      @pointerup="onPanEnd"
      @pointercancel="onPanEnd"
    >
    <div class="wf-chart" :style="{ width: `${width}px`, height: `${height}px` }">
      <svg class="wf-edges" :width="width" :height="height" aria-hidden="true">
        <defs>
          <marker id="wf-arrow" viewBox="0 0 6 6" refX="5" refY="3" markerWidth="7" markerHeight="7" orient="auto">
            <path d="M0,0 L6,3 L0,6 z" class="wf-arrow" />
          </marker>
        </defs>
        <g v-for="(e, i) in edges" :key="i">
          <path
            :d="e.d"
            class="wf-edge"
            :class="[e.kind, { doomed: doomed.includes(e.to) || doomed.includes(e.from) }]"
            marker-end="url(#wf-arrow)"
          >
            <title>{{ e.to }} {{ e.kind === 'target' ? 'is merged into the target' : e.kind === 'model' ? 'runs the model' : `${LONG_WORDS[e.key] ?? e.key} ${e.from}` }}</title>
          </path>
          <text v-if="e.label" :x="e.lx" :y="e.ly" class="wf-edge-label">{{ e.label }}</text>
        </g>
      </svg>

      <div
        v-if="modelBox"
        class="wf-box inherited"
        :style="{ left: `${modelBox.x}px`, top: `${modelBox.y}px`, width: `${W}px`, height: `${H}px` }"
        data-testid="workflow-node-model"
      >
        <div class="wf-row1"><strong>Model</strong></div>
        <div class="wf-row2" :title="modelName">{{ modelName }}</div>
        <div class="wf-tag" title="every value no step sets comes from the model">inherited</div>
      </div>

      <div
        v-for="b in boxes"
        :key="b.step.id"
        class="wf-box wf-node"
        :class="[classOf(b.step), {
          editing: b.step.id === editing,
          doomed: doomed.includes(b.step.id),
          dragging: dragging === b.step.id,
          over: over === b.step.id && dragging !== b.step.id,
        }]"
        :style="{ left: `${b.x}px`, top: `${b.y}px`, width: `${W}px`, height: `${H}px` }"
        :title="data(b.step)"
        :draggable="!busy"
        :data-testid="`workflow-node-${b.step.id}`"
        @click="emit('select', b.step.id)"
        @dblclick="emit('inspect', b.step.id)"
        @dragstart="onDragStart($event, b.step.id)"
        @dragend="onDragEnd"
        @dragover.prevent="onDragOver(b.step.id)"
        @drop.prevent="onDrop(b.step.id)"
      >
        <div class="wf-row1">
          <span class="wf-dot" :data-state="states[b.step.id] ?? 'not_run'" />
          <strong class="wf-id">{{ b.step.id }}</strong>
          <span class="wf-num">{{ cost(b.step) }}</span>
          <button
            class="wf-delete pi pi-trash"
            type="button"
            title="Delete this step (its results stay in the run directory)"
            :disabled="busy"
            :data-testid="`workflow-delete-${b.step.id}`"
            @click.stop="emit('delete', b.step.id)"
            @dblclick.stop
          />
        </div>
        <div class="wf-row2">
          <span class="wf-detail">{{ line2(b.step) }}</span>
          <span class="wf-num">{{ time(b.step) }}</span>
        </div>
        <div class="wf-tag">
          {{ tag(b.step) }}
          <span v-if="STATE_WORDS[states[b.step.id]]" class="wf-state">
            · {{ STATE_WORDS[states[b.step.id]] }}</span>
          <span v-if="b.step.id === editing" class="wf-badge" title="the next run changes this step">next run</span>
          <span v-if="doomed.includes(b.step.id)" class="wf-badge danger" title="a delete waiting for a confirmation would delete it">deleted?</span>
        </div>
      </div>

      <div
        v-if="targetBox"
        class="wf-box target"
        title="Click: open the target with every calibrated value as the current study"
        @click="emit('open-target')"
        :style="{ left: `${targetBox.x}px`, top: `${targetBox.y}px`, width: `${W}px`, height: `${H}px` }"
        data-testid="workflow-node-target"
      >
        <div class="wf-row1"><strong>Target</strong></div>
        <div class="wf-row2">{{ modelName }} with the merged values</div>
        <div class="wf-tag">from {{ merged.map((s) => s.id).join(', ') }}</div>
      </div>
    </div>
    </div>
    <div class="wf-key" aria-label="Colour key">
      <span class="k cal">calibrated: an optimiser fitted it</span>
      <span class="k hand">set by hand</span>
      <span class="k analysis">analysis</span>
      <span class="k surrogate">surrogate</span>
      <span class="k inherited">inherited: from the model</span>
    </div>
    <p class="wf-help">
      Drag the chart to move along it · click a step to change its settings · double-click to
      inspect it · drag a step onto another to take that one's place · the bin deletes it
    </p>
  </div>
</template>

<style scoped>
.wf-chart-wrap {
  --cal: #2f6fde;
  --hand: #c8372d;
  --analysis: #6b7680;
  --surrogate: #8a4fd6;
  --inherited: #8b949c;
  min-width: 0;
}
/* the chart moves by dragging it (and by a trackpad or wheel); no scroll bar, and the key
   and help below stay where they are */
.wf-pan {
  overflow: auto;
  scrollbar-width: none;
  cursor: grab;
  touch-action: none;
  user-select: none;
}
.wf-pan::-webkit-scrollbar {
  display: none;
}
.wf-pan.panning {
  cursor: grabbing;
}
.wf-chart {
  position: relative;
}
.wf-edges {
  position: absolute;
  inset: 0;
  overflow: visible;
}
.wf-edge {
  fill: none;
  stroke: var(--p-text-muted-color, #888);
  stroke-width: 1.4;
}
.wf-edge.model {
  stroke-dasharray: 4 3;
}
.wf-edge.doomed {
  stroke: var(--hand);
}
.wf-arrow {
  fill: var(--p-text-muted-color, #888);
}
.wf-edge-label {
  font-size: 9px;
  letter-spacing: 0.06em;
  text-transform: uppercase;
  fill: var(--p-text-muted-color, #888);
}
.wf-box {
  position: absolute;
  box-sizing: border-box;
  display: flex;
  flex-direction: column;
  justify-content: center;
  gap: 2px;
  padding: 6px 10px;
  border: 2px solid var(--analysis);
  border-radius: 6px;
  background: var(--p-content-background, #1e1e1e);
  font-size: 0.78rem;
  overflow: hidden;
}
.wf-box.cal { border-color: var(--cal); }
.wf-box.hand { border-color: var(--hand); }
.wf-box.surrogate { border-color: var(--surrogate); }
.wf-box.sim { border-color: var(--analysis); }
.wf-box.inherited { border: 2px dashed var(--inherited); }
.wf-box.target { border-color: var(--p-text-color, #ddd); cursor: pointer; }
.wf-node {
  cursor: pointer;
}
.wf-node:hover {
  box-shadow: 0 0 0 2px color-mix(in srgb, var(--p-primary-color, #4f8cff) 40%, transparent);
}
.wf-node.editing {
  box-shadow: 0 0 0 3px var(--p-primary-color, #4f8cff);
}
.wf-node.doomed {
  background: color-mix(in srgb, var(--hand) 14%, var(--p-content-background, #1e1e1e));
}
.wf-node.dragging {
  opacity: 0.45;
}
.wf-node.over {
  box-shadow: 0 0 0 3px var(--p-primary-color, #4f8cff);
  border-style: dashed;
}
.wf-row1,
.wf-row2 {
  display: flex;
  align-items: baseline;
  gap: 0.4rem;
  min-width: 0;
}
.wf-id {
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.wf-row2 {
  opacity: 0.8;
}
.wf-detail {
  flex: 1;
  min-width: 0;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.wf-num {
  margin-left: auto;
  font-family: ui-monospace, monospace;
  font-size: 0.74rem;
  white-space: nowrap;
}
.wf-tag {
  font-size: 0.66rem;
  letter-spacing: 0.06em;
  text-transform: uppercase;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  color: var(--analysis);
}
.wf-box.cal .wf-tag { color: var(--cal); }
.wf-box.hand .wf-tag { color: var(--hand); }
.wf-box.surrogate .wf-tag { color: var(--surrogate); }
.wf-state {
  text-transform: none;
  letter-spacing: 0;
}
.wf-badge {
  margin-left: 0.3rem;
  padding: 0 0.35rem;
  border-radius: 999px;
  text-transform: none;
  letter-spacing: 0;
  color: var(--p-text-color, #ddd);
  background: color-mix(in srgb, var(--p-primary-color, #4f8cff) 30%, transparent);
}
.wf-badge.danger {
  background: color-mix(in srgb, var(--hand) 35%, transparent);
}
.wf-delete {
  border: none;
  background: transparent;
  color: inherit;
  opacity: 0;
  cursor: pointer;
  padding: 0 0 0 0.3rem;
}
.wf-node:hover .wf-delete,
.wf-delete:focus-visible {
  opacity: 0.7;
}
.wf-delete:hover {
  opacity: 1;
  color: var(--hand);
}
.wf-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: #888;
  flex: none;
  align-self: center;
}
.wf-dot[data-state='done'] { background: #3fb950; }
.wf-dot[data-state='stale'] { background: #d29922; }
.wf-dot[data-state='running'],
.wf-dot[data-state='queued'] { background: #ffc000; }
.wf-dot[data-state='failed'] { background: #e84a5f; }
.wf-key {
  display: flex;
  flex-wrap: wrap;
  gap: 0.4rem 1rem;
  margin-top: 0.6rem;
  font-size: 0.68rem;
  letter-spacing: 0.06em;
  text-transform: uppercase;
}
.wf-key .k::before {
  content: '';
  display: inline-block;
  width: 10px;
  height: 10px;
  margin-right: 0.35rem;
  border: 2px solid var(--analysis);
  border-radius: 2px;
  vertical-align: -1px;
}
.wf-key .cal::before { border-color: var(--cal); }
.wf-key .hand::before { border-color: var(--hand); }
.wf-key .surrogate::before { border-color: var(--surrogate); }
.wf-key .inherited::before { border: 2px dashed var(--inherited); }
.wf-help {
  margin: 0.3rem 0 0;
  font-size: 0.75rem;
  opacity: 0.7;
}
</style>
