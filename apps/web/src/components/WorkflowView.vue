<script setup>
/**
 * The Workflow view, dropped down over the centre column from the Workflow bar.
 *
 * Its header picks which of the working dir's workflows is open, starts a new one, and
 * opens a workflow file or run from elsewhere (WorkflowPanel's controls). Below, the open
 * workflow as a chart (WorkflowDiagram): click a step to change its settings in its panel
 * on the left, double-click it to inspect it (WorkflowStepDetail), drag it to reorder,
 * delete it. "New step" makes the next run, of whatever kind, add a step rather than change
 * the latest one of its kind.
 *
 * Every other prop and event is WorkflowPanel's (run, export, provenance, ...), passed
 * through.
 */
import Button from 'primevue/button'
import Message from 'primevue/message'
import Select from 'primevue/select'
import { computed, ref } from 'vue'
import WorkflowDiagram from './WorkflowDiagram.vue'
import WorkflowPanel from './WorkflowPanel.vue'
import WorkflowStepDetail from './WorkflowStepDetail.vue'
import WorkflowEdgeDetail from './WorkflowEdgeDetail.vue'

defineOptions({ inheritAttrs: false })

const props = defineProps({
  // The working dir's workflows ({path, name, steps, modified}).
  workflows: { type: Array, default: () => [] },
  // The open workflow's file, or null.
  openPath: { type: String, default: null },
  // The mode the next run follows (useWorkflow.target).
  target: { type: Object, default: () => ({ mode: 'default' }) },
  // The open workflow's steps as its file has them.
  steps: { type: Array, default: () => [] },
  // Each step's words from the tabs (workflowTabs), by id.
  details: { type: Object, default: () => ({}) },
  states: { type: Object, default: () => ({}) },
  // CA's status of each step ({best_cost, duration_s, ...}), by id.
  results: { type: Object, default: () => ({}) },
  // The workflow's target model (the file's `target`).
  workflowTarget: { type: Object, default: null },
  // The workflow's external figures (`external_figures`).
  figures: { type: Array, default: () => [] },
  // The step the next run of the open panel changes (modeSummary), or null.
  editing: { type: String, default: null },
  // A delete waiting for a confirmation ({step, deleted, rewired, dropped}), or null.
  pendingDelete: { type: Object, default: null },
  busy: { type: Boolean, default: false },
  running: { type: Boolean, default: false },
})
const emit = defineEmits([
  'open-workflow',
  'new-workflow',
  'new-step',
  'select-step',
  'delete-step',
  'confirm-delete',
  'cancel-delete',
  'move-step',
  // a step's model, opened as the current study (the bar's tab does the same)
  'open-step-view',
])

// The step being inspected (a double-click in the chart), or null.
const inspected = ref(null)
const inspectedStep = computed(() => props.steps.find((s) => s.id === inspected.value) ?? null)
// The clicked arrow ({source, target, key, files}) or external figure, or null.
const clickedEdge = ref(null)
const clickedFigure = ref(null)

function fromDetail(event, id) {
  inspected.value = null
  emit(event, id)
}

const options = computed(() => {
  const listed = props.workflows.map((w) => ({
    value: w.path,
    label: `${w.name} (${w.steps.length} step${w.steps.length === 1 ? '' : 's'})`,
  }))
  if (props.openPath && !listed.some((o) => o.value === props.openPath)) {
    listed.unshift({ value: props.openPath, label: props.openPath.split(/[\\/]/).slice(-2).join('/') })
  }
  return listed
})

const doomed = computed(() => props.pendingDelete?.deleted ?? [])
const alsoDeleted = computed(() => doomed.value.filter((s) => s !== props.pendingDelete?.step))
</script>

