<script setup>
import { ref, computed, watch } from 'vue'
import Dialog from 'primevue/dialog'
import Button from 'primevue/button'
import Message from 'primevue/message'
import FileBrowserDialog from './FileBrowserDialog.vue'
import SearchableSelect from './SearchableSelect.vue'
import { useObsExtract } from '../stores/useObsExtract'
import { getObsDataOptions, saveObsExtractConfig, loadObsExtractConfig } from '../lib/api'

/**
 * Build obs_data from a directory of raw recordings (.wcp / .abf / .csv / .npy).
 *
 * Replaces a pair of CLI scripts that ask one terminal question per recording
 * across a corpus of several hundred. The whole point of the layout is that the
 * set is visible at once and an early answer can be revised: recordings are
 * grouped by protocol|subprotocol, settings live on the group, and any single
 * row can override them.
 *
 * Mounted inside EditObsDataDialog, the way EditOperationFuncsDialog already is
 * -- CUFLynx has no router and one window, so "a new window" is a Dialog.
 */
const props = defineProps({
  visible: { type: Boolean, default: false },
  modelId: { type: String, default: null },
  // Model variables, for the binding dropdowns. Passed through from the editor,
  // which already receives them.
  modelVariables: { type: Object, default: () => ({}) },
  outputsDir: { type: String, default: '' },
})
const emit = defineEmits(['update:visible', 'extracted'])

const extract = useObsExtract()
const config = ref(null)
const root = ref('')
const browserOpen = ref(false)
const expanded = ref({})
const opts = ref({ operations: [], operation_kwargs_schema: {} })
const savedPath = ref('')
const localError = ref('')

// The five roles the extraction has to bind before it can write anything. Named
// here rather than in the template so the labels and the config keys cannot
// drift apart.
const BINDING_ROLES = [
  ['clamp_mode_param', 'Clamp mode switch', 'params'],
  ['voltage_command_param', 'Voltage command', 'params'],
  ['current_command_param', 'Current command', 'params'],
  ['measured_voltage_variable', 'Measured voltage', 'variables'],
  ['measured_current_variable', 'Measured current', 'variables'],
]

const allNames = computed(() => props.modelVariables?.all_names ?? [])
const paramNames = computed(() => props.modelVariables?.params ?? [])

const groups = computed(() => Object.entries(config.value?.subprotocols ?? {}))
const datasets = computed(() => config.value?.datasets ?? [])

function datasetsIn(key) {
  return datasets.value.filter((d) => `${d.protocol}|${d.subprotocol}` === key)
}
function scanRowFor(caseName) {
  return (extract.scan.value?.datasets ?? []).find((d) => d.case_name === caseName)
}

const usedCount = computed(() => datasets.value.filter((d) => d.used).length)
const usedGroups = computed(() => groups.value.filter(([, g]) => g.used).length)

/**
 * Features whose unit has not been confirmed, in used groups.
 *
 * The CLI emits the literal string "unknown" as the unit of every scalar it
 * extracts, because its feature configs have no unit field. That silently
 * produces an obs_data whose units are wrong everywhere, so Extract is blocked
 * until each one is answered.
 */
const unconfirmedUnits = computed(() => {
  const out = []
  for (const [key, group] of groups.value) {
    if (!group.used) continue
    for (const feature of group.features ?? []) {
      if (!feature.unit_confirmed) out.push(`${key}: ${feature.operation}`)
    }
  }
  return out
})

const canExtract = computed(
  () => usedCount.value > 0 && usedGroups.value > 0 && !unconfirmedUnits.value.length
    && !extract.running.value,
)

/** Whether an operation is measured over a sub-range of the stimulus window.
 *
 * Asked of the operation's kwargs schema, not its name. The CLI tests for an
 * `_in_range` suffix, which is false for `calc_spike_count_windowed` and the
 * whole `mean_in_range_*` family -- all of which take the fractions and
 * therefore silently receive the defaults.
 */
function takesRange(operation) {
  const schema = opts.value.operation_kwargs_schema?.[operation] ?? []
  const names = schema.map((k) => k.name ?? k)
  return names.includes('start_frac') && names.includes('end_frac')
}

function kwargSchema(operation) {
  return (opts.value.operation_kwargs_schema?.[operation] ?? []).filter(
    (k) => !['start_frac', 'end_frac', 'series_output'].includes(k.name ?? k),
  )
}

