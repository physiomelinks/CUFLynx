<script setup>
/**
 * A workflow's steps as a diagram: one node per step in the order of the file, an arrow
 * from each step to every step that takes something from it (result_from, start_from,
 * fixed_from, ...), drawn in the gutter on the left.
 *
 * Clicking a node opens that step's settings in its panel; the handle drags it to another
 * place (the server refuses a place above a step it takes from); the bin deletes it.
 * The step the next run changes is outlined, and the steps a delete would take with it
 * (`doomed`) are marked until it is confirmed or cancelled.
 */
import { computed, ref } from 'vue'
import { KIND_WORDS, SOURCE_WORDS } from '../stores/useWorkflow'
import { edgesOf, referencesOf } from '../lib/workflowTarget'

const props = defineProps({
  // The workflow file's steps ({id, kind, result_from, ...}), in order.
  steps: { type: Array, default: () => [] },
  // The tabs' words for each step (workflowTabs): id -> {detail, place, ...}.
  details: { type: Object, default: () => ({}) },
  // id -> 'done' | 'stale' | 'not_run' | 'queued' | 'running' | 'failed' | 'cancelled'
  states: { type: Object, default: () => ({}) },
  // The step the next run changes, or null.
  editing: { type: String, default: null },
  // Steps a delete would take with it, waiting for a confirmation.
  doomed: { type: Array, default: () => [] },
  busy: { type: Boolean, default: false },
})
const emit = defineEmits(['select', 'delete', 'move'])

const ROW = 58
const LANE = 10
const GUTTER_PAD = 8

// Each edge in its own lane where it would cross another's rows.
const lanes = computed(() => {
  const edges = edgesOf(props.steps)
    .map((e) => ({ ...e, lo: Math.min(e.from, e.to), hi: Math.max(e.from, e.to) }))
    .sort((a, b) => a.hi - a.lo - (b.hi - b.lo))
  const used = []
  for (const e of edges) {
    let lane = 0
    while (used[lane]?.some((o) => !(e.hi <= o.lo || e.lo >= o.hi))) lane += 1
    ;(used[lane] ??= []).push(e)
    e.lane = lane
  }
  return edges
})
const gutter = computed(
  () => GUTTER_PAD * 2 + LANE * Math.max(1, ...lanes.value.map((e) => e.lane + 1)),
)

function edgePath(e) {
  const right = gutter.value
  const x = right - GUTTER_PAD - LANE * (e.lane + 1)
  const y1 = e.from * ROW + ROW / 2
  const y2 = e.to * ROW + ROW / 2
  return `M ${right} ${y1} H ${x} V ${y2} H ${right - 4}`
}

const KIND_NAMES = { calibration: 'calibration', uq: 'UQ', ...KIND_WORDS }
function kindWord(step) {
  const kind = step.kind ?? 'calibration'
  const s = step.settings ?? {}
  const method = kind === 'sensitivity' ? s.sa_options?.method : kind === 'uq' ? s.UQ_options?.method : null
  return method ? `${KIND_NAMES[kind] ?? kind} · ${method}` : (KIND_NAMES[kind] ?? kind)
}

const FROM_WORDS = { ...SOURCE_WORDS, start_from: 'starts at', fixed_from: 'fixed from',
  priors_from: 'priors from', scale_from: 'scales' }

/** A step in a line: its model, and what it takes from which step. */
function detailOf(step) {
  const refs = referencesOf(step).map(([key, id]) => `${FROM_WORDS[key] ?? key} ${id}`)
  return [props.details[step.id]?.detail, ...refs].filter(Boolean).join(' · ')
}

// Drag to reorder: the row being dragged and the row it would land on.
const dragging = ref(null)
const over = ref(null)

function onDragStart(event, index) {
  dragging.value = index
  event.dataTransfer.effectAllowed = 'move'
  event.dataTransfer.setData('text/plain', props.steps[index].id)
}
function onDragOver(index) {
  if (dragging.value !== null) over.value = index
}
function onDrop(index) {
  const from = dragging.value
  dragging.value = null
  over.value = null
  if (from === null || from === index) return
  emit('move', { step: props.steps[from].id, index })
}
function onDragEnd() {
  dragging.value = null
  over.value = null
}
</script>

