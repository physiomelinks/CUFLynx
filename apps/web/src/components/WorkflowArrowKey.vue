<script setup>
/**
 * One thing an arrow passes (CA's key: fixed values, a prior, a start point, ...), in the
 * arrow dialog (WorkflowLinkEditor): what passes now, read from the run, and its options --
 * for a prior, which parameters, as what distribution, how widened, and whether the
 * calibration optimises the log posterior so it uses it; for fixed values, which parameters
 * where CA takes them. A new one (`isNew`) has only its options and is added by its owner.
 */
import Button from 'primevue/button'
import { computed, ref, watch } from 'vue'
import { errorMessage, getWorkflowEdge } from '../lib/api'
import WorkflowEdgeBody from './WorkflowEdgeBody.vue'

const props = defineProps({
  source: { type: String, required: true },
  target: { type: String, required: true },
  // CA's key
  name: { type: String, required: true },
  // Its options now ({kind, inflate, parameters}), {} for none.
  options: { type: Object, default: () => ({}) },
  // The target step's optimiser objective now (for a prior's tick box).
  objectiveNow: { type: String, default: null },
  // A key being added (no detail, no remove), or one waiting for a new step to run.
  isNew: { type: Boolean, default: false },
  pending: { type: Boolean, default: false },
  // The source's parameters (model names), for a prior or a fixed arrow that names some, and
  // each one's name as the run reports its value ({model name: component/parameter}).
  parameters: { type: Array, default: () => [] },
  labels: { type: Object, default: () => ({}) },
  fixedParameters: { type: Boolean, default: false },
  // The target is (or may become) a calibration, which uses a prior only with its objective.
  takesCalibration: { type: Boolean, default: true },
  busy: { type: Boolean, default: false },
})
const emit = defineEmits(['save', 'remove'])

const WORDS = {
  fixed_from: ['Fixed values', 'its values, fixed in this step'],
  priors_from: ['Prior', 'its posterior, as this step’s prior'],
  start_from: ['Start point', 'its values, where this step’s fit starts'],
  result_from: ['Analysed at', 'its values, the point this analysis is at'],
  emulator_from: ['Emulator', 'its emulator, run in place of the model'],
  sensitivity_from: ['Sensitivity', 'its sensitivity indices, to choose parameters by'],
  params_for_id_from: ['Parameters', 'the parameters it chose, calibrated here'],
}
const PRIOR_KINDS = [
  ['mvnormal', 'multivariate normal'],
  ['normal', 'independent normals'],
  ['kde', 'kernel density estimate'],
]

const chosen = ref([])
const priorKind = ref('mvnormal')
const inflate = ref(1)
const objective = ref(true)
const detail = ref(null)
const detailError = ref('')

function reset() {
  const o = props.options ?? {}
  chosen.value = Array.isArray(o.parameters) ? [...o.parameters] : [...props.parameters]
  priorKind.value = o.kind ?? 'mvnormal'
  inflate.value = o.inflate ?? 1
  objective.value = props.isNew ? true : props.objectiveNow === 'likelihood'
}
watch(() => [props.name, props.options, props.parameters], reset, { immediate: true })

watch(() => [props.source, props.target, props.name, props.isNew, props.pending], async () => {
  detail.value = null
  detailError.value = ''
  // a new arrow too: what it would pass (the source's values now), so a choice of
  // parameters can be made seeing them; a new step at either end has none to read
  if (props.pending) return
  try {
    detail.value = await getWorkflowEdge(props.source, props.target, props.name)
  } catch (e) {
    detailError.value = errorMessage(e)
  }
}, { immediate: true })

const names = computed(() => props.name === 'priors_from' ||
  (props.name === 'fixed_from' && props.fixedParameters))
const hasOptions = computed(() => props.name === 'priors_from' || (names.value && props.parameters.length))

/** The options as the server takes them: none when they say nothing. */
function current() {
  const out = {}
  if (names.value && props.parameters.length && chosen.value.length < props.parameters.length) {
    out.parameters = [...chosen.value]
  }
  if (props.name === 'priors_from') {
    if (priorKind.value !== 'mvnormal') out.kind = priorKind.value
    if (Number(inflate.value) !== 1) out.inflate = Number(inflate.value)
    if (objective.value && props.takesCalibration) out.objective = 'likelihood'
  }
  return Object.keys(out).length ? out : null
}

const empty = computed(() => names.value && props.parameters.length > 0 && chosen.value.length === 0)

// each parameter's value now, as the run reports it, by model name
const valueOf = computed(() => {
  const rows = Object.fromEntries((detail.value?.rows ?? []).map((r) => [r.name, r]))
  return Object.fromEntries(props.parameters.map((p) => [p, rows[props.labels[p] ?? p] ?? rows[p] ?? null]))
})
// the detail shown above the choice: its values are in the choice, so not twice
const shownDetail = computed(() => (detail.value && names.value && props.parameters.length
  ? { ...detail.value, rows: [] } : detail.value))

function num(v) {
  return typeof v === 'number' ? v.toPrecision(5) : ''
}
defineExpose({ current, empty })
</script>