watch(
  () => props.visible,
  async (open) => {
    if (!open) return
    localError.value = ''
    try {
      opts.value = await getObsDataOptions()
    } catch {
      // The operation list degrades to whatever the backend could report; the
      // dialog is still usable for everything else.
    }
  },
  // immediate, because the dialog can be mounted already visible -- without it
  // the operation list would be empty exactly when it is first needed.
  { immediate: true },
)

async function onScan() {
  if (!root.value.trim()) return
  localError.value = ''
  const found = await extract.rescan({
    root: root.value.trim(),
    model_id: props.modelId ?? '',
    reader_opts: readerOpts(),
  })
  if (!found) return
  config.value = mergeScan(config.value, found)
}

/** Per-dataset reader settings, so a rescan keeps a .npy's supplied rate. */
function readerOpts() {
  const out = {}
  for (const d of datasets.value) {
    const reader = { ...(d.reader ?? {}) }
    delete reader.format
    if (Object.keys(reader).length) out[d.case_name] = reader
  }
  return out
}

/**
 * Fold a scan into the config without undoing any selection.
 *
 * The backend does this too, for a config loaded from disk; this is the
 * in-dialog twin so a rescan is instant and does not round-trip the whole
 * document.
 */
function mergeScan(existing, found) {
  const next = existing
    ? JSON.parse(JSON.stringify(existing))
    : newConfig(found.root)
  next.source.root = found.root
  const byCase = new Map((next.datasets ?? []).map((d) => [d.case_name, d]))
  for (const row of found.datasets ?? []) {
    const already = byCase.get(row.case_name)
    if (already) {
      already.path = row.path
      continue
    }
    next.datasets.push({
      source_id: '0', path: row.path, case_name: row.case_name,
      protocol: row.protocol, subprotocol: row.subprotocol,
      used: false, study_role: null, sweep_limit: null, sweep_indices: null,
      features_override: null, reader: { format: row.format },
      condition: null, pair_id: null, notes: '',
    })
  }
  for (const group of found.groups ?? []) {
    if (!next.subprotocols[group.group]) {
      next.subprotocols[group.group] = newGroup()
    }
  }
  if (found.suggested_binding && !existing) {
    next.model_binding = { ...next.model_binding, ...found.suggested_binding }
  }
  return next
}

function newConfig(rootPath) {
  return {
    obs_extraction_config_version: 1,
    name: 'extraction',
    source: { id: '0', root: rootPath, recurse: true, suffixes: null, exclude: [] },
    data_modifiers: [],
    preprocess: {},
    model_binding: {
      clamp_mode_param: { qname: null, voltage_value: 1.0, current_value: 0.0 },
      voltage_command_param: null, current_command_param: null,
      measured_voltage_variable: null, measured_current_variable: null,
    },
    channel_map: {},
    subprotocols: {},
    datasets: [],
    provenance: { source_text: '', species: '', location: '' },
    report: { title: null, author: null, compile_pdf: true },
    outputs: {},
  }
}

function newGroup() {
  return {
    used: false, study_role: 'calibration', input: 'current', sweep_limit: null,
    modulated_parameter: null, param_pre_value: 'auto', param_stim_value: 'auto',
    include_pre_stim_zerofrequency: false, emit_ground_truth_series: true,
    plot_time_window: { time_start: null, time_end: null },
    // null means "derive from `input`": a group switched to voltage clamp must
    // not keep the current-clamp settling sub-experiment.
    timeline: null,
    features: [],
  }
}

function bindingValue(role) {
  const binding = config.value?.model_binding ?? {}
  if (role === 'clamp_mode_param') return binding.clamp_mode_param?.qname ?? ''
  return binding[role] ?? ''
}

function setBinding(role, value) {
  if (!config.value) return
  if (role === 'clamp_mode_param') {
    config.value.model_binding.clamp_mode_param = {
      ...(config.value.model_binding.clamp_mode_param ?? {}),
      qname: value || null,
    }
    return
  }
  config.value.model_binding[role] = value || null
}

function toggleGroup(key) {
  expanded.value[key] = !expanded.value[key]
}

function setGroupUsed(key, used) {
  config.value.subprotocols[key].used = used
  // Turning a group on selects its recordings, which is what the tick means to
  // a user; turning it off deselects them so nothing is extracted from a group
  // that is no longer configured.
  for (const d of datasetsIn(key)) d.used = used
}

