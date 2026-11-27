<script setup>
/**
 * An arrow of the workflow chart: everything the target step takes from the source, each as
 * a card (WorkflowArrowKey) showing what passes now (read from the run) with its options and
 * a way to stop passing it, and "Pass more": the other things CA lets the target take from
 * the source, added from a menu. Opened by clicking or double-clicking an arrow, and when an
 * arrow drawn from a box's handle is dropped on another box (nothing passed yet).
 *
 * What an arrow can be comes from CA (describe's link_kinds), and CA checks every change:
 * its reason for refusing is shown here, and nothing changes.
 */
import Button from 'primevue/button'
import Dialog from 'primevue/dialog'
import { computed, ref, watch } from 'vue'
import { linkChoices, linkEntry, referencesOf } from '../lib/workflowTarget'
import WorkflowArrowKey from './WorkflowArrowKey.vue'

const props = defineProps({
  // {source, target}, or null.
  arrow: { type: Object, default: null },
  // The workflow file's steps, and the new steps on the chart ({id, draft: true}).
  steps: { type: Array, default: () => [] },
  // The tabs' words for each step, by id: {calibrates: [{model_name}], ...}.
  details: { type: Object, default: () => ({}) },
  // CA's {key: {sources, users}}.
  linkKinds: { type: Object, default: () => ({}) },
  // Arrows waiting for a new step to run ({source, target, key, options}).
  pendingLinks: { type: Array, default: () => [] },
  // Whether this CA fixes only some of a fixed_from step's values.
  fixedParameters: { type: Boolean, default: false },
  // async ({source, target, key, options, oldKey}) => '' when done, else why not; and
  // async ({source, target, key}) for stopping one.
  save: { type: Function, required: true },
  remove: { type: Function, required: true },
  busy: { type: Boolean, default: false },
})
const emit = defineEmits(['close'])

const DRAWN = ['fixed_from', 'priors_from', 'start_from', 'result_from', 'emulator_from',
  'sensitivity_from', 'params_for_id_from']
const LABELS = { fixed_from: 'fixed values', priors_from: 'a prior', start_from: 'a start point',
  result_from: 'the point it is analysed at', emulator_from: 'its emulator',
  sensitivity_from: 'its sensitivity indices', params_for_id_from: 'the parameters it chose' }

const byId = computed(() => Object.fromEntries(props.steps.map((s) => [s.id, s])))
const source = computed(() => byId.value[props.arrow?.source] ?? null)
const target = computed(() => byId.value[props.arrow?.target] ?? null)

/** What the target takes from the source now: [{name, options, pending}]. */
const passed = computed(() => {
  const a = props.arrow
  if (!a) return []
  const out = []
  if (target.value && !target.value.draft) {
    for (const [key, id] of referencesOf(target.value, props.steps)) {
      if (id === a.source && DRAWN.includes(key) && !out.some((p) => p.name === key)) {
        out.push({ name: key, options: linkEntry(target.value, a.source, key) ?? {}, pending: false })
      }
    }
  }
  for (const l of props.pendingLinks) {
    if (l.source === a.source && l.target === a.target) {
      out.push({ name: l.key, options: l.options ?? {}, pending: true })
    }
  }
  return out
})
const more = computed(() => (props.arrow ? linkChoices(props.linkKinds, source.value, target.value) : [])
  .filter((k) => !passed.value.some((p) => p.name === k)))
const pending = computed(() => !!(source.value?.draft || target.value?.draft))
// the source's parameters as CA names them in a fixed_from or priors_from entry (its model
// names), and each one's name as the run reports its value (component/parameter)
const calibrates = computed(() => (props.details[props.arrow?.source]?.calibrates ?? []).filter((c) => c.model_name))
const parameters = computed(() => calibrates.value.map((c) => c.model_name))
const labels = computed(() => Object.fromEntries(calibrates.value.map((c) =>
  [c.model_name, c.target_parameterisation_name ?? c.target_host_name ?? c.model_name])))
