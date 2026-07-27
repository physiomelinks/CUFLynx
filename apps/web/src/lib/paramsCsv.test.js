import { describe, it, expect } from 'vitest'
import {
  defaultRange,
  mergedRows,
  splitQname,
  buildParamsCsv,
  versionedFilename,
} from './paramsCsv'

describe('defaultRange (±10% of initial value)', () => {
  it('returns [0,1] when the initial value is unknown or zero', () => {
    expect(defaultRange(null)).toEqual({ min: 0, max: 1 })
    expect(defaultRange(undefined)).toEqual({ min: 0, max: 1 })
    expect(defaultRange(0)).toEqual({ min: 0, max: 1 })
  })

  it('is ±10% around a positive value', () => {
    expect(defaultRange(10)).toEqual({ min: 9, max: 11 })
  })

  it('is sign-safe for negative values (min < max)', () => {
    const r = defaultRange(-10)
    expect(r.min).toBeCloseTo(-11)
    expect(r.max).toBeCloseTo(-9)
    expect(r.min).toBeLessThan(r.max)
  })
})

describe('splitQname (split on last slash)', () => {
  it('splits vessel/param', () => {
    expect(splitQname('v/a')).toEqual({ vessel_name: 'v', param_name: 'a' })
  })
  it('keeps everything before the last slash as the vessel', () => {
    expect(splitQname('a/b/c')).toEqual({ vessel_name: 'a/b', param_name: 'c' })
  })
  it('handles a bare name with no slash', () => {
    expect(splitQname('lonely')).toEqual({ vessel_name: '', param_name: 'lonely' })
  })
})

describe('mergedRows', () => {
  const current = [
    {
      qname: 'v/a',
      min: 1,
      max: 2,
      name_for_plotting: '\\alpha',
      param_type: 'global',
      initial_value: 1.5,
    },
  ]
  const modelVars = { params: ['v/a', 'v/b'], initial_values: { 'v/b': 2 } }

  it('pre-includes CSV params and offers model params unchecked', () => {
    const rows = mergedRows(current, modelVars)
    expect(rows).toHaveLength(2)
    const a = rows.find((r) => r.qname === 'v/a')
    const b = rows.find((r) => r.qname === 'v/b')
    expect(a).toMatchObject({ included: true, min: 1, max: 2, param_type: 'global' })
    // v/b not in CSV -> unchecked, default ±10% of its initial value (2)
    expect(b.included).toBe(false)
    expect(b.min).toBeCloseTo(1.8)
    expect(b.max).toBeCloseTo(2.2)
  })

  it('dedupes by qname (CSV wins) and sorts included first', () => {
    const rows = mergedRows(current, modelVars)
    expect(rows.map((r) => r.qname)).toEqual(['v/a', 'v/b'])
    expect(rows.filter((r) => r.qname === 'v/a')).toHaveLength(1)
  })

  it('works with no loaded CSV (all model rows unchecked)', () => {
    const rows = mergedRows([], modelVars)
    expect(rows.every((r) => !r.included)).toBe(true)
    expect(rows.map((r) => r.qname).sort()).toEqual(['v/a', 'v/b'])
  })
})

describe('buildParamsCsv', () => {
  it('emits the standard header and splits qnames into vessel/param', () => {
    const csv = buildParamsCsv([
      { qname: 'mod/alpha', min: 0.1, max: 7, name_for_plotting: '\\alpha', param_type: null },
    ])
    const lines = csv.trim().split('\n')
    expect(lines[0]).toBe('vessel_name,param_name,min,max,name_for_plotting')
    expect(lines[1]).toBe('mod,alpha,0.1,7,\\alpha')
    expect(csv.endsWith('\n')).toBe(true)
  })

  it('adds a param_type column only when some row has one', () => {
    const withType = buildParamsCsv([
      { qname: 'v/a', min: 0, max: 1, name_for_plotting: 'a', param_type: 'global' },
    ])
    expect(withType.split('\n')[0]).toBe(
      'vessel_name,param_name,min,max,name_for_plotting,param_type',
    )
    const withoutType = buildParamsCsv([
      { qname: 'v/a', min: 0, max: 1, name_for_plotting: 'a', param_type: null },
    ])
    expect(withoutType.split('\n')[0]).not.toContain('param_type')
  })

  it('quotes fields containing commas', () => {
    const csv = buildParamsCsv([
      { qname: 'v/a', min: 0, max: 1, name_for_plotting: 'a, b', param_type: null },
    ])
    expect(csv).toContain('"a, b"')
  })

  it('adds a comment column only when some row has an annotation', () => {
    const withComment = buildParamsCsv([
      { qname: 'v/a', min: 0, max: 1, name_for_plotting: 'a', param_type: null, comment: 'from Smith 2020' },
    ])
    const lines = withComment.trim().split('\n')
    expect(lines[0]).toBe('vessel_name,param_name,min,max,name_for_plotting,comment')
    expect(lines[1]).toBe('v,a,0,1,a,from Smith 2020')

    const withoutComment = buildParamsCsv([
      { qname: 'v/a', min: 0, max: 1, name_for_plotting: 'a', param_type: null, comment: '' },
    ])
    expect(withoutComment.split('\n')[0]).not.toContain('comment')
  })

  it('quotes a comment containing commas so CA can still parse it', () => {
    const csv = buildParamsCsv([
      { qname: 'v/a', min: 0, max: 1, name_for_plotting: 'a', param_type: null, comment: 'lit range, tentative' },
    ])
    expect(csv).toContain('"lit range, tentative"')
  })
})

describe('mergedRows annotation round-trip', () => {
  it('carries the comment from a loaded CSV param and defaults to empty', () => {
    const rows = mergedRows(
      [{ qname: 'v/a', min: 1, max: 2, name_for_plotting: 'a', comment: 'note A' }],
      { params: ['v/a', 'v/b'], initial_values: { 'v/b': 2 } },
    )
    expect(rows.find((r) => r.qname === 'v/a').comment).toBe('note A')
    expect(rows.find((r) => r.qname === 'v/b').comment).toBe('')
  })
})

describe('versionedFilename', () => {
  const d = new Date(2026, 5, 15) // 2026-06-15 -> 260615

  it('appends _yymmdd to the loaded CSV stem', () => {
    expect(versionedFilename('Lotka_Volterra_params_for_id.csv', 'LV', d)).toBe(
      'Lotka_Volterra_params_for_id_260615.csv',
    )
  })

  it('falls back to <model>_params_for_id when no CSV was loaded', () => {
    expect(versionedFilename(null, 'LV', d)).toBe('LV_params_for_id_260615.csv')
  })
})
