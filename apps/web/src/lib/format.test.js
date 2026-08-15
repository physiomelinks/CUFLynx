import { describe, it, expect } from 'vitest'
import { fmtSci, fmtAxis, fmtAxisTick, fmtSigFigs } from './format'

describe('fmtSci', () => {
  it('uses scientific notation for very small magnitudes (e.g. compliances)', () => {
    expect(fmtSci(1e-9)).toBe('1e-9')
    expect(fmtSci(5e-8)).toBe('5e-8')
    expect(fmtSci(200e-6)).toBe('2e-4')
    expect(fmtSci(-2e-3)).toBe('-2e-3')
  })

  it('uses scientific notation for large magnitudes', () => {
    expect(fmtSci(1_500_000)).toBe('1.5e6')
    expect(fmtSci(1e6)).toBe('1e6')
  })

  it('keeps moderate values plain', () => {
    expect(fmtSci(0.5)).toBe('0.5')
    expect(fmtSci(50)).toBe('50')
    expect(fmtSci(1500)).toBe('1500')
    expect(fmtSci(0)).toBe('0')
  })

  it('returns empty for blank / non-finite so inputs show nothing', () => {
    expect(fmtSci(null)).toBe('')
    expect(fmtSci('')).toBe('')
    expect(fmtSci(undefined)).toBe('')
    expect(fmtSci(NaN)).toBe('')
  })

  it('parses back to the same number (round-trips through Number())', () => {
    for (const v of [1e-9, 5e-8, 2e-4, 1.5e6, 0.5, 1500]) {
      expect(Number(fmtSci(v))).toBeCloseTo(v, 20)
    }
  })
})

describe('fmtAxis', () => {
  it('is a concise (1-digit) scientific form for axis ticks', () => {
    expect(fmtAxis(1.5e-8)).toBe('1.5e-8')
    expect(fmtAxis(1e-9)).toBe('1e-9')
    expect(fmtAxis(50)).toBe('50')
  })
})

describe('fmtSigFigs', () => {
  it('rounds plain-range values to the requested significant figures', () => {
    expect(fmtSigFigs(0.123456, 3)).toBe('0.123')
    expect(fmtSigFigs(1.23456, 3)).toBe('1.23')
    expect(fmtSigFigs(1234.5, 3)).toBe('1230')
  })

  it('bounds precision where fmtSci prints the float in full', () => {
    // fmtSci's plain branch is String(n), so a float artefact leaks through.
    expect(fmtSci(0.35000000000000003)).toBe('0.35000000000000003')
    expect(fmtSigFigs(0.35000000000000003, 3)).toBe('0.35')
  })

  it('drops trailing zeros rather than padding to the figure count', () => {
    expect(fmtSigFigs(2.5, 3)).toBe('2.5')
    expect(fmtSigFigs(2, 3)).toBe('2')
    expect(fmtSigFigs(0, 3)).toBe('0')
  })

  it('keeps the scientific branch for extreme magnitudes', () => {
    expect(fmtSigFigs(1.23456e-9, 3)).toBe('1.23e-9')
    expect(fmtSigFigs(1.5e-9, 3)).toBe('1.5e-9')
    expect(fmtSigFigs(1.23456e6, 3)).toBe('1.23e6')
  })

  it('defaults to 3 significant figures', () => {
    expect(fmtSigFigs(0.123456)).toBe(fmtSigFigs(0.123456, 3))
  })

  it('returns empty for blank / non-finite input, like fmtSci', () => {
    expect(fmtSigFigs(null)).toBe('')
    expect(fmtSigFigs('')).toBe('')
    expect(fmtSigFigs(NaN)).toBe('')
    expect(fmtSigFigs(Infinity)).toBe('')
  })
})

describe('fmtAxis (2 significant figures)', () => {
  it('collapses float artefacts that fmtSci printed in full (heat1d y axis)', () => {
    expect(fmtAxis(0.30000000000000004)).toBe('0.3')
    expect(fmtAxis(0.7000000000000001)).toBe('0.7')
  })

  it('bounds plain-range ticks to 2 significant figures', () => {
    expect(fmtAxis(0.123456)).toBe('0.12')
    expect(fmtAxis(987.6)).toBe('990')
  })

  it('keeps short labels short', () => {
    expect(fmtAxis(0.5)).toBe('0.5')
    expect(fmtAxis(50)).toBe('50')
    expect(fmtAxis(0)).toBe('0')
  })
})

describe('fmtAxisTick', () => {
  const t = (vals) => vals.map((value) => ({ value }))

  it('formats at 2 significant figures when labels stay distinct', () => {
    const ticks = t([0, 0.2, 0.4, 0.6000000000000001, 0.8, 1])
    expect(fmtAxisTick(0.6000000000000001, ticks)).toBe('0.6')
    expect(fmtAxisTick(0.2, ticks)).toBe('0.2')
  })

  it('widens precision on a narrow offset range so ticks keep distinct labels', () => {
    const ticks = t([292, 294, 296, 298])
    // At 2 sig figs every tick would read "290" or "300" — same label, wrong value.
    expect(fmtAxisTick(294, ticks)).toBe('294')
    expect(fmtAxisTick(298, ticks)).toBe('298')
  })

  it('widens only as far as needed', () => {
    const ticks = t([0.98, 0.99, 1.0, 1.01, 1.02])
    expect(fmtAxisTick(1.01, ticks)).toBe('1.01')
  })

  it('falls back to plain fmtAxis without tick context', () => {
    expect(fmtAxisTick(0.30000000000000004, undefined)).toBe('0.3')
    expect(fmtAxisTick(0.30000000000000004, [])).toBe('0.3')
  })
})
