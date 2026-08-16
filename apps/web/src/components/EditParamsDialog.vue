<script setup>
import { ref, computed, watch } from 'vue'
import Dialog from 'primevue/dialog'
import Button from 'primevue/button'
import Checkbox from 'primevue/checkbox'
import Message from 'primevue/message'
import {
  mergedRows,
  addToGroup,
  removeFromGroup,
  rowsToSave,
  canCreateModifier,
  createModifier,
  removeModifier,
} from '../lib/paramsCsv'
import { rowsToDoc, versionedJsonName } from '../lib/paramsJson'
import { evalPriorDefault, formatPriorDefault } from '../lib/priorDefaults'
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
  // The CasADi backend refuses grouped and modifier rows at run time
  // (casadi_backend refuses them by design), so the buttons that create them
  // are disabled rather than letting the run fail later.
  generatedModelFormat: { type: String, default: '' },
  // Where Save writes the dated copy (#215); '' falls back to the config dir.
  outputsDir: { type: String, default: '' },
})
const emit = defineEmits(['update:visible', 'saved'])

const rows = ref([])
const saving = ref(false)
const error = ref('')
const search = ref('')
// qnames whose free-text annotation row is expanded (issue #25).
const expanded = ref(new Set())
// qnames whose prior-settings panel is open. Its own disclosure rather than
// extra columns: the values differ per prior, so as columns they would be blank
// for most rows and make the grid ragged.
const priorOpen = ref(new Set())

// The prior vocabulary comes from CA (via /api/config), never a hardcoded list
// here — CA owns what a prior may be, and one it grows should appear without a
// change in this file. Empty until loaded, and left empty when the backend
// doesn't report any, which hides the column rather than offering a wrong menu.
const priorTypes = ref([])
const priorDefault = ref('')
// The modifier operation vocabulary (CA's PARAM_MODIFIER_OPERATIONS via
// /api/config) — never hardcoded. Empty hides the create-modifier button.
const modifierOps = ref([])

async function loadPriorTypes() {
  try {
    const cfg = await getConfig(props.outputsDir)
    const p = cfg?.param_prior_types ?? {}
    priorTypes.value = Array.isArray(p.types) ? p.types : []
    priorDefault.value = p.default ?? ''
    const m = cfg?.param_modifier_operations ?? {}
    modifierOps.value = Array.isArray(m.operations) ? m.operations : []
  } catch {
    // An older backend has no vocabulary to offer. The column stays hidden and
    // each row's prior is still round-tripped untouched, so nothing is lost.
    priorTypes.value = []
    priorDefault.value = ''
    modifierOps.value = []
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
      // Same for prior settings: a row that states one is showing it, a row on
      // the defaults stays shut and says so in the collapsed summary.
      priorOpen.value = new Set(
        rows.value
          .filter((r) => Object.values(r.priorParams ?? {}).some((v) => v !== '' && v != null))
          .map((r) => r.qname),
      )
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
  // A row absorbed into a group, or claimed by a modifier, is no longer a
  // parameter of its own; it shows in its owner's member list instead of as a
  // row that could be ticked separately.
  const listed = rows.value.filter((r) => !r.groupedInto && !r.modifiedBy)
  const q = search.value.trim().toLowerCase()
  if (!q) return listed
  return listed.filter(
    (r) =>
      r.qname.toLowerCase().includes(q) ||
      (r.name || '').toLowerCase().includes(q) ||
      (r.name_for_plotting || '').toLowerCase().includes(q) ||
      // A group is findable by any of its members, not just the one it is named
      // after -- that name is an accident of which vessel came first in the CSV.
      r.qnames.some((n) => n.toLowerCase().includes(q)),
  )
})

