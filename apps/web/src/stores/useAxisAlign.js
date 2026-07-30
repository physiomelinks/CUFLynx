import { reactive, computed } from 'vue'

/**
 * Shared y-axis width so plots in a window line up (issue #145).
 *
 * Chart.js sizes each y axis to its own tick labels, so a plot showing `1.5e-9`
 * reserves more left margin than one showing `20`. Side by side in a grid the
 * plot areas then start at different x, and traces that share a time axis do not
 * line up — which is the whole point of putting them beside each other.
 *
 * Each plot reports the width it *would* take, and every plot is then widened to
 * the largest. Widening only: a plot is never squeezed below the width its own
 * labels need, so nothing is ever clipped.
 *
 * Phase-plane plots (#124) are excluded by the caller — their x axis is another
 * variable, so aligning them against time plots would line up axes that have
 * nothing to do with each other.
 */
export function useAxisAlign() {
  // key -> the width that plot's own labels need.
  const widths = reactive({})

  /** Report a plot's natural y-axis width. */
  function report(key, width) {
    if (!key || !Number.isFinite(width)) return
    // Round to whole pixels: sub-pixel jitter between re-layouts would otherwise
    // keep changing the max and re-trigger everyone's layout forever.
    const w = Math.ceil(width)
    if (widths[key] !== w) widths[key] = w
  }

  /** Forget a plot (unmounted, or switched to a non-time x axis). */
  function forget(key) {
    if (key in widths) delete widths[key]
  }

  /** The width every aligned plot should use; 0 when nothing has reported. */
  const maxWidth = computed(() => {
    const all = Object.values(widths)
    return all.length ? Math.max(...all) : 0
  })

  function clear() {
    for (const k of Object.keys(widths)) delete widths[k]
  }

  return { widths, report, forget, maxWidth, clear }
}
