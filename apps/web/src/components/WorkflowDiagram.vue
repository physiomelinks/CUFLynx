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

import { figureReads, layoutSteps, referencesOf } from '../lib/workflowTarget'
import { routeEdges } from '../lib/workflowRoutes'

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
  // The workflow's external figures (`external_figures`: {id, from, command, reads, writes}).
  figures: { type: Array, default: () => [] },
  // The step the next run changes, or null.
  editing: { type: String, default: null },
  // The arrow whose detail is open ({source, target, key}), lit until it closes.
  active: { type: Object, default: null },
  // Where the model's values come from, as CA's provenance graph boxes them
  // (describe's value_origins): {boxes: [{id, title, mark, values, to}], model_counts}.
  origins: { type: Object, default: null },
  // Steps a delete would take with it, waiting for a confirmation.
  doomed: { type: Array, default: () => [] },
  busy: { type: Boolean, default: false },
})
const emit = defineEmits(['select', 'inspect', 'delete', 'move', 'open-target', 'edge', 'figure', 'origin'])

const W = 250
const H = 78
const COL_GAP = 96
const ROW_GAP = 16
const PAD = 8
// room above and below the boxes for the corridors of arrows that skip columns
const PAD_Y = 28

// An arrow's label: short, as it sits in the gap between two columns (the full words are in
// its tooltip and the step's detail).
const KEY_WORDS = { result_from: 'at fit', start_from: 'start', fixed_from: 'fixed',
  priors_from: 'prior', scale_from: 'scale', emulator_from: 'emulator', emulator_dir: 'emulator',
  sensitivity_from: 'ranks', params_for_id_from: 'params', on_target: 'merged', reads: 'reads',
  given: 'given' }
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
// Columns, left to right: the model; the steps by what they take from (layoutSteps); the
// target the calibrated values merge into; the steps run on that merged target (a sweep
// `on_target`); the external figures, each after what it reads.
const onTarget = computed(() => props.steps.filter((s) => s.on_target))
const mainSteps = computed(() => props.steps.filter((s) => !s.on_target))
const placed = computed(() => layoutSteps(mainSteps.value))
const hasModel = computed(() => props.steps.length > 0)
const colOffset = computed(() => (hasModel.value ? 1 : 0))
const stepCols = computed(() => Math.max(0, ...Object.values(placed.value).map((p) => p.col + 1)))
// the calibrated and hand-set values that are merged into the target
const merged = computed(() =>
  mainSteps.value.filter((s) => kindOf(s) === 'calibration' || (kindOf(s) === 'set' && !s.point)),
)
const figureSources = computed(() =>
  Object.fromEntries(props.figures.map((f) => [f.id, figureReads(f, props.steps, props.figures)])),
)
const hasTarget = computed(
  () => merged.value.length > 0 || onTarget.value.length > 0 || props.figures.length > 0,
)
const targetCol = computed(() => stepCols.value + colOffset.value)

/** Every box's column and row: `{id: {col, row, kind}}` (`model`, a step id, `target`, or
 * `figure:<id>`). A column holds its boxes in that order. */
