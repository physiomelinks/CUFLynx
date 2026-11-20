<script setup>
/**
 * The form of a model box's Choose parameters: the entries of the study's params_for_id (the
 * candidates, named as circulatory_autogen labels them) with their bounds, a tick for each
 * one to keep, and why. Saving makes the box CA's reduction chosen by hand (an orange box,
 * POST /api/workflow/choose_parameters), which a calibration takes with params_for_id_from.
 */
import Button from 'primevue/button'
import Dialog from 'primevue/dialog'
import { computed, ref, watch } from 'vue'

const props = defineProps({
  // The model box being set up, or null.
  draft: { type: String, default: null },
  // async () => {params_for_id, candidates: [{name, min, max}]}, or throws why not.
  load: { type: Function, required: true },
  // async ({draft, parameters, reason}) => '' when done, else why not.
  save: { type: Function, required: true },
  busy: { type: Boolean, default: false },
})
const emit = defineEmits(['close'])

const candidates = ref([])
const file = ref('')
const kept = ref([])
const reason = ref('')
const why = ref('')
const saving = ref(false)

watch(() => props.draft, async (draft) => {
  candidates.value = []
  kept.value = []
  reason.value = ''
  why.value = ''
  if (!draft) return
  try {
    const got = await props.load()
    candidates.value = got?.candidates ?? []
    file.value = got?.params_for_id ?? ''
  } catch (err) {
    why.value = err?.response?.data?.detail ?? err?.message ?? String(err)
  }
}, { immediate: true })

const ready = computed(() => kept.value.length > 0 && reason.value.trim() !== '')

function fmt(v) {
  return typeof v === 'number' ? Number(v.toPrecision(4)).toString() : '—'
}

async function onSave() {
  saving.value = true
  why.value = await props.save({ draft: props.draft, parameters: [...kept.value], reason: reason.value.trim() })
  saving.value = false
  if (!why.value) emit('close')
}
</script>

<template>
  <Dialog
    :visible="!!draft"
    modal
    :header="draft ? `Choose parameters by hand: ${draft}` : ''"
    :style="{ width: 'min(640px, 95vw)' }"
    data-testid="workflow-choose-params-form"
    @update:visible="(v) => !v && emit('close')"
  >
    <template v-if="draft">
      <p class="cp-hint">
        Tick the parameters to keep<template v-if="file"> from <code>{{ file }}</code></template>.
        A calibration that takes this box (params_for_id) fits only these, in their bounds.
      </p>
      <div class="cp-list" data-testid="workflow-choose-params-list">
        <label v-for="c in candidates" :key="c.name" class="cp-row">
          <input v-model="kept" type="checkbox" :value="c.name" :data-testid="`workflow-choose-param-${c.name}`" />
          <code>{{ c.name }}</code>
          <span class="cp-bounds">{{ fmt(c.min) }} … {{ fmt(c.max) }}</span>
        </label>
      </div>
      <label class="cp-reason">
        Why these parameters
        <textarea v-model="reason" rows="2" data-testid="workflow-choose-params-reason"
                  placeholder="e.g. the data constrain only these; the rest are measured" />
      </label>
      <p v-if="why" class="cp-why" data-testid="workflow-choose-params-why">{{ why }}</p>
    </template>
    <template #footer>
      <Button label="Cancel" text @click="emit('close')" />
      <Button label="Make the step" icon="pi pi-check" data-testid="workflow-choose-params-save"
              :disabled="!ready || saving || busy" :loading="saving" @click="onSave" />
    </template>
  </Dialog>
</template>

<style scoped>
.cp-hint { margin: 0 0 0.5rem; }
.cp-list { display: flex; flex-direction: column; gap: 0.25rem; max-height: 50vh; overflow: auto; }
.cp-row { display: flex; gap: 0.5rem; align-items: baseline; }
.cp-bounds { opacity: 0.7; font-size: 0.85em; }
.cp-reason { display: flex; flex-direction: column; gap: 0.25rem; margin-top: 0.6rem; }
.cp-reason textarea { font: inherit; }
.cp-why { color: #c8372d; margin: 0.6rem 0 0; }
</style>
