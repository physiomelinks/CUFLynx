<script setup>
/**
 * What passes along an arrow of the Workflow view's chart, or what an external figure does,
 * when one is clicked: the values one step fixes in the next, the distribution it gives as a
 * prior, the point an analysis is at, the emulator, the model, the values merged into the
 * target -- read from CA's records by the server (`/api/workflow/edge`) -- or the files a
 * figure reads and writes.
 */
import { ref, watch } from 'vue'
import Dialog from 'primevue/dialog'
import { errorMessage, getWorkflowEdge } from '../lib/api'
import WorkflowEdgeBody from './WorkflowEdgeBody.vue'

const props = defineProps({
  // The clicked arrow ({source, target, key, files}), or null.
  edge: { type: Object, default: null },
  // The clicked external figure ({id, description, command, reads, writes}), or null.
  figure: { type: Object, default: null },
  // The clicked box of values by origin ({title, mark, values: [{name, value, units, note,
  // calibrated_by}]}).
  origin: { type: Object, default: null },
})
// step: a step id named in "calibrated in", to open that step
const emit = defineEmits(['close', 'step'])

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
      </p>
      <table class="ed-table" data-testid="workflow-origin-values">
        <tr><th>parameter</th><th>value</th><th>units</th><th>why</th><th>calibrated in</th></tr>
        <tr v-for="(r, i) in origin.values" :key="i">
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
        </tr>
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
</style>
