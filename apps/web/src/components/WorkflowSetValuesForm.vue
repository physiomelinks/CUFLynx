<script setup>
/**
 * The form of a Set values by hand action box: each parameter, its value and why it has it
 * (a value set by hand always carries its reason, and is drawn red). "A point only" keeps
 * the values out of the target: a point a later step starts from or is analysed at.
 * Saving makes the box a set step (POST /api/workflow/set_values), checked by CA.
 */
import Button from 'primevue/button'
import Dialog from 'primevue/dialog'
import { ref, watch } from 'vue'

const props = defineProps({
  // The action box being set up, or null.
  draft: { type: String, default: null },
  // Constants that can be set: component/name.
  parametersOffered: { type: Array, default: () => [] },
  // async ({draft, entries, point}) => '' when done, else why not.
  save: { type: Function, required: true },
  busy: { type: Boolean, default: false },
})
const emit = defineEmits(['close'])

const rows = ref([])
const point = ref(false)
const why = ref('')
const saving = ref(false)

watch(() => props.draft, () => {
  rows.value = [{ parameter: '', value: null, reason: '' }]
  point.value = false
  why.value = ''
}, { immediate: true })

async function onSave() {
  why.value = ''
  const entries = rows.value.filter((r) => r.parameter.trim())
  for (const r of entries) {
    if (!Number.isFinite(r.value)) {
      why.value = `${r.parameter}: give a number`
      return
    }
    if (!r.reason.trim()) {
      why.value = `${r.parameter}: say why it has this value (a source, or the reason it was chosen)`
      return
    }
  }
  if (!entries.length) {
    why.value = 'Give at least one parameter and its value.'
    return
  }
  saving.value = true
  why.value = await props.save({ draft: props.draft, entries: entries.map((r) => ({ ...r })), point: point.value })
  saving.value = false
  if (!why.value) emit('close')
}
</script>

<template>
  <Dialog
    :visible="!!draft"
    modal
    :header="draft ? `Set values by hand: ${draft}` : ''"
    :style="{ width: 'min(760px, 95vw)' }"
    data-testid="workflow-set-form"
    @update:visible="(v) => !v && emit('close')"
  >
    <template v-if="draft">
      <datalist id="sv-params"><option v-for="p in parametersOffered" :key="p" :value="p" /></datalist>
      <div v-for="(r, i) in rows" :key="i" class="sv-row" :data-testid="`workflow-set-row-${i}`">
        <input v-model="r.parameter" list="sv-params" class="sv-name" placeholder="component/parameter" />
        <input v-model.number="r.value" type="number" step="any" class="sv-num" placeholder="value" />
        <input v-model="r.reason" class="sv-reason" placeholder="why: a source, or the reason it was chosen" />
        <Button icon="pi pi-times" text size="small" title="Remove" @click="rows.splice(i, 1)" />
      </div>
      <Button label="Add a value" icon="pi pi-plus" text size="small" data-testid="workflow-set-add"
              @click="rows.push({ parameter: '', value: null, reason: '' })" />
      <label class="sv-point">
        <input v-model="point" type="checkbox" data-testid="workflow-set-point" />
        A point only: where a later step starts or is analysed, not merged into the target
      </label>
      <p v-if="why" class="sv-error" data-testid="workflow-set-error">{{ why }}</p>
    </template>
    <template #footer>
      <Button label="Cancel" text :disabled="saving" @click="emit('close')" />
      <Button label="Set values" :disabled="busy || saving" data-testid="workflow-set-save" @click="onSave" />
    </template>
  </Dialog>
</template>

<style scoped>
input {
  font: inherit;
  padding: 0.2rem 0.4rem;
  border: 1px solid var(--p-content-border-color, #555);
  border-radius: 4px;
  background: transparent;
  color: inherit;
}
input[type='checkbox'] {
  padding: 0;
}
.sv-row {
  display: flex;
  gap: 0.4rem;
  align-items: center;
  margin-bottom: 0.3rem;
}
.sv-name {
  width: 14rem;
}
.sv-num {
  width: 7rem;
}
.sv-reason {
  flex: 1;
}
.sv-point {
  display: flex;
  gap: 0.4rem;
  align-items: center;
  font-size: 0.9em;
  margin-top: 0.5rem;
}
.sv-error {
  color: #e84a5f;
  font-size: 0.9em;
}
</style>
