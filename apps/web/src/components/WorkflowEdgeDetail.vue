<script setup>
/**
 * What passes along an arrow of the Workflow view's chart, or what an external figure does,
 * when one is clicked: the values one step fixes in the next, the distribution it gives as a
 * prior, the point an analysis is at, the emulator, the model, the values merged into the
 * target -- read from CA's records by the server (`/api/workflow/edge`) -- or the files a
 * figure reads and writes.
 */
import { computed, ref, watch } from 'vue'
import Dialog from 'primevue/dialog'
import { errorMessage, getWorkflowEdge } from '../lib/api'
import WorkflowEdgeBody from './WorkflowEdgeBody.vue'

const props = defineProps({
  // The clicked arrow ({source, target, key, files}), or null.
  edge: { type: Object, default: null },
  // The clicked external figure ({id, description, command, reads, writes}), or null.
  figure: { type: Object, default: null },
  // The clicked box of values by origin ({id, title, mark, values: [{name, value, units, note,
  // calibrated_by, origin, data_reference, reason}]}).
  origin: { type: Object, default: null },
  // Every box of values by origin, to say where an edited value has gone.
  boxes: { type: Array, default: () => [] },
  // What a value's origin can be (libcuflynx's); empty when origins cannot be recorded.
  originKinds: { type: Array, default: () => [] },
  // async ({name, origin, dataReference, reason}) => '' when recorded, else why not
  saveOrigin: { type: Function, default: null },
})
// step: a step id named in "calibrated in", to open that step; open-origin: a box id
const emit = defineEmits(['close', 'step', 'open-origin'])

// the words for an origin in the form (the boxes' own titles)
const ORIGIN_LABELS = {
  '': 'not recorded', set_by_hand: 'set by hand', derived: 'from data',
  measured: 'from literature', calibrated: 'calibrated before', inherited: 'inherited',
}
const originLabel = (o) => ORIGIN_LABELS[o ?? ''] ?? o

// the value whose origin is being edited, its form, and the last one recorded
const editing = ref(null)
const form = ref({ origin: '', dataReference: '', reason: '' })
const saveError = ref('')
const saving = ref(false)
const saved = ref(null)

function startEdit(r) {
  editing.value = r.name
  form.value = { origin: r.origin ?? '', dataReference: r.data_reference ?? '', reason: r.reason ?? '' }
  saveError.value = ''
  saved.value = null
}

async function save() {
  saving.value = true
  saveError.value = (await props.saveOrigin?.({ name: editing.value, ...form.value })) ?? ''
  saving.value = false
  if (!saveError.value) {
    saved.value = editing.value
    editing.value = null
  }
}

// where the value last recorded is now: null while it is in this box
const movedTo = computed(() => {
  if (!saved.value || props.origin?.values?.some((v) => v.name === saved.value)) return null
  return props.boxes.find((b) => b.values?.some((v) => v.name === saved.value)) ?? null
})

// a new box (or none) forgets the form
watch(() => props.origin?.id, (id, before) => {
  if (id === before) return
  editing.value = null
  saveError.value = ''
  if (!id) saved.value = null
})

const detail = ref(null)
const error = ref('')
const loading = ref(false)

function name(id) {
  if (id === 'model') return 'the model'
  if (id === 'target') return 'the target'
  return String(id).replace(/^figure:/, '')
}

watch(
  () => props.edge,
  async (edge) => {
    detail.value = null
    error.value = ''
    if (!edge) return
    if (edge.key === 'reads') {
      detail.value = { what: 'files the figures read', rows: [], files: edge.files ?? [] }
      return
    }
    loading.value = true
    try {
      detail.value = await getWorkflowEdge(edge.source, edge.target, edge.key)
    } catch (e) {
      error.value = errorMessage(e)
    } finally {
      loading.value = false
    }
  },
  { immediate: true },
)

function num(v) {
  return typeof v === 'number' ? v.toPrecision(5) : (v ?? '')
}
</script>

