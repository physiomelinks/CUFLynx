<script setup>
import { computed } from 'vue'
import Slider from 'primevue/slider'
import Button from 'primevue/button'
import { SLIDER_STEPS, valueToSlider, sliderToValue, positionFor } from '../stores/useSliders'
import { renderMath } from '../lib/math'

const props = defineProps({
  sliders: { type: Object, default: () => ({}) },
  // Whether a calibration best-fit is available (gates "Reset to best fit").
  hasBestFit: { type: Boolean, default: false },
  // Saved runs available to overlay (#126): [{prefix, shown, color, params}].
  savedRuns: { type: Array, default: () => [] },
})
const emit = defineEmits([
  'update',
  'remove',
  'reset-init',
  'reset-best',
  'save-current',
  'reset-saved',
  'toggle-saved',
])

const entries = computed(() => Object.values(props.sliders))

// Only the ticked runs mark the sliders — an untickable list of every saved run
// would bury the handle.
const shownRuns = computed(() => props.savedRuns.filter((r) => r.shown))

/**
 * Where each shown run's saved value sits on this slider's track, as a percent
 * (#126). Uses the slider's own value->position mapping, so a marker lands
 * exactly where the handle would for that value — including on a log slider.
 *
 * A run that has no value for this parameter contributes no marker rather than
 * a misleading one at zero.
 */
function markersFor(s) {
  const out = []
  for (const run of shownRuns.value) {
    const value = run.params?.[s.qname]
    if (typeof value !== 'number' || !Number.isFinite(value)) continue
    out.push({
      prefix: run.prefix,
      color: run.color,
      value,
      // Clamped by positionFor, so a saved value outside the current range
      // pins to the end it lies beyond instead of drawing off-track.
      percent: (positionFor(s, value) / SLIDER_STEPS) * 100,
      outOfRange: value < s.min || value > s.max,
    })
  }
  return out
}

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
        <div class="slider-track">
          <Slider
            :model-value="valueToSlider(s)"
            :min="0"
            :max="SLIDER_STEPS"
            :step="1"
            @update:model-value="onPosition(s, $event)"
          />
          <!--
            Where each shown saved run had this parameter (#126). Deliberately
            not draggable — it records where a run *was*, so letting it be moved
            would imply it could be edited. pointer-events:none also keeps it
            from stealing drags aimed at the handle underneath.
          -->
          <span
            v-for="m in markersFor(s)"
            :key="m.prefix"
            class="saved-marker"
            :class="{ 'out-of-range': m.outOfRange }"
            :style="{ left: `${m.percent}%`, color: m.color }"
            :title="
              m.outOfRange
                ? `${m.prefix}: ${m.value} (outside the current range)`
                : `${m.prefix}: ${m.value}`
            "
            data-testid="saved-marker"
            aria-hidden="true"
            >&times;</span
          >
        </div>
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

    <!-- Commands below the parameters, two per row (issue #106). -->
    <div class="param-commands" data-testid="param-commands">
      <Button
        label="Reset to init"
        icon="pi pi-undo"
        size="small"
        outlined
        data-testid="reset-init"
        title="Reset all parameter values to their initial values"
        :disabled="entries.length === 0"
        @click="emit('reset-init')"
      />
      <Button
        label="Reset to best fit"
        icon="pi pi-star"
        size="small"
        outlined
        data-testid="reset-best"
        title="Reset all parameter values to the latest calibration best-fit"
        :disabled="!hasBestFit || entries.length === 0"
        @click="emit('reset-best')"
      />
      <Button
        label="Save current"
        icon="pi pi-save"
        size="small"
        outlined
        data-testid="save-current"
        title="Save the current parameter values to a file (.npy or .csv)"
        :disabled="entries.length === 0"
        @click="emit('save-current')"
      />
      <Button
        label="Reset to saved"
        icon="pi pi-folder-open"
        size="small"
        outlined
        data-testid="reset-saved"
        title="Load parameter values from a saved .npy or .csv file"
        :disabled="entries.length === 0"
        @click="emit('reset-saved')"
      />
    </div>

    <!--
      Saved runs, ticked to overlay them on the plots (#126). The tick box takes
      the colour that run's traces are drawn in, so the list doubles as the
      legend; the title carries the file prefix, which is too long to show inline
      for every row.
    -->
    <div v-if="savedRuns.length" class="saved-runs" data-testid="saved-runs">
      <h3 class="saved-runs-title">Show saved</h3>
      <label
        v-for="run in savedRuns"
        :key="run.prefix"
        class="saved-run"
        :title="run.prefix"
        data-testid="saved-run"
      >
        <input
          type="checkbox"
          class="saved-check"
          :checked="run.shown"
          :style="run.shown ? { accentColor: run.color } : {}"
          :data-prefix="run.prefix"
          data-testid="saved-run-check"
          @change="emit('toggle-saved', run.prefix)"
        />
        <span class="saved-run-name">{{ run.prefix }}</span>
      </label>
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
/* Two commands per row, below the parameter list (issue #106). */
.param-commands {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 0.4rem;
  margin-top: 0.25rem;
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
/* The track wrapper positions the saved-run markers over the slider; it takes
   the flex sizing the Slider itself used to have. */
.slider-track {
  position: relative;
  flex: 1 1 auto;
  min-width: 8rem;
}
/* The PrimeVue Slider has no intrinsic width; let it fill the row so it sits
   beside the value input instead of collapsing into a single point. */
.slider-body :deep(.p-slider) {
  width: 100%;
}
/* A saved run's parameter value (#126): a cross in that run's colour, centred
   on the position the handle would take. pointer-events:none is load-bearing —
   without it the marker swallows drags aimed at the handle beneath it. */
.saved-marker {
  position: absolute;
  top: 50%;
  transform: translate(-50%, -50%);
  pointer-events: none;
  font-size: 1rem;
  line-height: 1;
  font-weight: 700;
  text-shadow: 0 0 2px var(--p-content-background, #fff);
}
/* Pinned to the end of the track rather than sitting at a true position. */
.saved-marker.out-of-range {
  opacity: 0.45;
}
.saved-runs {
  display: flex;
  flex-direction: column;
  gap: 0.15rem;
  margin-top: 0.25rem;
}
.saved-runs-title {
  margin: 0;
  font-size: 0.75rem;
  text-transform: uppercase;
  letter-spacing: 0.04em;
  opacity: 0.6;
}
.saved-run {
  display: flex;
  align-items: center;
  gap: 0.4rem;
  font-size: 0.8rem;
  cursor: pointer;
}
.saved-check {
  cursor: pointer;
  flex: 0 0 auto;
}
/* Prefixes can be long; the full one is in the row's title. */
.saved-run-name {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
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
