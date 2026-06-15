<script setup>
import { ref, computed, watch } from 'vue'
import Dialog from 'primevue/dialog'
import Button from 'primevue/button'
import Message from 'primevue/message'
import {
  splitItems,
  predToRow,
  newRow,
  newPredRow,
  buildObsData,
  versionedJsonName,
  experimentIdxMax,
  FALLBACK_OPERATIONS,
  FALLBACK_COST_TYPES,
  FALLBACK_PLOT_TYPES,
} from '../lib/obsDataJson'
import { uploadObsData, getObsDataOptions } from '../lib/api'
import ProtocolInfoEditor from './ProtocolInfoEditor.vue'
import {
  protocolToModel,
  buildProtocolInfo,
  emptyModel,
  validateModel,
} from '../lib/protocolInfo'

const props = defineProps({
  visible: { type: Boolean, default: false },
  modelId: { type: String, default: null },
  currentDataItems: { type: Array, default: () => [] },
  currentPredictionItems: { type: Array, default: () => [] },
  // Loaded obs_data protocol_info (null for data-only); passed through verbatim.
  protocolInfo: { type: Object, default: null },
  experimentCount: { type: Number, default: 0 },
  // Model variables store slice (for operand / prediction variable choices).
  modelVariables: { type: Object, default: () => ({}) },
  modelName: { type: String, default: null },
  loadedFilename: { type: String, default: null },
})
const emit = defineEmits(['update:visible', 'saved'])

const editableRows = ref([])
const preservedItems = ref([])
const predRows = ref([])
const operations = ref(FALLBACK_OPERATIONS)
const costTypes = ref(FALLBACK_COST_TYPES)
const plotTypes = ref(FALLBACK_PLOT_TYPES)
const protocolModel = ref(null)
const activeExp = ref(0)
const saving = ref(false)
const error = ref('')

// Rebuild on open: fetch the CA-sourced option lists FIRST, then split the
// loaded items against the live operations so user-op constants stay editable.
watch(
  () => props.visible,
  async (v) => {
    if (!v) return
    error.value = ''
    try {
      const opts = await getObsDataOptions()
      if (opts?.operations?.length) operations.value = opts.operations
      if (opts?.cost_types?.length) costTypes.value = opts.cost_types
      if (opts?.plot_types?.length) plotTypes.value = opts.plot_types
    } catch {
      /* keep fallbacks — editor still works offline */
    }
    const { editable, preserved } = splitItems(props.currentDataItems, operations.value)
    editableRows.value = editable
    preservedItems.value = preserved
    predRows.value = (props.currentPredictionItems ?? []).map(predToRow)
    protocolModel.value = props.protocolInfo ? protocolToModel(props.protocolInfo) : null
    activeExp.value = 0
  },
  { immediate: true },
)

const allNames = computed(() => props.modelVariables?.all_names ?? [])
const operandOptions = computed(() => ['time', ...allNames.value])
const hasProtocol = computed(() => protocolModel.value != null)
// Experiment/subexperiment counts come from the working protocol model once one
// exists, so data_item idx selects track edits to the protocol.
const experimentCountModel = computed(() =>
  protocolModel.value ? protocolModel.value.experiments.length : props.experimentCount,
)
const expMax = computed(() => experimentIdxMax(experimentCountModel.value))
const expOptions = computed(() => Array.from({ length: expMax.value + 1 }, (_, i) => i))
function subexpOptions(expIdx) {
  const n = protocolModel.value?.experiments?.[expIdx]?.subexps?.length ?? 1
  return Array.from({ length: Math.max(1, n) }, (_, i) => i)
}

