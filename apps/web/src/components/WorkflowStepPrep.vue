<script setup>
/**
 * The step prep box, opened by New step: the boxes a new step can be, to drag onto the
 * chart. Two drop-downs drawn as the boxes they give: Models, one box per model the
 * workflow uses (its target and every step's own; what a model box does is chosen by
 * clicking it on the chart), and Actions, boxes on no model, each made by its own form
 * (values set by hand; a parameter set chosen from several calibrations by cost, and figures
 * drawn from other steps' results, where CA has them). The box chosen in a drop-down is the drop-down itself, and is dragged from there.
 *
 * A dragged box carries `{model}` or `{action}` as `application/x-cuflynx-new-step`, which
 * the chart (WorkflowDiagram) reads when it is dropped.
 */
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { modelShort } from '../lib/workflowTarget'

const props = defineProps({
  // The workflow file ({target, steps}).
  workflow: { type: Object, default: null },
  // CA's step kinds (describe's step_kinds), for which actions this CA can make.
  stepKinds: { type: Array, default: () => [] },
})
const emit = defineEmits(['place', 'close'])

const NEW_STEP_TYPE = 'application/x-cuflynx-new-step'

/** Every model the workflow uses, once each, as a box: the target first, then each step's. */
const models = computed(() => {
  const out = []
  const add = (model, used) => {
    const key = `model:${JSON.stringify(model ?? null)}`
    const have = out.find((m) => m.key === key)
    if (have) {
      have.used.push(used)
      return
    }
    out.push({ key, kind: 'model', title: modelShort(model), used: [used], payload: { model } })
  }
  const target = props.workflow?.target
  if (target) add(target, 'the target')
  for (const s of props.workflow?.steps ?? []) {
    if (s.model && !s.on_target) add(s.model, s.id)
  }
  return out.map((m) => ({ ...m, line: `used by ${m.used.join(', ')}`,
    tag: 'model box · click it on the chart to choose what it does' }))
})

const ACTIONS = [
  { key: 'action:combine', kind: 'combine', title: 'Choose a parameter set by cost',
    line: 'the lowest-cost set of the calibrations drawn to it', tag: 'action',
    payload: { action: 'combine' } },
  { key: 'action:set', kind: 'set', title: 'Set values by hand',
    line: 'each value with the reason it was chosen', tag: 'action · set by hand',
    payload: { action: 'set' } },
  { key: 'action:figure', kind: 'figure', title: 'Draw figures',
    line: 'from the results of the steps it reads', tag: 'action · figure',
    payload: { action: 'figure' } },
]
const actions = computed(() => ACTIONS.map((a) => {
  const available = props.stepKinds.includes(a.kind)
  return { ...a, available, ...(available ? {} : { tag: 'needs a newer circulatory_autogen' }) }
}))

// which drop-down is open, and the box chosen (from either)
const open = ref(null)
const chosenKey = ref('')
const chosen = computed(() =>
  [...models.value, ...actions.value].find((b) => b.key === chosenKey.value) ?? null)
const chosenIn = computed(() => (chosen.value?.payload.model !== undefined ? 'models' : chosen.value ? 'actions' : null))

function pick(box) {
  if (box.available === false) return
  chosenKey.value = box.key
  open.value = null
}

function onDragStart(event, box = chosen.value) {
  if (!box || box.available === false) {
    event.preventDefault()
    return
  }
  event.dataTransfer.setData(NEW_STEP_TYPE, JSON.stringify(box.payload))
  event.dataTransfer.effectAllowed = 'copy'
  // a box dragged from the open list is the one chosen, too
  chosenKey.value = box.key
  // the list closes once the drag is under way (closing it at once would end the drag)
  setTimeout(() => { open.value = null }, 0)
}

// a click outside, or Escape, closes an open drop-down
const root = ref(null)
function onDocDown(event) {
  if (open.value && !root.value?.contains(event.target)) open.value = null
}
function onKey(event) {
  if (event.key === 'Escape') open.value = null
}
onMounted(() => {
  document.addEventListener('pointerdown', onDocDown)
  document.addEventListener('keydown', onKey)
})
onBeforeUnmount(() => {
  document.removeEventListener('pointerdown', onDocDown)
  document.removeEventListener('keydown', onKey)
})
</script>

