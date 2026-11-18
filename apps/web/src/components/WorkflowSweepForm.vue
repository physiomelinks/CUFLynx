<script setup>
/**
 * The Simulation form of a new step dropped after the target: a sweep run on the merged
 * target (CA's `kind: sweep, on_target: true`). Its protocol (the study's obs_data, or a
 * file), the variables recorded over time, the parameters swept (values, or a range of
 * points) and how they combine (every combination, or one at a time), and the output step.
 * Saving writes the step (POST /api/workflow/sweep), checked by CA; the workflow's Run runs it.
 */
import Button from 'primevue/button'
import Dialog from 'primevue/dialog'
import { computed, ref, watch } from 'vue'

const props = defineProps({
  // The after-target new step being set up, or null.
  draft: { type: String, default: null },
  // Variables that can be recorded (states, algebraic): component/name.
  outputsOffered: { type: Array, default: () => [] },
  // Constants that can be swept: component/name.
  parametersOffered: { type: Array, default: () => [] },
  // async ({draft, obsData, outputs, parameters, design, dt}) => '' when done, else why not.
  save: { type: Function, required: true },
  busy: { type: Boolean, default: false },
})
const emit = defineEmits(['close'])

const useStudyObs = ref(true)
const obsPath = ref('')
const dt = ref(null)
const design = ref('grid')
const outputs = ref([])
const filter = ref('')
const rows = ref([])
const why = ref('')
const saving = ref(false)

watch(() => props.draft, () => {
  useStudyObs.value = true
  obsPath.value = ''
  dt.value = null
  design.value = 'grid'
  outputs.value = []
  filter.value = ''
  rows.value = []
  why.value = ''
})

const shown = computed(() => {
  const f = filter.value.trim().toLowerCase()
  return props.outputsOffered.filter((v) => !f || v.toLowerCase().includes(f)).slice(0, 200)
})

function addRow() {
  rows.value.push({ name: '', how: 'values', values: '', min: null, max: null, points: 5, scale: 'linear' })
}

/** A row as CA's sweep_range, or a reason it is not one. */
function range(r) {
  if (r.how === 'values') {
    const values = String(r.values).split(/[\s,;]+/).filter(Boolean).map(Number)
    if (!values.length || values.some((v) => !Number.isFinite(v))) return `${r.name}: give numbers, separated by commas`
    return { values }
  }
  if (!Number.isFinite(r.min) || !Number.isFinite(r.max)) return `${r.name}: give a minimum and a maximum`
  const out = { min: r.min, max: r.max, points: Math.max(2, Math.round(r.points || 5)) }
  if (r.scale === 'log') out.scale = 'log'
  return out
}

async function onSave() {
  why.value = ''
  const parameters = {}
  for (const r of rows.value) {
    if (!r.name.trim()) continue
    const got = range(r)
    if (typeof got === 'string') {
      why.value = got
      return
    }
    parameters[r.name.trim()] = got
  }
  if (!useStudyObs.value && !obsPath.value.trim()) {
    why.value = "Give the protocol's obs_data file, or use the study's."
    return
  }
  saving.value = true
  why.value = await props.save({
    draft: props.draft, obsData: useStudyObs.value ? null : obsPath.value.trim(),
    outputs: [...outputs.value], parameters, design: design.value,
    dt: Number.isFinite(dt.value) && dt.value > 0 ? dt.value : null,
  })
  saving.value = false
  if (!why.value) emit('close')
}
</script>

