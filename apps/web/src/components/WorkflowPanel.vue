<script setup>
/**
 * The Workflow tab: open a calibration workflow (a calibration_workflow.json, or a
 * run directory, which holds the one it ran), run all of it or part of it, and
 * read what the selected tab was given by earlier steps.
 *
 * All of a workflow is circulatory_autogen's: what a step calibrates, how its
 * values are named in the supermodule, the run itself and the run directory. This
 * panel only shows what the server reports from CA.
 */
import { computed, ref } from 'vue'
import Button from 'primevue/button'
import InputNumber from 'primevue/inputnumber'
import InputText from 'primevue/inputtext'
import Message from 'primevue/message'
import FileBrowserDialog from './FileBrowserDialog.vue'

const props = defineProps({
  described: { type: Object, default: () => ({ loaded: false }) },
  tabs: { type: Array, default: () => [] },
  selected: { type: String, default: null },
  view: { type: Object, default: null },
  states: { type: Object, default: () => ({}) },
  jobState: { type: String, default: 'idle' },
  currentStep: { type: String, default: null },
  jobError: { type: String, default: '' },
  lines: { type: Array, default: () => [] },
  error: { type: String, default: '' },
  busy: { type: Boolean, default: false },
  offered: { type: Object, default: null },
  outputsDir: { type: String, default: '' },
  moduleLibraryDirs: { type: Array, default: () => [] },
  mpiexecAvailable: { type: Boolean, default: true },
})
const emit = defineEmits(['open', 'close', 'select', 'run', 'cancel', 'open-settings'])

const path = ref('')
const numCores = ref(1)
const browseFile = ref(false)
const browseRun = ref(false)
const showLog = ref(false)
const fileInput = ref(null)

const loaded = computed(() => !!props.described?.loaded)
const running = computed(() => props.jobState === 'running')
const workflow = computed(() => props.described?.workflow ?? {})
const steps = computed(() => props.tabs.filter((t) => t.kind === 'step'))
const selectedStep = computed(() =>
  steps.value.find((t) => t.id === props.selected) ?? null,
)
const canRun = computed(
  () => loaded.value && !props.described?.plan_error && !running.value && !props.busy,
)
const coresInvalid = computed(() => numCores.value > 1 && !props.mpiexecAvailable)

const STATE_WORDS = {
  done: 'calibrated',
  stale: 'stale -- an input changed',
  not_run: 'not run',
  queued: 'queued',
  running: 'running',
  failed: 'failed',
  cancelled: 'cancelled',
}

function openPath(p) {
  const value = (p ?? path.value).trim()
  if (value) emit('open', { path: value })
}

function onFile(event) {
  const file = event.target.files?.[0]
  if (file) emit('open', { file })
  event.target.value = ''
}

function run(which = {}) {
  emit('run', { ...which, numCores: numCores.value || 1 })
}

function fmt(value) {
  return typeof value === 'number' ? value.toPrecision(6) : String(value)
}

const progressText = computed(() => {
  if (!running.value) return ''
  const total = steps.value.length
  const index = steps.value.findIndex((s) => s.id === props.currentStep)
  return props.currentStep
    ? `Calibrating ${props.currentStep} (${index + 1} of ${total})`
    : 'Starting...'
})
</script>