// ---------------------------------------------------------------------------
// Multi-select + modifier parameters (#208)
// ---------------------------------------------------------------------------
const selectedRows = computed(() => rows.value.filter((r) => r.selected))
const casadiGated = computed(() => props.generatedModelFormat === 'casadi_python')
const scaleOpMeta = computed(
  () => modifierOps.value.find((o) => o.value === 'scale') ?? null,
)
// Group (override): several free rows become one multi-target entry -- one
// value written to all of them. Two selected single free rows minimum.
const canGroupSelection = computed(
  () =>
    !casadiGated.value &&
    selectedRows.value.length >= 2 &&
    selectedRows.value.every(
      (r) =>
        r.kind !== 'modifier' &&
        r.groupedInto == null &&
        r.modifiedBy == null &&
        (r.qnames?.length ?? 1) === 1,
    ),
)
const canModifySelection = computed(
  () => !casadiGated.value && !!scaleOpMeta.value && canCreateModifier(selectedRows.value),
)

/** The just-created row goes to the top: it is what the user is working on,
 *  and at the bottom of a hundreds-long list it looks like nothing happened. */
function moveToTop(row) {
  const idx = rows.value.indexOf(row)
  if (idx > 0) {
    rows.value.splice(idx, 1)
    rows.value.unshift(row)
  }
}

function onGroupSelected() {
  if (!canGroupSelection.value) return
  const [head, ...members] = selectedRows.value
  for (const m of members) addToGroup(head, m)
  head.included = true
  for (const r of selectedRows.value) r.selected = false
  moveToTop(head)
}

function onCreateModifier() {
  if (!canModifySelection.value) return
  createModifier(rows.value, selectedRows.value, scaleOpMeta.value ?? {})
}

function onRemoveModifier(row) {
  removeModifier(rows.value, row)
}

/** The θ·baseline preview for one of a modifier's targets. */
function baselineLabel(row, qname) {
  const b = row.baselines?.[qname]
  return b == null ? `${qname} (no model default)` : `${qname} (baseline ${b})`
}

// ---------------------------------------------------------------------------
// Grouped parameters (issue #193 / #208): created via the toolbar's
// multi-select Group (override); the old per-row same-name panel is gone.
// ---------------------------------------------------------------------------
/** Dissolve a group: every absorbed member becomes its own row again. */
function onUngroup(row) {
  for (const r of rows.value) {
    if (r.groupedInto === row.qname) removeFromGroup(row, r)
  }
}

/** CA's display label for a prior value, for the panel heading. */
function priorLabel(value) {
  return priorTypes.value.find((p) => p.value === value)?.label ?? value
}

/** CA's description of a prior, for the select's tooltip. */
function priorHint(value) {
  const hit = priorTypes.value.find((p) => p.value === value)
  return hit?.description || 'Prior distribution used by MCMC / UQ'
}

/** Whether the row's prior can stand in for its range (CA decides, not us). */
function supportsUnbounded(row) {
  return !!priorTypes.value.find((p) => p.value === row.prior)?.supports_unbounded
}

/** Ticking it hands the range to the prior; unticking gives the row its bounds back. */
function onUnboundedChange(row, checked) {
  row.unbounded = checked
  if (checked) {
    // The centre and width must be stated: their usual defaults are computed
    // *from* the range, so with no range there is nothing to derive them from.
    const next = new Set(priorOpen.value)
    next.add(row.qname)
    priorOpen.value = next
  }
}

/** The values the row's chosen prior takes, as CA declares them ([] if none). */
function priorFields(row) {
  return priorTypes.value.find((p) => p.value === row.prior)?.params ?? []
}

/** Placeholder for an unstated value: the number CA will actually use.
 *
 *  Computed from CA's own `default_expr` against this row's bounds, so the field
 *  shows the value rather than a description of it — "1.5", not "from min/max".
 *
 *  Unbounded reverses the relationship: the range is derived *from* the centre
 *  and width, so there is nothing left to derive them from and they are required.
 *
 *  When the number cannot be computed — a bound cleared or half-typed, a sibling
 *  hyper-parameter left at something the formula cannot divide by — the fallback
 *  is CA's own `default_expr`, shown as "= (min + max) / 2". It used to be the
 *  fixed phrase "from min/max", which said neither what the default is nor which
 *  field to fill in to get it, and for the exponential's prior_scale (whose
 *  formula is `max / prior_lambda`) named a bound that plays no part in it (#198).
 *  Echoing CA's string keeps it true for a formula CA changes, and for one it
 *  invents, which is the whole reason the expression is published rather than
 *  restated here. */