<template>
  <section class="ak" :data-testid="`workflow-arrow-key-${name}`">
    <header class="ak-head">
      <strong>{{ WORDS[name]?.[0] ?? name }}</strong>
      <span class="ak-hint">{{ WORDS[name]?.[1] ?? '' }}</span>
      <code>{{ name }}</code>
    </header>

    <p v-if="pending && !isNew" class="ak-hint">
      Waits for the new step to run: then it is written into the workflow.
    </p>
    <p v-if="detailError" class="ak-error">{{ detailError }}</p>
    <WorkflowEdgeBody v-if="shownDetail" :edge="{ source, target, key: name }" :detail="shownDetail" :heading="false" />

    <div v-if="hasOptions" class="ak-options">
      <fieldset v-if="names && parameters.length" class="ak-params" :data-testid="`workflow-link-params-${name}`">
        <legend>{{ name === 'fixed_from' ? 'Fix these values' : 'Give these a prior' }}
          ({{ chosen.length }} of {{ parameters.length }})</legend>
        <label v-for="p in parameters" :key="p">
          <input v-model="chosen" type="checkbox" :value="p" />
          <code>{{ labels[p] ?? p }}</code>
          <span class="ak-value">{{ num(valueOf[p]?.value) }} {{ valueOf[p]?.units ?? '' }}</span>
        </label>
        <span class="ak-all">
          <button type="button" @click="chosen = [...parameters]">all</button>
          <button type="button" @click="chosen = []">none</button>
        </span>
      </fieldset>
      <template v-if="name === 'priors_from'">
        <label>As
          <select v-model="priorKind" data-testid="workflow-link-prior-kind">
            <option v-for="[v, label] in PRIOR_KINDS" :key="v" :value="v">{{ label }}</option>
          </select>
        </label>
        <label title="multiplies the distribution's variance, to temper an overconfident earlier posterior">
          Widen the variance ×
          <input v-model.number="inflate" type="number" min="0.01" step="0.5" data-testid="workflow-link-inflate" />
        </label>
        <label v-if="takesCalibration" class="ak-objective">
          <input v-model="objective" type="checkbox" data-testid="workflow-link-objective" />
          The calibration optimises the log posterior, so it uses this prior
          (<code>objective_function: likelihood</code>)
        </label>
      </template>
    </div>
    <p v-else-if="name === 'fixed_from' && !fixedParameters" class="ak-hint" data-testid="workflow-link-fixed-all">
      Every value {{ source }} calibrated or set is fixed: this circulatory_autogen cannot fix
      only some of them.
    </p>

    <footer v-if="!isNew" class="ak-actions">
      <Button
        v-if="hasOptions"
        label="Save"
        size="small"
        outlined
        :disabled="busy || empty"
        :data-testid="`workflow-link-save-${name}`"
        @click="emit('save', current())"
      />
      <Button
        label="Stop passing this"
        icon="pi pi-trash"
        size="small"
        severity="danger"
        text
        :disabled="busy"
        :data-testid="`workflow-link-remove-${name}`"
        @click="emit('remove')"
      />
    </footer>
  </section>
</template>

<style scoped>
.ak {
  border: 1px solid var(--p-content-border-color, #555);
  border-radius: 6px;
  padding: 0.5rem 0.75rem;
  margin-bottom: 0.6rem;
}
.ak-head {
  display: flex;
  gap: 0.5rem;
  align-items: baseline;
}
.ak-head code {
  margin-left: auto;
  font-size: 0.8em;
  opacity: 0.7;
}
.ak-hint {
  opacity: 0.75;
  font-size: 0.85em;
}
.ak-error {
  color: #e84a5f;
  font-size: 0.9em;
}
.ak-options {
  display: flex;
  flex-direction: column;
  gap: 0.4rem;
  margin-top: 0.4rem;
}
.ak-params {
  border: 1px solid var(--p-content-border-color, #555);
  border-radius: 6px;
  padding: 0.3rem 0.6rem 0.5rem;
  margin: 0;
}
.ak-params legend {
  font-size: 0.8rem;
  opacity: 0.8;
}
.ak-params label {
  display: grid;
  grid-template-columns: auto minmax(12rem, auto) 1fr;
  gap: 0.5rem;
  align-items: center;
}
.ak-value {
  font-variant-numeric: tabular-nums;
  opacity: 0.85;
}
.ak-all {
  display: flex;
  gap: 0.6rem;
  margin-top: 0.2rem;
}
.ak-all button {
  background: none;
  border: none;
  padding: 0;
  color: var(--p-primary-color, #4f8cff);
  cursor: pointer;
  font-size: 0.8em;
}
.ak-options input[type='number'] {
  width: 5rem;
}
.ak-objective {
  display: flex;
  gap: 0.4rem;
  align-items: baseline;
  font-size: 0.9em;
}
.ak-actions {
  display: flex;
  justify-content: flex-end;
  gap: 0.4rem;
  margin-top: 0.4rem;
}
</style>
