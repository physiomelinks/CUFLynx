<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import Button from 'primevue/button'
import InputText from 'primevue/inputtext'
import FileBrowserDialog from './FileBrowserDialog.vue'
import {
  cancelRomRun,
  checkRomSetup,
  errorMessage,
  getRomAvailability,
  getRomResults,
  getRomStatus,
  startRomRun,
} from '../lib/api'

/**
 * Reduced-order models (ROMs): the Surrogate tab's second section, beside the emulator.
 *
 * A ROM is described by a *rom setup* file (libcuflynx's `rom_setup.schema.json`: the model, which states are
 * fitted, the run design, the scales) and built by libcuflynx's `cuflynx-rom setup`. This section picks the file,
 * checks it (problems by where they are, or what it names), then makes the true runs and fits the reduced models,
 * and shows each method's scores. Everything is kept in the output folder and read back on a rerun.
 *
 * ROMs are not in the public libcuflynx release yet. Where the analysis interpreter's libcuflynx has none, the
 * section shows the reason and nothing else.
 */
const STORE_KEY = 'cuflynx.rom.setup'
const POLL_MS = 1000

const availability = ref(null) // { available, reason, interpreter } | null while asking
const availabilityError = ref('')
const setupPath = ref(readStored())
const root = ref('')
const methods = ref('')
const check = ref(null) // the /api/rom/check answer
const checking = ref(false)
const error = ref('')
const job = ref(null) // { id, action }
const state = ref('idle') // idle | running | done | error | cancelled
const lines = ref([])
const rows = ref([])
const browserOpen = ref(false)
let offset = 0
let timer = null

function readStored() {
  try {
    return window.localStorage.getItem(STORE_KEY) || ''
  } catch {
    return ''
  }
}

function store(path) {
  try {
    window.localStorage.setItem(STORE_KEY, path)
  } catch {
    // a private window: the path is only a convenience
  }
}

const running = computed(() => state.value === 'running')
const canRun = computed(() => Boolean(check.value?.ok) && !running.value)
const description = computed(() => check.value?.description ?? null)
const runVariableNames = computed(() =>
  (description.value?.run_variables ?? []).flatMap((rv) => rv.names ?? []),
)

async function loadAvailability(refresh = false) {
  availabilityError.value = ''
  try {
    availability.value = await getRomAvailability(refresh)
  } catch (e) {
    availability.value = { available: false, reason: '' }
    availabilityError.value = errorMessage(e)
  }
}

async function runCheck() {
  error.value = ''
  check.value = null
  rows.value = []
  const path = setupPath.value.trim()
  if (!path) return
  checking.value = true
  try {
    check.value = await checkRomSetup(path)
    store(path)
    if (!root.value) root.value = check.value.default_root
    if (check.value.has_results) await loadResults()
  } catch (e) {
    error.value = errorMessage(e)
  } finally {
    checking.value = false
  }
}

async function loadResults() {
  try {
    rows.value = (await getRomResults(root.value || check.value.default_root)).rows
  } catch {
    rows.value = []
  }
}

async function start(action) {
  error.value = ''
  const list = methods.value
    .split(',')
    .map((m) => m.trim())
    .filter(Boolean)
  try {
    const started = await startRomRun(setupPath.value.trim(), {
      action,
      root: root.value.trim(),
      methods: list.length ? list : null,
    })
    root.value = started.root
    job.value = { id: started.job_id, action }
    state.value = 'running'
    lines.value = []
    offset = 0
    poll()
  } catch (e) {
    error.value = errorMessage(e)
  }
}

async function poll() {
  if (!job.value) return
  try {
    const s = await getRomStatus(job.value.id, offset)
    lines.value = lines.value.concat(s.lines)
    offset = s.next_offset
    state.value = s.state
    if (s.state === 'running') {
      timer = setTimeout(poll, POLL_MS)
      return
    }
    if (s.error) error.value = s.error
    if (s.rows?.length) rows.value = s.rows
  } catch (e) {
    error.value = errorMessage(e)
    state.value = 'error'
  }
}