function addFeature(key) {
  const operation = opts.value.operations?.[0] ?? ''
  config.value.subprotocols[key].features.push({
    operation, operation_kwargs: {}, unit: '', unit_confirmed: false,
    range: { basis: 'stimulus_window', start_s: null, end_s: null },
    std: { mode: 'fraction', value: 0.1 }, weight: 1.0,
    cost_type: '', cost_kwargs: {}, plot_type: 'horizontal', name_suffix: '',
  })
}

function removeFeature(key, index) {
  config.value.subprotocols[key].features.splice(index, 1)
}

async function onSaveConfig() {
  localError.value = ''
  try {
    const { path } = await saveObsExtractConfig(config.value, {
      outputsDir: props.outputsDir,
    })
    savedPath.value = path
  } catch (e) {
    localError.value = e?.response?.data?.detail || String(e)
  }
}

async function onLoadConfig(path) {
  localError.value = ''
  try {
    const { config: loaded } = await loadObsExtractConfig(path)
    config.value = loaded
    root.value = loaded?.source?.root ?? ''
  } catch (e) {
    localError.value = e?.response?.data?.detail || String(e)
  }
}

function onExtract() {
  extract.start(config.value, {
    outputsDir: props.outputsDir,
    modelId: props.modelId ?? '',
  })
}

/**
 * Hand the finished document up when the *job* finishes, not when `start`
 * returns.
 *
 * `start` resolves as soon as the first poll has been scheduled, so reading the
 * result there gets null every time -- the run has not happened yet. Watching
 * the store also means a run that finishes while the user is looking at
 * something else is still delivered.
 *
 * A cancelled run can carry a partial result, and keeping it is the point of
 * cancelling rather than closing the dialog, so it is handed up too.
 */
watch(
  () => extract.result.value,
  (result) => {
    if (!result?.obs_data) return
    emit('extracted', {
      obsData: result.obs_data,
      configPath: result.config_path,
      texPath: result.tex_path,
      pdfPath: result.pdf_path,
      partial: extract.state.value === 'cancelled',
      warnings: result.warnings ?? [],
    })
  },
)

function onBrowsed(path) {
  browserOpen.value = false
  if (!path) return
  if (String(path).toLowerCase().endsWith('.json')) onLoadConfig(path)
  else root.value = path
}
</script>

