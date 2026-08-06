<script setup>
import { ref, computed, watch } from 'vue'
import Dialog from 'primevue/dialog'
import Button from 'primevue/button'
import Checkbox from 'primevue/checkbox'
import Message from 'primevue/message'
import { mergedRows, buildParamsCsv, versionedFilename } from '../lib/paramsCsv'
import { uploadParamsForId, getConfig } from '../lib/api'

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
// qnames whose free-text annotation row is expanded (issue #25).
const expanded = ref(new Set())

// The prior vocabulary comes from CA (via /api/config), never a hardcoded list
// here — CA owns what a prior may be, and one it grows should appear without a
// change in this file. Empty until loaded, and left empty when the backend
// doesn't report any, which hides the column rather than offering a wrong menu.
const priorTypes = ref([])
const priorDefault = ref('')

async function loadPriorTypes() {
  try {
    const cfg = await getConfig()
    const p = cfg?.param_prior_types ?? {}
    priorTypes.value = Array.isArray(p.types) ? p.types : []
    priorDefault.value = p.default ?? ''
  } catch {
    // An older backend has no vocabulary to offer. The column stays hidden and
    // each row's prior is still round-tripped untouched, so nothing is lost.
    priorTypes.value = []
    priorDefault.value = ''
  }
}

// Rebuild the merged row set each time the dialog opens, so it reflects the
// latest loaded CSV + model params without stale edits leaking between opens.
watch(
  () => props.visible,
  (v) => {
    if (v) {
      rows.value = mergedRows(props.currentParams, props.modelVariables)
      // Auto-expand rows that already carry an annotation so it's visible.
      expanded.value = new Set(rows.value.filter((r) => r.comment).map((r) => r.qname))
      error.value = ''
      search.value = ''
      loadPriorTypes()
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

/** CA's description of a prior, for the select's tooltip. */
function priorHint(value) {
  const hit = priorTypes.value.find((p) => p.value === value)
  return hit?.description || 'Prior distribution used by MCMC / UQ'
}

/** The values the row's chosen prior takes, as CA declares them ([] if none). */
function priorFields(row) {
  return priorTypes.value.find((p) => p.value === row.prior)?.params ?? []
}

/** Placeholder for an unstated value: CA's default, or what it derives instead. */
function priorFieldPlaceholder(field) {
  return field.default == null ? 'from min/max' : String(field.default)
}

function setPriorParam(row, name, value) {
  // Kept as typed text, not coerced: CA parses and validates these, and a
  // half-typed "-" or "1e" must survive long enough to finish typing.
  row.priorParams = { ...(row.priorParams ?? {}), [name]: value }
}

/** Changing the prior drops values the new one does not take — CA rejects a
 *  hyper-parameter set on a prior that ignores it, so leaving them behind would
 *  make the file unsavable for a reason the user cannot see. */
function onPriorChange(row, value) {
  row.prior = value
  const keep = new Set(priorFields(row).map((f) => f.name))
  row.priorParams = Object.fromEntries(
    Object.entries(row.priorParams ?? {}).filter(([k]) => keep.has(k)),
  )
}

function toggleComment(qname) {
  const next = new Set(expanded.value)
  next.has(qname) ? next.delete(qname) : next.add(qname)
  expanded.value = next
}

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

    <div class="ep-head" :class="{ 'has-prior': priorTypes.length }">
      <span class="ep-inc">Use</span>
      <span class="ep-name">Parameter</span>
      <span class="ep-num">min</span>
      <span class="ep-num">max</span>
      <span class="ep-plot">Plot label</span>
      <span v-if="priorTypes.length" class="ep-prior" title="Prior distribution used by MCMC / UQ">
        Prior
      </span>
      <span class="ep-note-col">Note</span>
    </div>

    <ul class="ep-list">
      <li
        v-for="row in visibleRows"
        :key="row.qname"
        :class="{ invalid: rowInvalid(row), 'has-prior': priorTypes.length }"
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
        <select
          v-if="priorTypes.length"
          class="ep-prior"
          :value="row.prior || ''"
          :disabled="!row.included"
          data-testid="ep-prior"
          :title="priorHint(row.prior)"
          @change="onPriorChange(row, $event.target.value)"
        >
          <!-- "not stated" is its own choice, distinct from picking the default
               explicitly: it leaves the column out of the row entirely, so a CSV
               that never had a prior does not grow one just by being opened. -->
          <option value="">— ({{ priorDefault || 'default' }})</option>
          <option v-for="p in priorTypes" :key="p.value" :value="p.value">
            {{ p.label }}
          </option>
        </select>
        <button
          type="button"
          class="ep-note-btn"
          :class="{ 'has-note': !!row.comment }"
          data-testid="ep-note-toggle"
          :aria-expanded="expanded.has(row.qname)"
          :title="row.comment ? 'Edit annotation' : 'Add annotation'"
          @click="toggleComment(row.qname)"
        >
          <i class="pi pi-comment" />
        </button>
        <!-- The values the chosen prior takes. Shown whenever that prior declares
             any, rather than behind a toggle: having picked Normal, its centre and
             width are the next thing you want, and a hidden field reads as absent. -->
        <div v-if="row.included && priorFields(row).length" class="ep-prior-params">
          <span
            v-for="f in priorFields(row)"
            :key="f.name"
            class="ep-prior-param"
            :title="f.description"
          >
            <label :for="`${row.qname}-${f.name}`">{{ f.name }}</label>
            <input
              :id="`${row.qname}-${f.name}`"
              type="number"
              step="any"
              :placeholder="priorFieldPlaceholder(f)"
              :value="(row.priorParams ?? {})[f.name] ?? ''"
              :data-testid="`ep-prior-param-${f.name}`"
              @input="setPriorParam(row, f.name, $event.target.value)"
            />
          </span>
        </div>
        <div v-if="expanded.has(row.qname)" class="ep-note">
          <textarea
            class="ep-note-input"
            rows="2"
            placeholder="Annotation / note (e.g. source of this range)"
            data-testid="ep-note-input"
            :value="row.comment"
            @input="row.comment = $event.target.value"
          />
        </div>
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
  grid-template-columns: 2.5rem 1fr 6rem 6rem 7rem 2rem;
  align-items: center;
  gap: 0.5rem;
}
/* The prior column only exists when the backend reported a vocabulary, so the
   track list has to match — otherwise the note button lands under "Prior". */
.ep-head.has-prior,
.ep-list li.has-prior {
  grid-template-columns: 2.5rem 1fr 6rem 6rem 7rem 7rem 2rem;
}
select.ep-prior {
  width: 100%;
  font-size: 0.8rem;
}
/* Spans the row like the annotation field, so the grid stays the width it was
   whatever the chosen prior needs. */
.ep-prior-params {
  grid-column: 1 / -1;
  display: flex;
  flex-wrap: wrap;
  gap: 0.75rem;
  padding: 0.15rem 0.2rem 0.3rem 3rem;
}
.ep-prior-param {
  display: flex;
  align-items: center;
  gap: 0.3rem;
  font-size: 0.75rem;
  opacity: 0.85;
}
.ep-prior-param input {
  width: 7rem;
  font-size: 0.78rem;
}
select:disabled {
  opacity: 0.4;
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
.ep-note-col {
  text-align: center;
}
.ep-note-btn {
  background: none;
  border: none;
  cursor: pointer;
  color: inherit;
  opacity: 0.55;
  padding: 0.2rem;
  font-size: 0.85rem;
  justify-self: center;
}
.ep-note-btn:hover,
.ep-note-btn[aria-expanded='true'] {
  opacity: 1;
}
.ep-note-btn.has-note {
  color: #5b9bd5;
  opacity: 1;
}
.ep-note {
  grid-column: 1 / -1;
  padding: 0 0.2rem 0.2rem;
}
.ep-note-input {
  width: 100%;
  font-size: 0.8rem;
  font-family: inherit;
  resize: vertical;
  box-sizing: border-box;
}
.ep-count {
  margin-right: auto;
  font-size: 0.78rem;
  opacity: 0.6;
}
</style>
