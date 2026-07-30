import { describe, it, expect } from 'vitest'
import { useAxisAlign } from './useAxisAlign'

describe('useAxisAlign (#145)', () => {
  it('is inert until something reports', () => {
    expect(useAxisAlign().maxWidth.value).toBe(0)
  })

  it('reports the widest, which is the one everyone must match', () => {
    const a = useAxisAlign()
    a.report('p1', 40)
    a.report('p2', 62)
    a.report('p3', 51)
    expect(a.maxWidth.value).toBe(62)
  })

  it('tracks a plot shrinking as well as growing', () => {
    const a = useAxisAlign()
    a.report('p1', 62)
    a.report('p2', 40)
    expect(a.maxWidth.value).toBe(62)
    a.report('p1', 30) // its labels got shorter
    expect(a.maxWidth.value).toBe(40)
  })

  // Otherwise a removed plot goes on padding everything else out to a width
  // nothing needs any more.
  it('forgets a plot that is gone', () => {
    const a = useAxisAlign()
    a.report('p1', 62)
    a.report('p2', 40)
    a.forget('p1')
    expect(a.maxWidth.value).toBe(40)
    a.forget('nope') // no such plot: harmless
    expect(a.maxWidth.value).toBe(40)
  })

  // Sub-pixel jitter between re-layouts would keep changing the max and
  // re-trigger every plot's layout, forever.
  it('rounds up to whole pixels so a re-layout cannot oscillate', () => {
    const a = useAxisAlign()
    a.report('p1', 40.2)
    expect(a.maxWidth.value).toBe(41)
    a.report('p1', 40.4)
    expect(a.widths.p1).toBe(41)
  })

  it('ignores a missing key or a non-finite width', () => {
    const a = useAxisAlign()
    a.report('', 40)
    a.report('p1', NaN)
    a.report('p2', Infinity)
    expect(a.maxWidth.value).toBe(0)
  })

  it('clears', () => {
    const a = useAxisAlign()
    a.report('p1', 40)
    a.clear()
    expect(a.maxWidth.value).toBe(0)
  })
})
