<script setup>
/**
 * The Workflow view, dropped down over the centre column from the Workflow bar.
 *
 * Its header picks which of the working dir's workflows is open, starts a new one, and
 * opens a workflow file or run from elsewhere (WorkflowPanel's controls). Below, the open
 * workflow as a chart (WorkflowDiagram): click a step to change its settings in its panel
 * on the left, double-click it to inspect it (WorkflowStepDetail), drag it to move it,
 * delete it. "New step" puts a new box on the chart, to place and draw arrows to; the next
 * run, of whatever kind, makes it a step of that kind. Arrows are drawn from a box's handle
 * and changed by double-clicking them (WorkflowLinkEditor).
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
import WorkflowLinkEditor from './WorkflowLinkEditor.vue'
import WorkflowSweepForm from './WorkflowSweepForm.vue'
import WorkflowStepPrep from './WorkflowStepPrep.vue'
import WorkflowSetValuesForm from './WorkflowSetValuesForm.vue'
import WorkflowDraftChoice from './WorkflowDraftChoice.vue'
import { modelShort } from '../lib/workflowTarget'

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
  // Where the model's values come from (describe's value_origins).
  origins: { type: Object, default: null },
  // Renames a step: async (step, to) => '' when done, else why not.
  renameStep: { type: Function, default: null },
  // The chart as arranged (describe's layout), what an arrow can be (link_kinds), and
  // whether a fixed arrow can name its parameters.
  layout: { type: Object, default: null },
  linkKinds: { type: Object, default: () => ({}) },
  fixedParameters: { type: Boolean, default: false },
  // The chart's edits (useWorkflow): {link, unlink, place, placeMany, addDraft, makeSweep,
  // chooseDraft, deleteDraft}, each async; '' when done, else why not (addDraft: the id).
  editor: { type: Object, default: null },
  // What a simulation can record (states, algebraic) and sweep (constants): component/name.
  outputsOffered: { type: Array, default: () => [] },
  parametersOffered: { type: Array, default: () => [] },
  // CA's step kinds (describe's step_kinds), and the left-hand tabs that make steps.
  stepKinds: { type: Array, default: () => [] },
  draftPanels: { type: Array, default: () => [] },
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
  // a model box's choice: {draft, panel}: its model as the study, that tab open
  'draft-action',
])

// The step being inspected (a double-click in the chart), or null.
const inspected = ref(null)
const inspectedStep = computed(() => props.steps.find((s) => s.id === inspected.value) ?? null)
// The clicked arrow ({source, target, key, files}) or external figure, or null.
const clickedEdge = ref(null)
const clickedFigure = ref(null)
const clickedOrigin = ref(null)

// An arrow being drawn or changed ({source, target, key, pending}), and why an edit of the
// chart was refused.
const editedArrow = ref(null)
const editError = ref('')
// what a chart edit did that it would not show by itself (a move that pushed other boxes on)
const editNotice = ref('')
const draftIds = computed(() => (props.layout?.drafts ?? []).map((d) => d.id))
const chartSteps = computed(() => [...props.steps,
  ...(props.layout?.drafts ?? []).map((d) => ({ id: d.id, draft: true, on_target: !!d.on_target }))])
// The after-target new step whose Simulation form is open, or null.
const sweepDraft = ref(null)

function onConnect({ source, target }) {
  clickedEdge.value = null
  editedArrow.value = { source, target }
}
function onEditEdge({ source, target }) {
  clickedEdge.value = null
  editedArrow.value = { source, target }
}
async function onEdit(action, ...args) {
  editError.value = props.editor ? await props.editor[action](...args) : 'editing is not available'
}

// The step prep box (New step), the model box whose choice is open, and the Set values by
// hand box whose form is open.
const prepOpen = ref(false)
const choosing = ref(null)
const setDraft = ref(null)

/** New step dropped on the chart: a box there (a new column in a gap, moving the boxes right
 * of it on); after the target, a simulation, whose form opens. A model box or an action box
 * from the step prep box carries its model or action. */
async function onAddDraft({ col = null, row = null, shift = null, onTarget = false, model = null,
  action = null }) {
  if (!props.editor) return
  editError.value = ''
  const draft = await props.editor.addDraft(col, row, onTarget, model, action)
  if (!draft) return
  if (shift && Object.keys(shift).length) editError.value = await props.editor.placeMany(shift)
  if (onTarget) sweepDraft.value = draft
  prepOpen.value = false
}

/** A new step clicked: a model box asks what it does; an action box opens its form; a
 * plain new step is what the next run makes. */
function onSelectDraft(id) {
  const d = (props.layout?.drafts ?? []).find((x) => x.id === id)
  if (!d) return
  if (d.model) choosing.value = d
  else if (d.action === 'set') setDraft.value = id
  else if (d.action === 'combine') editError.value = 'Choosing a parameter set by cost needs a newer circulatory_autogen (its combine step).'
  else props.editor?.chooseDraft(id)
}

function onChoose({ panel, simulation }) {
  const d = choosing.value
  choosing.value = null
  if (!d) return
  if (simulation) sweepDraft.value = d.id
  else emit('draft-action', { draft: d.id, model: d.model, panel })
}

