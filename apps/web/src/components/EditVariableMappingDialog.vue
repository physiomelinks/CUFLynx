<script setup>
/**
 * Edit the LaTeX symbol of every variable of the model -- the study's
 * `<prefix>_variable_mapping.csv`, which circulatory_autogen's methods writer
 * (`cuflynx-methods-latex`) typesets the equations with.
 *
 * The rows, their default symbols and the file are CA's; this dialog edits the
 * `latex` column, shows each symbol as it will be typeset, and says when two
 * variables share one (they could not be told apart in the equations).
 */
import { computed, ref, watch } from 'vue'
import Button from 'primevue/button'
import Dialog from 'primevue/dialog'
import InputText from 'primevue/inputtext'
import Message from 'primevue/message'
import { errorMessage, getVariableMapping, saveVariableMapping } from '../lib/api'
import { renderSymbol } from '../lib/math'

const props = defineProps({
  visible: { type: Boolean, default: false },
  modelId: { type: String, default: null },
  outputsDir: { type: String, default: '' },
  // Open filtered to this variable (the row the user clicked), if any.
  focus: { type: String, default: '' },
})
const emit = defineEmits(['update:visible', 'saved'])

const KINDS = ['all', 'parameter', 'state', 'algebraic', 'constant', 'time']

const loading = ref(false)
const saving = ref(false)
const error = ref('')
const path = ref('')
const exists = ref(false)
const rows = ref([])
const original = ref({})
const filter = ref('')
const kind = ref('all')

async function load() {
  if (!props.modelId) return
  loading.value = true
  error.value = ''
  try {
    const data = await getVariableMapping(props.modelId, props.outputsDir)
    path.value = data.path
    exists.value = data.exists
    rows.value = data.rows.map((r) => ({ ...r }))
    original.value = Object.fromEntries(data.rows.map((r) => [r.variable_name, r.latex]))
    filter.value = props.focus || ''
  } catch (e) {
    error.value = errorMessage(e)
    rows.value = []
  } finally {
    loading.value = false
  }
}

watch(() => props.visible, (open) => {
  if (open) load()
})

const shown = computed(() => {
  const text = filter.value.trim().toLowerCase()
  return rows.value.filter(
    (r) =>
      (kind.value === 'all' || r.kind === kind.value) &&
      (!text ||
        r.variable_name.toLowerCase().includes(text) ||
        (r.latex || '').toLowerCase().includes(text)),
  )
})

const changed = computed(() =>
  rows.value.filter((r) => (r.latex || '') !== (original.value[r.variable_name] || '')),
)

// Live, so a clash is visible while typing rather than after saving.
const duplicates = computed(() => {
  const by = {}
  for (const r of rows.value) {
    const key = (r.latex || '').trim()
    if (key) (by[key] ??= []).push(r.variable_name)
  }
  return Object.fromEntries(Object.entries(by).filter(([, names]) => names.length > 1))
})
const duplicateNames = computed(() => new Set(Object.values(duplicates.value).flat()))
const empty = computed(() => rows.value.filter((r) => !(r.latex || '').trim()))

function resetRow(row) {
  row.latex = row.default_latex
}

async function save() {
  saving.value = true
  error.value = ''
  try {
    const data = await saveVariableMapping(
      props.modelId,
      rows.value.map((r) => ({ variable_name: r.variable_name, latex: r.latex })),
      props.outputsDir,
    )
    path.value = data.path
    exists.value = true
    rows.value = data.rows.map((r) => ({ ...r }))
    original.value = Object.fromEntries(data.rows.map((r) => [r.variable_name, r.latex]))
    emit('saved', data)
  } catch (e) {
    error.value = errorMessage(e)
  } finally {
    saving.value = false
  }
}

function close() {
  emit('update:visible', false)
}
</script>