<template>
  <Dialog
    :visible="visible"
    modal
    maximizable
    header="Add from dataset"
    :style="{ width: '80rem' }"
    data-testid="obsx-dialog"
    @update:visible="emit('update:visible', $event)"
  >
    <div class="obsx">
      <!-- Source -->
      <div class="obsx-source">
        <input
          type="text"
          class="obsx-root"
          placeholder="Folder of recordings"
          data-testid="obsx-root"
          :value="root"
          @input="root = $event.target.value"
        />
        <Button label="Browse" size="small" outlined data-testid="obsx-browse"
                @click="browserOpen = true" />
        <Button label="Scan" size="small" data-testid="obsx-scan"
                :disabled="!root.trim() || extract.scanning.value" @click="onScan" />
        <Button label="Save config" size="small" outlined data-testid="obsx-save-config"
                :disabled="!config" @click="onSaveConfig" />
      </div>

      <Message v-if="localError || extract.error.value" severity="error"
               :closable="false" data-testid="obsx-error">
        {{ localError || extract.error.value }}
      </Message>
      <Message v-if="savedPath" severity="success" :closable="false"
               data-testid="obsx-saved">
        Config saved to {{ savedPath }}
      </Message>
      <Message v-for="w in extract.scan.value?.warnings ?? []" :key="w"
               severity="warn" :closable="false" data-testid="obsx-scan-warning">
        {{ w }}
      </Message>

      <!-- Model binding -->
      <template v-if="config">
        <h3 class="obsx-section">Model binding</h3>
        <p class="obsx-hint">
          Which model variable each recorded signal corresponds to. Pre-filled
          from the loaded model; a command has to be a parameter, since that is
          all a protocol can change.
        </p>
        <div class="obsx-binding">
          <label v-for="[role, label, kind] in BINDING_ROLES" :key="role">
            <span>{{ label }}</span>
            <SearchableSelect
              :model-value="bindingValue(role)"
              :options="kind === 'params' ? paramNames : allNames"
              :data-testid="`obsx-binding-${role}`"
              @update:model-value="setBinding(role, $event)"
            />
          </label>
        </div>

        <!-- Groups -->
        <h3 class="obsx-section">
          Recordings ({{ usedCount }} of {{ datasets.length }} selected)
        </h3>
        <div v-for="[key, group] in groups" :key="key" class="obsx-group"
             data-testid="obsx-group">
          <div class="obsx-group-head">
            <input
              type="checkbox"
              :checked="group.used"
              data-testid="obsx-group-used"
              @change="setGroupUsed(key, $event.target.checked)"
            />
            <button type="button" class="obsx-group-name" @click="toggleGroup(key)">
              {{ key }}
              <span class="obsx-count">({{ datasetsIn(key).length }})</span>
            </button>
            <select
              :value="group.input"
              data-testid="obsx-group-input"
              @change="group.input = $event.target.value"
            >
              <option value="current">current clamp</option>
              <option value="voltage">voltage clamp</option>
            </select>
            <select
              :value="group.study_role"
              data-testid="obsx-group-role"
              @change="group.study_role = $event.target.value"
            >
              <option value="calibration">calibration</option>
              <option value="validation">validation</option>
            </select>
            <input
              type="number" min="1" class="obsx-sweeps" placeholder="all sweeps"
              :value="group.sweep_limit ?? ''"
              data-testid="obsx-group-sweeps"
              @input="group.sweep_limit = $event.target.value === '' ? null : Number($event.target.value)"
            />
          </div>

          <div v-show="expanded[key]" class="obsx-group-body">
            <!-- Features -->
            <div v-for="(feature, i) in group.features" :key="i" class="obsx-feature"
                 data-testid="obsx-feature">
              <SearchableSelect
                :model-value="feature.operation"
                :options="opts.operations ?? []"
                :data-testid="'obsx-feature-op'"
                @update:model-value="feature.operation = $event"
              />
              <template v-if="takesRange(feature.operation)">
                <input
                  type="number" step="any" placeholder="start (s)"
                  class="obsx-range" data-testid="obsx-range-start"
                  :value="feature.range?.start_s ?? ''"
                  @input="feature.range.start_s = $event.target.value === '' ? null : Number($event.target.value)"
                />
                <input
                  type="number" step="any" placeholder="end (s)"
                  class="obsx-range" data-testid="obsx-range-end"
                  :value="feature.range?.end_s ?? ''"
                  @input="feature.range.end_s = $event.target.value === '' ? null : Number($event.target.value)"
                />
              </template>
              <input
                v-for="kw in kwargSchema(feature.operation)" :key="kw.name ?? kw"
                type="text" class="obsx-kwarg"
                :placeholder="kw.name ?? kw"
                :data-testid="`obsx-kwarg-${kw.name ?? kw}`"
                :value="feature.operation_kwargs?.[kw.name ?? kw] ?? ''"
                @input="feature.operation_kwargs[kw.name ?? kw] = $event.target.value"
              />
              <input
                type="text" class="obsx-unit" placeholder="unit"
                data-testid="obsx-feature-unit"
                :value="feature.unit"
                @input="feature.unit = $event.target.value"
              />
              <label class="obsx-confirm" title="The extraction will not run until every unit is confirmed">
                <input
                  type="checkbox"
                  :checked="feature.unit_confirmed"
                  data-testid="obsx-feature-unit-confirm"
                  @change="feature.unit_confirmed = $event.target.checked"
                />
                unit ok
              </label>
              <Button icon="pi pi-times" text rounded size="small" severity="danger"
                      aria-label="remove feature" @click="removeFeature(key, i)" />
            </div>
            <Button label="Add feature" icon="pi pi-plus" size="small" text
                    data-testid="obsx-feature-add" @click="addFeature(key)" />

            <!-- Datasets in this group -->
            <div v-for="d in datasetsIn(key)" :key="d.case_name" class="obsx-row"
                 data-testid="obsx-dataset-row">
              <input
                type="checkbox" :checked="d.used"
                data-testid="obsx-dataset-used"
                @change="d.used = $event.target.checked"
              />
              <span class="obsx-name" :title="d.path">{{ d.case_name }}</span>
              <span class="obsx-badge">{{ d.reader?.format }}</span>
              <span class="obsx-meta">
                {{ scanRowFor(d.case_name)?.sweep_count ?? '?' }} sweeps
              </span>
              <span
                v-if="scanRowFor(d.case_name) && !scanRowFor(d.case_name).readable"
                class="obsx-bad"
                data-testid="obsx-dataset-error"
                :title="scanRowFor(d.case_name).error"
              >unreadable</span>
            </div>
          </div>
        </div>
      </template>

      <!-- Log -->
      <pre v-if="extract.lines.value.length" class="obsx-log" data-testid="obsx-log">{{ extract.lines.value.join('\n') }}</pre>
      <Message v-if="extract.result.value" severity="success" :closable="false"
               data-testid="obsx-result">
        {{ extract.result.value.n_data_items }} data item(s) from
        {{ extract.result.value.n_experiments }} experiment(s).
        Report: {{ extract.result.value.pdf_path || extract.result.value.tex_path }}
      </Message>
      <Message v-if="unconfirmedUnits.length" severity="warn" :closable="false"
               data-testid="obsx-unit-block">
        Confirm the unit for: {{ unconfirmedUnits.join(', ') }}
      </Message>
    </div>

    <template #footer>
      <span class="obsx-summary">
        {{ usedGroups }} group(s), {{ usedCount }} recording(s)
      </span>
      <Button v-if="extract.running.value" label="Cancel run" size="small" text
              data-testid="obsx-cancel-run" @click="extract.cancel()" />
      <Button label="Close" size="small" text @click="emit('update:visible', false)" />
      <Button label="Extract" size="small" data-testid="obsx-extract"
              :disabled="!canExtract" @click="onExtract" />
    </template>

    <FileBrowserDialog
      :visible="browserOpen"
      mode="dir"
      title="Folder of recordings"
      :start-dir="root"
      @update:visible="browserOpen = $event"
      @select="onBrowsed"
    />
  </Dialog>