async function cancel() {
  if (!job.value) return
  try {
    await cancelRomRun(job.value.id)
    state.value = 'cancelled'
  } catch (e) {
    error.value = errorMessage(e)
  }
}

function fmt(v, digits = 3) {
  return typeof v === 'number' && Number.isFinite(v) ? v.toPrecision(digits) : '–'
}

function pick(path) {
  setupPath.value = path
  runCheck()
}

onMounted(() => loadAvailability())
onBeforeUnmount(() => clearTimeout(timer))
</script>

<template>
  <div class="rom-section" data-testid="rom-section">
    <h3 class="section-title" data-testid="surrogate-rom-heading">Reduced-order model (ROM)</h3>

    <p v-if="availability === null" class="hint" data-testid="rom-asking">Checking for reduced-order models…</p>

    <div v-else-if="!availability.available" class="rom-unavailable" data-testid="rom-unavailable">
      <p class="hint" data-testid="rom-unavailable-reason">{{ availability.reason || availabilityError }}</p>
      <Button
        label="Check again"
        size="small"
        text
        data-testid="rom-refresh"
        @click="loadAvailability(true)"
      />
    </div>

    <template v-else>
      <p class="hint">
        A rom setup file names the model, the states to fit, how runs start and vary, and the scales. Check it,
        then make the true runs and fit the reduced models.
      </p>
      <label class="field">
        <span>Rom setup (.json)</span>
        <div class="row">
          <InputText v-model="setupPath" size="small" class="grow" data-testid="rom-setup-path" />
          <Button label="Browse" size="small" text data-testid="rom-browse" @click="browserOpen = true" />
          <Button
            label="Check"
            size="small"
            :disabled="!setupPath.trim() || checking || running"
            data-testid="rom-check"
            @click="runCheck"
          />
        </div>
      </label>

      <ul v-if="check && !check.ok" class="rom-problems" data-testid="rom-problems">
        <li v-for="(p, i) in check.problems" :key="i"><code>{{ p.where }}</code> {{ p.message }}</li>
      </ul>

      <dl v-if="description" class="rom-description" data-testid="rom-description">
        <dt>Setup</dt>
        <dd>{{ description.label || description.name }}</dd>
        <dt>Fitted states</dt>
        <dd>{{ description.fitted_states.length }}</dd>
        <dt>Run variables</dt>
        <dd>{{ runVariableNames.join(', ') || 'none' }}</dd>
        <dt>Inputs (W)</dt>
        <dd>{{ (description.W || []).join(', ') || 'none' }}</dd>
        <dt>Beats</dt>
        <dd>{{ description.beat.kind }}</dd>
        <dt>Runs</dt>
        <dd>
          train {{ description.sizes.train.join(' × ') }}, calibration {{ description.sizes.cal.join(' × ') }},
          test {{ description.sizes.test.join(' × ') }} (runs × beats)
        </dd>
      </dl>
      <p v-if="description && !description.runnable" class="hint" data-testid="rom-not-runnable">
        Only CellML setups run yet; this one can be checked but not fitted.
      </p>

      <template v-if="check && check.ok">
        <label class="field">
          <span>Output folder</span>
          <InputText v-model="root" size="small" data-testid="rom-root" />
        </label>
        <label class="field">
          <span>Methods (comma-separated; blank fits every method)</span>
          <InputText v-model="methods" size="small" placeholder="beat map, discrete T/50" data-testid="rom-methods" />
        </label>
        <div class="row">
          <Button
            label="Make runs"
            size="small"
            severity="secondary"
            :disabled="!canRun || !description?.runnable"
            data-testid="rom-runs"
            @click="start('runs')"
          />
          <Button
            label="Fit"
            icon="pi pi-play"
            size="small"
            :disabled="!canRun || !description?.runnable"
            data-testid="rom-fit"
            @click="start('fit')"
          />
          <Button
            v-if="running"
            label="Cancel"
            icon="pi pi-times"
            severity="danger"
            size="small"
            text
            data-testid="rom-cancel"
            @click="cancel"
          />
          <span class="cal-state" :data-state="state" data-testid="rom-state">{{ state }}</span>
        </div>
      </template>

      <p v-if="error" class="cal-error" data-testid="rom-error">{{ error }}</p>

      <table v-if="rows.length" class="rom-results" data-testid="rom-results">
        <thead>
          <tr>
            <th>Method</th>
            <th title="mean score over the test forecasts (lower is better)">Test</th>
            <th title="the score that chose the rank and ridge, on the calibration runs">Calibration</th>
            <th>Rank</th>
            <th>Diverged</th>
            <th>Fit (s)</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="r in rows" :key="r.method" :class="{ best: r.best }">
            <td>{{ r.method }}<span v-if="r.best" class="best-mark" title="best of its kind by calibration score"> ★</span></td>
            <td>{{ fmt(r.test_score) }}</td>
            <td>{{ fmt(r.selection_score) }}</td>
            <td>{{ r.rank ?? '–' }}</td>
            <td>{{ r.diverged ?? '–' }}<template v-if="r.forecasts"> / {{ r.forecasts }}</template></td>
            <td>{{ fmt(r.fit_s, 2) }}</td>
          </tr>
        </tbody>
      </table>

      <pre v-if="lines.length" class="terminal" data-testid="rom-terminal">{{ lines.join('\n') }}</pre>
    </template>

    <FileBrowserDialog
      v-model:visible="browserOpen"
      mode="file"
      title="Select a rom setup"
      :start-path="setupPath"
      @select="pick"
    />
  </div>
