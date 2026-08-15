// Number formatting shared by the params_to_change editor and its plot.
//
// Physiological parameters span a huge range (compliances ~1e-9, resistances
// large), so a plain decimal like 0.0000000015 or 1500000 is unreadable. Show
// scientific notation for large or small magnitudes, plain otherwise.

const BIG = 1e4 // >= this -> scientific
const SMALL = 1e-2 // (nonzero) < this -> scientific

/**
 * Format a number for display, using scientific notation for very large or very
 * small magnitudes. Returns '' for null/blank/non-finite so inputs show empty.
 * @param {number|string|null} v
 * @param {number} digits significant digits after the point in scientific form
 */
export function fmtSci(v, digits = 4) {
  if (v === null || v === undefined || v === '') return ''
  const n = Number(v)
  if (!Number.isFinite(n)) return ''
  const a = Math.abs(n)
  if (a !== 0 && (a >= BIG || a < SMALL)) {
    // Trim trailing zeros in the mantissa: 1.5000e-8 -> 1.5e-8.
    return n
      .toExponential(digits)
      .replace(/\.?0+e/, 'e')
      .replace('e+', 'e')
  }
  return String(n)
}

/**
 * Concise variant for plot axis ticks (fewer digits, short labels).
 *
 * Bounded to 2 significant figures on BOTH branches (via fmtSigFigs), because
 * Chart.js auto-ticks carry float artefacts — 0.30000000000000004 — and
 * fmtSci's plain branch prints them in full (issue: 10+ decimal places on the
 * heat1d y axis).
 */
export function fmtAxis(v) {
  return fmtSigFigs(v, 2)
}

/**
 * Axis-tick formatter with collision widening. 2 significant figures is right
 * for the common 0-anchored axis, but on a narrow offset range (ticks
 * 292, 294, 296…) it would label every tick "290"/"300" — same label, wrong
 * value. Given the full tick list (Chart.js passes it as the callback's third
 * argument), widen the precision just enough that every tick keeps a distinct
 * label, and no further.
 * @param {number} v the tick being formatted
 * @param {Array<{value: number}|number>} ticks the axis's full tick list
 */
export function fmtAxisTick(v, ticks) {
  const values = Array.isArray(ticks)
    ? ticks.map((t) => (t !== null && typeof t === 'object' ? t.value : t)).filter(Number.isFinite)
    : []
  if (values.length < 2) return fmtAxis(v)
  for (let sf = 2; sf <= 8; sf++) {
    const labels = values.map((x) => fmtSigFigs(x, sf))
    if (new Set(labels).size === values.length) return fmtSigFigs(v, sf)
  }
  return fmtSigFigs(v, 8)
}

/**
 * Format to a fixed number of *significant figures*, in the same scientific/plain
 * split as fmtSci. Unlike fmtSci — whose `digits` counts mantissa decimals and
 * whose plain branch prints the number in full — this bounds the precision on
 * both branches, so a float artefact like 0.35000000000000003 reads as 0.35.
 * Used for the cursor's time value, where a handful of figures is plenty.
 * @param {number|string|null} v
 * @param {number} sf significant figures
 */
export function fmtSigFigs(v, sf = 3) {
  if (v === null || v === undefined || v === '') return ''
  const n = Number(v)
  if (!Number.isFinite(n)) return ''
  const a = Math.abs(n)
  if (a !== 0 && (a >= BIG || a < SMALL)) {
    return n
      .toExponential(sf - 1)
      .replace(/\.?0+e/, 'e')
      .replace('e+', 'e')
  }
  // Round to sf, then re-parse so trailing zeros collapse: 2.50 -> 2.5, 1230 stays
  // 1230 (toPrecision would render it as 1.23e+3).
  return String(Number(n.toPrecision(sf)))
}
