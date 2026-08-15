<script setup>
import { ref, watch } from 'vue'

/**
 * A picture the solver drew, shown as an output plot cell.
 *
 * An external python model's `extra_plots()` returns matplotlib figures — a 2D
 * FEM field, a mesh, anything a time series cannot express — which the backend
 * renders to PNG after each completed run. Chart.js has nothing to say about
 * those, so this is a sibling of PlotPanel rather than a mode of it: the same
 * head chrome (title, maximize) around an <img> instead of a canvas.
 *
 * The url carries a per-run token, so a new run is a new url and the browser
 * reloads it without any cache-busting of ours.
 */
const props = defineProps({
  url: { type: String, default: '' },
  title: { type: String, default: '' },
  // Same gate as PlotPanel: `maximizable` offers the affordance, `maximized`
  // says this cell is currently filling the window.
  maximizable: { type: Boolean, default: false },
  maximized: { type: Boolean, default: false },
})

defineEmits(['toggle-maximize'])

// A figure that failed to load is a real state, not a missing one: the run
// completed and the picture is on the server, so say what is broken and offer
// the one action that helps rather than leaving a browser's grey placeholder.
const failed = ref(false)
function onError() {
  failed.value = true
}
function onLoad() {
  failed.value = false
}
// A new url is a new run's figure: give it its own chance to load.
watch(
  () => props.url,
  () => {
    failed.value = false
  },
)

// Retry the same url. The token changes per run, so this is deliberately not a
// cache-buster — it re-requests exactly what the run produced.
const attempt = ref(0)
function retry() {
  failed.value = false
  attempt.value += 1
}

defineExpose({ failed, retry, attempt })
</script>

<template>
  <section class="image-panel" :class="{ maximized }" data-testid="image-panel">
    <div class="plot-head">
      <h3 v-if="title" class="plot-title" data-testid="image-title">{{ title }}</h3>
      <button
        v-if="maximizable"
        type="button"
        class="plot-maximize"
        :title="maximized ? 'Restore plot' : 'Maximize plot'"
        :aria-label="maximized ? 'Restore plot' : 'Maximize plot'"
        :aria-pressed="maximized"
        data-testid="image-maximize"
        @click="$emit('toggle-maximize')"
      >
        <i :class="maximized ? 'pi pi-window-minimize' : 'pi pi-window-maximize'" />
      </button>
    </div>
    <div class="image-wrap">
      <img
        v-show="!failed"
        :key="attempt"
        :src="url"
        :alt="title || 'Solver plot'"
        class="solver-image"
        data-testid="image-img"
        @error="onError"
        @load="onLoad"
      />
      <p v-if="failed" class="image-broken" data-testid="image-broken">
        <span>{{ title || 'Solver plot' }} could not be loaded.</span>
        <button type="button" class="image-retry" data-testid="image-retry" @click="retry">
          Retry
        </button>
        <small>
          It is written when the run finishes, so re-running the model produces it
          again.
        </small>
      </p>
    </div>
  </section>
</template>

<style scoped>
.image-panel {
  display: flex;
  flex-direction: column;
  height: 100%;
  padding: 0.5rem;
}
/* Deliberately the same head as PlotPanel's: a solver figure sits in the same
   grid as the charts and should not read as a different kind of object. */
.plot-head {
  display: flex;
  align-items: center;
  gap: 0.4rem;
  margin: 0 0 0.25rem;
}
.plot-title {
  margin: 0;
  font-size: 0.8rem;
  font-weight: 600;
  opacity: 0.85;
}
.plot-maximize {
  margin-left: auto;
  border: none;
  background: none;
  color: inherit;
  cursor: pointer;
  opacity: 0.5;
  font-size: 0.8rem;
  line-height: 1;
  padding: 0.1rem 0.25rem;
}
.plot-maximize:hover {
  opacity: 1;
}
/* Matches PlotPanel's chart area: a fixed height in the grid, filling the
   window when maximized. */
.image-wrap {
  display: flex;
  align-items: center;
  justify-content: center;
  overflow: auto;
  flex: 0 0 var(--plot-chart-height, 220px);
  height: var(--plot-chart-height, 220px);
  min-height: 160px;
}
.image-panel.maximized .image-wrap {
  flex: 1 1 auto;
  height: auto;
}
.solver-image {
  max-width: 100%;
  max-height: 100%;
  object-fit: contain;
}
.image-broken {
  margin: 0;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 0.3rem;
  font-size: 0.8rem;
  opacity: 0.75;
  text-align: center;
}
.image-retry {
  border: 1px solid var(--p-content-border-color, #555);
  border-radius: 4px;
  background: transparent;
  color: inherit;
  font: inherit;
  font-size: 0.78rem;
  padding: 0.1rem 0.5rem;
  cursor: pointer;
}
.image-retry:hover {
  border-color: var(--p-primary-color, #5b9bd5);
}
.image-broken small {
  opacity: 0.8;
  font-size: 0.7rem;
}
</style>
