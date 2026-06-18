<script setup>
import { ref, watch, computed } from 'vue'
import Dialog from 'primevue/dialog'
import Button from 'primevue/button'
import Checkbox from 'primevue/checkbox'
import InputText from 'primevue/inputtext'
import { listDir, makeDir } from '../lib/api'

const props = defineProps({
  visible: { type: Boolean, default: false },
  mode: { type: String, default: 'file' }, // 'file' picks a file, 'dir' a folder
  title: { type: String, default: 'Browse' },
})
const emit = defineEmits(['update:visible', 'select'])

const path = ref('')
const parent = ref(null)
const entries = ref([])
const selectedFile = ref('')
const error = ref('')
const loading = ref(false)
// Hide dotfiles/dot-dirs by default; the toolbar checkbox reveals them.
const showHidden = ref(false)
// Inline "new folder" creation in the current directory.
const creatingFolder = ref(false)
const newFolderName = ref('')

const visibleEntries = computed(() =>
  showHidden.value
    ? entries.value
    : entries.value.filter((e) => !e.name.startsWith('.')),
)

async function load(p) {
  loading.value = true
  error.value = ''
  try {
    // dirs_only when picking a folder: hides files to reduce clutter.
    const data = await listDir(p, props.mode === 'dir')
    path.value = data.path
    parent.value = data.parent
    entries.value = data.entries
    selectedFile.value = ''
  } catch (e) {
    error.value = e?.response?.data?.detail || String(e)
  } finally {
    loading.value = false
  }
}

// Open at the user's home dir each time the dialog is shown.
watch(
  () => props.visible,
  (v) => {
    if (v) {
      creatingFolder.value = false
      newFolderName.value = ''
      load(null)
    }
  },
  { immediate: true },
)

async function createFolder() {
  const name = newFolderName.value.trim()
  if (!name) return
  try {
    const res = await makeDir(path.value, name)
    creatingFolder.value = false
    newFolderName.value = ''
    await load(res.path) // step into the new folder so it's ready to select
  } catch (e) {
    error.value = e?.response?.data?.detail || String(e)
  }
}

function onEntryClick(entry) {
  if (entry.is_dir) load(entry.path)
  else if (props.mode === 'file') selectedFile.value = entry.path
}

function confirm() {
  const chosen = props.mode === 'dir' ? path.value : selectedFile.value
  if (!chosen) return
  emit('select', chosen)
  emit('update:visible', false)
}
</script>

<template>
  <Dialog
    :visible="visible"
    modal
    :header="title"
    :style="{ width: '42rem' }"
    data-testid="file-browser"
    @update:visible="emit('update:visible', $event)"
  >
    <div class="fb-toolbar">
      <Button
        icon="pi pi-arrow-up"
        label="Up"
        size="small"
        text
        :disabled="!parent"
        @click="load(parent)"
      />
      <code class="fb-path">{{ path }}</code>
      <Button
        icon="pi pi-folder-plus"
        label="New folder"
        size="small"
        text
        data-testid="fb-new-folder"
        @click="creatingFolder = true"
      />
      <label class="fb-hidden-toggle" title="Show dotfiles and hidden folders">
        <Checkbox v-model="showHidden" :binary="true" data-testid="fb-show-hidden" />
        <span>show hidden</span>
      </label>
    </div>
    <div v-if="creatingFolder" class="fb-new-folder">
      <InputText
        v-model="newFolderName"
        placeholder="New folder name"
        size="small"
        autofocus
        data-testid="fb-new-folder-name"
        @keyup.enter="createFolder"
      />
      <Button label="Create" size="small" data-testid="fb-new-folder-create" @click="createFolder" />
      <Button label="Cancel" size="small" text @click="creatingFolder = false" />
    </div>
    <p v-if="error" class="fb-error" data-testid="fb-error">{{ error }}</p>
    <ul class="fb-list">
      <li
        v-for="e in visibleEntries"
        :key="e.path"
        :class="{ dir: e.is_dir, selected: e.path === selectedFile }"
        @click="onEntryClick(e)"
        @dblclick="e.is_dir ? load(e.path) : confirm()"
      >
        <i :class="e.is_dir ? 'pi pi-folder' : 'pi pi-file'" />
        <span>{{ e.name }}</span>
      </li>
      <li v-if="!visibleEntries.length && !loading" class="fb-empty">(empty)</li>
    </ul>
    <template #footer>
      <Button label="Cancel" size="small" text @click="emit('update:visible', false)" />
      <Button
        :label="mode === 'dir' ? 'Select this folder' : 'Select'"
        size="small"
        :disabled="mode === 'file' && !selectedFile"
        data-testid="fb-confirm"
        @click="confirm"
      />
    </template>
  </Dialog>
</template>

<style scoped>
.fb-toolbar {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  margin-bottom: 0.5rem;
}
.fb-path {
  font-size: 0.78rem;
  opacity: 0.8;
  word-break: break-all;
}
.fb-new-folder {
  display: flex;
  align-items: center;
  gap: 0.4rem;
  margin-bottom: 0.5rem;
}
.fb-new-folder :deep(input) {
  flex: 1;
}
.fb-hidden-toggle {
  margin-left: auto;
  display: flex;
  align-items: center;
  gap: 0.3rem;
  font-size: 0.72rem;
  opacity: 0.55;
  cursor: pointer;
  white-space: nowrap;
  flex-shrink: 0;
}
.fb-hidden-toggle:hover {
  opacity: 0.85;
}
/* shrink the checkbox so the toggle reads as a subtle control */
.fb-hidden-toggle :deep(.p-checkbox) {
  transform: scale(0.78);
}
.fb-list {
  list-style: none;
  margin: 0;
  padding: 0;
  max-height: 50vh;
  overflow-y: auto;
  border: 1px solid var(--p-content-border-color, #333);
  border-radius: 6px;
}
.fb-list li {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  padding: 0.35rem 0.6rem;
  font-size: 0.85rem;
  cursor: pointer;
}
.fb-list li.dir {
  font-weight: 600;
}
.fb-list li:hover {
  background: var(--p-content-hover-background, rgba(127, 127, 127, 0.15));
}
.fb-list li.selected {
  background: var(--p-highlight-background, rgba(91, 155, 213, 0.25));
}
.fb-empty {
  opacity: 0.5;
  cursor: default !important;
}
.fb-error {
  color: #e84a5f;
  font-size: 0.78rem;
  margin: 0 0 0.5rem;
}
</style>
