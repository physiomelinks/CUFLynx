import { describe, it, expect } from 'vitest'
import { fmtSci, fmtAxis } from './format'

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
