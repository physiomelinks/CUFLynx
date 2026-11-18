<script setup>
/**
 * What a model box on the chart does, chosen by clicking it: a calibration or analysis opens
 * its tab on the left (the box's model loaded as the study, the tab's next run making the
 * box that step); a simulation opens its form; choosing parameters by hand waits for CA.
 */
import Button from 'primevue/button'
import Dialog from 'primevue/dialog'

defineProps({
  // The model box ({id, model}), or null.
  draft: { type: Object, default: null },
  // The left-hand tabs that make steps here (calibration, sensitivity, uq, emulator, ia).
  panels: { type: Array, default: () => [] },
  // Its model, in words.
  modelWords: { type: String, default: '' },
})
const emit = defineEmits(['choose', 'close'])

const PANELS = {
  calibration: ['Calibrate', 'fit its parameters to data (the Calibration tab)'],
  sensitivity: ['Sensitivity', 'Sobol or local sensitivity (the Sensitivity tab)'],
  uq: ['Uncertainty', 'MCMC or Laplace UQ (the UQ tab)'],
  emulator: ['Surrogate', 'train an emulator of it (the Emulator tab)'],
  ia: ['Identifiability', 'which parameters the data can identify (the Identifiability tab)'],
}
</script>

<template>
  <Dialog
    :visible="!!draft"
    modal
    :header="draft ? `What should ${draft.id} do?` : ''"
    :style="{ width: 'min(560px, 95vw)' }"
    data-testid="workflow-draft-choice"
    @update:visible="(v) => !v && emit('close')"
  >
    <template v-if="draft">
      <p class="dc-hint">On {{ modelWords || 'the target' }}.</p>
      <div class="dc-list">
        <button
          v-for="p in panels.filter((x) => PANELS[x])"
          :key="p"
          type="button"
          class="dc-item"
          :data-testid="`workflow-draft-choice-${p}`"
          @click="emit('choose', { panel: p })"
        >
          <strong>{{ PANELS[p][0] }}</strong><span>{{ PANELS[p][1] }}</span>
        </button>
        <button
          type="button"
          class="dc-item"
          data-testid="workflow-draft-choice-simulation"
          @click="emit('choose', { simulation: true })"
        >
          <strong>Simulation</strong><span>run it, sweeping parameters if wanted (a form)</span>
        </button>
        <button type="button" class="dc-item hand" disabled title="Needs a circulatory_autogen that records a choice made by hand (asked)">
          <strong>Choose parameters</strong><span>by hand, an orange box (needs a newer circulatory_autogen)</span>
        </button>
      </div>
    </template>
    <template #footer>
      <Button label="Cancel" text @click="emit('close')" />
    </template>
  </Dialog>
</template>

<style scoped>
.dc-hint {
  opacity: 0.75;
  font-size: 0.85em;
  margin-top: 0;
}
.dc-list {
  display: flex;
  flex-direction: column;
  gap: 0.35rem;
}
.dc-item {
  display: flex;
  gap: 0.6rem;
  align-items: baseline;
  text-align: left;
  padding: 0.45rem 0.7rem;
  border: 1px solid var(--p-content-border-color, #555);
  border-radius: 6px;
  background: transparent;
  color: inherit;
  font: inherit;
  cursor: pointer;
}
.dc-item:hover:not(:disabled) {
  border-color: var(--p-primary-color, #4f8cff);
}
.dc-item span {
  opacity: 0.75;
  font-size: 0.85em;
}
.dc-item:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}
.dc-item.hand {
  border-color: #e8912d;
}
</style>
