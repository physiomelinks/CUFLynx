<script setup>
/**
 * The step prep box, opened by New step: the boxes a new step can be, to drag onto the
 * chart. Models: one box per model the workflow uses (its target and every step's own); what
 * a model box does is chosen by clicking it on the chart (a calibration, an analysis, a
 * simulation...). Actions: boxes on no model, each made by its own form (values set by hand;
 * a parameter set chosen from several calibrations by cost, where CA has it).
 *
 * A dragged box carries `{model}` or `{action}` as `application/x-cuflynx-new-step`, which
 * the chart (WorkflowDiagram) reads when it is dropped.
 */
import { computed, ref } from 'vue'
import { modelShort } from '../lib/workflowTarget'

const props = defineProps({
  // The workflow file ({target, steps}).
  workflow: { type: Object, default: null },
  // CA's step kinds (describe's step_kinds), for which actions this CA can make.
  stepKinds: { type: Array, default: () => [] },
})
const emit = defineEmits(['place', 'close'])

const NEW_STEP_TYPE = 'application/x-cuflynx-new-step'

/** Every model the workflow uses, once each: the target first, then each step's own. */
const models = computed(() => {
  const out = []
  const seen = new Set()
  const add = (model, used) => {
    const key = JSON.stringify(model ?? null)
    if (seen.has(key)) {
      out.find((m) => m.key === key)?.used.push(used)
      return
    }
    seen.add(key)
    out.push({ key, model, label: modelShort(model), used: [used] })
  }
  const target = props.workflow?.target
  if (target) add(target, 'the target')
  for (const s of props.workflow?.steps ?? []) {
    if (s.model && !s.on_target) add(s.model, s.id)
  }
  return out
})

const ACTIONS = [
  { action: 'combine', kind: 'combine', label: 'Choose a parameter set by cost',
    words: 'the lowest-cost parameter set of the calibrations drawn to it' },
  { action: 'set', kind: 'set', label: 'Set values by hand',
    words: 'values with the reason each was chosen (a red set-by-hand box)' },
]
const actions = computed(() => ACTIONS.map((a) => ({ ...a, available: props.stepKinds.includes(a.kind) })))

const modelKey = ref('')
const actionKey = ref('')
const chosen = computed(() => {
  if (modelKey.value) {
    const m = models.value.find((x) => x.key === modelKey.value)
    return m ? { kind: 'model', title: m.label, words: `a step on this model (used by ${m.used.join(', ')}): click it on the chart to choose what it does`, payload: { model: m.model } } : null
  }
  if (actionKey.value) {
    const a = actions.value.find((x) => x.action === actionKey.value)
    return a ? { kind: a.action, title: a.label, words: a.words, payload: { action: a.action } } : null
  }
  return null
})

function pickModel(key) {
  modelKey.value = key
  if (key) actionKey.value = ''
}
function pickAction(key) {
  actionKey.value = key
  if (key) modelKey.value = ''
}

function onDragStart(event) {
  event.dataTransfer.setData(NEW_STEP_TYPE, JSON.stringify(chosen.value.payload))
  event.dataTransfer.effectAllowed = 'copy'
}
</script>

<template>
  <div class="sp" data-testid="workflow-step-prep">
    <div class="sp-head">
      <strong>New step</strong>
      <span class="sp-hint">choose a box, then drag it onto the chart</span>
      <button type="button" class="sp-close pi pi-times" title="Close" @click="emit('close')" />
    </div>
    <div class="sp-pickers">
      <label>
        Models
        <select :value="modelKey" data-testid="workflow-prep-models" @change="pickModel($event.target.value)">
          <option value="">choose a model…</option>
          <option v-for="m in models" :key="m.key" :value="m.key">{{ m.label }}</option>
        </select>
      </label>
      <label>
        Actions
        <select :value="actionKey" data-testid="workflow-prep-actions" @change="pickAction($event.target.value)">
          <option value="">choose an action…</option>
          <option v-for="a in actions" :key="a.action" :value="a.action" :disabled="!a.available">
            {{ a.label }}{{ a.available ? '' : ' (needs a newer circulatory_autogen)' }}
          </option>
        </select>
      </label>
    </div>
    <div
      v-if="chosen"
      class="sp-box"
      :class="chosen.kind"
      draggable="true"
      title="Drag onto the chart: into a column, or a gap between columns for a new stage"
      data-testid="workflow-prep-box"
      @dragstart="onDragStart"
    >
      <strong>{{ chosen.title }}</strong>
      <span>{{ chosen.words }}</span>
      <button
        type="button"
        class="sp-place"
        title="Put it at the end of the chart instead of dragging it"
        data-testid="workflow-prep-place"
        @click="emit('place', chosen.payload)"
      >place at the end</button>
    </div>
  </div>
</template>

<style scoped>
.sp {
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
  gap: 1rem;
  flex-wrap: wrap;
  margin: 0.5rem 0;
}
.sp-pickers label {
  display: flex;
  gap: 0.4rem;
  align-items: center;
  font-size: 0.85rem;
}
.sp-pickers select {
  font: inherit;
  padding: 0.2rem 0.4rem;
  border: 1px solid var(--p-content-border-color, #555);
  border-radius: 4px;
  background: transparent;
  color: inherit;
  max-width: 20rem;
}
.sp-box {
  display: flex;
  flex-direction: column;
  gap: 2px;
  width: 250px;
  box-sizing: border-box;
  padding: 6px 10px;
  border: 2px dashed var(--p-primary-color, #4f8cff);
  border-radius: 6px;
  font-size: 0.78rem;
  cursor: grab;
}
.sp-box.set {
  border-color: #c8372d;
}
.sp-box span {
  opacity: 0.8;
}
.sp-place {
  align-self: flex-start;
  margin-top: 4px;
  background: none;
  border: none;
  padding: 0;
  color: var(--p-primary-color, #4f8cff);
  cursor: pointer;
  font-size: 0.75rem;
}
</style>
