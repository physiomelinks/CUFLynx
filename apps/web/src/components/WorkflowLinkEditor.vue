<script setup>
/**
 * An arrow of the workflow chart, being drawn or changed: what the target step takes from
 * the source (CA's key: fixed values, a prior, a start point, ...), and for a prior, which
 * parameters, as what distribution, how widened, and whether the calibration optimises the
 * log posterior so it uses it. Opened when an arrow drawn from a box's handle is dropped on
 * another box, and by double-clicking an arrow.
 *
 * What an arrow can be comes from CA (describe's link_kinds), and CA checks the result:
 * its reason for refusing is shown here, and nothing changes.
 */
import Button from 'primevue/button'
import Dialog from 'primevue/dialog'
import { computed, ref, watch } from 'vue'
import { linkChoices, linkEntry } from '../lib/workflowTarget'

const props = defineProps({
  // {source, target, key (null for a new arrow), pending (a draft at either end)}, or null.
  arrow: { type: Object, default: null },
  // The workflow file's steps, and the new steps on the chart ({id, draft: true}).
  steps: { type: Array, default: () => [] },
  // The tabs' words for each step, by id: {calibrates: [{model_name}], ...}.
  details: { type: Object, default: () => ({}) },
  // CA's {key: {sources, users}}.
  linkKinds: { type: Object, default: () => ({}) },
  // An arrow still waiting for a new step to run: its options ({source, target, key, options}).
  pendingLinks: { type: Array, default: () => [] },
  // Whether this CA fixes only some of a fixed_from step's values.
  fixedParameters: { type: Boolean, default: false },
  // async (arrow) => '' when done, else why not; the same for removing one.
  save: { type: Function, required: true },
  remove: { type: Function, required: true },
  busy: { type: Boolean, default: false },
})
const emit = defineEmits(['close'])

const WORDS = {
  fixed_from: ['fixed values', 'its values, fixed in this step'],
  priors_from: ['prior', 'its posterior, as this step’s prior'],
  start_from: ['start point', 'its values, where this step’s fit starts'],
  result_from: ['analysed at', 'its values, the point this analysis is at'],
  emulator_from: ['emulator', 'its emulator, run in place of the model'],
  sensitivity_from: ['sensitivity', 'its sensitivity indices, to choose parameters by'],
  params_for_id_from: ['parameters', 'the parameters it chose, calibrated here'],
}
const PRIOR_KINDS = [
  ['mvnormal', 'multivariate normal'],
  ['normal', 'independent normals'],
  ['kde', 'kernel density estimate'],
]

const byId = computed(() => Object.fromEntries(props.steps.map((s) => [s.id, s])))
const source = computed(() => byId.value[props.arrow?.source] ?? null)
const target = computed(() => byId.value[props.arrow?.target] ?? null)
const choices = computed(() =>
  props.arrow ? linkChoices(props.linkKinds, source.value, target.value) : [])
// the source's parameters, for a prior or a fixed arrow that names some of them
const parameters = computed(() =>
  (props.details[props.arrow?.source]?.calibrates ?? []).map((c) => c.model_name).filter(Boolean))

const key = ref(null)
const chosen = ref([])
const priorKind = ref('mvnormal')
const inflate = ref(1)
const objective = ref(true)
const why = ref('')
const saving = ref(false)

/** The arrow's current options: from its target step, or the pending arrow's. */
function current() {
  const a = props.arrow
  if (!a?.key) return {}
  const pending = props.pendingLinks.find(
    (l) => l.source === a.source && l.target === a.target && l.key === a.key)
  if (pending) return pending.options ?? {}
  return linkEntry(target.value, a.source, a.key) ?? {}
}

watch(() => props.arrow, (a) => {
  why.value = ''
  if (!a) return
  key.value = a.key ?? (choices.value.length === 1 ? choices.value[0] : null)
  const opts = current()
  chosen.value = Array.isArray(opts.parameters) ? [...opts.parameters] : [...parameters.value]
  priorKind.value = opts.kind ?? 'mvnormal'
  inflate.value = opts.inflate ?? 1
  const set = target.value?.settings?.optimiser_options?.objective_function
  objective.value = a.key === 'priors_from' ? set === 'likelihood' : true
}, { immediate: true })

const takesCalibration = computed(() => !target.value || target.value.draft ||
  (target.value.kind ?? 'calibration') === 'calibration')
const names = computed(() => key.value === 'priors_from' ||
  (key.value === 'fixed_from' && props.fixedParameters))

function options() {
  const out = {}
  if (names.value && parameters.value.length && chosen.value.length < parameters.value.length) {
    out.parameters = [...chosen.value]
  }
  if (key.value === 'priors_from') {
    if (priorKind.value !== 'mvnormal') out.kind = priorKind.value
    if (Number(inflate.value) !== 1) out.inflate = Number(inflate.value)
    if (objective.value && takesCalibration.value) out.objective = 'likelihood'
  }
  return Object.keys(out).length ? out : null
}

