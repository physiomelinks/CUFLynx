<script setup>
/**
 * The Workflow bar above the plots: the workflow's name (click to drop the Workflow view
 * down) and what the next run does -- "[Editing] sglt1", or that it adds a step, or starts a
 * workflow. Kept to that, so where the next run goes can be read at a glance; the steps
 * themselves are in the Workflow view's chart.
 */
const props = defineProps({
  name: { type: String, default: '' },
  // Whether the Workflow view is dropped down.
  open: { type: Boolean, default: false },
  // What the next run from the open panel is (lib/workflowTarget modeSummary):
  // {mode: 'new_workflow' | 'new_step' | 'edit', step, text}.
  summary: { type: Object, default: null },
})
const emit = defineEmits(['toggle'])

// the chip says what the next run does; the step it changes follows it: "[Editing] sglt1"
function chip(summary) {
  if (!summary) return ''
  if (summary.mode === 'edit') return 'Editing'
  return summary.mode === 'new_step' ? 'New step' : 'New workflow'
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
      v-if="props.summary"
      class="wf-mode"
      :title="props.summary.text"
      data-testid="workflow-mode"
      :data-mode="props.summary.mode"
    >{{ chip(props.summary) }}</span>
    <strong
      v-if="props.summary?.step && props.summary.mode !== 'new_workflow'"
      class="wf-step"
      data-testid="workflow-mode-step"
    >{{ props.summary.step }}</strong>
  </div>
</template>

<style scoped>
.workflow-bar {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 0.5rem;
  padding: 0.3rem 0.5rem;
  border-bottom: 1px solid var(--p-content-border-color, #444);
  font-size: 0.8rem;
}
.wf-toggle {
  display: inline-flex;
  align-items: center;
  gap: 0.35rem;
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
.wf-mode {
  padding: 0.1rem 0.5rem;
  border-radius: 999px;
  font-weight: 600;
  white-space: nowrap;
  background: color-mix(in srgb, var(--p-primary-color, #4f8cff) 28%, transparent);
}
.wf-model {
  opacity: 0.8;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  min-width: 0;
}
</style>
