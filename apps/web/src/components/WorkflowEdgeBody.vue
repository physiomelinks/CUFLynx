<script setup>
/**
 * What passes along one arrow of the workflow chart, as the server reads it from CA's records
 * (`/api/workflow/edge`): the values one step fixes in the next, the distribution it gives as
 * a prior, the point an analysis is at, the emulator, the model, or the merged values. Shown
 * by WorkflowEdgeDetail (an arrow that cannot be changed) and WorkflowLinkEditor (one that can).
 */
import { modelLabel } from '../stores/useWorkflow'

defineProps({
  // {source, target, key}
  edge: { type: Object, required: true },
  // The server's detail ({what, rows, prior, distribution, emulator, model, files, note}).
  detail: { type: Object, required: true },
  // Show the "what" line (a caller with its own heading leaves it out).
  heading: { type: Boolean, default: true },
})

function name(id) {
  if (id === 'model') return 'the model'
  if (id === 'target') return 'the target'
  return String(id).replace(/^figure:/, '')
}

function num(v) {
  return typeof v === 'number' ? v.toPrecision(5) : (v ?? '')
}
</script>

<template>
  <div class="ed-body">
    <p v-if="heading" class="ed-what" data-testid="workflow-edge-what">
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
  </div>
</template>

<style scoped>
.ed-hint {
  opacity: 0.7;
  font-size: 0.85em;
}
.ed-table {
  border-collapse: collapse;
  font-size: 0.85em;
  margin: 0.3rem 0;
}
.ed-table th,
.ed-table td {
  text-align: left;
  padding: 0.15rem 0.6rem 0.15rem 0;
}
.ed-files {
  font-size: 0.85em;
  margin: 0.3rem 0;
  padding-left: 1.2rem;
}
</style>
