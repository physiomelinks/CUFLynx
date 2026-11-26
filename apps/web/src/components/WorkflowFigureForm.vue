<script setup>
/**
 * The form of a Draw figures action box: the figure function (libcuflynx's standard figures,
 * or a script beside the workflow) and, per parameter of it, the step it reads and what it
 * reads from that step (CA's input kinds, offered only where the step's kind gives them).
 * Saving makes the box CA's figure step (POST /api/workflow/figure_step), checked by CA; the
 * workflow's Run draws it.
 */
import Button from 'primevue/button'
import Dialog from 'primevue/dialog'
import { computed, ref, watch } from 'vue'

const props = defineProps({
  // The action box being set up, or null.
  draft: { type: String, default: null },
  // The workflow file's steps ({id, kind}).
  steps: { type: Array, default: () => [] },
  // async () => {functions: [{function, standard, name, parameters, doc}], kinds: {name:
  // {step_kinds, description}}}
  load: { type: Function, required: true },
  // async ({draft, function, inputs: {param: {kind, step}}, description}) => '' when done,
  // else why not.
  save: { type: Function, required: true },
  busy: { type: Boolean, default: false },
})
const emit = defineEmits(['close'])

const functions = ref([])
const kinds = ref({})
const loadError = ref('')
const chosen = ref('')
// {param: {step, kind}}
const inputs = ref({})
const description = ref('')
const why = ref('')
const saving = ref(false)

const kindOf = (step) => step?.kind ?? 'calibration'
const figure = computed(() => functions.value.find((f) => f.function === chosen.value) ?? null)
const groups = computed(() => [
  { label: 'Standard figures (libcuflynx)', items: functions.value.filter((f) => f.standard) },
  { label: 'Scripts beside the workflow', items: functions.value.filter((f) => !f.standard) },
].filter((g) => g.items.length))

/** The input kinds a step gives: those whose step kinds include its kind. */
function kindsFrom(stepId) {
  const step = props.steps.find((s) => s.id === stepId)
  if (!step) return []
  return Object.keys(kinds.value).filter((k) => kinds.value[k].step_kinds.includes(kindOf(step)))
}
// the steps a figure can read: those that give at least one input kind
const readable = computed(() => props.steps.filter((s) => s.kind !== 'figure' && kindsFrom(s.id).length))

/** The input kind named like `param`, or like the function when it has one parameter (the
 * standard figures' `best_fit(fit)`, `laplace(laplace)`), or ''. CA declares no kind per
 * parameter, so a name is the only hint. */
function namedKind(param) {
  const names = [param, ...(figure.value?.parameters.length === 1 ? [figure.value.name] : [])]
  return names.find((n) => kinds.value[n]) ?? ''
}

/** The kind for `param` read from `stepId`: the one it is named like when the step gives it,
 * else the only one the step gives, else none (the user picks). */
function guessKind(param, stepId) {
  const offered = kindsFrom(stepId)
  const named = namedKind(param)
  if (offered.includes(named)) return named
  return offered.length === 1 ? offered[0] : ''
}

function pickFunction(fn) {
  chosen.value = fn
  const out = {}
  for (const param of figure.value?.parameters ?? []) {
    // a parameter named like an input kind starts at the latest step that gives it
    const named = namedKind(param)
    const step = named
      ? [...readable.value].reverse().find((s) => kindsFrom(s.id).includes(named))?.id ?? ''
      : ''
    out[param] = { step, kind: step ? guessKind(param, step) : '' }
  }
  inputs.value = out
}

function pickStep(param, step) {
  const kind = inputs.value[param]?.kind
  inputs.value[param] = { step, kind: kindsFrom(step).includes(kind) ? kind : guessKind(param, step) }
}

const complete = computed(() => !!figure.value
  && figure.value.parameters.every((p) => inputs.value[p]?.step && inputs.value[p]?.kind))

watch(() => props.draft, async (draft) => {
  chosen.value = ''
  inputs.value = {}
  description.value = ''
  why.value = ''
  loadError.value = ''
  if (!draft) return
  try {
    const data = await props.load()
    functions.value = data?.functions ?? []
    kinds.value = data?.kinds ?? {}
  } catch (e) {
    loadError.value = e?.response?.data?.detail ?? e?.message ?? String(e)
  }
}, { immediate: true })