async function onSave() {
  if (!key.value) return
  if (names.value && parameters.value.length && !chosen.value.length) {
    why.value = 'Choose at least one parameter, or remove the arrow.'
    return
  }
  saving.value = true
  why.value = await props.save({
    source: props.arrow.source, target: props.arrow.target, key: key.value,
    options: options(), oldKey: props.arrow.key && props.arrow.key !== key.value ? props.arrow.key : null,
  })
  saving.value = false
  if (!why.value) emit('close')
}

async function onRemove() {
  saving.value = true
  why.value = await props.remove({ source: props.arrow.source, target: props.arrow.target,
                                   key: props.arrow.key })
  saving.value = false
  if (!why.value) emit('close')
}
</script>

<template>
  <Dialog
    :visible="!!arrow"
    modal
    :header="arrow ? `${arrow.key ? 'Arrow' : 'New arrow'} ${arrow.source} → ${arrow.target}` : ''"
    :style="{ width: '34rem' }"
    data-testid="workflow-link-editor"
    @update:visible="(v) => !v && emit('close')"
  >
    <template v-if="arrow">
      <p v-if="arrow.pending" class="le-hint">
        A new step is at one end: the arrow is written into the workflow when that step is
        made by a run.
      </p>
      <fieldset class="le-keys">
        <legend>What {{ arrow.target }} takes from {{ arrow.source }}</legend>
        <p v-if="!choices.length" class="le-error" data-testid="workflow-link-none">
          Nothing: {{ arrow.target }} cannot take anything from {{ arrow.source }}.
        </p>
        <label v-for="c in choices" :key="c" class="le-key">
          <input v-model="key" type="radio" :value="c" :data-testid="`workflow-link-key-${c}`" />
          <strong>{{ WORDS[c]?.[0] ?? c }}</strong>
          <span>{{ WORDS[c]?.[1] ?? '' }}</span>
          <code>{{ c }}</code>
        </label>
      </fieldset>

      <fieldset v-if="names && parameters.length" class="le-params" data-testid="workflow-link-params">
        <legend>Parameters</legend>
        <label v-for="p in parameters" :key="p">
          <input v-model="chosen" type="checkbox" :value="p" />
          <code>{{ p }}</code>
        </label>
      </fieldset>
      <p v-else-if="key === 'fixed_from' && !fixedParameters" class="le-hint" data-testid="workflow-link-fixed-all">
        Every value {{ arrow.source }} calibrated or set is fixed: this circulatory_autogen
        cannot fix only some of them.
      </p>

      <fieldset v-if="key === 'priors_from'" class="le-prior" data-testid="workflow-link-prior">
        <legend>Prior</legend>
        <label>As
          <select v-model="priorKind" data-testid="workflow-link-prior-kind">
            <option v-for="[v, label] in PRIOR_KINDS" :key="v" :value="v">{{ label }}</option>
          </select>
        </label>
        <label title="multiplies the distribution's variance, to temper an overconfident earlier posterior">
          Widen the variance ×
          <input v-model.number="inflate" type="number" min="0.01" step="0.5" data-testid="workflow-link-inflate" />
        </label>
        <label v-if="takesCalibration" class="le-objective">
          <input v-model="objective" type="checkbox" data-testid="workflow-link-objective" />
          The calibration optimises the log posterior, so it uses this prior
          (<code>objective_function: likelihood</code>)
        </label>
      </fieldset>

      <p v-if="why" class="le-error" data-testid="workflow-link-error">{{ why }}</p>
    </template>
    <template #footer>
      <Button
        v-if="arrow?.key"
        label="Remove arrow"
        icon="pi pi-trash"
        severity="danger"
        text
        :disabled="busy || saving"
        data-testid="workflow-link-remove"
        @click="onRemove"
      />
      <Button label="Cancel" text :disabled="saving" @click="emit('close')" />
      <Button
        :label="arrow?.key ? 'Save' : 'Draw arrow'"
        :disabled="busy || saving || !key"
        data-testid="workflow-link-save"
        @click="onSave"
      />
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
.le-key {
  display: grid;
  grid-template-columns: auto auto 1fr auto;
  gap: 0.5rem;
  align-items: baseline;
  padding: 0.2rem 0;
  cursor: pointer;
}
.le-key span {
  opacity: 0.75;
  font-size: 0.85em;
}
.le-key code,
.le-params code {
  font-size: 0.8em;
  opacity: 0.7;
}
.le-params label {
  display: flex;
  gap: 0.4rem;
  align-items: center;
  padding: 0.1rem 0;
}
.le-prior {
  display: flex;
  flex-direction: column;
  gap: 0.4rem;
}
.le-prior input[type='number'] {
  width: 5rem;
}
.le-objective {
  display: flex;
  gap: 0.4rem;
  align-items: baseline;
  font-size: 0.9em;
}
.le-hint {
  opacity: 0.75;
  font-size: 0.85em;
}
.le-error {
  color: #e84a5f;
  font-size: 0.9em;
}
</style>
