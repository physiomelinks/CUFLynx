import { describe, it, expect } from 'vitest'
import {
  defaultRange,
  mergedRows,
  splitQname,
  buildParamsCsv,
  versionedFilename,
  addToGroup,
  removeFromGroup,
  rowsToSave,
  canCreateModifier,
  createModifier,
  removeModifier,
  suggestModifierName,
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

describe('buildParamsCsv — prior column', () => {
  it('emits the column only when a row carries a prior', () => {
    const withPrior = buildParamsCsv([{ qname: 'v/a', min: 1, max: 2, prior: 'normal' }])
    expect(withPrior.split('\n')[0]).toContain('prior')
    expect(withPrior).toContain('normal')

    const without = buildParamsCsv([{ qname: 'v/a', min: 1, max: 2, prior: '' }])
    expect(without.split('\n')[0]).not.toContain('prior')
  })

  it('keeps prior before comment so the column order is stable', () => {
    const csv = buildParamsCsv([
      { qname: 'v/a', min: 1, max: 2, prior: 'normal', comment: 'note' },
    ])
    const header = csv.split('\n')[0].split(',')
    expect(header.indexOf('prior')).toBeLessThan(header.indexOf('comment'))
  })

  it('writes an empty cell for rows without a prior when others have one', () => {
    const csv = buildParamsCsv([
      { qname: 'v/a', min: 1, max: 2, prior: 'normal' },
      { qname: 'v/b', min: 1, max: 2, prior: '' },
    ])
    const [, , second] = csv.split('\n')
    // trailing empty prior cell -> CA reads it as its default, which is what
    // "not stated" means; it must not inherit the row above.
    expect(second.endsWith(',')).toBe(true)
  })
})

describe('mergedRows — prior', () => {
  it('carries a loaded prior onto the row', () => {
    const [row] = mergedRows([{ qname: 'v/a', min: 1, max: 2, prior: 'exponential' }], {})
    expect(row.prior).toBe('exponential')
  })

  it('leaves a model param that was never in the CSV without a prior', () => {
    const rows = mergedRows([], { params: ['v/b'], initial_values: { 'v/b': 2 } })
    expect(rows[0].prior).toBe('')
  })
})

// ---------------------------------------------------------------------------
// Grouped parameters (issue #193): one row, several vessels, one quantity.
// ---------------------------------------------------------------------------
describe('grouped parameters — reading', () => {
  const groupedParam = {
    qname: 'ao_A/E',
    qnames: ['ao_A/E', 'ao_B/E'],
    min: 3e5,
    max: 1.3e6,
    name_for_plotting: 'E_{AR}',
  }

  it('keeps the row whole instead of one row per vessel', () => {
    const rows = mergedRows([groupedParam], {})
    expect(rows).toHaveLength(1)
    expect(rows[0].qnames).toEqual(['ao_A/E', 'ao_B/E'])
  })

  it('does not offer a grouped member again as a parameter of its own', () => {
    // It is already set by the group; a second row for it would be a second way
    // to set the same variable, and the two would disagree.
    const rows = mergedRows([groupedParam], {
      params: ['ao_A/E', 'ao_B/E', 'ao_C/E'],
      initial_values: {},
    })
    expect(rows.map((r) => r.qname)).toEqual(['ao_A/E', 'ao_C/E'])
  })

  it('gives an ordinary row a one-member group', () => {
    const rows = mergedRows([{ qname: 'v/a', min: 1, max: 2 }], {})
    expect(rows[0].qnames).toEqual(['v/a'])
  })
})

describe('grouped parameters — writing', () => {
  it('writes every vessel into the one vessel_name cell', () => {
    // Writing only the first would dissolve the group on the next save.
    const csv = buildParamsCsv([
      {
        qname: 'ao_A/E',
        qnames: ['ao_A/E', 'ao_B/E', 'ao_C/E'],
        min: 3e5,
        max: 1.3e6,
        name_for_plotting: 'E_{AR}',
      },
    ])
    const [, row] = csv.trim().split('\n')
    expect(row).toBe('ao_A ao_B ao_C,E,300000,1300000,E_{AR}')
  })

  it('round-trips the issue #193 example unchanged', () => {
    const params = [
      {
        qname: 'ascending_aorta_A/E',
        qnames: [
          'ascending_aorta_A/E',
          'ascending_aorta_B/E',
          'ascending_aorta_C/E',
          'ascending_aorta_D/E',
        ],
        min: 300000,
        max: 1300000,
        name_for_plotting: 'E_{AR}',
      },
    ]
    const csv = buildParamsCsv(rowsToSave(mergedRows(params, {})))
    expect(csv.trim().split('\n')[1]).toBe(
      'ascending_aorta_A ascending_aorta_B ascending_aorta_C ascending_aorta_D,E,' +
        '300000,1300000,E_{AR}',
    )
  })
})

describe('grouped parameters — creating one', () => {
  function fixture() {
    return mergedRows([], {
      params: ['ao_A/E', 'ao_B/E', 'ao_C/R'],
      initial_values: { 'ao_A/E': 1, 'ao_B/E': 1, 'ao_C/R': 2 },
    })
  }

  it('absorbing a row removes it from the list but keeps its edits', () => {
    const rows = fixture()
    const [a, b] = [rows.find((r) => r.qname === 'ao_A/E'), rows.find((r) => r.qname === 'ao_B/E')]
    b.min = 42
    addToGroup(a, b)
    expect(a.qnames).toEqual(['ao_A/E', 'ao_B/E'])
    expect(b.groupedInto).toBe('ao_A/E')
    expect(rowsToSave(rows).map((r) => r.qname)).not.toContain('ao_B/E')
    expect(b.min).toBe(42)
  })

  it('releasing a row gives it back, with what it had', () => {
    const rows = fixture()
    const [a, b] = [rows.find((r) => r.qname === 'ao_A/E'), rows.find((r) => r.qname === 'ao_B/E')]
    b.included = true
    b.min = 42
    addToGroup(a, b)
    removeFromGroup(a, b)
    expect(a.qnames).toEqual(['ao_A/E'])
    expect(b.groupedInto).toBeNull()
    expect(rowsToSave(rows).map((r) => r.qname)).toContain('ao_B/E')
    expect(b.min).toBe(42)
  })

})

// ---------------------------------------------------------------------------
// Modifier parameters (#208)
// ---------------------------------------------------------------------------
describe('modifier parameters', () => {
  const SCALE = { value: 'scale', default_min: 0.5, default_max: 2.0, identity: 1.0 }

  function fixture() {
    return mergedRows([], {
      params: ['a/C', 'b/C', 'c/R'],
      initial_values: { 'a/C': 2e-8, 'b/C': 4e-8, 'c/R': 5 },
    })
  }

  it('creates a scale modifier over the selection: θ bounds from the vocabulary, targets claimed', () => {
    const rows = fixture()
    const selected = rows.filter((r) => r.qname !== 'c/R')
    const mod = createModifier(rows, selected, SCALE)

    expect(mod.kind).toBe('modifier')
    expect(mod.operation).toBe('scale')
    expect(mod.qname).toBe('a/C') // the anchor: modifies[0]
    expect(mod.qnames).toEqual(['a/C', 'b/C'])
    expect(mod.min).toBe(0.5)
    expect(mod.max).toBe(2.0)
    expect(mod.initial_value).toBe(1.0) // identity θ
    expect(mod.baselines).toEqual({ 'a/C': 2e-8, 'b/C': 4e-8 })
    // The targets stop being rows of their own but keep their edits.
    for (const r of selected) expect(r.modifiedBy).toBe(mod.name)
    expect(rowsToSave(rows).map((r) => r.name)).toEqual([mod.name])
  })

  it('suggests a unique name from the shared param_name', () => {
    const rows = fixture()
    expect(suggestModifierName(rows, ['a/C', 'b/C'])).toBe('scale_C')
    rows.push({ name: 'scale_C' })
    expect(suggestModifierName(rows, ['a/C', 'b/C'])).toBe('scale_C_2')
    expect(suggestModifierName(rows, ['a/C', 'c/R'])).toBe('scale_params')
  })

  it('refuses a selection containing a modifier or an already-claimed row', () => {
    const rows = fixture()
    const selected = rows.filter((r) => r.qname !== 'c/R')
    createModifier(rows, selected, SCALE)
    // The claimed rows cannot be modified again (CA's no-double-modification),
    // and a modifier cannot modify a modifier (no chains).
    expect(canCreateModifier([rows.find((r) => r.qname === 'a/C' && r.kind === 'free')])).toBe(false)
    expect(canCreateModifier([rows.find((r) => r.kind === 'modifier')])).toBe(false)
  })

  it('deleting a modifier restores its targets as their own rows', () => {
    const rows = fixture()
    const selected = rows.filter((r) => r.qname !== 'c/R')
    const mod = createModifier(rows, selected, SCALE)
    removeModifier(rows, mod)
    expect(rows.find((r) => r.kind === 'modifier')).toBeUndefined()
    expect(rowsToSave(rows).map((r) => r.qname)).toEqual(
      expect.arrayContaining(['a/C', 'b/C']),
    )
  })

  it('a loaded modifier entry hydrates as a modifier row', () => {
    const rows = mergedRows(
      [{
        qname: 'a/C', qnames: ['a/C', 'b/C'], name: 'C_scale',
        modifies: ['a/C', 'b/C'], operation: 'scale',
        baselines: { 'a/C': 2e-8, 'b/C': 4e-8 },
        min: 0.5, max: 2, initial_value: 1.0, identity: 1.0,
      }],
      { params: ['a/C', 'b/C', 'c/R'], initial_values: { 'c/R': 5 } },
    )
    const mod = rows.find((r) => r.kind === 'modifier')
    expect(mod.name).toBe('C_scale')
    expect(mod.baselines).toEqual({ 'a/C': 2e-8, 'b/C': 4e-8 })
    // Its targets are claimed, not offered again as separate rows.
    expect(rows.filter((r) => r.qname === 'b/C')).toHaveLength(0)
  })
})
