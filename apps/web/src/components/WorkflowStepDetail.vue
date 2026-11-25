<script setup>
/**
 * One step of the workflow, inspected (a double-click in the Workflow view's chart): what
 * it is, what set its values, its model and data, what it takes from, its result, and its
 * settings as the workflow file has them. From here the step's settings open in its panel,
 * its model opens as the current study, or the step is deleted.
 */
import { computed, ref, watch } from 'vue'
import Button from 'primevue/button'
import Dialog from 'primevue/dialog'
import { modelLabel } from '../stores/useWorkflow'
import { figureInputs, referencesOf, stepPanel } from '../lib/workflowTarget'
import { workflowFigures } from '../lib/workflowFigures'

const props = defineProps({
  // The step as the workflow file has it, or null when nothing is inspected.
  step: { type: Object, default: null },
  // Every step of the file, so its references can be named.
  steps: { type: Array, default: () => [] },
  // The tab's words for it (workflowTabs), and CA's status row ({status, best_cost, ...}).
  detail: { type: Object, default: null },
  result: { type: Object, default: null },
  state: { type: String, default: 'not_run' },
  busy: { type: Boolean, default: false },
  // Renames the step: async (step, to) => '' when done, else why not.
  rename: { type: Function, default: null },
})
const emit = defineEmits(['close', 'select', 'open-view', 'delete'])

// the name being typed, and why a rename was refused
const name = ref('')
const renameError = ref('')
const renaming = ref(false)
watch(() => props.step?.id, (id) => { name.value = id ?? ''; renameError.value = '' }, { immediate: true })

async function doRename() {
  const to = name.value.trim()
  if (!props.rename || !props.step || !to || to === props.step.id) return
  renaming.value = true
  renameError.value = await props.rename(props.step.id, to)
  renaming.value = false
}

const kind = computed(() => props.step?.kind ?? 'calibration')
const ORIGIN = { calibration: 'Calibrated: an optimiser fits these values',
  set: 'Set by hand', emulation: 'A surrogate of the model',
  sweep: 'A simulation', simulate: 'A simulation', figure: 'Figures drawn from other steps\' results',
  identifiability: 'An analysis', sensitivity: 'An analysis',
  uq: 'An analysis', reduction: 'An analysis',
  combine: 'Chosen by cost: one value per parameter from the steps it combines' }
const COMBINE_WORDS = { best_fit: 'the input with the lowest cost', mean: "each parameter's mean over its inputs" }
const refs = computed(() => (props.step ? referencesOf(props.step, props.steps) : []))
const panel = computed(() => stepPanel(kind.value))
// a figure step: what its function reads, and the files it drew
const inputs = computed(() => Object.entries(figureInputs(props.step)))
const drawn = computed(() =>
  (props.step ? workflowFigures([props.step], { [props.step.id]: props.result })[0]?.files : null) ?? [])
const settingsText = computed(() => JSON.stringify(props.step?.settings ?? {}, null, 1))

function path(value) {
  if (typeof value === 'string') return value
  return value ? '(inline in the workflow file)' : '—'
}
function num(value) {
  return typeof value === 'number' ? value.toPrecision(6) : '—'
}
</script>