</template>

<style scoped>
.rom-section {
  display: flex;
  flex-direction: column;
  gap: 0.4rem;
  margin-top: 0.8rem;
  padding-top: 0.6rem;
  border-top: 1px solid var(--p-content-border-color, rgba(128, 128, 128, 0.3));
}
.section-title {
  margin: 0.2rem 0 0;
  font-size: 0.85rem;
  font-weight: 600;
}
.field {
  display: flex;
  flex-direction: column;
  gap: 0.15rem;
  font-size: 0.8rem;
}
.row {
  display: flex;
  align-items: center;
  gap: 0.4rem;
  flex-wrap: wrap;
}
.grow {
  flex: 1 1 12rem;
  min-width: 0;
}
.hint {
  font-size: 0.75rem;
  opacity: 0.75;
  margin: 0;
}
.cal-state {
  font-size: 0.7rem;
  text-transform: uppercase;
  opacity: 0.7;
}
.cal-error {
  color: var(--p-red-500, #c0392b);
  font-size: 0.8rem;
}
.rom-problems {
  margin: 0;
  padding-left: 1.1rem;
  font-size: 0.78rem;
  color: var(--p-red-500, #c0392b);
}
.rom-description {
  display: grid;
  grid-template-columns: max-content 1fr;
  gap: 0.1rem 0.6rem;
  margin: 0;
  font-size: 0.78rem;
}
.rom-description dt {
  opacity: 0.7;
}
.rom-description dd {
  margin: 0;
  overflow-wrap: anywhere;
}
.rom-results {
  border-collapse: collapse;
  font-size: 0.75rem;
  width: 100%;
}
.rom-results th,
.rom-results td {
  text-align: left;
  padding: 0.15rem 0.4rem;
  border-bottom: 1px solid var(--p-content-border-color, rgba(128, 128, 128, 0.25));
}
.rom-results tr.best td {
  font-weight: 600;
}
.terminal {
  max-height: 14rem;
  overflow: auto;
  font-size: 0.7rem;
  margin: 0;
  white-space: pre-wrap;
}
</style>
