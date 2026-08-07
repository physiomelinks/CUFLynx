import { describe, it, expect } from 'vitest'
import { evalPriorDefault, formatPriorDefault } from './priorDefaults'

describe('evalPriorDefault', () => {
  it("computes CA's declared defaults", () => {
    expect(evalPriorDefault('(min + max) / 2', { min: 1, max: 2 })).toBe(1.5)
    expect(evalPriorDefault('(max - min) / 6', { min: 0, max: 6 })).toBe(1)
    expect(evalPriorDefault('max / prior_lambda', { max: 10, prior_lambda: 2 })).toBe(5)
    expect(evalPriorDefault('0', {})).toBe(0)
  })

  it('honours precedence and parentheses', () => {
    expect(evalPriorDefault('1 + 2 * 3', {})).toBe(7)
    expect(evalPriorDefault('(1 + 2) * 3', {})).toBe(9)
    expect(evalPriorDefault('-max', { max: 4 })).toBe(-4)
  })

  it('gives no answer when a name it needs is missing', () => {
    // An unbounded row has no max, so nothing is invented from one.
    expect(evalPriorDefault('(min + max) / 2', { min: 1 })).toBeNull()
    expect(evalPriorDefault('(min + max) / 2', { min: 1, max: null })).toBeNull()
  })

  it('gives no answer for a division by zero', () => {
    expect(evalPriorDefault('max / prior_lambda', { max: 1, prior_lambda: 0 })).toBeNull()
  })

  it('does arithmetic and nothing else', () => {
    // A parser, not eval: the strings come from CA's schema, but one that can only
    // add and divide cannot become anything else.
    for (const hostile of [
      'globalThis',
      'max.constructor',
      'alert(1)',
      'max; alert(1)',
      '[1,2]',
      '`${max}`',
    ]) {
      expect(evalPriorDefault(hostile, { min: 1, max: 2 })).toBeNull()
    }
  })

  it('rejects a malformed expression rather than guessing', () => {
    for (const bad of ['(1 + 2', '1 +', '', null, undefined]) {
      expect(evalPriorDefault(bad, { min: 1, max: 2 })).toBeNull()
    }
  })
})

describe('formatPriorDefault', () => {
  it('does not pretend to more precision than is meaningful', () => {
    expect(formatPriorDefault(1 / 6)).toBe('0.1667')
    expect(formatPriorDefault(1.5)).toBe('1.5')
    expect(formatPriorDefault(0)).toBe('0')
  })

  it('uses exponent notation at the extremes', () => {
    expect(formatPriorDefault(1e-8)).toContain('e')
    expect(formatPriorDefault(5e9)).toContain('e')
  })

  it('passes null through', () => {
    expect(formatPriorDefault(null)).toBeNull()
  })
})
