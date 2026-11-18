<script setup>
/**
 * The form of a Choose a parameter set by cost action box: the steps drawn into it (its
 * "combined" arrows) and how it gives one value per parameter from them. Saving makes the
 * box CA's combine step (POST /api/workflow/combine), checked by CA; the workflow's Run
 * runs it. Two calibrations that give one parameter to the same box, neither taking from
 * the other, are an error until a combine step joins them.
 */
import Button from 'primevue/button'
import Dialog from 'primevue/dialog'
import { computed, ref, watch } from 'vue'

const props = defineProps({
  // The action box being set up, or null.
  draft: { type: String, default: null },
  // The step ids drawn into it (its pending combine_from arrows).
  inputs: { type: Array, default: () => [] },
  // async ({draft, method}) => '' when done, else why not.
  save: { type: Function, required: true },
  busy: { type: Boolean, default: false },
})
const emit = defineEmits(['close'])

// CA's combine.method, best_fit its default.
const METHODS = [
  { value: 'best_fit', label: 'Lowest cost',
    words: 'Every value of the input with the lowest cost. The inputs are calibrations of the same obs_data, with the same cost and the same parameters.' },
  { value: 'mean', label: 'Mean',
    words: "Each parameter's mean over the inputs that give it." },
]

const method = ref('best_fit')
const why = ref('')
const saving = ref(false)
const enough = computed(() => props.inputs.length >= 2)

watch(() => props.draft, () => {
  method.value = 'best_fit'
  why.value = ''
}, { immediate: true })

async function onSave() {
  saving.value = true
  why.value = await props.save({ draft: props.draft, method: method.value })
  saving.value = false
  if (!why.value) emit('close')
}
</script>

<template>
  <Dialog
    :visible="!!draft"
    modal
    :header="draft ? `Choose a parameter set by cost: ${draft}` : ''"
    :style="{ width: 'min(620px, 95vw)' }"
    data-testid="workflow-combine-form"
    @update:visible="(v) => !v && emit('close')"
  >
    <template v-if="draft">
      <p class="cf-inputs" data-testid="workflow-combine-inputs">
        <template v-if="inputs.length">Chooses from: <b>{{ inputs.join(', ') }}</b>.</template>
        <template v-else>Nothing is drawn into it yet.</template>
      </p>
      <p v-if="!enough" class="cf-hint" data-testid="workflow-combine-hint">
        Draw an arrow from each calibration to choose from (the handle on its right edge) into
        this box, and pick <b>combined</b>: two or more.
      </p>
      <fieldset class="cf-methods">
        <label v-for="m in METHODS" :key="m.value" class="cf-method">
          <input v-model="method" type="radio" name="cf-method" :value="m.value"
                 :data-testid="`workflow-combine-method-${m.value}`" />
          <span><b>{{ m.label }}</b>: {{ m.words }}</span>
        </label>
      </fieldset>
      <p v-if="why" class="cf-why" data-testid="workflow-combine-why">{{ why }}</p>
    </template>
    <template #footer>
      <Button label="Cancel" text @click="emit('close')" />
      <Button label="Make the step" icon="pi pi-check" data-testid="workflow-combine-save"
              :disabled="!enough || saving || busy" :loading="saving" @click="onSave" />
    </template>
  </Dialog>
</template>

<style scoped>
.cf-inputs, .cf-hint { margin: 0 0 0.6rem; }
.cf-hint { color: var(--p-text-muted-color, #666); font-size: 0.9rem; }
.cf-methods { border: 0; padding: 0; margin: 0; display: flex; flex-direction: column; gap: 0.5rem; }
.cf-method { display: flex; gap: 0.5rem; align-items: flex-start; }
.cf-method input { margin-top: 0.25rem; }
.cf-why { color: #c8372d; margin: 0.6rem 0 0; }
</style>
