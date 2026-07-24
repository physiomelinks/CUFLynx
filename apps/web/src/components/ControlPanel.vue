<script setup>
import { computed } from 'vue'
import Slider from 'primevue/slider'
import Button from 'primevue/button'
import { SLIDER_STEPS, valueToSlider, sliderToValue } from '../stores/useSliders'
import { renderMath } from '../lib/math'

const props = defineProps({
  sliders: { type: Object, default: () => ({}) },
  // Whether a calibration best-fit is available (gates "Reset to best fit").
  hasBestFit: { type: Boolean, default: false },
  // Whether a user-saved snapshot exists (gates "Reset to saved" / "Export").
  hasSaved: { type: Boolean, default: false },
})
const emit = defineEmits([
  'update',
  'remove',
  'import-csv',
  'reset-init',
  'reset-best',
  'save-snapshot',
  'reset-saved',
  'export-snapshot',
])

const entries = computed(() => Object.values(props.sliders))

// The Slider operates on an integer [0, SLIDER_STEPS] track; values are mapped
// to/from that position so log-scale params spread across the whole track
// instead of bunching against the left edge.
function onPosition(s, pos) {
  emit('update', { qname: s.qname, value: sliderToValue(s, Number(pos)) })
}

function onValue(qname, value) {
  emit('update', { qname, value: Number(value) })
}
</script>

<template>
  <section class="control-panel">
    <header class="panel-header">
      <h2>Parameters</h2>
      <div class="panel-actions">
        <Button
          label="Reset to init"
          icon="pi pi-undo"
          size="small"
          text
          data-testid="reset-init"
          title="Reset all parameter values to their initial values"
          :disabled="entries.length === 0"
          @click="emit('reset-init')"
        />
        <Button
          label="Reset to best fit"
          icon="pi pi-star"
          size="small"
          text
          data-testid="reset-best"
          title="Reset all parameter values to the latest calibration best-fit"
          :disabled="!hasBestFit || entries.length === 0"
          @click="emit('reset-best')"
        />
        <Button
          label="Save current"
          icon="pi pi-bookmark"
          size="small"
          text
          data-testid="save-snapshot"
          title="Lock in the current parameter values as a saved snapshot"
          :disabled="entries.length === 0"
          @click="emit('save-snapshot')"
        />
        <Button
          label="Reset to saved"
          icon="pi pi-history"
          size="small"
          text
          data-testid="reset-saved"
          title="Reset all parameter values to the saved snapshot"
          :disabled="!hasSaved || entries.length === 0"
          @click="emit('reset-saved')"
        />
        <Button
          label="Export values"
          icon="pi pi-download"
          size="small"
          text
          data-testid="export-snapshot"
          title="Download the saved parameter values as a CSV"
          :disabled="!hasSaved"
          @click="emit('export-snapshot')"
        />
        <Button
          label="Import CSV"
          icon="pi pi-upload"
          size="small"
          text
          data-testid="import-csv"
          @click="emit('import-csv')"
        />
      </div>
    </header>

    <p v-if="entries.length === 0" class="empty-hint">
      No active sliders. Add a parameter from the variable list or import a
      params_for_id.csv file.
    </p>

    <div
      v-for="s in entries"
      :key="s.qname"
      class="slider-row"
      data-testid="slider-row"
    >
      <div class="slider-label">
        <span class="qname" :title="s.qname" v-html="renderMath(s.name_for_plotting)" />
        <Button
          icon="pi pi-times"
          text
          rounded
          size="small"
          aria-label="remove"
          @click="emit('remove', { qname: s.qname })"
        />
      </div>
      <div class="slider-body">
        <Slider
          :model-value="valueToSlider(s)"
          :min="0"
          :max="SLIDER_STEPS"
          :step="1"
          @update:model-value="onPosition(s, $event)"
        />
        <input
          type="number"
          class="value-input"
          data-testid="value-input"
          :value="s.value"
          :min="s.min"
          :max="s.max"
          @input="onValue(s.qname, $event.target.value)"
        />
      </div>
      <div class="range-hint">[{{ s.min }}, {{ s.max }}]</div>
    </div>
  </section>
</template>

<style scoped>
.control-panel {
  display: flex;
  flex-direction: column;
  gap: 0.75rem;
  padding: 0.75rem;
  overflow-y: auto;
}
.panel-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 0.5rem;
}
.panel-actions {
  display: flex;
  align-items: center;
  gap: 0.25rem;
  flex-wrap: wrap;
  justify-content: flex-end;
}
.slider-row {
  border: 1px solid var(--p-content-border-color, #333);
  border-radius: 6px;
  padding: 0.5rem;
}
.slider-label {
  display: flex;
  align-items: center;
  justify-content: space-between;
}
.slider-body {
  display: flex;
  align-items: center;
  gap: 0.75rem;
  margin: 0.5rem 0;
}
/* The PrimeVue Slider has no intrinsic width; let it fill the row so it sits
   beside the value input instead of collapsing into a single point. */
.slider-body :deep(.p-slider) {
  flex: 1 1 auto;
  min-width: 8rem;
}
.value-input {
  flex: 0 0 5.5rem;
  width: 5.5rem;
}
.range-hint {
  font-size: 0.75rem;
  opacity: 0.6;
}
.empty-hint {
  opacity: 0.6;
  font-size: 0.85rem;
}
</style>