const takesCalibration = computed(() => !target.value || target.value.draft ||
  (target.value.kind ?? 'calibration') === 'calibration')
const objectiveNow = computed(() => target.value?.settings?.optimiser_options?.objective_function ?? null)

const adding = ref(null)
const addCard = ref(null)
const why = ref('')
const working = ref(false)

watch(() => props.arrow, () => {
  why.value = ''
  adding.value = !passed.value.length && more.value.length === 1 ? more.value[0] : null
}, { immediate: true })

async function act(call) {
  working.value = true
  why.value = await call()
  working.value = false
  return !why.value
}

function saveKey(name, options) {
  return act(() => props.save({ source: props.arrow.source, target: props.arrow.target,
                                key: name, options, oldKey: null }))
}

async function removeKey(name) {
  const last = passed.value.length === 1
  if (await act(() => props.remove({ source: props.arrow.source, target: props.arrow.target, key: name }))) {
    if (last) emit('close')
  }
}

async function addKey() {
  if (!adding.value) return
  if (addCard.value?.empty) {
    why.value = 'Choose at least one parameter.'
    return
  }
  if (await saveKey(adding.value, addCard.value?.current() ?? null)) adding.value = null
}
</script>

<template>
  <Dialog
    :visible="!!arrow"
    modal
    :header="arrow ? `${arrow.source} → ${arrow.target}` : ''"
    :style="{ width: 'min(760px, 95vw)' }"
    data-testid="workflow-link-editor"
    @update:visible="(v) => !v && emit('close')"
  >
    <template v-if="arrow">
      <p v-if="pending" class="le-hint">
        A new step is at one end: what passes is written into the workflow when that step is
        made by a run.
      </p>
      <p v-if="!passed.length" class="le-hint">Nothing passes from {{ arrow.source }} to {{ arrow.target }} yet.</p>
      <WorkflowArrowKey
        v-for="p in passed"
        :key="p.name"
        :source="arrow.source"
        :target="arrow.target"
        :name="p.name"
        :options="p.options"
        :pending="p.pending"
        :objective-now="objectiveNow"
        :parameters="parameters"
        :labels="labels"
        :fixed-parameters="fixedParameters"
        :takes-calibration="takesCalibration"
        :busy="busy || working"
        @save="(o) => saveKey(p.name, o)"
        @remove="removeKey(p.name)"
      />

      <div class="le-more" data-testid="workflow-link-more">
        <label>
          <strong>{{ passed.length ? 'Pass more' : 'Pass' }}</strong>
          <select v-model="adding" :disabled="!more.length || busy || working" data-testid="workflow-link-more-select">
            <option :value="null">{{ more.length ? 'choose what…' : 'nothing else can pass' }}</option>
            <option v-for="k in more" :key="k" :value="k">{{ LABELS[k] ?? k }} ({{ k }})</option>
          </select>
        </label>
        <WorkflowArrowKey
          v-if="adding"
          ref="addCard"
          :key="`new-${adding}`"
          :source="arrow.source"
          :target="arrow.target"
          :name="adding"
          is-new
          :parameters="parameters"
          :labels="labels"
          :fixed-parameters="fixedParameters"
          :takes-calibration="takesCalibration"
          :busy="busy || working"
        />
        <Button
          v-if="adding"
          :label="`Pass ${LABELS[adding] ?? adding}`"
          icon="pi pi-plus"
          size="small"
          :disabled="busy || working"
          data-testid="workflow-link-add"
          @click="addKey"
        />
      </div>
      <p v-if="why" class="le-error" data-testid="workflow-link-error">{{ why }}</p>
    </template>
    <template #footer>
      <Button label="Close" text data-testid="workflow-link-close" @click="emit('close')" />
    </template>
  </Dialog>
</template>

<style scoped>
.le-more {
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
  align-items: flex-start;
  border-top: 1px dashed var(--p-content-border-color, #555);
  padding-top: 0.6rem;
}
.le-more > label {
  display: flex;
  gap: 0.6rem;
  align-items: center;
}
.le-more :deep(.ak) {
  align-self: stretch;
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