async function onSave() {
  saving.value = true
  const given = {}
  for (const p of figure.value.parameters) given[p] = { ...inputs.value[p] }
  why.value = await props.save({ draft: props.draft, function: chosen.value, inputs: given,
    description: description.value })
  saving.value = false
  if (!why.value) emit('close')
}
</script>

<template>
  <Dialog
    :visible="!!draft"
    modal
    :header="draft ? `Draw figures: ${draft}` : ''"
    :style="{ width: 'min(680px, 95vw)' }"
    data-testid="workflow-figure-form"
    @update:visible="(v) => !v && emit('close')"
  >
    <template v-if="draft">
      <p v-if="loadError" class="ff-why" data-testid="workflow-figure-load-error">{{ loadError }}</p>
      <label class="ff-row">
        <span>Figure</span>
        <select v-model="chosen" data-testid="workflow-figure-function"
                @change="pickFunction(chosen)">
          <option value="" disabled>choose a figure</option>
          <optgroup v-for="g in groups" :key="g.label" :label="g.label">
            <option v-for="f in g.items" :key="f.function" :value="f.function">
              {{ f.standard ? f.name : f.function }}
            </option>
          </optgroup>
        </select>
      </label>
      <p v-if="figure?.doc" class="ff-doc" data-testid="workflow-figure-doc">{{ figure.doc }}</p>
      <p v-if="figure && !readable.length" class="ff-hint">
        The workflow has no step a figure can read yet.
      </p>
      <table v-if="figure?.parameters.length" class="ff-inputs">
        <thead><tr><th>Input</th><th>Read from step</th><th>What it reads</th></tr></thead>
        <tbody>
          <tr v-for="p in figure.parameters" :key="p" :data-testid="`workflow-figure-input-${p}`">
            <td><code>{{ p }}</code></td>
            <td>
              <select v-model="inputs[p].step" :data-testid="`workflow-figure-step-${p}`"
                      @change="pickStep(p, inputs[p].step)">
                <option value="" disabled>choose a step</option>
                <option v-for="s in readable" :key="s.id" :value="s.id">{{ s.id }} ({{ kindOf(s) }})</option>
              </select>
            </td>
            <td>
              <select v-model="inputs[p].kind" :disabled="!inputs[p].step"
                      :title="kinds[inputs[p]?.kind]?.description ?? ''"
                      :data-testid="`workflow-figure-kind-${p}`">
                <option value="" disabled>choose</option>
                <option v-for="k in kindsFrom(inputs[p]?.step)" :key="k" :value="k"
                        :title="kinds[k].description">{{ k }}</option>
              </select>
            </td>
          </tr>
        </tbody>
      </table>
      <label v-if="figure" class="ff-row">
        <span>Description</span>
        <input v-model="description" type="text" placeholder="optional"
               data-testid="workflow-figure-description" />
      </label>
      <p v-if="why" class="ff-why" data-testid="workflow-figure-why">{{ why }}</p>
    </template>
    <template #footer>
      <Button label="Cancel" text @click="emit('close')" />
      <Button label="Make the step" icon="pi pi-check" data-testid="workflow-figure-save"
              :disabled="!complete || saving || busy" :loading="saving" @click="onSave" />
    </template>
  </Dialog>
</template>

<style scoped>
.ff-row { display: flex; gap: 0.6rem; align-items: center; margin: 0 0 0.6rem; }
.ff-row > span { min-width: 6rem; }
.ff-row select, .ff-row input { flex: 1; }
.ff-doc, .ff-hint { color: var(--p-text-muted-color, #666); font-size: 0.9rem; margin: 0 0 0.6rem; }
.ff-inputs { width: 100%; border-collapse: collapse; margin: 0 0 0.6rem; }
.ff-inputs th { text-align: left; font-weight: 600; font-size: 0.85rem; padding: 0.2rem 0.4rem; }
.ff-inputs td { padding: 0.2rem 0.4rem; }
.ff-inputs select { width: 100%; }
.ff-why { color: #c8372d; margin: 0.6rem 0 0; }
</style>
