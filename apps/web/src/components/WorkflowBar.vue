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
  // Whether the Workflow view is dropped down.
  open: { type: Boolean, default: false },
  // What the next run from the open panel is (lib/workflowTarget modeSummary):
  // {mode: 'new_workflow' | 'new_step' | 'edit', step, text}.
  summary: { type: Object, default: null },
})
const emit = defineEmits(['select', 'toggle'])

const MODES = [
  { mode: 'new_workflow', label: 'New workflow' },
  { mode: 'new_step', label: 'New step' },
  { mode: 'edit', label: 'Changing a step' },
]

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
  if (tab.viewable === false) {
    const why = tab.noViewReason || 'this step has no model to show'
    return tab.detail ? `${tab.detail}: ${why}` : why
  }
  const kind = tab.stepKind && tab.stepKind !== 'calibration' ? `${tab.stepKind}: ` : ''
  const where = tab.kind === 'target'
    ? 'the target, with every calibrated value'
    : `${kind}${tab.detail}${tab.place ? ` (at ${tab.place} in the target)` : ''}`
  return state ? `${where} -- ${STATE_TITLES[state] ?? state}` : where
}
</script>

<template>
  <div class="workflow-bar" data-testid="workflow-bar">
    <button
      class="wf-toggle"
      :class="{ open }"
      type="button"
      :title="open ? 'Hide the Workflow view' : 'Show the Workflow view: its steps, to change, reorder or delete'"
      data-testid="workflow-toggle"
      @click="emit('toggle')"
    >
      <i class="pi pi-sitemap" /> Workflow<template v-if="name">: {{ name }}</template>
      <i :class="open ? 'pi pi-chevron-up' : 'pi pi-chevron-down'" />
    </button>
    <span
      v-if="summary"
      class="wf-modes"
      :title="summary.text"
      data-testid="workflow-mode"
      :data-mode="summary.mode"
    >
      <span
        v-for="m in MODES"
        :key="m.mode"
        class="wf-mode"
        :class="{ on: summary.mode === m.mode }"
      >{{ summary.mode === 'edit' && m.mode === 'edit' ? `${m.label} ${summary.step}` : m.label }}</span>
    </span>
    <button
      v-for="tab in tabs"
      :key="tab.id"
      class="wf-tab"
      :class="{ active: tab.id === selected, target: tab.kind === 'target' }"
      :data-testid="`workflow-tab-${tab.id}`"
      :disabled="busy || tab.viewable === false"
      :title="title(tab)"
      @click="emit('select', tab.id)"
    >
      <span
        v-if="tab.kind === 'step'"
        class="wf-dot"
        :data-state="states[tab.id] ?? 'not_run'"
      />
      {{ tab.label }}
      <span v-if="tab.kind === 'step' && tab.place" class="wf-sub">
        → {{ tab.place }}
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
.wf-toggle {
  display: inline-flex;
  align-items: center;
  gap: 0.35rem;
  margin-right: 0.4rem;
  padding: 0.15rem 0.55rem;
  border: 1px solid var(--p-content-border-color, #555);
  border-radius: 4px;
  background: transparent;
  color: inherit;
  font: inherit;
  font-weight: 600;
  cursor: pointer;
  white-space: nowrap;
}
.wf-toggle.open {
  border-color: var(--p-primary-color, #4f8cff);
}
.wf-modes {
  display: inline-flex;
  border: 1px solid var(--p-content-border-color, #555);
  border-radius: 4px;
  overflow: hidden;
  margin-right: 0.5rem;
}
.wf-mode {
  padding: 0.1rem 0.45rem;
  opacity: 0.55;
  white-space: nowrap;
}
.wf-mode.on {
  opacity: 1;
  background: color-mix(in srgb, var(--p-primary-color, #4f8cff) 28%, transparent);
  font-weight: 600;
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