/** "New step" dragged from its button onto the chart. */
function onNewStepDragStart(event) {
  event.dataTransfer.setData('application/x-cuflynx-new-step', 'new')
  event.dataTransfer.effectAllowed = 'copy'
}

/** Rename the inspected step; the dialog stays on it under its new name. */
async function onRename(step, to) {
  const why = props.renameStep ? await props.renameStep(step, to) : 'renaming is not available'
  if (!why) inspected.value = to.trim()
  return why
}

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
            title="Click: choose a model box or an action box to drag onto the chart. Drag this button: a plain new step (after the target: a simulation)."
            draggable="true"
            class="wv-new-step"
            data-testid="workflow-new-step"
            @dragstart="onNewStepDragStart"
            @click="prepOpen = !prepOpen"
          />
          <span class="wv-hint">
            <template v-if="target.mode === 'new_step' && target.draft">
              Waiting: whatever you run next (a calibration, an analysis, a surrogate) becomes
              <strong>{{ target.draft }}</strong>, with the arrows drawn to it.
            </template>
            <template v-else-if="target.mode === 'new_step'">
              Waiting: whatever you run next (a calibration, an analysis, a surrogate) is added
              as a new step.
            </template>
            <template v-else>
              A run changes the step of its kind already here; New step adds one instead.
            </template>
          </span>
        </div>
        <WorkflowStepPrep
          v-if="prepOpen"
          :workflow="$attrs.described?.workflow ?? null"
          :step-kinds="stepKinds"
          @place="(box) => onAddDraft(box)"
          @close="prepOpen = false"
        />
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
        <Message
          v-if="editError"
          severity="error"
          closable
          data-testid="workflow-edit-error"
          @close="editError = ''"
        >
          {{ editError }}
        </Message>
        <Message
          v-if="editNotice"
          severity="info"
          closable
          data-testid="workflow-edit-notice"
          @close="editNotice = ''"
        >
          {{ editNotice }}
        </Message>
        <WorkflowDiagram
          :steps="steps"
          :details="details"
          :states="states"
          :results="results"
          :target="workflowTarget"
          :figures="figures"
          :active="clickedEdge"
          :origins="origins"
          :editing="editing"
          :doomed="doomed"
          :layout="layout"
          :busy="busy || running"
          @select="(id) => emit('select-step', id)"
          @inspect="(id) => (inspected = id)"
          @delete="(id) => emit('delete-step', id)"
          @place="({ node, col, row }) => onEdit('place', node, col, row)"
          @connect="onConnect"
          @edit-edge="onEditEdge"
          @select-draft="onSelectDraft"
          @refused="(why) => (editError = why)"
          @notice="(what) => (editNotice = what)"
          @delete-draft="(id) => onEdit('deleteDraft', id)"
          @place-many="(positions) => onEdit('placeMany', positions)"
          @add-draft="onAddDraft"
          @setup-sweep="(id) => (sweepDraft = id)"
          @open-target="emit('open-step-view', 'target')"
          @edge="(e) => (clickedEdge = e)"
          @figure="(id) => (clickedFigure = figures.find((f) => f.id === id) ?? null)"
          @origin="(id) => (clickedOrigin = (origins?.boxes ?? []).find((b) => b.id === id) ?? null)"
        />
        <WorkflowEdgeDetail
          :edge="clickedEdge"
          :figure="clickedFigure"
          :origin="clickedOrigin"
          @close="clickedEdge = null; clickedFigure = null; clickedOrigin = null"
        />
        <WorkflowDraftChoice
          :draft="choosing"
          :panels="draftPanels"
          :model-words="choosing ? modelShort(choosing.model) : ''"
          @choose="onChoose"
          @close="choosing = null"
        />
        <WorkflowSetValuesForm
          :draft="setDraft"
          :parameters-offered="parametersOffered"
          :save="(form) => editor ? editor.setValues(form) : Promise.resolve('editing is not available')"
          :busy="busy || running"
          @close="setDraft = null"
        />
        <WorkflowSweepForm
          :draft="sweepDraft"
          :outputs-offered="outputsOffered"
          :parameters-offered="parametersOffered"
          :save="(form) => editor ? editor.makeSweep(form) : Promise.resolve('editing is not available')"
          :busy="busy || running"
          @close="sweepDraft = null"
        />
        <WorkflowLinkEditor
          :arrow="editedArrow"
          :steps="chartSteps"
          :details="details"
          :link-kinds="linkKinds"
          :pending-links="layout?.links ?? []"
          :fixed-parameters="fixedParameters"
          :save="(a) => editor ? editor.link(a) : Promise.resolve('editing is not available')"
          :remove="(a) => editor ? editor.unlink(a) : Promise.resolve('editing is not available')"
          :busy="busy || running"
          @close="editedArrow = null"
        />
        <WorkflowStepDetail
          :step="inspectedStep"
          :steps="steps"
          :detail="inspectedStep ? details[inspectedStep.id] : null"
          :result="inspectedStep ? results[inspectedStep.id] : null"
          :state="inspectedStep ? (states[inspectedStep.id] ?? 'not_run') : 'not_run'"
          :busy="busy || running"
          :rename="renameStep ? onRename : null"
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
.wv-new-step {
  cursor: grab;
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