<template>
  <Dialog
    :visible="visible"
    modal
    header="LaTeX symbols"
    :style="{ width: '52rem', maxWidth: '95vw' }"
    data-testid="variable-mapping-dialog"
    @update:visible="(v) => emit('update:visible', v)"
  >
    <p class="vm-hint">
      How each variable is written in the methods
      (<code>cuflynx-methods-latex</code>). Symbols are LaTeX without <code>$</code>.
      <span v-if="path" :title="path">
        {{ exists ? 'Saved in' : 'Will be saved to' }} <code>{{ path }}</code>.
      </span>
    </p>
    <div class="vm-toolbar">
      <InputText
        v-model="filter"
        size="small"
        placeholder="Filter by name or symbol"
        data-testid="variable-mapping-filter"
      />
      <span class="vm-kinds">
        <button
          v-for="k in KINDS"
          :key="k"
          class="vm-kind"
          :class="{ active: kind === k }"
          :data-testid="`variable-mapping-kind-${k}`"
          @click="kind = k"
        >
          {{ k }}
        </button>
      </span>
    </div>
    <Message v-if="error" severity="error" :closable="false" data-testid="variable-mapping-error">
      {{ error }}
    </Message>
    <Message
      v-if="Object.keys(duplicates).length"
      severity="warn"
      :closable="false"
      data-testid="variable-mapping-duplicates"
    >
      Shared symbols cannot be told apart in the equations:
      <span v-for="(names, latex) in duplicates" :key="latex" class="vm-dup">
        <code>{{ latex }}</code> ({{ names.join(', ') }})
      </span>
    </Message>
    <p v-if="loading" class="vm-hint">Reading the model...</p>
    <div v-else class="vm-table-wrap">
      <table class="vm-table">
        <thead>
          <tr><th>Variable</th><th>Kind</th><th>Symbol (LaTeX)</th><th>Shown as</th><th /></tr>
        </thead>
        <tbody>
          <tr
            v-for="row in shown"
            :key="row.variable_name"
            :class="{ dup: duplicateNames.has(row.variable_name) }"
            :data-testid="`variable-mapping-row-${row.variable_name}`"
          >
            <td class="vm-name" :title="`${row.component} · ${row.units}`">
              <code>{{ row.variable_name }}</code>
            </td>
            <td class="vm-kind-cell">{{ row.kind }}</td>
            <td>
              <InputText
                v-model="row.latex"
                size="small"
                class="vm-input"
                :invalid="!(row.latex || '').trim()"
                :data-testid="`variable-mapping-input-${row.variable_name}`"
              />
            </td>
            <!-- eslint-disable-next-line vue/no-v-html -- KaTeX output of the user's own symbol -->
            <td class="vm-preview" v-html="renderSymbol(row.latex)" />
            <td>
              <Button
                v-if="row.latex !== row.default_latex"
                icon="pi pi-undo"
                size="small"
                text
                :title="`Back to the default, ${row.default_latex}`"
                :data-testid="`variable-mapping-reset-${row.variable_name}`"
                @click="resetRow(row)"
              />
            </td>
          </tr>
        </tbody>
      </table>
      <p v-if="!shown.length && rows.length" class="vm-hint">No variable matches.</p>
    </div>
    <template #footer>
      <span class="vm-hint vm-footer-note">
        {{ changed.length }} changed{{ empty.length ? ` · ${empty.length} empty` : '' }}
      </span>
      <Button label="Close" size="small" text @click="close" />
      <Button
        label="Save"
        icon="pi pi-check"
        size="small"
        :disabled="saving || loading || !rows.length || (!changed.length && exists)"
        data-testid="variable-mapping-save"
        @click="save"
      />
    </template>
  </Dialog>
</template>

<style scoped>
.vm-hint {
  margin: 0 0 0.4rem;
  font-size: 0.78rem;
  opacity: 0.75;
  overflow-wrap: anywhere;
}
.vm-toolbar {
  display: flex;
  flex-wrap: wrap;
  gap: 0.5rem;
  align-items: center;
  margin-bottom: 0.4rem;
}
.vm-kinds {
  display: flex;
  gap: 0.2rem;
}
.vm-kind {
  background: transparent;
  border: 1px solid var(--p-content-border-color, #444);
  color: inherit;
  border-radius: 4px;
  padding: 0.1rem 0.45rem;
  cursor: pointer;
  font-size: 0.75rem;
}
.vm-kind.active {
  background: var(--p-primary-color, #5b9bd5);
  color: #fff;
}
.vm-table-wrap {
  max-height: 55vh;
  overflow: auto;
}
.vm-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 0.8rem;
}
.vm-table th {
  text-align: left;
  position: sticky;
  top: 0;
  background: var(--p-content-background, #1e1e1e);
  padding: 0.25rem;
}
.vm-table td {
  padding: 0.15rem 0.25rem;
  vertical-align: middle;
}
.vm-name {
  max-width: 16rem;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.vm-kind-cell {
  opacity: 0.7;
}
.vm-input {
  width: 100%;
  font-family: monospace;
  /* room below the baseline: the small input clipped underscores, the one character
     a LaTeX symbol is most made of */
  line-height: 1.5;
  padding-bottom: 0.3rem;
}
.vm-preview {
  min-width: 6rem;
}
tr.dup .vm-preview {
  outline: 1px solid #d29922;
}
.vm-dup {
  margin-right: 0.5rem;
}
.vm-footer-note {
  margin-right: auto;
}
</style>
