<script setup>
import { ref, computed, watch } from 'vue'
import Dialog from 'primevue/dialog'
import Button from 'primevue/button'
import InputText from 'primevue/inputtext'
import Message from 'primevue/message'
import { getUserOperations, saveUserOperation, deleteUserOperation } from '../lib/api'

// Author custom observable "operations" (obs_funcs) without opening
// circulatory_autogen (issue #58). Saved funcs live in CA's
// funcs_user/operation_funcs_user_CUFLynx.py and become selectable operations in
// the obs_data editor; existing funcs are listed and editable.
const props = defineProps({
  visible: { type: Boolean, default: false },
})
const emit = defineEmits(['update:visible', 'saved'])

const DEFAULT_TEMPLATE = `def my_operation(x, series_output=False):
    """Return a scalar summarising the operand series x."""
    if series_output:
        return x
    return float(np.max(x) - np.min(x))
`

const functions = ref([])
const template = ref(DEFAULT_TEMPLATE)
const available = ref(true)
const name = ref('')
const source = ref(DEFAULT_TEMPLATE)
// The name currently being edited (an existing func) vs a brand-new one.
const editingName = ref(null)
const loading = ref(false)
const saving = ref(false)
const error = ref('')
const notice = ref('')

const nameValid = computed(() => /^[A-Za-z][A-Za-z0-9_]*$/.test(name.value.trim()))
const canSave = computed(() => nameValid.value && source.value.trim().length > 0 && !saving.value)

async function refresh() {
  loading.value = true
  error.value = ''
  try {
    const data = await getUserOperations()
    functions.value = data.functions ?? []
    template.value = data.template || DEFAULT_TEMPLATE
    available.value = data.available !== false
  } catch {
    error.value = 'Could not load custom operations.'
  } finally {
    loading.value = false
  }
}

function startNew() {
  editingName.value = null
  name.value = ''
  source.value = template.value
  error.value = ''
  notice.value = ''
}

function editFunc(fn) {
  editingName.value = fn.name
  name.value = fn.name
  source.value = fn.source
  error.value = ''
  notice.value = ''
}

watch(
  () => props.visible,
  async (v) => {
    if (!v) return
    await refresh()
    startNew()
  },
  { immediate: true },
)

async function onSave() {
  if (!canSave.value) return
  saving.value = true
  error.value = ''
  notice.value = ''
  try {
    const data = await saveUserOperation(name.value.trim(), source.value)
    functions.value = data.functions ?? []
    editingName.value = name.value.trim()
    notice.value = `Saved "${name.value.trim()}".`
    emit('saved', functions.value)
  } catch (e) {
    error.value = e?.response?.data?.detail ?? 'Save failed.'
  } finally {
    saving.value = false
  }
}

async function onDelete(fn) {
  saving.value = true
  error.value = ''
  notice.value = ''
  try {
    const data = await deleteUserOperation(fn.name)
    functions.value = data.functions ?? []
    if (editingName.value === fn.name) startNew()
    emit('saved', functions.value)
  } catch (e) {
    error.value = e?.response?.data?.detail ?? 'Delete failed.'
  } finally {
    saving.value = false
  }
}
</script>

