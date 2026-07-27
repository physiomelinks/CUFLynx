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

/** Concise variant for plot axis ticks (fewer digits, short labels). */
export function fmtAxis(v) {
  return fmtSci(v, 1)
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
