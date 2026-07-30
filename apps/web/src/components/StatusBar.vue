<script setup>
import Message from 'primevue/message'
import ProgressSpinner from 'primevue/progressspinner'

defineProps({
  status: { type: String, default: 'idle' },
  message: { type: String, default: '' },
  lastRunMs: { type: Number, default: null },
  // A standing caveat about the run rather than a failure — e.g. live plots
  // falling back to a backend this process can actually run (#122). Shown
  // alongside the status, not instead of it: the run did succeed.
  notice: { type: String, default: '' },
})
</script>

<template>
  <footer class="status-bar">
    <ProgressSpinner
      v-if="status === 'running'"
      style="width: 1rem; height: 1rem"
      stroke-width="6"
    />
    <span v-if="status === 'running'">Simulating…</span>
    <span v-else-if="status === 'ok'" class="ok">
      Done<span v-if="lastRunMs != null"> in {{ Math.round(lastRunMs) }} ms</span>
    </span>
    <!--
      A simulation failure is several lines now (issue #138): the solver's
      reason, the settings it failed under, then what to change. `pre-line`
      keeps those breaks instead of running them into one wall of text.
    -->
    <Message
      v-else-if="status === 'error'"
      severity="error"
      :closable="false"
      class="status-error"
      data-testid="status-error"
    >
      {{ message }}
    </Message>
    <span v-else class="idle">Ready</span>
    <Message
      v-if="notice"
      severity="warn"
      :closable="false"
      class="status-notice"
      data-testid="status-notice"
    >
      {{ notice }}
    </Message>
  </footer>
</template>

<style scoped>
.status-bar {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  padding: 0.4rem 0.75rem;
  border-top: 1px solid var(--p-content-border-color, #333);
  font-size: 0.85rem;
}
/* A multi-line failure must stay readable: keep its breaks, let it wrap rather
   than stretch the bar, and cap the height so a long solver dump can't push the
   plots off screen. */
.status-notice {
  flex: 1;
  min-width: 0;
  font-size: 0.78rem;
}
.status-error {
  flex: 1;
  min-width: 0;
  white-space: pre-line;
  overflow-wrap: anywhere;
  max-height: 8rem;
  overflow-y: auto;
}
.ok {
  color: #70ad47;
}
.idle {
  opacity: 0.6;
}
</style>
