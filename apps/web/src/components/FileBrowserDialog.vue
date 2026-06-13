<script setup>
import { ref, watch } from 'vue'
import Dialog from 'primevue/dialog'
import Button from 'primevue/button'
import { listDir } from '../lib/api'

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
    if (v) load(null)
  },
  { immediate: true },
)

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
    </div>
    <p v-if="error" class="fb-error" data-testid="fb-error">{{ error }}</p>
    <ul class="fb-list">
      <li
        v-for="e in entries"
        :key="e.path"
        :class="{ dir: e.is_dir, selected: e.path === selectedFile }"
        @click="onEntryClick(e)"
        @dblclick="e.is_dir ? load(e.path) : confirm()"
      >
        <i :class="e.is_dir ? 'pi pi-folder' : 'pi pi-file'" />
        <span>{{ e.name }}</span>
      </li>
      <li v-if="!entries.length && !loading" class="fb-empty">(empty)</li>
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