<template>
  <div class="workflow-view" data-testid="workflow-view-pane">
    <WorkflowPanel v-bind="$attrs" :busy="busy">
      <template #header>
        <div class="wv-pick">
          <Select
            v-if="options.length"
            :model-value="openPath"
            :options="options"
            option-label="label"
            option-value="value"
            size="small"
            placeholder="Workflows in the working dir"
            :disabled="busy || running"
            data-testid="workflow-pick"
            @update:model-value="(p) => p && p !== openPath && emit('open-workflow', p)"
          />
          <Button
            label="New workflow"
            icon="pi pi-plus"
            size="small"
            :outlined="target.mode !== 'new_workflow'"
            :disabled="running"
            title="The next run from a panel starts a new workflow of this model"
            data-testid="workflow-new"
            @click="emit('new-workflow')"
          />
        </div>
      </template>
      <template #steps>
        <div class="wv-new-steps">
          <Button
            label="New step"
            icon="pi pi-plus"
            size="small"
            :outlined="target.mode !== 'new_step'"
            :disabled="running"
            title="The next run you start, of whatever kind, is added as a new step"
            data-testid="workflow-new-step"
            @click="emit('new-step')"
          />
          <span class="wv-hint">
            <template v-if="target.mode === 'new_step'">
              Waiting: whatever you run next (a calibration, an analysis, a surrogate) is added
              as a new step.
            </template>
            <template v-else>
              A run changes the step of its kind already here; New step adds one instead.
            </template>
          </span>
        </div>
        <Message
          v-if="pendingDelete"
          severity="warn"
          :closable="false"
          data-testid="workflow-confirm-delete"
        >
          Deleting <strong>{{ pendingDelete.step }}</strong> also deletes
          {{ alsoDeleted.join(', ') }}:
          {{ alsoDeleted.length === 1 ? 'it takes' : 'they take' }} from it, and nothing above
          it is of a kind {{ alsoDeleted.length === 1 ? 'it' : 'they' }} could take from
          instead. Results stay in the run directory.
          <div class="wv-confirm">
            <Button
              label="Delete them"
              size="small"
              severity="danger"
              data-testid="workflow-confirm-delete-yes"
              @click="emit('confirm-delete')"
            />
            <Button
              label="Keep them"
              size="small"
              text
              data-testid="workflow-confirm-delete-no"
              @click="emit('cancel-delete')"
            />
          </div>
        </Message>
        <WorkflowDiagram
          :steps="steps"
          :details="details"
          :states="states"
          :results="results"
          :target="workflowTarget"
          :figures="figures"
          :active="clickedEdge"
          :editing="editing"
          :doomed="doomed"
          :busy="busy || running"
          @select="(id) => emit('select-step', id)"
          @inspect="(id) => (inspected = id)"
          @delete="(id) => emit('delete-step', id)"
          @move="(m) => emit('move-step', m)"
          @open-target="emit('open-step-view', 'target')"
          @edge="(e) => (clickedEdge = e)"
          @figure="(id) => (clickedFigure = figures.find((f) => f.id === id) ?? null)"
        />
        <WorkflowEdgeDetail
          :edge="clickedEdge"
          :figure="clickedFigure"
          @close="clickedEdge = null; clickedFigure = null"
        />
        <WorkflowStepDetail
          :step="inspectedStep"
          :steps="steps"
          :detail="inspectedStep ? details[inspectedStep.id] : null"
          :result="inspectedStep ? results[inspectedStep.id] : null"
          :state="inspectedStep ? (states[inspectedStep.id] ?? 'not_run') : 'not_run'"
          :busy="busy || running"
          @close="inspected = null"
          @select="(id) => fromDetail('select-step', id)"
          @open-view="(id) => fromDetail('open-step-view', id)"
          @delete="(id) => fromDetail('delete-step', id)"
        />
      </template>
    </WorkflowPanel>
  </div>
</template>

<style scoped>
.workflow-view {
  padding: 0.5rem 0.75rem;
  overflow: auto;
  flex: 1;
  min-height: 0;
}
.wv-pick {
  display: flex;
  gap: 0.4rem;
  align-items: center;
}
.wv-new-steps {
  display: flex;
  flex-wrap: wrap;
  gap: 0.3rem;
  align-items: center;
  margin: 0.4rem 0 0.6rem;
}
.wv-hint {
  font-size: 0.78rem;
  opacity: 0.75;
}
.wv-confirm {
  display: flex;
  gap: 0.4rem;
  margin-top: 0.4rem;
}
</style>