</template>

<style scoped>
.obsx {
  display: flex;
  flex-direction: column;
  gap: 0.6rem;
  max-height: 70vh;
  overflow-y: auto;
}
.obsx-source {
  display: flex;
  gap: 0.4rem;
  align-items: center;
}
.obsx-root {
  flex: 1;
  min-width: 0;
}
.obsx-section {
  margin: 0.5rem 0 0.15rem;
  font-size: 0.95rem;
}
.obsx-hint {
  margin: 0;
  font-size: 0.8rem;
  opacity: 0.75;
}
.obsx-binding {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(16rem, 1fr));
  gap: 0.4rem;
}
.obsx-binding label {
  display: flex;
  flex-direction: column;
  gap: 0.15rem;
  font-size: 0.8rem;
}
.obsx-group {
  border: 1px solid var(--p-content-border-color, #333);
  border-radius: 6px;
}
.obsx-group-head {
  display: flex;
  gap: 0.4rem;
  align-items: center;
  padding: 0.35rem 0.5rem;
}
.obsx-group-name {
  flex: 1;
  text-align: left;
  background: none;
  border: 0;
  color: inherit;
  cursor: pointer;
  font-weight: 600;
}
.obsx-count {
  opacity: 0.7;
  font-weight: 400;
}
.obsx-sweeps {
  width: 7rem;
}
.obsx-group-body {
  padding: 0 0.5rem 0.5rem;
  display: flex;
  flex-direction: column;
  gap: 0.3rem;
}
.obsx-feature,
.obsx-row {
  display: flex;
  gap: 0.35rem;
  align-items: center;
  font-size: 0.85rem;
}
.obsx-name {
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.obsx-badge {
  font-size: 0.7rem;
  padding: 0 0.3rem;
  border-radius: 3px;
  background: var(--p-content-border-color, #333);
}
.obsx-meta {
  opacity: 0.7;
  font-size: 0.75rem;
}
.obsx-bad {
  color: var(--p-red-400, #f87171);
  font-size: 0.75rem;
}
.obsx-range,
.obsx-kwarg,
.obsx-unit {
  width: 7rem;
}
.obsx-confirm {
  display: flex;
  gap: 0.2rem;
  align-items: center;
  font-size: 0.75rem;
}
.obsx-log {
  max-height: 12rem;
  overflow: auto;
  font-size: 0.72rem;
  margin: 0;
  padding: 0.4rem;
  background: var(--p-content-background, #111);
  border-radius: 4px;
}
.obsx-summary {
  margin-right: auto;
  font-size: 0.8rem;
  opacity: 0.8;
}
</style>