<template>
  <Dialog
    :visible="!!(edge || figure || origin)"
    modal
    :header="origin ? origin.title : figure ? `Figures: ${figure.id}` : edge ? `${name(edge.source)} → ${name(edge.target)}` : ''"
    :style="{ width: 'min(720px, 95vw)' }"
    data-testid="workflow-edge-detail"
    @update:visible="(v) => { if (!v) emit('close') }"
  >
    <template v-if="origin">
      <p class="ed-hint">
        Each value's origin and its reason or data reference come from the model's parameters
        files; a step named under "calibrated in" fits it.
        <template v-if="originKinds.length">Edit records them there, and the value moves to
          its origin's box.</template>
      </p>
      <p v-if="movedTo" class="ed-moved" data-testid="workflow-origin-moved">
        <code>{{ saved }}</code> is now in
        <a href="#" @click.prevent="emit('open-origin', movedTo.id)">{{ movedTo.title }}</a>.
      </p>
      <p v-else-if="saved" class="ed-moved" data-testid="workflow-origin-moved">
        Recorded for <code>{{ saved }}</code>.
      </p>
      <p v-if="!origin.values.length" class="ed-hint">No values are left in this box.</p>
      <table v-else class="ed-table" data-testid="workflow-origin-values">
        <tr>
          <th>parameter</th><th>value</th><th>units</th><th>why</th><th>calibrated in</th>
          <th v-if="originKinds.length" />
        </tr>
        <template v-for="r in origin.values" :key="r.name">
          <tr>
            <td><code>{{ r.name }}</code></td><td>{{ num(r.value) }}</td><td>{{ r.units }}</td>
            <td class="ed-hint">{{ r.note }}</td>
            <td>
              <a
                v-for="s in r.calibrated_by ?? []"
                :key="s"
                href="#"
                class="ed-step"
                :data-testid="`workflow-origin-step-${s}`"
                @click.prevent="emit('step', s)"
              >{{ s }}</a>
            </td>
            <td v-if="originKinds.length">
              <a
                v-if="editing !== r.name"
                href="#"
                :data-testid="`workflow-origin-edit-${r.name}`"
                @click.prevent="startEdit(r)"
              >Edit</a>
            </td>
          </tr>
          <tr v-if="editing === r.name" class="ed-form-row">
            <td colspan="6">
              <form class="ed-form" data-testid="workflow-origin-form" @submit.prevent="save">
                <label>Origin
                  <select v-model="form.origin" data-testid="workflow-origin-kind">
                    <option value="">{{ originLabel('') }}</option>
                    <option v-for="k in originKinds" :key="k" :value="k">{{ originLabel(k) }}</option>
                  </select>
                </label>
                <label>Data reference
                  <input v-model="form.dataReference" data-testid="workflow-origin-reference"
                         placeholder="a citation, a data file, a derivation" />
                </label>
                <label>Reason
                  <input v-model="form.reason" data-testid="workflow-origin-reason"
                         placeholder="why this value (for one set by hand)" />
                </label>
                <span class="ed-form-buttons">
                  <button type="submit" :disabled="saving" data-testid="workflow-origin-save">Save</button>
                  <button type="button" @click="editing = null">Cancel</button>
                </span>
              </form>
              <p v-if="saveError" class="ed-error" data-testid="workflow-origin-error">{{ saveError }}</p>
            </td>
          </tr>
        </template>
      </table>
    </template>

    <template v-else-if="figure">
      <p v-if="figure.description">{{ figure.description }}</p>
      <p class="ed-label">Command</p>
      <pre class="ed-code">{{ figure.command }}</pre>
      <p class="ed-label">Reads</p>
      <ul class="ed-files"><li v-for="f in figure.reads ?? []" :key="f"><code>{{ f }}</code></li></ul>
      <p class="ed-label">Writes</p>
      <ul class="ed-files"><li v-for="f in figure.writes ?? []" :key="f"><code>{{ f }}</code></li></ul>
    </template>

    <template v-else-if="edge">
      <p v-if="loading" class="ed-hint">Reading the run…</p>
      <p v-if="error" class="ed-error">{{ error }}</p>
      <WorkflowEdgeBody v-if="detail" :edge="edge" :detail="detail" />
    </template>
  </Dialog>
</template>

<style scoped>
.ed-what {
  margin: 0 0 0.4rem;
}
.ed-hint {
  opacity: 0.7;
  font-size: 0.88em;
}
.ed-error {
  color: #e84a5f;
}
.ed-label {
  margin: 0.6rem 0 0.2rem;
  font-size: 0.72rem;
  letter-spacing: 0.06em;
  text-transform: uppercase;
  opacity: 0.7;
}
.ed-code {
  white-space: pre-wrap;
  font-size: 0.8rem;
  margin: 0;
}
.ed-files {
  margin: 0;
  padding-left: 1.2rem;
  font-size: 0.82rem;
  max-height: 220px;
  overflow: auto;
}
.ed-table {
  border-collapse: collapse;
  font-size: 0.84rem;
  margin: 0.4rem 0;
  width: 100%;
}
.ed-table th,
.ed-table td {
  text-align: left;
  padding: 2px 10px 2px 0;
  border-bottom: 1px solid var(--p-content-border-color, #444);
  font-variant-numeric: tabular-nums;
}
.ed-table th {
  opacity: 0.7;
  font-weight: 600;
}
.ed-step + .ed-step { margin-left: 0.5em; }
.ed-moved {
  margin: 0.3rem 0;
  font-size: 0.88em;
}
.ed-form {
  display: flex;
  flex-wrap: wrap;
  gap: 0.4rem 0.8rem;
  align-items: flex-end;
  padding: 0.3rem 0;
}
.ed-form label {
  display: flex;
  flex-direction: column;
  font-size: 0.78rem;
  gap: 2px;
}
.ed-form input {
  min-width: 14rem;
}
.ed-form-buttons button + button { margin-left: 0.4rem; }
</style>