function priorFieldPlaceholder(field, row) {
  if (row?.unbounded && (field.role === 'location' || field.role === 'scale')) {
    return 'required'
  }
  const derived = evalPriorDefault(field.default_expr, {
    min: row?.min,
    max: row?.max,
    ...Object.fromEntries(
      priorFields(row).map((f) => [
        f.name,
        (row?.priorParams ?? {})[f.name] ?? f.default,
      ]),
    ),
  })
  const shown = formatPriorDefault(derived)
  if (shown != null) return shown
  if (field.default != null) return String(field.default)
  if (field.default_expr) return `= ${field.default_expr}`
  // No number, no formula: CA states no default, so the field has to be filled in.
  return 'required'
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
  // Open the panel on a prior that has settings, close it on one that hasn't:
  // having just chosen Normal, its centre and width are the next thing you want,
  // and a panel left open on Uniform would be empty.
  const next = new Set(priorOpen.value)
  keep.size ? next.add(row.qname) : next.delete(row.qname)
  priorOpen.value = next
}

function togglePriorPanel(qname) {
  const next = new Set(priorOpen.value)
  next.has(qname) ? next.delete(qname) : next.add(qname)
  priorOpen.value = next
}

/** A short summary for the collapsed panel, so a set value is visible without opening it. */
function priorSummary(row) {
  if (row.unbounded) return 'unbounded'
  const set = priorFields(row)
    .map((f) => [f.name, (row.priorParams ?? {})[f.name]])
    .filter(([, v]) => v != null && v !== '')
  return set.length ? set.map(([k, v]) => `${k} ${v}`).join(', ') : 'defaults'
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
  if (!row.included) return false
  if (row.unbounded) {
    // CA derives the range from the prior's centre and width, so both must be
    // given -- their usual defaults come from the range that is no longer there.
    return priorFields(row)
      .filter((f) => f.role === 'location' || f.role === 'scale')
      .some((f) => {
        const v = (row.priorParams ?? {})[f.name]
        return v == null || v === '' || !Number.isFinite(Number(v))
      })
  }
  return !Number.isFinite(row.min) || !Number.isFinite(row.max) || row.min >= row.max
}

// Parameters, not rows: a grouped member is part of one of these, not another one.
const savedRows = computed(() => rowsToSave(rows.value))
const includedCount = computed(() => savedRows.value.length)
const canSave = computed(
  () => includedCount.value > 0 && !savedRows.value.some(rowInvalid) && !saving.value,
)