<template>
  <div class="wf-diagram" data-testid="workflow-diagram" :style="{ '--row': `${ROW}px` }">
    <svg
      class="wf-edges"
      :width="gutter"
      :height="steps.length * ROW"
      aria-hidden="true"
    >
      <defs>
        <marker id="wf-arrow" viewBox="0 0 6 6" refX="5" refY="3" markerWidth="6" markerHeight="6" orient="auto">
          <path d="M0,0 L6,3 L0,6 z" class="wf-arrow" />
        </marker>
      </defs>
      <path
        v-for="(e, i) in lanes"
        :key="i"
        :d="edgePath(e)"
        class="wf-edge"
        :class="{ doomed: doomed.includes(steps[e.to]?.id) }"
        marker-end="url(#wf-arrow)"
      >
        <title>{{ steps[e.to].id }} takes from {{ steps[e.from].id }} ({{ e.key }})</title>
      </path>
    </svg>
    <ol class="wf-nodes">
      <li
        v-for="(step, i) in steps"
        :key="step.id"
        class="wf-node"
        :class="{
          editing: step.id === editing,
          doomed: doomed.includes(step.id),
          dragging: dragging === i,
          'drop-above': over === i && dragging !== null && dragging > i,
          'drop-below': over === i && dragging !== null && dragging < i,
        }"
        :data-testid="`workflow-node-${step.id}`"
        @click="emit('select', step.id)"
        @dragover.prevent="onDragOver(i)"
        @drop.prevent="onDrop(i)"
      >
        <span
          class="wf-handle pi pi-bars"
          :draggable="!busy"
          title="Drag to another place"
          :data-testid="`workflow-drag-${step.id}`"
          @click.stop
          @dragstart="onDragStart($event, i)"
          @dragend="onDragEnd"
        />
        <span class="wf-dot" :data-state="states[step.id] ?? 'not_run'" />
        <div class="wf-node-text">
          <div class="wf-node-head">
            <strong>{{ step.id }}</strong>
            <span class="wf-kind">{{ kindWord(step) }}</span>
            <span v-if="step.id === editing" class="wf-badge">the next run changes it</span>
            <span v-if="doomed.includes(step.id)" class="wf-badge danger">would be deleted</span>
          </div>
          <div class="wf-node-detail" :title="detailOf(step)">
            {{ detailOf(step) }}
          </div>
        </div>
        <button
          class="wf-delete pi pi-trash"
          type="button"
          title="Delete this step (its results stay in the run directory)"
          :disabled="busy"
          :data-testid="`workflow-delete-${step.id}`"
          @click.stop="emit('delete', step.id)"
        />
      </li>
    </ol>
  </div>
</template>

<style scoped>
.wf-diagram {
  display: flex;
  align-items: flex-start;
}
.wf-edges {
  flex: none;
  overflow: visible;
}
.wf-edge {
  fill: none;
  stroke: var(--p-text-muted-color, #888);
  stroke-width: 1.4;
}
.wf-edge.doomed {
  stroke: #e84a5f;
}
.wf-arrow {
  fill: var(--p-text-muted-color, #888);
}
.wf-nodes {
  list-style: none;
  margin: 0;
  padding: 0;
  flex: 1;
  min-width: 0;
}
.wf-node {
  height: calc(var(--row) - 6px);
  margin-bottom: 6px;
  box-sizing: border-box;
  display: flex;
  align-items: center;
  gap: 0.5rem;
  padding: 0 0.5rem;
  border: 1px solid var(--p-content-border-color, #555);
  border-radius: 6px;
  cursor: pointer;
  font-size: 0.82rem;
}
.wf-node:hover {
  border-color: var(--p-primary-color, #4f8cff);
}
.wf-node.editing {
  border-color: var(--p-primary-color, #4f8cff);
  box-shadow: 0 0 0 1px var(--p-primary-color, #4f8cff);
  background: color-mix(in srgb, var(--p-primary-color, #4f8cff) 10%, transparent);
}
.wf-node.doomed {
  border-color: #e84a5f;
  background: color-mix(in srgb, #e84a5f 12%, transparent);
}
.wf-node.dragging {
  opacity: 0.5;
}
.wf-node.drop-above {
  box-shadow: 0 -3px 0 var(--p-primary-color, #4f8cff);
}
.wf-node.drop-below {
  box-shadow: 0 3px 0 var(--p-primary-color, #4f8cff);
}
.wf-handle {
  cursor: grab;
  opacity: 0.6;
}
.wf-node-text {
  flex: 1;
  min-width: 0;
}
.wf-node-head {
  display: flex;
  gap: 0.5rem;
  align-items: baseline;
  flex-wrap: wrap;
}
.wf-node-detail {
  opacity: 0.7;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.wf-kind {
  opacity: 0.75;
}
.wf-badge {
  font-size: 0.72rem;
  padding: 0 0.4rem;
  border-radius: 999px;
  background: color-mix(in srgb, var(--p-primary-color, #4f8cff) 22%, transparent);
}
.wf-badge.danger {
  background: color-mix(in srgb, #e84a5f 25%, transparent);
}
.wf-delete {
  border: none;
  background: transparent;
  color: inherit;
  opacity: 0.6;
  cursor: pointer;
}
.wf-delete:hover {
  opacity: 1;
  color: #e84a5f;
}
.wf-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: #888;
  flex: none;
}
.wf-dot[data-state='done'] {
  background: #3fb950;
}
.wf-dot[data-state='stale'] {
  background: #d29922;
}
.wf-dot[data-state='running'],
.wf-dot[data-state='queued'] {
  background: #ffc000;
}
.wf-dot[data-state='failed'] {
  background: #e84a5f;
}
</style>
