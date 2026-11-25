<script setup>
import { computed } from 'vue'

/**
 * One run's progress file (CA #702, `progress.schema.json`): its state and a bar per phase,
 * done of total with the failed ones among the done in red. An emulation's `training`
 * phase also lists the models it compares, with their scores. Phase keys this does not
 * know are ignored, as the schema asks.
 */
const props = defineProps({
  progress: { type: Object, required: true },
  // CA names a profile-likelihood phase by its entry's label; a plotting name replaces it
  // when there is one.
  labels: { type: Object, default: () => ({}) },
  // Seconds without a write after which a running file is called quiet: CA writes as items
  // finish, so a long silence may be a slow item or a run that was killed elsewhere.
  quietAfterS: { type: Number, default: 120 },
})

const STATE_TEXT = {
  queued: 'queued',
  running: 'running',
  done: 'done',
  failed: 'failed',
  stopped: 'stopped',
}

function seconds(iso) {
  const t = Date.parse(iso)
  return Number.isFinite(t) ? t / 1000 : null
}

const elapsed = computed(() => {
  const a = seconds(props.progress.started)
  const b = seconds(props.progress.updated)
  return a != null && b != null ? Math.max(0, b - a) : null
})

const quiet = computed(() => {
  if (props.progress.state !== 'running') return false
  const updated = seconds(props.progress.updated)
  return updated != null && Date.now() / 1000 - updated > props.quietAfterS
})

function duration(s) {
  if (s == null) return '—'
  if (s < 60) return `${Math.round(s)} s`
  if (s < 3600) return `${Math.floor(s / 60)} min ${Math.round(s % 60)} s`
  return `${Math.floor(s / 3600)} h ${Math.round((s % 3600) / 60)} min`
}

function phaseName(phase) {
  if (phase.stage != null) {
    return `${phase.name} · stage ${phase.stage}${phase.method ? ` (${phase.method})` : ''}`
  }
  return props.labels[phase.name] ?? phase.name
}

function pct(n, total) {
  return total > 0 ? `${Math.min(100, (100 * n) / total)}%` : '0%'
}

const phases = computed(() =>
  (props.progress.phases ?? []).filter((p) => p && typeof p === 'object'),
)

function score(value) {
  if (value == null || !Number.isFinite(value)) return '—'
  return Number(value.toPrecision(4)).toString()
}
</script>

<template>
  <div class="run-phases" :data-kind="progress.kind" data-testid="run-phases">
    <p class="run-state">
      <span class="chip" :class="progress.state" data-testid="run-state">
        {{ STATE_TEXT[progress.state] ?? progress.state }}
      </span>
      <span v-if="elapsed != null" class="muted">{{ duration(elapsed) }}</span>
      <span v-if="quiet" class="muted" data-testid="run-quiet">
        · no update for {{ duration(Date.now() / 1000 - seconds(progress.updated)) }}
      </span>
    </p>
    <p v-if="!phases.length" class="muted">Starting…</p>
    <div v-for="(phase, i) in phases" :key="i" class="phase" data-testid="run-phase">
      <div class="phase-head">
        <span class="phase-name">{{ phaseName(phase) }}</span>
        <span class="muted">
          {{ phase.done }} / {{ phase.total }}
          <span v-if="phase.failed" class="failed-text" data-testid="phase-failed">
            · {{ phase.failed }} failed
          </span>
          <span v-if="phase.state !== 'running' && phase.state !== 'done'">
            · {{ STATE_TEXT[phase.state] ?? phase.state }}
          </span>
        </span>
      </div>
      <div
        class="bar"
        role="progressbar"
        :aria-label="phaseName(phase)"
        :aria-valuenow="phase.done"
        :aria-valuemax="phase.total"
        :title="`${phase.done - (phase.failed || 0)} ok, ${phase.failed || 0} failed, ${Math.max(0, phase.total - phase.done)} to go`"
      >
        <span class="ok" :style="{ width: pct(phase.done - (phase.failed || 0), phase.total) }" />
        <span class="bad" :style="{ width: pct(phase.failed || 0, phase.total) }" />
      </div>
      <table v-if="Array.isArray(phase.models) && phase.models.length" class="models"
             data-testid="training-models">
        <thead>
          <tr>
            <th>Emulator</th>
            <th>State</th>
            <th>{{ phase.metric ?? 'score' }}</th>
            <th>Time</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="model in phase.models" :key="model.name">
            <td>{{ model.name }}</td>
            <td :class="{ 'failed-text': model.state === 'failed' }">{{ model.state }}</td>
            <td>{{ score(model.score) }}</td>
            <td>{{ duration(model.duration_s) }}</td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>

<style scoped>
.run-phases {
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
}
.run-state {
  margin: 0;
  display: flex;
  gap: 0.5rem;
  align-items: center;
}
.chip {
  font-size: 0.75rem;
  padding: 0.1rem 0.5rem;
  border-radius: 999px;
  border: 1px solid currentColor;
}
.chip.failed,
.chip.stopped {
  color: #e84a5f;
}
.chip.done {
  color: #70ad47;
}
.muted {
  opacity: 0.7;
  font-size: 0.85rem;
}
.phase-head {
  display: flex;
  justify-content: space-between;
  gap: 1rem;
  font-size: 0.85rem;
}
.bar {
  display: flex;
  height: 8px;
  border-radius: 4px;
  overflow: hidden;
  background: var(--p-content-border-color, rgba(128, 128, 128, 0.25));
}
.bar .ok {
  background: #5b9bd5;
}
.bar .bad {
  background: #e84a5f;
}
.failed-text {
  color: #e84a5f;
}
.models {
  margin-top: 0.4rem;
  border-collapse: collapse;
  font-size: 0.8rem;
}
.models th,
.models td {
  text-align: left;
  padding: 0.15rem 0.75rem 0.15rem 0;
}
</style>