<template>
  <div class="workflow-panel" data-testid="workflow-panel">
    <template v-if="!loaded">
      <p class="wf-intro">
        A <strong>calibration workflow</strong> (<code>calibration_workflow.json</code>)
        calibrates a supermodule's submodules one by one, each against its own
        obs_data and params_for_id, then the supermodule with their values fixed --
        and repeats all of it with one click. Its instances come from a module
        library.
      </p>
      <Message v-if="!moduleLibraryDirs.length" severity="info" :closable="false">
        No module library is set.
        <a href="#" data-testid="workflow-open-settings" @click.prevent="emit('open-settings')">
          Add one in Settings
        </a>
        so the workflow's instances can be found.
      </Message>
      <Message
        v-if="offered"
        severity="info"
        :closable="false"
        data-testid="workflow-offered"
      >
        The study you opened carries a calibration workflow ({{ offered.filename }}).
        <Button
          label="Open it"
          size="small"
          text
          data-testid="workflow-open-offered"
          @click="openPath(offered.path)"
        />
      </Message>
      <label class="wf-label" for="wf-path">Workflow file</label>
      <div class="wf-row">
        <InputText
          id="wf-path"
          v-model="path"
          size="small"
          class="wf-grow"
          placeholder="/path/to/instances/<name>/calibration_workflow.json"
          data-testid="workflow-path"
          @keyup.enter="openPath()"
        />
        <Button
          icon="pi pi-folder-open"
          size="small"
          text
          title="Browse for a calibration_workflow.json"
          @click="browseFile = true"
        />
        <Button
          label="Open"
          size="small"
          :disabled="!path.trim() || busy"
          data-testid="workflow-open"
          @click="openPath()"
        />
      </div>
      <div class="wf-row">
        <Button
          label="Upload a workflow file"
          icon="pi pi-upload"
          size="small"
          text
          @click="fileInput?.click()"
        />
        <input
          ref="fileInput"
          type="file"
          accept=".json,application/json"
          hidden
          data-testid="workflow-upload"
          @change="onFile"
        />
        <Button
          label="Reopen a workflow run"
          icon="pi pi-history"
          size="small"
          text
          title="A directory a workflow ran into: it holds the workflow and every step's results"
          @click="browseRun = true"
        />
      </div>
      <p class="wf-hint">
        Runs go to <code>{{ outputsDir ? `${outputsDir}/workflows/<name>` : 'a temporary folder (set an outputs directory to keep them)' }}</code>.
      </p>
    </template>

    <template v-else>
      <div class="wf-header">
        <strong data-testid="workflow-name">{{ workflow.workflow_name }}</strong>
        <Button
          icon="pi pi-times"
          size="small"
          text
          title="Close this workflow"
          :disabled="running"
          data-testid="workflow-close"
          @click="emit('close')"
        />
      </div>
      <p v-if="workflow.description" class="wf-hint">{{ workflow.description }}</p>
      <p class="wf-hint" :title="described.path">
        <code>{{ described.path }}</code><br />
        Run directory: <code>{{ described.output_dir }}</code>
      </p>
      <Message
        v-if="described.plan_error"
        severity="warn"
        :closable="false"
        data-testid="workflow-plan-error"
      >
        {{ described.plan_error }}
      </Message>

      <ol class="wf-steps">
        <li
          v-for="step in steps"
          :key="step.id"
          :class="{ active: step.id === selected }"
          :data-testid="`workflow-step-${step.id}`"
          @click="emit('select', step.id)"
        >
          <span class="wf-step-id">{{ step.id }}</span>
          <span class="wf-state" :data-state="states[step.id] ?? 'not_run'">
            {{ STATE_WORDS[states[step.id] ?? 'not_run'] }}
          </span>
          <div class="wf-step-detail">
            {{ step.detail }}<span v-if="step.submodulePath"> → submodule {{ step.submodulePath }}</span>
          </div>
          <div v-if="step.calibrates.length" class="wf-step-detail">
            calibrates {{ step.calibrates.map((c) => c.target_instance_name ?? c.model_name).join(', ') }}
          </div>
          <div v-if="step.fixedFrom.length" class="wf-step-detail">
            fixed from {{ [...new Set(step.fixedFrom.map((f) => f.from_step))].join(', ') }}
          </div>
          <div v-if="step.priorsFrom.length" class="wf-step-detail">
            priors from {{ step.priorsFrom.map((p) => `${p.step} (${p.kind})`).join(', ') }}
          </div>
        </li>
      </ol>

      <div class="wf-run">
        <label class="wf-label" for="wf-cores">Cores</label>
        <InputNumber
          v-model="numCores"
          input-id="wf-cores"
          :min="1"
          :max="256"
          size="small"
          :invalid="coresInvalid"
          :title="coresInvalid ? 'No mpiexec for the selected interpreter: run on one core' : ''"
        />
      </div>
      <div class="wf-row wf-buttons">
        <Button
          label="Run workflow"
          icon="pi pi-play"
          size="small"
          :disabled="!canRun || coresInvalid"
          data-testid="workflow-run"
          @click="run()"
        />
        <Button
          v-if="selectedStep"
          :label="`Run from ${selectedStep.id}`"
          size="small"
          outlined
          :disabled="!canRun || coresInvalid"
          data-testid="workflow-run-from"
          @click="run({ fromStep: selectedStep.id })"
        />
        <Button
          v-if="selectedStep"
          :label="`Run only ${selectedStep.id}`"
          size="small"
          outlined
          :disabled="!canRun || coresInvalid"
          data-testid="workflow-run-only"
          @click="run({ only: selectedStep.id })"
        />
        <Button
          v-if="running"
          label="Cancel"
          size="small"
          severity="danger"
          text
          data-testid="workflow-cancel"
          @click="emit('cancel')"
        />
      </div>
      <p v-if="running" class="wf-progress" data-testid="workflow-progress">
        {{ progressText }}
      </p>
      <Message
        v-if="jobError"
        severity="error"
        :closable="false"
        data-testid="workflow-job-error"
      >
        {{ jobError }}
      </Message>
      <a v-if="lines.length" href="#" class="wf-hint" @click.prevent="showLog = !showLog">
        {{ showLog ? 'Hide' : 'Show' }} the run's output ({{ lines.length }} lines)
      </a>
      <pre v-if="showLog" class="wf-log">{{ lines.slice(-200).join('\n') }}</pre>

      <div v-if="view" class="wf-view" data-testid="workflow-view">
        <h4>
          {{ view.kind === 'target' ? 'Supermodule' : `Step ${view.view}` }}
          <span class="wf-hint">
            {{ view.target.module_type }} / {{ view.target.version }} / {{ view.target.instance }}
          </span>
        </h4>
        <Message
          v-if="view.waiting_for?.length"
          severity="info"
          :closable="false"
          data-testid="workflow-waiting"
        >
          Not yet calibrated: {{ view.waiting_for.join(', ') }}. Their values are the
          instance defaults until they run.
        </Message>
        <Message v-if="view.stale?.length" severity="warn" :closable="false">
          Inputs changed since {{ view.stale.join(', ') }} ran; rerun from there.
        </Message>
        <template v-if="view.fixed?.length">
          <h5>Fixed from earlier steps</h5>
          <table class="wf-table" data-testid="workflow-fixed">
            <tr v-for="f in view.fixed" :key="f.model_name">
              <td><code>{{ f.model_name }}</code></td>
              <td>{{ fmt(f.value) }}</td>
              <td class="wf-hint">← {{ f.from_step }}</td>
            </tr>
          </table>
        </template>
        <template v-if="view.calibrated?.length">
          <h5>{{ view.kind === 'target' ? 'Calibrated values (merged)' : 'Calibrated here' }}</h5>
          <table class="wf-table" data-testid="workflow-calibrated">
            <tr v-for="c in view.calibrated" :key="c.model_name">
              <td><code>{{ c.model_name }}</code></td>
              <td>{{ fmt(c.value) }}</td>
              <td class="wf-hint">{{ view.kind === 'target' ? `← ${c.from_step}` : '' }}</td>
            </tr>
          </table>
        </template>
        <p v-if="view.kind === 'target' && !view.step_id" class="wf-hint">
          The supermodule has no calibration of its own in this workflow: it is shown
          with every step's values.
        </p>
      </div>
    </template>

    <Message v-if="error" severity="error" :closable="false" data-testid="workflow-error">
      {{ error }}
    </Message>

    <FileBrowserDialog
      v-model:visible="browseFile"
      mode="file"
      title="Select a calibration_workflow.json"
      @select="(p) => { path = p; openPath(p) }"
    />
    <FileBrowserDialog
      v-model:visible="browseRun"
      mode="dir"
      title="Select a workflow run directory"
      :start-dir="outputsDir"
      @select="(d) => emit('open', { runDir: d })"
    />
  </div>