<template>
  <div ref="root" class="sp" data-testid="workflow-step-prep">
    <div class="sp-head">
      <strong>New step</strong>
      <span class="sp-hint">choose a box, then drag it onto the chart</span>
      <button type="button" class="sp-close pi pi-times" title="Close" @click="emit('close')" />
    </div>
    <div class="sp-pickers">
      <div
        v-for="picker in [{ id: 'models', label: 'Models', boxes: models, empty: 'choose a model' },
                          { id: 'actions', label: 'Actions', boxes: actions, empty: 'choose an action' }]"
        :key="picker.id"
        class="sp-picker"
      >
        <span class="sp-label">{{ picker.label }}</span>
        <!-- the drop-down: the box it gives, dragged from here once chosen -->
        <div
          class="sp-box sp-trigger"
          :class="chosenIn === picker.id ? [chosen.kind] : ['empty']"
          role="button"
          tabindex="0"
          :aria-expanded="open === picker.id"
          :draggable="chosenIn === picker.id"
          :title="chosenIn === picker.id ? 'Drag onto the chart: into a column, or a gap between columns for a new stage' : ''"
          :data-testid="`workflow-prep-${picker.id}`"
          @click="open = open === picker.id ? null : picker.id"
          @keydown.enter.prevent="open = open === picker.id ? null : picker.id"
          @dragstart="onDragStart"
        >
          <template v-if="chosenIn === picker.id">
            <strong class="sp-title">{{ chosen.title }}</strong>
            <span class="sp-line">{{ chosen.line }}</span>
            <span class="sp-tag">{{ chosen.tag }}</span>
          </template>
          <template v-else>
            <strong class="sp-title">{{ picker.empty }}</strong>
            <span class="sp-line">{{ picker.boxes.length }} to choose from</span>
          </template>
          <i class="pi pi-chevron-down sp-caret" />
        </div>
        <div v-if="open === picker.id" class="sp-list" :data-testid="`workflow-prep-${picker.id}-list`">
          <div
            v-for="box in picker.boxes"
            :key="box.key"
            class="sp-box sp-option"
            :class="[box.kind, { off: box.available === false, on: box.key === chosenKey }]"
            role="option"
            :aria-disabled="box.available === false"
            :draggable="box.available !== false"
            :title="box.available === false ? '' : 'Click to choose it, or drag it straight onto the chart'"
            :data-testid="`workflow-prep-option-${box.key}`"
            @click="pick(box)"
            @dragstart="onDragStart($event, box)"
          >
            <strong class="sp-title">{{ box.title }}</strong>
            <span class="sp-line">{{ box.line }}</span>
            <span class="sp-tag">{{ box.tag }}</span>
          </div>
        </div>
      </div>
    </div>
    <button
      v-if="chosen"
      type="button"
      class="sp-place"
      title="Put it at the end of the chart instead of dragging it"
      data-testid="workflow-prep-place"
      @click="emit('place', chosen.payload)"
    >or place {{ chosen.title }} at the end</button>
  </div>
</template>

<style scoped>
.sp {
  --box-w: 250px;
  border: 1px solid var(--p-content-border-color, #555);
  border-radius: 8px;
  padding: 0.5rem 0.75rem 0.7rem;
  margin: 0.2rem 0 0.6rem;
  max-width: 44rem;
  background: var(--p-content-background, #1e1e1e);
}
.sp-head {
  display: flex;
  gap: 0.6rem;
  align-items: baseline;
}
.sp-hint {
  opacity: 0.7;
  font-size: 0.8rem;
}
.sp-close {
  margin-left: auto;
  background: none;
  border: none;
  color: inherit;
  cursor: pointer;
  opacity: 0.7;
}
.sp-pickers {
  display: flex;
  gap: 1.25rem;
  flex-wrap: wrap;
  margin: 0.5rem 0 0.3rem;
}
.sp-picker {
  position: relative;
  display: flex;
  flex-direction: column;
  gap: 0.25rem;
}
.sp-label {
  font-size: 0.75rem;
  opacity: 0.75;
  text-transform: uppercase;
  letter-spacing: 0.04em;
}
/* a box as the chart draws one */
.sp-box {
  position: relative;
  display: flex;
  flex-direction: column;
  gap: 2px;
  width: var(--box-w);
  box-sizing: border-box;
  padding: 6px 28px 6px 10px;
  border: 2px dashed var(--p-primary-color, #4f8cff);
  border-radius: 6px;
  background: color-mix(in srgb, var(--p-primary-color, #4f8cff) 6%, var(--p-content-background, #1e1e1e));
  font-size: 0.78rem;
  cursor: pointer;
}
.sp-box.set {
  border-color: #c8372d;
  background: color-mix(in srgb, #c8372d 6%, var(--p-content-background, #1e1e1e));
}
.sp-box.combine {
  border-color: #2f6fde;
}
.sp-box.empty {
  border-style: dotted;
  border-color: var(--p-content-border-color, #666);
  background: transparent;
}
.sp-trigger[draggable='true'] {
  cursor: grab;
}
.sp-title {
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.sp-line {
  opacity: 0.8;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.sp-tag {
  font-size: 0.68rem;
  letter-spacing: 0.04em;
  text-transform: uppercase;
  opacity: 0.7;
}
.sp-box.set .sp-tag {
  color: #e05a4f;
  opacity: 1;
}
.sp-caret {
  position: absolute;
  right: 9px;
  top: 50%;
  transform: translateY(-50%);
  font-size: 0.7rem;
  opacity: 0.7;
}
.sp-list {
  position: absolute;
  top: 100%;
  left: 0;
  z-index: 20;
  display: flex;
  flex-direction: column;
  gap: 6px;
  margin-top: 4px;
  padding: 6px;
  max-height: 22rem;
  overflow: auto;
  border: 1px solid var(--p-content-border-color, #555);
  border-radius: 8px;
  background: var(--p-content-background, #1e1e1e);
  box-shadow: 0 8px 24px rgb(0 0 0 / 40%);
}
.sp-option[draggable='true'] {
  cursor: grab;
}
.sp-option:hover:not(.off) {
  box-shadow: 0 0 0 2px color-mix(in srgb, var(--p-primary-color, #4f8cff) 45%, transparent);
}
.sp-option.on {
  border-style: solid;
}
.sp-option.off {
  opacity: 0.45;
  cursor: not-allowed;
}
.sp-place {
  background: none;
  border: none;
  padding: 0;
  margin-top: 0.3rem;
  color: var(--p-primary-color, #4f8cff);
  cursor: pointer;
  font-size: 0.75rem;
}
</style>