// Clamp data_item / prediction idxs into range whenever experiments or
// subexperiments are added/removed, so the backend never sees an out-of-range idx.
watch(
  () =>
    protocolModel.value
      ? protocolModel.value.experiments.map((e) => e.subexps.length).join(',')
      : '',
  () => {
    const m = protocolModel.value
    if (!m) return
    const nExp = m.experiments.length
    const clampExp = (r) => {
      if ((r.experiment_idx ?? 0) > nExp - 1) r.experiment_idx = Math.max(0, nExp - 1)
    }
    for (const r of editableRows.value) {
      clampExp(r)
      const nSub = m.experiments[r.experiment_idx]?.subexps.length ?? 1
      if ((r.subexperiment_idx ?? 0) > nSub - 1) r.subexperiment_idx = Math.max(0, nSub - 1)
    }
    predRows.value.forEach(clampExp)
  },
)

const modelErrors = computed(() =>
  protocolModel.value ? validateModel(protocolModel.value) : [],
)

function onNum(row, field, value) {
  row[field] = value === '' ? null : Number(value)
}

function rowInvalid(row) {
  return (
    !Number.isFinite(Number(row.value)) ||
    !Number.isFinite(Number(row.std)) ||
    Number(row.std) <= 0 ||
    (row.operands ?? []).filter((o) => o).length < 1
  )
}

const canSave = computed(
  () => !saving.value && !editableRows.value.some(rowInvalid) && modelErrors.value.length === 0,
)

function addProtocol() {
  protocolModel.value = emptyModel()
  activeExp.value = 0
}

function addRow() {
  editableRows.value.push(newRow(0))
}
function removeRow(i) {
  editableRows.value.splice(i, 1)
}
function onOperandChange(row, i, val) {
  row.operands[i] = val
  if (!row.variable && row.operands[0]) row.variable = row.operands[0]
}
function addOperand(row) {
  row.operands.push('')
}
function removeOperand(row, i) {
  row.operands.splice(i, 1)
}
function addPred() {
  predRows.value.push(newPredRow(0))
}
function removePred(i) {
  predRows.value.splice(i, 1)
}

function downloadJson(text, filename) {
  if (typeof URL === 'undefined' || !URL.createObjectURL) return
  const href = URL.createObjectURL(new Blob([text], { type: 'application/json' }))
  const a = document.createElement('a')
  a.href = href
  a.download = filename
  document.body.appendChild(a)
  a.click()
  a.remove()
  URL.revokeObjectURL(href)
}

async function onSave() {
  error.value = ''
  const protocolInfo = protocolModel.value
    ? buildProtocolInfo(protocolModel.value, props.protocolInfo)
    : null
  const obsData = buildObsData({
    protocolInfo,
    editableRows: editableRows.value,
    preservedItems: preservedItems.value,
    predictionRows: predRows.value,
  })
  const filename = versionedJsonName(props.loadedFilename, props.modelName)
  downloadJson(JSON.stringify(obsData, null, 2), filename)
  saving.value = true
  try {
    const summary = await uploadObsData(props.modelId, obsData)
    emit('saved', { ...summary, filename })
    emit('update:visible', false)
  } catch (e) {
    error.value = e?.response?.data?.detail || String(e)
  } finally {
    saving.value = false
  }
}
</script>