<template>
  <Dialog
    :visible="visible"
    modal
    header="Custom operations"
    :style="{ width: '48rem' }"
    data-testid="edit-op-funcs"
    @update:visible="emit('update:visible', $event)"
  >
    <p class="of-hint">
      Write your own observable <strong>operation</strong> — the reduction applied
      to a data_item's operand series to produce the scalar a cost function
      compares. Saved operations are stored in circulatory_autogen's
      <code>funcs_user/operation_funcs_user_CUFLynx.py</code> and become selectable
      in the operation dropdown.
    </p>

    <Message
      v-if="!available"
      severity="warn"
      :closable="false"
      data-testid="of-unavailable"
    >
      circulatory_autogen is not configured — set the CA dir in Settings first.
    </Message>
    <Message v-if="error" severity="error" data-testid="of-error" :closable="false">
      {{ error }}
    </Message>
    <Message v-if="notice && !error" severity="success" data-testid="of-notice" :closable="false">
      {{ notice }}
    </Message>

    <div class="of-body">
      <div class="of-list">
        <div class="of-list-head">
          <span>Your operations</span>
          <Button
            label="New"
            icon="pi pi-plus"
            size="small"
            text
            data-testid="of-new"
            @click="startNew"
          />
        </div>
        <p v-if="!functions.length" class="of-empty">No custom operations yet.</p>
        <ul v-else>
          <li
            v-for="fn in functions"
            :key="fn.name"
            :class="{ active: fn.name === editingName }"
          >
            <button type="button" class="of-name" data-testid="of-item" @click="editFunc(fn)">
              {{ fn.name }}
            </button>
            <Button
              icon="pi pi-trash"
              size="small"
              text
              severity="danger"
              :title="`Delete ${fn.name}`"
              data-testid="of-delete"
              @click="onDelete(fn)"
            />
          </li>
        </ul>
      </div>

      <div class="of-editor">
        <label class="of-field">
          <span>Operation name</span>
          <InputText
            v-model="name"
            placeholder="my_operation"
            :invalid="name.length > 0 && !nameValid"
            data-testid="of-name-input"
          />
        </label>
        <small v-if="name.length > 0 && !nameValid" class="of-name-err">
          Must be a valid Python identifier (letters, digits, underscore; not
          starting with a digit).
        </small>
        <label class="of-field of-code-field">
          <span>Python code</span>
          <textarea
            v-model="source"
            class="of-code"
            spellcheck="false"
            rows="14"
            data-testid="of-source"
          />
        </label>
      </div>
    </div>

    <template #footer>
      <Button label="Close" text data-testid="of-close" @click="emit('update:visible', false)" />
      <Button
        label="Save operation"
        icon="pi pi-save"
        :disabled="!canSave"
        :loading="saving"
        data-testid="of-save"
        @click="onSave"
      />
    </template>
  </Dialog>
</template>

<style scoped>
.of-hint {
  font-size: 0.85rem;
  margin: 0 0 0.5rem;
}
.of-body {
  display: flex;
  gap: 1rem;
}
.of-list {
  flex: 0 0 12rem;
}
.of-list-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  font-weight: 600;
  font-size: 0.85rem;
}
.of-empty {
  font-size: 0.8rem;
  opacity: 0.7;
}
.of-list ul {
  list-style: none;
  margin: 0.25rem 0 0;
  padding: 0;
}
.of-list li {
  display: flex;
  align-items: center;
  justify-content: space-between;
  border-radius: 4px;
}
.of-list li.active {
  background: var(--p-highlight-background, rgba(91, 155, 213, 0.2));
}
.of-name {
  flex: 1;
  text-align: left;
  background: none;
  border: none;
  color: inherit;
  cursor: pointer;
  padding: 0.3rem 0.4rem;
  font-family: monospace;
  font-size: 0.85rem;
}
.of-editor {
  flex: 1;
  display: flex;
  flex-direction: column;
  gap: 0.4rem;
}
.of-field {
  display: flex;
  flex-direction: column;
  gap: 0.2rem;
  font-size: 0.85rem;
}
.of-code-field {
  flex: 1;
}
.of-name-err {
  color: var(--p-red-400, #e57373);
  font-size: 0.75rem;
}
.of-code {
  width: 100%;
  min-height: 16rem;
  font-family: monospace;
  font-size: 0.8rem;
  line-height: 1.4;
  tab-size: 4;
  padding: 0.5rem;
  border-radius: 4px;
  border: 1px solid var(--p-content-border-color, #555);
  background: var(--p-content-background, #1e1e1e);
  color: inherit;
  resize: vertical;
  white-space: pre;
  overflow: auto;
}
</style>