<template>
  <Dialog
    :visible="!!step"
    modal
    :header="step ? `Step ${step.id}` : ''"
    :style="{ width: 'min(720px, 95vw)' }"
    data-testid="workflow-step-detail"
    @update:visible="(v) => { if (!v) emit('close') }"
  >
    <template v-if="step">
      <form v-if="rename" class="sd-rename" @submit.prevent="doRename">
        <label for="sd-rename-input">Name</label>
        <input
          id="sd-rename-input"
          v-model="name"
          :disabled="busy || renaming"
          spellcheck="false"
          data-testid="workflow-step-rename-input"
        />
        <Button
          type="submit"
          label="Rename"
          size="small"
          outlined
          :disabled="busy || renaming || !name.trim() || name.trim() === step.id"
          data-testid="workflow-step-rename"
        />
      </form>
      <p v-if="renameError" class="sd-error" data-testid="workflow-step-rename-error">{{ renameError }}</p>
      <p class="sd-origin" :data-kind="kind">{{ ORIGIN[kind] ?? kind }} · {{ kind }}</p>
      <p v-if="step.description" class="sd-hint">{{ step.description }}</p>
      <dl class="sd-kv">
        <template v-if="kind !== 'figure'">
          <dt>Model</dt>
          <dd>{{ step.model ? modelLabel(step.model, true) : (detail?.detail || '—') }}</dd>
        </template>
        <template v-if="kind === 'combine'">
          <dt>Values</dt>
          <dd data-testid="workflow-step-detail-combine">
            {{ COMBINE_WORDS[step.combine?.method ?? 'best_fit'] ?? step.combine?.method }}
            of {{ (step.combine_from ?? []).join(', ') }}<template v-if="result?.chosen_from">:
              chose <b>{{ result.chosen_from }}</b></template>
          </dd>
        </template>
        <template v-else-if="kind === 'figure'">
          <dt>Function</dt>
          <dd><code>{{ step.function }}</code></dd>
          <dt>Reads</dt>
          <dd data-testid="workflow-step-detail-inputs">
            <div v-for="[name, input] in inputs" :key="name">
              <code>{{ name }}</code> ← {{ input.kind }} of {{ input.step }}
            </div>
          </dd>
        </template>
        <template v-else-if="kind !== 'set'">
          <dt>obs_data</dt>
          <dd><code>{{ path(step.obs_data) }}</code></dd>
          <dt>params_for_id</dt>
          <dd><code>{{ path(step.params_for_id ?? (step.params_for_id_from ? `from ${step.params_for_id_from}` : null)) }}</code></dd>
        </template>
        <template v-else>
          <dt>Values</dt>
          <dd>
            <div v-for="e in step.set ?? []" :key="e.parameter">
              <code>{{ e.parameter }}</code> = {{ e.value ?? `${e.scale} × ${e.of}` }}
              <span class="sd-hint">{{ e.reason }}</span>
            </div>
            <span v-if="step.point" class="sd-hint">A point only: not merged into the target.</span>
          </dd>
        </template>
        <dt v-if="kind !== 'figure'">Takes from</dt>
        <dd v-if="kind !== 'figure'">
          <template v-if="refs.length">
            <div v-for="[key, id] in refs" :key="key + id"><code>{{ key }}</code> {{ id }}</div>
          </template>
          <template v-else>the model's own values</template>
        </dd>
        <dt>Result</dt>
        <dd data-testid="workflow-step-detail-result">
          {{ state }}<template v-if="result?.stale"> (stale: an input changed since it ran)</template>
          <template v-if="result?.best_cost != null"> · cost {{ num(result.best_cost) }}</template>
          <template v-if="result?.duration_s != null"> · {{ num(result.duration_s) }} s</template>
          <template v-if="result?.finished"> · finished {{ result.finished }}</template>
        </dd>
        <template v-if="detail?.calibrates?.length">
          <dt>Calibrates</dt>
          <dd>{{ detail.calibrates.map((c) => c.target_parameterisation_name ?? c.model_name).join(', ') }}</dd>
        </template>
        <template v-if="drawn.length">
          <dt>Figures</dt>
          <dd class="sd-figures" data-testid="workflow-step-detail-figures">
            <a v-for="f in drawn" :key="f.url" :href="f.url" target="_blank" rel="noopener" :title="f.name">
              <img v-if="f.image" :src="f.url" :alt="f.name" />
              <code v-else>{{ f.name }}</code>
            </a>
          </dd>
        </template>
      </dl>
      <details class="sd-settings">
        <summary>Settings, as the workflow file has them</summary>
        <pre>{{ settingsText }}</pre>
      </details>
    </template>
    <template #footer>
      <Button
        v-if="panel"
        label="Change its settings"
        size="small"
        :disabled="busy"
        data-testid="workflow-step-detail-edit"
        @click="emit('select', step.id)"
      />
      <Button
        v-if="detail?.viewable !== false"
        label="Open its model"
        size="small"
        outlined
        :disabled="busy"
        title="Load this step's model, with the values earlier steps set, as the current study"
        data-testid="workflow-step-detail-open"
        @click="emit('open-view', step.id)"
      />
      <Button
        label="Delete"
        size="small"
        severity="danger"
        text
        :disabled="busy"
        data-testid="workflow-step-detail-delete"
        @click="emit('delete', step.id)"
      />
    </template>
  </Dialog>
</template>

<style scoped>
.sd-origin {
  margin: 0 0 0.4rem;
  font-size: 0.75rem;
  letter-spacing: 0.06em;
  text-transform: uppercase;
  font-weight: 600;
}
.sd-origin[data-kind='calibration'] { color: #2f6fde; }
.sd-origin[data-kind='set'] { color: #c8372d; }
.sd-origin[data-kind='emulation'] { color: #8a4fd6; }
.sd-figures {
  display: flex;
  flex-wrap: wrap;
  gap: 0.4rem;
}
.sd-figures img {
  max-width: 200px;
  max-height: 150px;
  border: 1px solid var(--p-content-border-color, #ccc);
}
.sd-hint {
  opacity: 0.7;
  font-size: 0.85em;
}
.sd-rename {
  display: flex;
  gap: 0.5rem;
  align-items: center;
  margin-bottom: 0.6rem;
}
.sd-rename input {
  flex: 1;
  min-width: 0;
  font: inherit;
  font-family: ui-monospace, monospace;
  padding: 0.3rem 0.5rem;
  border: 1px solid var(--p-content-border-color, #555);
  border-radius: 4px;
  background: transparent;
  color: inherit;
}
.sd-error {
  color: #e84a5f;
  font-size: 0.85em;
  margin: -0.3rem 0 0.6rem;
}
.sd-kv {
  display: grid;
  grid-template-columns: max-content 1fr;
  gap: 0.35rem 0.9rem;
  margin: 0.4rem 0;
}
.sd-kv dt {
  opacity: 0.7;
}
.sd-kv dd {
  margin: 0;
  min-width: 0;
  overflow-wrap: anywhere;
}
.sd-settings pre {
  max-height: 260px;
  overflow: auto;
  font-size: 0.78rem;
}
</style>
