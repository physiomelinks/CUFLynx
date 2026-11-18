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
import { modelLabel } from '../stores/useWorkflow'

const props = defineProps({
  // The clicked arrow ({source, target, key, files}), or null.
  edge: { type: Object, default: null },
  // The clicked external figure ({id, description, command, reads, writes}), or null.
  figure: { type: Object, default: null },
  // The clicked box of values by origin ({title, mark, values: [{name, value, units, note}]}).
  origin: { type: Object, default: null },
})
const emit = defineEmits(['close'])

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
        As CA's provenance records them: each value's origin and its reason or data reference
        come from the model's parameters files.
      </p>
      <table class="ed-table" data-testid="workflow-origin-values">
        <tr><th>parameter</th><th>value</th><th>units</th><th>why</th></tr>
        <tr v-for="(r, i) in origin.values" :key="i">
          <td><code>{{ r.name }}</code></td><td>{{ num(r.value) }}</td><td>{{ r.units }}</td>
          <td class="ed-hint">{{ r.note }}</td>
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
      <template v-if="detail">
        <p class="ed-what" data-testid="workflow-edge-what">
          <strong>{{ detail.what }}</strong>
          <span v-if="edge.key && !['model', 'merged', 'on_target', 'reads'].includes(edge.key)"
                class="ed-hint"> ({{ edge.key }})</span>
        </p>
        <p v-if="detail.recorded === false && (detail.rows ?? []).length" class="ed-hint">
          Not recorded by a run yet.
        </p>
        <p v-if="detail.model"><code>{{ modelLabel(detail.model, true) }}</code></p>

        <template v-if="detail.prior">
          <p>
            A <strong>{{ detail.prior.kind }}</strong> prior
            <template v-if="detail.prior.inflate && detail.prior.inflate !== 1">
              (inflated × {{ detail.prior.inflate }})</template>
            on {{ (detail.prior.parameters ?? []).join(', ') || 'the parameters both steps fit' }},
            fitted to {{ name(edge.source) }}'s stored distribution:
          </p>
          <table v-if="detail.distribution" class="ed-table" data-testid="workflow-edge-distribution">
            <tr><th>parameter</th><th>mean</th><th>sd</th><th>95 % interval</th></tr>
            <tr v-for="(d, p) in detail.distribution" :key="p">
              <td><code>{{ p }}</code></td><td>{{ num(d.mean) }}</td><td>{{ num(d.sd) }}</td>
              <td>{{ num(d.q025) }} – {{ num(d.q975) }}</td>
            </tr>
          </table>
        </template>

        <template v-if="detail.emulator">
          <p v-if="detail.emulator.dir"><code>{{ detail.emulator.dir }}</code></p>
          <p v-if="detail.emulator.worst_r2 != null">
            Held-out R²: worst {{ num(detail.emulator.worst_r2) }}<template
              v-if="detail.emulator.median_r2 != null">, median {{ num(detail.emulator.median_r2) }}</template>.
          </p>
        </template>

        <table v-if="(detail.rows ?? []).length" class="ed-table" data-testid="workflow-edge-rows">
          <tr><th>parameter</th><th>value</th><th>units</th><th /></tr>
          <tr v-for="(r, i) in detail.rows" :key="i">
            <td><code>{{ r.name }}</code></td><td>{{ num(r.value) }}</td><td>{{ r.units }}</td>
            <td class="ed-hint">{{ r.note }}</td>
          </tr>
        </table>

        <ul v-if="(detail.files ?? []).length" class="ed-files">
          <li v-for="f in detail.files" :key="f"><code>{{ f }}</code></li>
        </ul>
        <p v-if="detail.note" class="ed-hint">{{ detail.note }}</p>
      </template>
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
</style>
