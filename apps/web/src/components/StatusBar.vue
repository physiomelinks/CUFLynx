<script setup>
import Message from 'primevue/message'
import ProgressSpinner from 'primevue/progressspinner'

defineProps({
  status: { type: String, default: 'idle' },
  message: { type: String, default: '' },
  lastRunMs: { type: Number, default: null },
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
    <Message v-else-if="status === 'error'" severity="error" :closable="false">
      {{ message }}
    </Message>
    <span v-else class="idle">Ready</span>
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
.ok {
  color: #70ad47;
}
.idle {
  opacity: 0.6;
}
</style>