<template>
  <Dialog
    :visible="visible"
    modal
    header="Edit obs_data"
    :style="{ width: '60rem' }"
    data-testid="edit-obs"
    @update:visible="emit('update:visible', $event)"
  >
    <p class="eo-hint">
      Edit the observation targets. Saving downloads a new
      <code>…_yymmdd.json</code> (the original is kept) and applies it.
      <template v-if="!hasProtocol"> (data-only obs_data — no protocol/predictions)</template>
    </p>

    <Message v-if="error" severity="error" data-testid="eo-error" :closable="false">
      {{ error }}
    </Message>

    <h3 class="eo-section">protocol_info</h3>
    <template v-if="!protocolModel">
      <Button
        label="Add protocol_info"
        icon="pi pi-plus"
        size="small"
        data-testid="add-protocol"
        @click="addProtocol"
      />
      <p class="eo-hint">
        This is a data-only obs_data with no protocol. Add one to define
        experiments, controlled inputs (params_to_change), and prediction_items.
      </p>
    </template>
    <ProtocolInfoEditor
      v-else
      v-model:active-exp="activeExp"
      :model="protocolModel"
      :all-names="allNames"
    />
    <Message
      v-if="modelErrors.length"
      severity="warn"
      :closable="false"
      data-testid="eo-model-error"
    >
      {{ modelErrors.join('; ') }}
    </Message>

    <h3 class="eo-section">data_items</h3>
    <div class="eo-head">
      <span>Variable</span>
      <span>value</span>
      <span>std</span>
      <span>operation</span>
      <span>exp</span>
      <span>sub</span>
      <span />
    </div>
    <ul class="eo-list">
      <li
        v-for="(row, i) in editableRows"
        :key="i"
        :class="{ invalid: rowInvalid(row) }"
        data-testid="eo-row"
      >
        <div class="eo-main">
          <input
            type="text"
            class="eo-var"
            :value="row.variable"
            @input="row.variable = $event.target.value"
          />
          <input
            type="number"
            step="any"
            :value="row.value"
            @input="onNum(row, 'value', $event.target.value)"
          />
          <input
            type="number"
            step="any"
            :value="row.std"
            @input="onNum(row, 'std', $event.target.value)"
          />
          <select :value="row.operation" @change="row.operation = $event.target.value">
            <option v-for="op in operations" :key="op" :value="op">{{ op || '(none)' }}</option>
          </select>
          <select
            :value="row.experiment_idx"
            @change="row.experiment_idx = Number($event.target.value)"
          >
            <option v-for="e in expOptions" :key="e" :value="e">{{ e }}</option>
          </select>
          <select
            :value="row.subexperiment_idx"
            data-testid="eo-subexp"
            @change="row.subexperiment_idx = Number($event.target.value)"
          >
            <option v-for="su in subexpOptions(row.experiment_idx)" :key="su" :value="su">{{ su }}</option>
          </select>
          <span class="eo-rowbtns">
            <Button
              :icon="row._expanded ? 'pi pi-chevron-up' : 'pi pi-chevron-down'"
              text
              rounded
              size="small"
              aria-label="details"
              @click="row._expanded = !row._expanded"
            />
            <Button
              icon="pi pi-times"
              text
              rounded
              size="small"
              aria-label="remove"
              data-testid="eo-remove-row"
              @click="removeRow(i)"
            />
          </span>
        </div>

        <div v-if="row._expanded" class="eo-detail">
          <label>operands
            <span class="eo-operands">
              <span v-for="(op, oi) in row.operands" :key="oi" class="eo-operand">
                <select :value="op" @change="onOperandChange(row, oi, $event.target.value)">
                  <option value="">—</option>
                  <option v-for="name in operandOptions" :key="name" :value="name">{{ name }}</option>
                </select>
                <Button icon="pi pi-minus" text size="small" @click="removeOperand(row, oi)" />
              </span>
              <Button icon="pi pi-plus" label="operand" text size="small" @click="addOperand(row)" />
            </span>
          </label>
          <label>plot label
            <input type="text" :value="row.name_for_plotting" @input="row.name_for_plotting = $event.target.value" />
          </label>
          <label>unit
            <input type="text" :value="row.unit" @input="row.unit = $event.target.value" />
          </label>
          <label>weight
            <input type="number" step="any" :value="row.weight" @input="onNum(row, 'weight', $event.target.value)" />
          </label>
          <label>cost_type
            <select :value="row.cost_type" @change="row.cost_type = $event.target.value">
              <option value="">(default)</option>
              <option v-for="ct in costTypes" :key="ct" :value="ct">{{ ct }}</option>
            </select>
          </label>
          <label>plot_type
            <select :value="row.plot_type" @change="row.plot_type = $event.target.value">
              <option v-for="pt in plotTypes" :key="pt" :value="pt">{{ pt || '(none)' }}</option>
            </select>
          </label>
        </div>
      </li>
      <li v-if="!editableRows.length" class="eo-empty">No editable data_items. Add one below.</li>
    </ul>
    <Button label="Add data item" icon="pi pi-plus" size="small" text data-testid="obs-add-row" @click="addRow" />

    <template v-if="hasProtocol">
      <h3 class="eo-section">prediction_items</h3>
      <ul class="eo-list">
        <li v-for="(row, i) in predRows" :key="i" data-testid="eo-pred-row">
          <div class="eo-pred">
            <select :value="row.variable" @change="row.variable = $event.target.value">
              <option value="">—</option>
              <option v-for="name in allNames" :key="name" :value="name">{{ name }}</option>
            </select>
            <input type="text" placeholder="unit" :value="row.unit" @input="row.unit = $event.target.value" />
            <input type="text" placeholder="plot label" :value="row.name_for_plotting" @input="row.name_for_plotting = $event.target.value" />
            <select :value="row.experiment_idx" @change="row.experiment_idx = Number($event.target.value)">
              <option v-for="e in expOptions" :key="e" :value="e">{{ e }}</option>
            </select>
            <Button icon="pi pi-times" text rounded size="small" aria-label="remove" @click="removePred(i)" />
          </div>
        </li>
      </ul>
      <Button label="Add prediction" icon="pi pi-plus" size="small" text data-testid="obs-add-pred" @click="addPred" />
    </template>

    <p v-if="preservedItems.length" class="eo-preserved" data-testid="obs-preserved">
      {{ preservedItems.length }} non-editable item(s) (series / frequency / custom
      operation) will be preserved unchanged.
    </p>

    <template #footer>
      <span class="eo-count">{{ editableRows.length }} data item(s)</span>
      <Button label="Cancel" size="small" text @click="emit('update:visible', false)" />
      <Button
        label="Save & download"
        size="small"
        :disabled="!canSave"
        data-testid="eo-save"
        @click="onSave"
      />
    </template>
  </Dialog>
