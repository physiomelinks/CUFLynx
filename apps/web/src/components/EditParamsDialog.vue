<script setup>
import { ref, computed, watch } from 'vue'
import Dialog from 'primevue/dialog'
import Button from 'primevue/button'
import Checkbox from 'primevue/checkbox'
import Message from 'primevue/message'
import { mergedRows, buildParamsCsv, versionedFilename } from '../lib/paramsCsv'
import { uploadParamsForId } from '../lib/api'

const props = defineProps({
  visible: { type: Boolean, default: false },
  modelId: { type: String, default: null },
  // Loaded params_for_id entries (with param_type/initial_value); [] if none.
  currentParams: { type: Array, default: () => [] },
  // Model variables store slice: { params: [qname], initial_values: {qname: v} }.
  modelVariables: { type: Object, default: () => ({}) },
  loadedFilename: { type: String, default: null },
  modelName: { type: String, default: null },
})
const emit = defineEmits(['update:visible', 'saved'])

const rows = ref([])
const saving = ref(false)
const error = ref('')
const search = ref('')

// Rebuild the merged row set each time the dialog opens, so it reflects the
// latest loaded CSV + model params without stale edits leaking between opens.
watch(
  () => props.visible,
  (v) => {
    if (v) {
      rows.value = mergedRows(props.currentParams, props.modelVariables)
      error.value = ''
      search.value = ''
    }
  },
  { immediate: true },
)

// Rows shown in the list, filtered by the search box (qname / plot label,
// case-insensitive). Filtering is display-only: `rows` stays the source of
// truth for inclusion and saving, so hidden rows keep their edits.
const visibleRows = computed(() => {
  const q = search.value.trim().toLowerCase()
  if (!q) return rows.value
  return rows.value.filter(
    (r) =>
      r.qname.toLowerCase().includes(q) ||
      (r.name_for_plotting || '').toLowerCase().includes(q),
  )
})

function onNum(row, field, value) {
  row[field] = value === '' ? null : Number(value)
}

function rowInvalid(row) {
  return (
    row.included &&
    (!Number.isFinite(row.min) || !Number.isFinite(row.max) || row.min >= row.max)
  )
}

const includedCount = computed(() => rows.value.filter((r) => r.included).length)
const canSave = computed(
  () => includedCount.value > 0 && !rows.value.some(rowInvalid) && !saving.value,
)

function downloadCsv(text, filename) {
  // jsdom (tests) and some sandboxes lack createObjectURL — skip the download
  // there but still run the apply path below.
  if (typeof URL === 'undefined' || !URL.createObjectURL) return
  const href = URL.createObjectURL(new Blob([text], { type: 'text/csv' }))
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
  const included = rows.value.filter((r) => r.included)
  const csv = buildParamsCsv(included)
  const filename = versionedFilename(props.loadedFilename, props.modelName)
  downloadCsv(csv, filename)
  saving.value = true
  try {
    const file = new File([csv], filename, { type: 'text/csv' })
    const data = await uploadParamsForId(file, props.modelId)
    emit('saved', { ...data, filename })
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
    header="Edit params_for_id"
    :style="{ width: '46rem' }"
    data-testid="edit-params"
    @update:visible="emit('update:visible', $event)"
  >
    <p class="ep-hint">
      Tick the parameters to include and set their ranges. Saving downloads a new
      <code>…_yymmdd.csv</code> (the original is kept) and applies it.
      <i
        class="pi pi-info-circle ep-hint-info"
        data-testid="ep-ranges-hint"
        title="You should choose your parameter ranges to be physiologically realistic, otherwise the sensitivity analysis lacks meaning."
        tabindex="0"
        role="img"
        aria-label="You should choose your parameter ranges to be physiologically realistic, otherwise the sensitivity analysis lacks meaning."
      />
    </p>

    <Message v-if="error" severity="error" data-testid="ep-error" :closable="false">
      {{ error }}
    </Message>

    <input
      v-model="search"
      type="text"
      class="ep-search"
      placeholder="Search parameters…"
      data-testid="ep-search"
    />

    <div class="ep-head">
      <span class="ep-inc">Use</span>
      <span class="ep-name">Parameter</span>
      <span class="ep-num">min</span>
      <span class="ep-num">max</span>
      <span class="ep-plot">Plot label</span>
    </div>

    <ul class="ep-list">
      <li
        v-for="row in visibleRows"
        :key="row.qname"
        :class="{ invalid: rowInvalid(row) }"
        data-testid="ep-row"
      >
        <span class="ep-inc">
          <Checkbox v-model="row.included" :binary="true" />
        </span>
        <span class="ep-name" :title="row.qname">{{ row.qname }}</span>
        <input
          type="number"
          step="any"
          class="ep-num"
          :value="row.min"
          :disabled="!row.included"
          @input="onNum(row, 'min', $event.target.value)"
        />
        <input
          type="number"
          step="any"
          class="ep-num"
          :value="row.max"
          :disabled="!row.included"
          @input="onNum(row, 'max', $event.target.value)"
        />
        <input
          type="text"
          class="ep-plot"
          :value="row.name_for_plotting"
          :disabled="!row.included"
          @input="row.name_for_plotting = $event.target.value"
        />
      </li>
    </ul>

    <template #footer>
      <span class="ep-count">{{ includedCount }} included</span>
      <Button label="Cancel" size="small" text @click="emit('update:visible', false)" />
      <Button
        label="Save & download"
        size="small"
        :disabled="!canSave"
        data-testid="ep-save"
        @click="onSave"
      />
    </template>
  </Dialog>
</template>

<style scoped>
.ep-hint {
  font-size: 0.8rem;
  opacity: 0.75;
  margin: 0 0 0.5rem;
}
.ep-hint-info {
  margin-left: 0.35rem;
  cursor: help;
  color: #5b9bd5;
  opacity: 1;
}
.ep-hint-info:hover,
.ep-hint-info:focus {
  color: #7db3e0;
}
.ep-search {
  width: 100%;
  box-sizing: border-box;
  margin-bottom: 0.5rem;
  padding: 0.35rem 0.5rem;
  font-size: 0.82rem;
}
.ep-head,
.ep-list li {
  display: grid;
  grid-template-columns: 2.5rem 1fr 6rem 6rem 7rem;
  align-items: center;
  gap: 0.5rem;
}
.ep-head {
  font-size: 0.72rem;
  text-transform: uppercase;
  opacity: 0.55;
  padding: 0 0.3rem 0.3rem;
}
.ep-list {
  list-style: none;
  margin: 0;
  padding: 0;
  max-height: 55vh;
  overflow-y: auto;
  border: 1px solid var(--p-content-border-color, #333);
  border-radius: 6px;
}
.ep-list li {
  padding: 0.3rem;
  border-top: 1px solid var(--p-content-border-color, #2a2a2a);
}
.ep-list li:first-child {
  border-top: none;
}
.ep-list li.invalid {
  background: rgba(232, 74, 95, 0.12);
}
.ep-name {
  font-size: 0.82rem;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
input.ep-num,
input.ep-plot {
  width: 100%;
  font-size: 0.8rem;
}
input:disabled {
  opacity: 0.4;
}
.ep-count {
  margin-right: auto;
  font-size: 0.78rem;
  opacity: 0.6;
}
</style>
