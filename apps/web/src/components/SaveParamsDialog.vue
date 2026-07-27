<script setup>
import { ref, computed, watch } from 'vue'
import Dialog from 'primevue/dialog'
import Button from 'primevue/button'
import InputText from 'primevue/inputtext'
import Select from 'primevue/select'

// Ask for a name + format when saving the current slider values (issue #106).
// Defaults to manual_params.npy; CSV is offered too (self-describing).
const props = defineProps({
  visible: { type: Boolean, default: false },
  // Where the file will be written (shown so the user knows the destination).
  outputDir: { type: String, default: '' },
})
const emit = defineEmits(['update:visible', 'save'])

const FORMATS = [
  { value: 'npy', label: '.npy (numpy array)' },
  { value: 'csv', label: '.csv (names + values)' },
]

const baseName = ref('manual_params')
const format = ref('npy')

// Reset to the defaults each time the dialog opens.
watch(
  () => props.visible,
  (v) => {
    if (!v) return
    baseName.value = 'manual_params'
    format.value = 'npy'
  },
)

const filename = computed(() => `${(baseName.value || 'manual_params').trim()}.${format.value}`)
const canSave = computed(() => baseName.value.trim().length > 0)

function onSave() {
  if (!canSave.value) return
  emit('save', { filename: filename.value })
  emit('update:visible', false)
}
</script>

<template>
  <Dialog
    :visible="visible"
    modal
    header="Save current parameters"
    :style="{ width: '26rem' }"
    data-testid="save-params-dialog"
    @update:visible="emit('update:visible', $event)"
  >
    <div class="sp-body">
      <label class="sp-field">
        <span>File name</span>
        <InputText v-model="baseName" data-testid="save-params-name" />
      </label>
      <label class="sp-field">
        <span>Format</span>
        <Select
          v-model="format"
          :options="FORMATS"
          option-label="label"
          option-value="value"
          data-testid="save-params-format"
        />
      </label>
      <p class="sp-preview" data-testid="save-params-preview">
        Saves <code>{{ filename }}</code>
        <template v-if="outputDir"> in <code>{{ outputDir }}</code></template>
      </p>
    </div>
    <template #footer>
      <Button label="Cancel" text data-testid="save-params-cancel" @click="emit('update:visible', false)" />
      <Button
        label="Save"
        icon="pi pi-save"
        :disabled="!canSave"
        data-testid="save-params-confirm"
        @click="onSave"
      />
    </template>
  </Dialog>
</template>

<style scoped>
.sp-body {
  display: flex;
  flex-direction: column;
  gap: 0.75rem;
}
.sp-field {
  display: flex;
  flex-direction: column;
  gap: 0.25rem;
  font-size: 0.85rem;
}
.sp-preview {
  font-size: 0.8rem;
  opacity: 0.75;
  margin: 0;
  word-break: break-all;
}
</style>