</template>

<style scoped>
.eo-hint {
  font-size: 0.8rem;
  opacity: 0.75;
  margin: 0 0 0.5rem;
}
.eo-section {
  margin: 0.75rem 0 0.35rem;
  font-size: 0.9rem;
}
.eo-head,
.eo-main {
  display: grid;
  grid-template-columns: 1.4fr 0.8fr 0.8fr 1.2fr 0.5fr 0.5fr 4rem;
  align-items: center;
  gap: 0.4rem;
}
.eo-head {
  font-size: 0.7rem;
  text-transform: uppercase;
  opacity: 0.55;
  padding: 0 0.3rem 0.25rem;
}
.eo-list {
  list-style: none;
  margin: 0;
  padding: 0;
  max-height: 48vh;
  overflow-y: auto;
  border: 1px solid var(--p-content-border-color, #333);
  border-radius: 6px;
}
.eo-list li {
  padding: 0.3rem;
  border-top: 1px solid var(--p-content-border-color, #2a2a2a);
}
.eo-list li:first-child {
  border-top: none;
}
.eo-list li.invalid {
  background: rgba(232, 74, 95, 0.12);
}
.eo-rowbtns {
  display: flex;
  justify-content: flex-end;
}
.eo-detail {
  display: grid;
  grid-template-columns: 1fr 1fr 1fr;
  gap: 0.4rem;
  margin: 0.4rem 0 0.2rem;
  padding-left: 0.3rem;
}
.eo-detail label {
  display: flex;
  flex-direction: column;
  font-size: 0.7rem;
  opacity: 0.8;
  gap: 0.15rem;
}
.eo-operands {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 0.25rem;
}
.eo-operand {
  display: flex;
  align-items: center;
}
.eo-pred {
  display: grid;
  grid-template-columns: 1.4fr 0.9fr 1.1fr 0.6fr 2rem;
  align-items: center;
  gap: 0.4rem;
}
.eo-empty {
  opacity: 0.5;
  font-size: 0.8rem;
}
.eo-preserved {
  font-size: 0.75rem;
  opacity: 0.7;
  margin: 0.5rem 0 0;
}
.eo-list input,
.eo-list select,
.eo-detail input,
.eo-detail select {
  width: 100%;
  font-size: 0.78rem;
}
.eo-count {
  margin-right: auto;
  font-size: 0.78rem;
  opacity: 0.6;
}
</style>