const cells = computed(() => {
  const col = {}
  const boxesOf = props.origins?.boxes ?? []
  if (hasModel.value) {
    for (const o of boxesOf.filter((b) => b.mark === 'set_by_hand')) col[`origin:${o.id}`] = { col: 0, kind: 'origin' }
    col.model = { col: 0, kind: 'model' }
    for (const o of boxesOf.filter((b) => b.mark !== 'set_by_hand')) col[`origin:${o.id}`] = { col: 0, kind: 'origin' }
  }
  for (const s of mainSteps.value) col[s.id] = { col: placed.value[s.id].col + colOffset.value, kind: 'step' }
  if (hasTarget.value) col.target = { col: targetCol.value, kind: 'target' }
  for (const s of onTarget.value) col[s.id] = { col: targetCol.value + 1, kind: 'step' }
  // a figure is one column right of the furthest thing it reads
  const figCol = {}
  for (let pass = 0; pass <= props.figures.length; pass += 1) {
    for (const f of props.figures) {
      const after = figureSources.value[f.id].map(({ from }) =>
        from.startsWith('figure:') ? (figCol[from] ?? targetCol.value + 1) : (col[from]?.col ?? targetCol.value))
      figCol[`figure:${f.id}`] = Math.max(targetCol.value, ...after) + 1
    }
  }
  for (const f of props.figures) col[`figure:${f.id}`] = { col: figCol[`figure:${f.id}`], kind: 'figure' }
  const rows = {}
  for (const c of Object.values(col)) {
    c.row = rows[c.col] ?? 0
    rows[c.col] = c.row + 1
  }
  return col
})
const rowsIn = computed(() => {
  const out = {}
  for (const c of Object.values(cells.value)) out[c.col] = Math.max(out[c.col] ?? 0, c.row + 1)
  return out
})
const height = computed(() => {
  const most = Math.max(1, ...Object.values(rowsIn.value))
  return PAD_Y * 2 + most * H + (most - 1) * ROW_GAP
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

function at(id) {
  const c = cells.value[id]
  return c ? { x: colX(c.col), y: colY(c.col, c.row) } : null
}

const boxes = computed(() => props.steps.map((s) => ({ step: s, ...at(s.id) })))
const byId = computed(() => Object.fromEntries(boxes.value.map((b) => [b.step.id, b])))
const modelBox = computed(() => (hasModel.value ? at('model') : null))
const targetBox = computed(() => (hasTarget.value ? at('target') : null))
const figureBoxes = computed(() => props.figures.map((f) => ({ figure: f, ...at(`figure:${f.id}`) })))
const originBoxes = computed(() =>
  hasModel.value ? (props.origins?.boxes ?? []).map((o) => ({ origin: o, ...at(`origin:${o.id}`) })) : [],
)
const ORIGIN_CLASS = { set_by_hand: 'hand', measured: 'measured', derived: 'derived', calibrated: 'cal' }
const ORIGIN_WORDS = { set_by_hand: 'set by hand', measured: 'measured: from a cited source',
  derived: 'derived from data', calibrated: 'calibrated before this run' }
const modelCounts = computed(() =>
  Object.entries(props.origins?.model_counts ?? {}).map(([mark, n]) => `${n} ${mark.replace(/_/g, ' ')}`).join(' · '),
)
const width = computed(() =>
  colX(Math.max(0, ...Object.values(cells.value).map((c) => c.col)) + 1) - COL_GAP + PAD,
)

/** Every arrow before routing: what it joins, by box and column. */
function link(from, to, fromId, toId, key, kind, extra = {}) {
  return { id: `${fromId}->${toId}:${key}`, from: fromId, to: toId, key, kind,
           fromBox: from, toBox: to, fromCol: cells.value[fromId]?.col,
           toCol: cells.value[toId]?.col, ...extra }
}

const links = computed(() => {
  const out = []
  for (const b of boxes.value) {
    const refs = referencesOf(b.step, props.steps).filter(([, id]) => byId.value[id])
    for (const [key, id] of refs) out.push(link(byId.value[id], b, id, b.step.id, key, 'step'))
    if (b.step.on_target && targetBox.value) {
      out.push(link(targetBox.value, b, 'target', b.step.id, 'on_target', 'from-target'))
    } else if (!refs.length && modelBox.value) {
      out.push(link(modelBox.value, b, 'model', b.step.id, 'model', 'model'))
    }
  }
  if (targetBox.value) {
    for (const s of merged.value) out.push(link(byId.value[s.id], targetBox.value, s.id, 'target', 'merged', 'target'))
  }
  for (const ob of originBoxes.value) {
    for (const to of ob.origin.to) {
      const toBox = to === 'target' ? targetBox.value : byId.value[to]
      if (toBox) out.push(link(ob, toBox, `origin:${ob.origin.id}`, to, 'given', 'origin'))
    }
  }
  for (const fb of figureBoxes.value) {
    for (const src of figureSources.value[fb.figure.id]) {
      const fromBox = src.from === 'target' ? targetBox.value
        : src.from.startsWith('figure:') ? at(src.from) : byId.value[src.from]
      if (fromBox) {
        out.push(link(fromBox, fb, src.from, `figure:${fb.figure.id}`, 'reads', 'figure',
                      { files: src.files }))
      }
    }
  }
  return out.filter((e) => e.fromCol != null && e.toCol != null && e.toCol > e.fromCol)
})

// each arrow's own route: ports spread along the boxes, a lane of its own in each gap, and a
// corridor clear of the boxes when it skips columns (lib/workflowRoutes)
const routes = computed(() =>
  routeEdges(
    links.value.map((e) => ({
      id: e.id, fromCol: e.fromCol, toCol: e.toCol,
      from: { x: e.fromBox.x, y: e.fromBox.y, w: W, h: H },
      to: { x: e.toBox.x, y: e.toBox.y, w: W, h: H },
    })),
    Object.values(cells.value).map((c) => ({ col: c.col, x: colX(c.col), y: colY(c.col, c.row), w: W, h: H })),
    { colGap: COL_GAP, height: height.value },
  ),
)

const edges = computed(() =>
  links.value.map((e) => {
    const r = routes.value[e.id]
    return { ...e, d: r.d, end: r.end, label: KEY_WORDS[e.key] ?? '' }
  }),
)

// The arrow lit: the one under the pointer, else the one whose detail is open (`active`).
const hovered = ref(null)
function isLit(e) {
  if (hovered.value) return hovered.value === e.id
  const a = props.active
  return !!a && a.source === e.from && a.target === e.to && a.key === e.key
}
// the lit arrow is drawn last, so it is on top along its whole path
const drawOrder = computed(() => [...edges.value].sort((a, b) => Number(isLit(a)) - Number(isLit(b))))

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
  if (event.button !== 0 || event.target.closest('.wf-node, .wf-hit, .wf-figure, .wf-origin')) return
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
          <marker id="wf-arrow-lit" viewBox="0 0 6 6" refX="5" refY="3" markerWidth="6" markerHeight="6" orient="auto">
            <path d="M0,0 L6,3 L0,6 z" class="wf-arrow lit" />
          </marker>
        </defs>
        <g v-for="e in drawOrder" :key="e.id">
          <path
            :d="e.d"
            class="wf-edge"
            :class="[e.kind, { doomed: doomed.includes(e.to) || doomed.includes(e.from), lit: isLit(e) }]"
            :marker-end="isLit(e) ? 'url(#wf-arrow-lit)' : 'url(#wf-arrow)'"
          />
          <text
            v-if="isLit(e) && e.label"
            :x="e.end.x - 6"
            :y="e.end.y - 5"
            text-anchor="end"
            class="wf-edge-label lit"
          >{{ e.label }}</text>
          <path
            :d="e.d"
            class="wf-hit"
            :data-testid="`workflow-edge-${e.from}-${e.to}`"
            @mouseenter="hovered = e.id"
            @mouseleave="hovered = null"
            @click.stop="e.kind === 'origin' ? emit('origin', e.from.replace(/^origin:/, '')) : emit('edge', { source: e.from, target: e.to, key: e.key, files: e.files ?? null })"
          >
            <title>{{ e.from }} → {{ e.to }}: {{ LONG_WORDS[e.key] ?? e.key }}. Click to see what passes.</title>
          </path>
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
        <div class="wf-tag" title="every value no step sets comes from the model; what set each, as CA's provenance counts them">
          inherited<template v-if="modelCounts"> · {{ modelCounts }}</template></div>
      </div>

      <div
        v-for="ob in originBoxes"
        :key="ob.origin.id"
        class="wf-box wf-origin"
        :class="ORIGIN_CLASS[ob.origin.mark] ?? 'analysis'"
        :style="{ left: `${ob.x}px`, top: `${ob.y}px`, width: `${W}px`, height: `${H}px` }"
        :title="ob.origin.values.map((v) => `${v.name} = ${v.value}${v.note ? `: ${v.note}` : ''}`).join('\n')"
        :data-testid="`workflow-origin-${ob.origin.mark}`"
        @click="emit('origin', ob.origin.id)"
      >
        <div class="wf-row1"><strong class="wf-id">{{ ob.origin.title }}</strong></div>
        <div class="wf-row2"><span class="wf-detail">{{ ob.origin.values.map((v) => v.name).join(', ') }}</span></div>
        <div class="wf-tag">{{ ORIGIN_WORDS[ob.origin.mark] ?? ob.origin.mark }} · {{ ob.origin.values.length }} values</div>
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
        v-for="fb in figureBoxes"
        :key="fb.figure.id"
        class="wf-box sim wf-figure"
        :style="{ left: `${fb.x}px`, top: `${fb.y}px`, width: `${W}px`, height: `${H}px` }"
        :title="`${fb.figure.description ?? ''}\n${fb.figure.command ?? ''}`"
        :data-testid="`workflow-figure-${fb.figure.id}`"
        @click="emit('figure', fb.figure.id)"
      >
        <div class="wf-row1"><strong class="wf-id">{{ fb.figure.id }}</strong></div>
        <div class="wf-row2"><span class="wf-detail">{{ fb.figure.description ?? fb.figure.command }}</span></div>
        <div class="wf-tag">external figures · {{ (fb.figure.writes ?? []).length }} files</div>
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
        <div class="wf-tag">{{ merged.length ? `from ${merged.map((s) => s.id).join(', ')}` : 'the model as merged' }}</div>
      </div>
    </div>
    </div>
    <div class="wf-key" aria-label="Colour key">
      <span class="k cal">calibrated: an optimiser fitted it</span>
      <span class="k hand">set by hand</span>
      <span class="k measured">measured: set by data</span>
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
  --measured: #2f9e5b;
  --derived: #2a9d9f;
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
/* an arrow is clicked by a band wider than its line */
.wf-hit {
  fill: none;
  stroke: transparent;
  stroke-width: 8;
  pointer-events: stroke;
  cursor: pointer;
}
.wf-edge.lit {
  stroke: var(--p-primary-color, #4f8cff);
  stroke-width: 2.6;
  stroke-dasharray: none;
}
.wf-arrow.lit {
  fill: var(--p-primary-color, #4f8cff);
}
.wf-edge-label.lit {
  fill: var(--p-primary-color, #4f8cff);
  font-weight: 700;
  paint-order: stroke;
  stroke: var(--p-content-background, #1e1e1e);
  stroke-width: 3px;
}
.wf-figure {
  cursor: pointer;
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
.wf-box.measured { border-color: var(--measured); }
.wf-box.derived { border-color: var(--derived); }
.wf-box.measured .wf-tag { color: var(--measured); }
.wf-origin { cursor: pointer; }
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
.wf-key .measured::before { border-color: var(--measured); }
.wf-key .inherited::before { border: 2px dashed var(--inherited); }
.wf-help {
  margin: 0.3rem 0 0;
  font-size: 0.75rem;
  opacity: 0.7;
}
</style>
