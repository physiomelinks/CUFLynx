import { ref, computed } from 'vue'
import { listSavedRuns, loadSavedRun } from '../lib/api'
import { SAVED_PALETTE } from '../lib/plot'

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

export function useSavedRuns() {
  // Metadata for every saved run in the output dir, newest first.
  const runs = ref([])
  // Runs not backed by a file — currently the live calibration best fit (#126).
  // Each is {prefix, params, load}: `load` produces the record on demand,
  // because a best fit is a set of parameter values with no traces until
  // something runs the model at them.
  const virtual = ref([])
  // prefix -> the loaded record ({params, time, outputs} or {experiments}).
  const series = ref({})
  // Prefixes currently ticked, in tick order — the order fixes the colours, so
  // a run keeps its colour while it stays shown.
  const shown = ref([])
  const error = ref('')

  function colorFor(prefix) {
    const i = shown.value.indexOf(prefix)
    if (i < 0) return ''
    return SAVED_PALETTE[i % SAVED_PALETTE.length]
  }

  const isShown = (prefix) => shown.value.includes(prefix)

  /**
   * Everything tickable, for the checkbox list. Virtual runs lead: the best fit
   * is the one most often compared against, and it stays put while the file
   * list changes underneath.
   */
  const items = computed(() =>
    [
      ...virtual.value.map((v) => ({
        prefix: v.prefix,
        params: v.params,
        virtual: true,
        title: v.title || v.prefix,
      })),
      ...runs.value,
    ].map((r) => ({
      ...r,
      shown: isShown(r.prefix),
      color: colorFor(r.prefix),
    })),
  )

  const isVirtual = (prefix) => virtual.value.some((v) => v.prefix === prefix)

  /**
   * Register (or replace) a virtual run. Replacing one drops its cached record,
   * so a re-tick re-derives it — a new calibration best fit is a different run
   * under the same name, and serving the old traces would be a lie.
   */
  function setVirtualRun(entry) {
    const rest = virtual.value.filter((v) => v.prefix !== entry.prefix)
    virtual.value = [entry, ...rest]
    if (series.value[entry.prefix]) {
      const { [entry.prefix]: _dropped, ...keep } = series.value
      series.value = keep
      // It was on screen showing the previous fit; take it down rather than
      // leave a stale trace labelled "best fit".
      shown.value = shown.value.filter((p) => p !== entry.prefix)
    }
  }

  function removeVirtualRun(prefix) {
    virtual.value = virtual.value.filter((v) => v.prefix !== prefix)
    shown.value = shown.value.filter((p) => p !== prefix)
  }

  /** Re-read the output directory. Never throws: an unreadable dir just lists nothing. */
  async function refresh(outputDir = '') {
    try {
      const { runs: found } = await listSavedRuns(outputDir)
      runs.value = found ?? []
    } catch {
      runs.value = []
    }
    // Drop ticks whose file has gone, so a stale tick can't hold a colour.
    // Virtual runs have no file to disappear, so they survive a refresh.
    const alive = new Set([
      ...runs.value.map((r) => r.prefix),
      ...virtual.value.map((v) => v.prefix),
    ])
    shown.value = shown.value.filter((p) => alive.has(p))
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
      const entry = virtual.value.find((v) => v.prefix === prefix)
      const run = entry ? null : runs.value.find((r) => r.prefix === prefix)
      if (!entry && !run) return false
      try {
        // A virtual run derives its record (the best fit has to be simulated);
        // a saved one reads it off disk.
        const record = entry ? await entry.load() : await loadSavedRun(run.path)
        if (!record) return false
        series.value = { ...series.value, [prefix]: record }
      } catch (e) {
        error.value = `Could not load ${prefix}: ${e?.message || e}`
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
    virtual.value = []
    series.value = {}
    shown.value = []
    error.value = ''
  }

  return {
    runs,
    virtual,
    items,
    shown,
    series,
    error,
    colorFor,
    isShown,
    isVirtual,
    setVirtualRun,
    removeVirtualRun,
    refresh,
    toggle,
    seriesFor,
    markersFor,
    clear,
  }
}
