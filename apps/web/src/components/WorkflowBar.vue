<script setup>
/**
 * The open calibration workflow's tabs: one per submodule step, in the order they
 * run, then the supermodule the workflow calibrates. Choosing one loads that model
 * -- with the values earlier steps have calibrated fixed in it -- as the current
 * study, so every other tab (parameters, plots, calibration) works on it.
 *
 * Sits above the plots rather than in the Workflow pane, so switching between the
 * submodules and the supermodule is one click from anywhere in the app.
 */
const props = defineProps({
  name: { type: String, default: '' },
  tabs: { type: Array, default: () => [] },
  selected: { type: String, default: null },
  // tab id -> 'done' | 'stale' | 'not_run' | 'queued' | 'running' | 'failed' | 'cancelled'
  states: { type: Object, default: () => ({}) },
  busy: { type: Boolean, default: false },
})
const emit = defineEmits(['select'])

const STATE_TITLES = {
  done: 'calibrated',
  stale: 'calibrated, but an input changed since -- rerun it',
  not_run: 'not calibrated yet',
  queued: 'waiting to run',
  running: 'calibrating now',
  failed: 'the last run of this step failed',
  cancelled: 'cancelled',
}

function title(tab) {
  const state = props.states[tab.id]
  const where = tab.kind === 'target'
    ? 'the supermodule, with every calibrated value'
    : `${tab.detail}${tab.submodulePath ? ` (submodule ${tab.submodulePath})` : ''}`
  return state ? `${where} -- ${STATE_TITLES[state] ?? state}` : where
}
</script>

<template>
  <div class="workflow-bar" data-testid="workflow-bar">
    <span class="wf-name" :title="`Calibration workflow ${name}`">
      <i class="pi pi-sitemap" /> {{ name }}
    </span>
    <button
      v-for="tab in tabs"
      :key="tab.id"
      class="wf-tab"
      :class="{ active: tab.id === selected, target: tab.kind === 'target' }"
      :data-testid="`workflow-tab-${tab.id}`"
      :disabled="busy"
      :title="title(tab)"
      @click="emit('select', tab.id)"
    >
      <span
        v-if="tab.kind === 'step'"
        class="wf-dot"
        :data-state="states[tab.id] ?? 'not_run'"
      />
      {{ tab.label }}
      <span v-if="tab.kind === 'step' && tab.submodulePath" class="wf-sub">
        → {{ tab.submodulePath }}
      </span>
    </button>
  </div>
</template>

<style scoped>
.workflow-bar {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 0.25rem;
  padding: 0.3rem 0.5rem;
  border-bottom: 1px solid var(--p-content-border-color, #444);
  font-size: 0.8rem;
}
.wf-name {
  margin-right: 0.5rem;
  opacity: 0.8;
  white-space: nowrap;
}
.wf-tab {
  display: inline-flex;
  align-items: center;
  gap: 0.3rem;
  padding: 0.15rem 0.55rem;
  border: 1px solid var(--p-content-border-color, #555);
  border-radius: 999px;
  background: transparent;
  color: inherit;
  cursor: pointer;
  font: inherit;
}
.wf-tab.active {
  border-color: var(--p-primary-color, #4f8cff);
  background: color-mix(in srgb, var(--p-primary-color, #4f8cff) 18%, transparent);
}
.wf-tab.target {
  font-weight: 600;
}
.wf-tab:disabled {
  opacity: 0.6;
  cursor: progress;
}
.wf-sub {
  opacity: 0.6;
}
.wf-dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: #888;
}
.wf-dot[data-state='done'] {
  background: #3fb950;
}
.wf-dot[data-state='stale'] {
  background: #d29922;
}
.wf-dot[data-state='running'],
.wf-dot[data-state='queued'] {
  background: #ffc000;
}
.wf-dot[data-state='failed'] {
  background: #e84a5f;
}
</style>