</template>

<style scoped>
.workflow-panel {
  display: flex;
  flex-direction: column;
  gap: 0.45rem;
  padding: 0.6rem 0.75rem;
  font-size: 0.85rem;
  min-width: 0;
  /* workflow and run paths are long: break them rather than widen the pane */
  overflow-wrap: anywhere;
}
.wf-intro,
.wf-hint {
  margin: 0;
  opacity: 0.75;
  font-size: 0.78rem;
}
.wf-label {
  font-size: 0.75rem;
  opacity: 0.8;
}
.wf-row {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 0.3rem;
}
.wf-grow {
  flex: 1;
  min-width: 10rem;
}
.wf-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
}
.wf-steps {
  margin: 0;
  padding-left: 1.2rem;
  display: flex;
  flex-direction: column;
  gap: 0.3rem;
}
.wf-steps li {
  cursor: pointer;
  padding: 0.2rem 0.35rem;
  border-radius: 4px;
}
.wf-steps li.active {
  background: color-mix(in srgb, var(--p-primary-color, #4f8cff) 15%, transparent);
}
.wf-step-id {
  font-weight: 600;
  margin-right: 0.4rem;
}
.wf-step-detail {
  font-size: 0.75rem;
  opacity: 0.75;
}
.wf-state {
  font-size: 0.7rem;
  text-transform: uppercase;
  opacity: 0.8;
}
.wf-state[data-state='done'] {
  color: #3fb950;
}
.wf-state[data-state='stale'] {
  color: #d29922;
}
.wf-state[data-state='running'],
.wf-state[data-state='queued'] {
  color: #ffc000;
}
.wf-state[data-state='failed'] {
  color: #e84a5f;
}
.wf-run {
  display: flex;
  align-items: center;
  gap: 0.4rem;
}
.wf-progress {
  margin: 0;
  color: #ffc000;
}
.wf-log {
  max-height: 14rem;
  overflow: auto;
  font-size: 0.7rem;
  margin: 0;
}
.wf-view h4,
.wf-view h5 {
  margin: 0.4rem 0 0.2rem;
}
.wf-table {
  font-size: 0.78rem;
  border-collapse: collapse;
}
.wf-table td {
  padding: 0.1rem 0.5rem 0.1rem 0;
}
</style>