async function onSave() {
  error.value = ''
  // Saved as the JSON form from here on: the CSV cannot express an override of
  // differently-named parameters, nor a modifier at all. CSV stays load-only.
  const text = JSON.stringify(rowsToDoc(savedRows.value), null, 1)
  const filename = versionedJsonName(props.loadedFilename, props.modelName)
  saving.value = true
  try {
    const file = new File([text], filename, { type: 'application/json' })
    // The server writes the dated copy where the study lives (#215): the
    // outputs directory, or its own config dir when none is set. No browser
    // download — a file the user cannot find is not saved.
    const data = await uploadParamsForId(file, props.modelId, {
      filename,
      outputsDir: props.outputsDir,
    })
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
    :style="{ width: '72rem', maxWidth: '95vw' }"
    data-testid="edit-params"
    @update:visible="emit('update:visible', $event)"
  >
    <p class="ep-hint">
      Tick the parameters to include and set their ranges. Saving writes a new
      <code>…_yymmdd.json</code> to the output directory (the original is kept) and applies it.
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

    <!-- Multi-select actions (#208). Select rows with the ○ column, then either
         group them (one value written to all — the override) or create a scale
         modifier (one θ slider multiplying each target's model default). -->
    <div class="ep-toolbar">
      <Button
        label="Group (override)"
        size="small"
        outlined
        data-testid="ep-group-selected"
        :disabled="!canGroupSelection"
        :title="
          casadiGated
            ? 'The CasADi backend does not support grouped or modifier parameters'
            : 'One value written to every selected parameter (one slider, one calibrated value)'
        "
        @click="onGroupSelected"
      />
      <Button
        v-if="modifierOps.length"
        label="Create scale modifier"
        size="small"
        outlined
        data-testid="ep-create-modifier"
        :disabled="!canModifySelection"
        :title="
          casadiGated
            ? 'The CasADi backend does not support grouped or modifier parameters'
            : 'A new dimensionless θ parameter; each selected target follows θ × its model default'
        "
        @click="onCreateModifier"
      />
      <Button
        label="Calculate…"
        size="small"
        outlined
        data-testid="ep-create-calculate"
        :disabled="true"
        title="Compute a parameter from a user python function — needs a newer circulatory_autogen (pending upstream support)"
      />
      <span v-if="selectedRows.length" class="ep-selcount">
        {{ selectedRows.length }} selected
      </span>
    </div>

    <div class="ep-head" :class="{ 'has-prior': priorTypes.length }">
      <span class="ep-sel" title="Select for Group / Create modifier">○</span>
      <!-- The guided tour rings this column head and spans down through every
           row's ep-include, so the highlight is the whole Use column. -->
      <span class="ep-inc" data-testid="ep-use-header">Use</span>
      <span class="ep-name">Parameter</span>
      <span class="ep-num">min</span>
      <span class="ep-num">max</span>
      <span class="ep-plot">Plot label</span>
      <!-- Like ep-use-header: the guided tour rings this head and spans down
           through the rows' ep-prior selects. Both are `v-if`'d on the backend
           offering priors at all, so the tour step skips itself when it does not. -->
      <span
        v-if="priorTypes.length"
        class="ep-prior"
        data-testid="ep-prior-header"
        title="Prior distribution used by MCMC / UQ"
      >
        Prior
      </span>
      <!-- Delete/ungroup actions live in this slot (the per-row grouping panel
           was replaced by the toolbar's Group override). -->
      <span class="ep-note-col" />
      <span class="ep-note-col">Note</span>
    </div>

    <ul class="ep-list">
      <!-- The per-row testids below (ep-include / ep-min / ep-max) repeat on
           every row: they mark the first row for the guided tour, which anchors
           on the first match. -->
      <li
        v-for="row in visibleRows"
        :key="row.kind === 'modifier' ? `mod:${row.name}` : row.qname"
        :class="{
          invalid: rowInvalid(row),
          'has-prior': priorTypes.length,
          'is-modifier': row.kind === 'modifier',
        }"
        data-testid="ep-row"
      >
        <span class="ep-sel">
          <input
            v-if="row.kind !== 'modifier'"
            v-model="row.selected"
            type="checkbox"
            data-testid="ep-select"
          />
        </span>
        <span class="ep-inc">
          <Checkbox v-model="row.included" :binary="true" data-testid="ep-include" />
        </span>
        <span v-if="row.kind === 'modifier'" class="ep-name">
          <input
            type="text"
            class="ep-mod-name"
            :value="row.name"
            data-testid="ep-modifier-name"
            title="The modifier's name (must be unique in the file)"
            @input="row.name = $event.target.value"
          />
          <span class="ep-mod-badge" data-testid="ep-modifier-badge"
            >{{ row.operation }} &times;{{ row.qnames.length }}</span
          >
        </span>
        <span v-else class="ep-name" :title="row.qnames.join('\n')">
          {{ row.qname
          }}<span v-if="row.qnames.length > 1" class="ep-group-badge" data-testid="ep-group-badge"
            >&times;{{ row.qnames.length }}</span
          >
        </span>
        <input
          type="number"
          step="any"
          class="ep-num"
          :value="row.min"
          :disabled="!row.included || row.unbounded"
          :title="row.unbounded ? 'Derived from the prior' : ''"
          data-testid="ep-min"
          @input="onNum(row, 'min', $event.target.value)"
        />
        <input
          type="number"
          step="any"
          class="ep-num"
          :value="row.max"
          :disabled="!row.included || row.unbounded"
          :title="row.unbounded ? 'Derived from the prior' : ''"
          data-testid="ep-max"
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
          v-if="row.kind === 'modifier'"
          type="button"
          class="ep-note-btn"
          data-testid="ep-remove-modifier"
          title="Delete this modifier and restore its targets as their own rows"
          @click="onRemoveModifier(row)"
        >
          <i class="pi pi-trash" />
        </button>
        <!-- Groups are created via the toolbar's multi-select Group (override);
             this dissolves one back into its member rows. -->
        <button
          v-else-if="row.qnames.length > 1"
          type="button"
          class="ep-note-btn has-note"
          data-testid="ep-ungroup"
          title="Ungroup: each component becomes its own row again"
          @click="onUngroup(row)"
        >
          <i class="pi pi-times-circle" />
        </button>
        <span v-else class="ep-note-btn-spacer" />
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
        <!-- The values the chosen prior takes, in their own disclosure rather than
             as extra columns: which values exist differs per prior, so as columns
             they would be blank for most rows and leave the grid ragged. -->
        <div
          v-if="row.included && (priorFields(row).length || supportsUnbounded(row))"
          class="ep-prior-block"
        >
          <div class="ep-prior-head">
            <button
              type="button"
              class="ep-prior-toggle"
              :aria-expanded="priorOpen.has(row.qname)"
              data-testid="ep-prior-toggle"
              @click="togglePriorPanel(row.qname)"
            >
              <i :class="priorOpen.has(row.qname) ? 'pi pi-chevron-down' : 'pi pi-chevron-right'" />
              <span>{{ priorLabel(row.prior) }} prior settings</span>
              <span v-if="!priorOpen.has(row.qname)" class="ep-prior-summary">
                {{ priorSummary(row) }}
              </span>
            </button>
            <!-- On the header, not inside the panel: it hands the range to the
                 prior and greys out min/max, which is too consequential to hide
                 behind a disclosure. Offered only where CA says the prior has a
                 centre and a width to derive a range from. -->
            <label v-if="supportsUnbounded(row)" class="ep-unbounded">
              <input
                type="checkbox"
                :checked="row.unbounded"
                data-testid="ep-unbounded"
                @change="onUnboundedChange(row, $event.target.checked)"
              />
              <span title="min and max are derived from this prior instead of typed">
                unbounded
              </span>
            </label>
          </div>
          <div v-if="priorOpen.has(row.qname)" class="ep-prior-params">
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
                :placeholder="priorFieldPlaceholder(f, row)"
                :value="(row.priorParams ?? {})[f.name] ?? ''"
                :data-testid="`ep-prior-param-${f.name}`"
                @input="setPriorParam(row, f.name, $event.target.value)"
              />
            </span>
          </div>
        </div>
        <!-- A modifier's targets, always visible: θ's bounds are dimensionless
             (min/max above are θ's, not a target's), and what θ multiplies is
             the one thing a reader needs to see. -->
        <div
          v-if="row.kind === 'modifier'"
          class="ep-group-block"
          data-testid="ep-modifier-targets"
        >
          <p class="ep-group-hint">
            θ multiplies each target's model default (θ = {{ row.initial_value ?? 1 }} leaves
            them at their baselines). Bounds above are θ's, not a target's.
          </p>
          <div class="ep-group-list">
            <span
              v-for="q in row.qnames"
              :key="q"
              class="ep-group-item"
              :title="baselineLabel(row, q)"
            >
              {{ q }}
            </span>
          </div>
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
        label="Save"
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
  grid-template-columns: 1.4rem 2.5rem 1fr 6rem 6rem 7rem 2rem 2rem;
  align-items: center;
  gap: 0.5rem;
}
/* The prior column only exists when the backend reported a vocabulary, so the
   track list has to match — otherwise the note button lands under "Prior". */
.ep-head.has-prior,
.ep-list li.has-prior {
  grid-template-columns: 1.4rem 2.5rem 1fr 6rem 6rem 7rem 7rem 2rem 2rem;
}
.ep-sel {
  text-align: center;
}
.ep-sel input {
  cursor: pointer;
}
.ep-toolbar {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  margin-bottom: 0.5rem;
}
.ep-selcount {
  font-size: 0.75rem;
  opacity: 0.65;
}
/* A modifier row is a different kind of thing (θ, not a model value): tinted so
   it reads as such at a glance. */
.ep-list li.is-modifier {
  background: color-mix(in srgb, var(--p-primary-color, #5b9bd5) 8%, transparent);
}
input.ep-mod-name {
  width: 60%;
  font-size: 0.8rem;
}
.ep-mod-badge {
  margin-left: 0.35rem;
  padding: 0 0.35rem;
  border-radius: 999px;
  border: 1px solid var(--p-primary-color, #5b9bd5);
  color: var(--p-primary-color, #5b9bd5);
  font-size: 0.7rem;
}
select.ep-prior {
  width: 100%;
  font-size: 0.8rem;
}
/* Its own block, spanning the row, so the grid keeps its column widths whatever
   the chosen prior needs. Tinted and rule-marked to read as a detail *of* the
   row rather than another column in it. */
.ep-prior-block {
  grid-column: 1 / -1;
  margin: 0.15rem 0 0.1rem 2.9rem;
  border-left: 2px solid var(--p-primary-color, #5b9bd5);
  background: color-mix(in srgb, var(--p-primary-color, #5b9bd5) 7%, transparent);
  border-radius: 0 4px 4px 0;
}
.ep-prior-toggle {
  display: flex;
  align-items: center;
  gap: 0.4rem;
  width: 100%;
  padding: 0.25rem 0.5rem;
  background: none;
  border: none;
  cursor: pointer;
  color: inherit;
  font-size: 0.74rem;
  text-transform: uppercase;
  letter-spacing: 0.02em;
  opacity: 0.7;
}
.ep-prior-toggle:hover,
.ep-prior-toggle[aria-expanded='true'] {
  opacity: 1;
}
.ep-prior-toggle i {
  font-size: 0.7rem;
}
/* The values themselves when the panel is shut, so a set prior is legible
   without opening every row to find out. */
.ep-prior-summary {
  margin-left: auto;
  text-transform: none;
  letter-spacing: 0;
  font-size: 0.72rem;
  opacity: 0.75;
}
.ep-prior-params {
  display: flex;
  flex-wrap: wrap;
  gap: 0.75rem;
  padding: 0.1rem 0.6rem 0.45rem 1.5rem;
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
/* Sits with the prior's own settings because that is what it changes -- it hands
   the range to the prior -- but reads as a mode rather than a value. */
.ep-prior-head {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  padding-right: 0.6rem;
}
.ep-prior-head .ep-prior-toggle {
  flex: 1;
  min-width: 0;
}
/* Reads as a mode rather than a value, so it sits on the header beside the
   prior's name rather than among its numbers. */
.ep-unbounded {
  display: flex;
  align-items: center;
  gap: 0.3rem;
  cursor: pointer;
  font-size: 0.74rem;
  text-transform: uppercase;
  letter-spacing: 0.02em;
  opacity: 0.8;
  white-space: nowrap;
}
.ep-unbounded input {
  width: auto;
  cursor: pointer;
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
/* Same disclosure treatment as the prior settings: a detail *of* the row rather
   than another column in it. */
.ep-group-block {
  grid-column: 1 / -1;
  margin: 0.15rem 0 0.1rem 2.9rem;
  padding: 0.3rem 0.6rem 0.4rem;
  border-left: 2px solid var(--p-primary-color, #5b9bd5);
  background: color-mix(in srgb, var(--p-primary-color, #5b9bd5) 7%, transparent);
  border-radius: 0 4px 4px 0;
}
.ep-group-hint {
  margin: 0 0 0.3rem;
  font-size: 0.72rem;
  opacity: 0.7;
}
/* Wraps and scrolls: a param name shared by every vessel in a circulatory model
   is a long list, and it must not push the footer off the dialog. */
.ep-group-list {
  display: flex;
  flex-wrap: wrap;
  gap: 0.15rem 0.75rem;
  max-height: 9rem;
  overflow-y: auto;
}
.ep-group-item {
  display: flex;
  align-items: center;
  gap: 0.3rem;
  font-size: 0.76rem;
  cursor: pointer;
}
.ep-group-item input {
  cursor: pointer;
}
/* The count of components the row drives, beside its name. */
.ep-group-badge {
  margin-left: 0.35rem;
  padding: 0 0.3rem;
  border-radius: 999px;
  border: 1px solid var(--p-content-border-color, #444);
  font-size: 0.7rem;
  opacity: 0.75;
}
.ep-note-btn:disabled {
  opacity: 0.2;
  cursor: default;
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