<template>
  <Dialog
    :visible="!!draft"
    modal
    :header="draft ? `Simulation ${draft}: run on the merged target` : ''"
    :style="{ width: 'min(760px, 95vw)' }"
    data-testid="workflow-sweep-form"
    @update:visible="(v) => !v && emit('close')"
  >
    <template v-if="draft">
      <p class="sw-hint">
        Runs the target model with every calibrated and set value merged, after every
        calibration: to record what it predicts, and how it changes as parameters are swept.
      </p>

      <fieldset>
        <legend>Protocol</legend>
        <label class="sw-line">
          <input v-model="useStudyObs" type="radio" :value="true" data-testid="workflow-sweep-study-obs" />
          the study's obs_data (its protocol_info)
        </label>
        <label class="sw-line">
          <input v-model="useStudyObs" type="radio" :value="false" />
          a file:
          <input v-model="obsPath" class="sw-path" placeholder="/path/to/protocol_obs_data.json"
                 :disabled="useStudyObs" data-testid="workflow-sweep-obs-path" />
        </label>
        <label class="sw-line">
          Output step (dt)
          <input v-model.number="dt" type="number" min="0" step="any" class="sw-num"
                 placeholder="as the solver" data-testid="workflow-sweep-dt" />
        </label>
      </fieldset>

      <fieldset>
        <legend>Record ({{ outputs.length }} chosen)</legend>
        <input v-model="filter" class="sw-path" placeholder="filter the model's variables"
               data-testid="workflow-sweep-filter" />
        <div class="sw-list">
          <label v-for="v in shown" :key="v" class="sw-line">
            <input v-model="outputs" type="checkbox" :value="v" /><code>{{ v }}</code>
          </label>
          <p v-if="!outputsOffered.length" class="sw-hint">Load the model as the study to choose its variables.</p>
        </div>
      </fieldset>

      <fieldset>
        <legend>Sweep (none: one run as merged)</legend>
        <datalist id="sw-params"><option v-for="p in parametersOffered" :key="p" :value="p" /></datalist>
        <div v-for="(r, i) in rows" :key="i" class="sw-row" :data-testid="`workflow-sweep-row-${i}`">
          <input v-model="r.name" list="sw-params" class="sw-name" placeholder="component/parameter" />
          <select v-model="r.how">
            <option value="values">values</option>
            <option value="range">range</option>
          </select>
          <input v-if="r.how === 'values'" v-model="r.values" class="sw-values" placeholder="0.5, 1, 2" />
          <template v-else>
            <input v-model.number="r.min" type="number" step="any" class="sw-num" placeholder="min" />
            <input v-model.number="r.max" type="number" step="any" class="sw-num" placeholder="max" />
            <input v-model.number="r.points" type="number" min="2" class="sw-num" title="points" />
            <select v-model="r.scale"><option value="linear">linear</option><option value="log">log</option></select>
          </template>
          <Button icon="pi pi-times" text size="small" title="Remove" @click="rows.splice(i, 1)" />
        </div>
        <Button label="Add a parameter" icon="pi pi-plus" text size="small"
                data-testid="workflow-sweep-add" @click="addRow" />
        <label v-if="rows.length > 1" class="sw-line">
          Combine
          <select v-model="design" data-testid="workflow-sweep-design">
            <option value="grid">every combination</option>
            <option value="one_at_a_time">one at a time (the others as merged)</option>
          </select>
        </label>
      </fieldset>

      <p v-if="why" class="sw-error" data-testid="workflow-sweep-error">{{ why }}</p>
    </template>
    <template #footer>
      <Button label="Cancel" text :disabled="saving" @click="emit('close')" />
      <Button label="Add simulation" :disabled="busy || saving" data-testid="workflow-sweep-save" @click="onSave" />
    </template>
  </Dialog>
</template>

<style scoped>
fieldset {
  border: 1px solid var(--p-content-border-color, #555);
  border-radius: 6px;
  margin: 0 0 0.7rem;
  padding: 0.4rem 0.7rem 0.6rem;
}
legend {
  font-size: 0.8rem;
  opacity: 0.8;
  padding: 0 0.3rem;
}
input,
select {
  font: inherit;
  padding: 0.2rem 0.4rem;
  border: 1px solid var(--p-content-border-color, #555);
  border-radius: 4px;
  background: transparent;
  color: inherit;
}
input[type='radio'],
input[type='checkbox'] {
  padding: 0;
}
.sw-line {
  display: flex;
  gap: 0.5rem;
  align-items: center;
  padding: 0.15rem 0;
}
.sw-path {
  flex: 1;
  width: 100%;
  box-sizing: border-box;
}
.sw-num {
  width: 6rem;
}
.sw-list {
  max-height: 12rem;
  overflow: auto;
  margin-top: 0.3rem;
}
.sw-row {
  display: flex;
  gap: 0.4rem;
  align-items: center;
  margin-bottom: 0.3rem;
}
.sw-name {
  flex: 1;
  min-width: 10rem;
}
.sw-values {
  flex: 1;
}
.sw-hint {
  opacity: 0.75;
  font-size: 0.85em;
}
.sw-error {
  color: #e84a5f;
  font-size: 0.9em;
}
</style>
