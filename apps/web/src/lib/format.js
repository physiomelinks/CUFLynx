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
