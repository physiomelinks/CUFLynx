import { ref, computed } from 'vue'
import { listSavedRuns, loadSavedRun } from '../lib/api'
import { PALETTE } from '../lib/plot'

/**
 * Saved runs the user can overlay on the plots (issue #126).
 *
 * "Save current" writes the slider values plus the traces they produced; this
 * tracks what is on disk, which of those are ticked, and the colour each is
 * drawn in — the tick box, the overlay trace and the slider marker all use that
 * one colour, which is what lets the eye tie them together.
 *
 * Series are fetched lazily, on the first tick: the list only needs metadata to
 * render checkboxes, and a run holds every plotted trace.
 */

// Saved runs are offset in the palette so they don't open on the same hue as
// the live trace they are being compared against.
const SAVED_COLOR_OFFSET = 2

export function useSavedRuns() {
  // Metadata for every saved run in the output dir, newest first.
  const runs = ref([])
  // prefix -> the loaded record ({params, time, outputs} or {experiments}).
  const series = ref({})
  // Prefixes currently ticked, in tick order — the order fixes the colours, so
  // a run keeps its colour while it stays shown.
  const shown = ref([])
  const error = ref('')

  function colorFor(prefix) {
    const i = shown.value.indexOf(prefix)
    if (i < 0) return ''
    return PALETTE[(i + SAVED_COLOR_OFFSET) % PALETTE.length]
  }

  const isShown = (prefix) => shown.value.includes(prefix)

  /** Saved runs with their display state, for the checkbox list. */
  const items = computed(() =>
    runs.value.map((r) => ({
      ...r,
      shown: isShown(r.prefix),
      color: colorFor(r.prefix),
    })),
  )

  /** Re-read the output directory. Never throws: an unreadable dir just lists nothing. */
  async function refresh(outputDir = '') {
    try {
      const { runs: found } = await listSavedRuns(outputDir)
      runs.value = found ?? []
      // Drop ticks whose file has gone, so a stale tick can't hold a colour.
      const alive = new Set(runs.value.map((r) => r.prefix))
      shown.value = shown.value.filter((p) => alive.has(p))
    } catch {
      runs.value = []
    }
  }

  /**
   * Tick / untick a saved run, loading its series the first time it is shown.
   * Returns true when it ended up shown.
   */
  async function toggle(prefix) {
    if (isShown(prefix)) {
      shown.value = shown.value.filter((p) => p !== prefix)
      return false
    }
    if (!series.value[prefix]) {
      const run = runs.value.find((r) => r.prefix === prefix)
      if (!run) return false
      try {
        series.value = { ...series.value, [prefix]: await loadSavedRun(run.path) }
      } catch (e) {
        error.value = `Could not load saved run ${prefix}: ${e?.message || e}`
        return false
      }
    }
    shown.value = [...shown.value, prefix]
    return true
  }

  /**
   * The shown runs' traces for one variable, as the plot lib wants them.
   *
   * `expIdx` selects the experiment for a protocol run. A saved run recorded
   * with fewer experiments than are now on screen simply contributes nothing to
   * the extra ones, rather than borrowing experiment 0's trace for them.
   */
  function seriesFor(qname, expIdx = null) {
    const out = []
    for (const prefix of shown.value) {
      const rec = series.value[prefix]
      if (!rec) continue
      let source = rec
      if (expIdx !== null && Array.isArray(rec.experiments)) {
        source = rec.experiments[expIdx]
        if (!source) continue
      }
      const values = source?.outputs?.[qname]
      if (!values?.length) continue
      out.push({
        prefix,
        color: colorFor(prefix),
        time: source.time ?? [],
        values,
      })
    }
    return out
  }

  /** Shown runs' saved value for one parameter: [{prefix, color, value}]. */
  function markersFor(qname) {
    const out = []
    for (const prefix of shown.value) {
      const value = series.value[prefix]?.params?.[qname]
      if (typeof value !== 'number' || !Number.isFinite(value)) continue
      out.push({ prefix, color: colorFor(prefix), value })
    }
    return out
  }

  /** Forget everything — a new model's saved runs are a different set. */
  function clear() {
    runs.value = []
    series.value = {}
    shown.value = []
    error.value = ''
  }

  return {
    runs,
    items,
    shown,
    series,
    error,
    colorFor,
    isShown,
    refresh,
    toggle,
    seriesFor,
    markersFor,
    clear,
  }
}
