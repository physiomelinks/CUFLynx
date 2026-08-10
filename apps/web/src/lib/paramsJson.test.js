import { describe, it, expect } from 'vitest'
import { rowsToDoc, versionedJsonName } from './paramsJson'

const freeRow = (over = {}) => ({
  qname: 'v/a',
  qnames: ['v/a'],
  name: 'v/a',
  kind: 'free',
  operation: null,
  min: 1,
  max: 2,
  name_for_plotting: 'v/a',
  param_type: null,
  comment: '',
  prior: '',
  priorParams: {},
  unbounded: false,
  ...over,
})

describe('rowsToDoc', () => {
  it('writes a free row as a targets entry', () => {
    const doc = rowsToDoc([freeRow()])
    expect(doc).toEqual({
      version: 1,
      defaults: {},
      params: [{ name: 'v/a', targets: ['v/a'], min: 1, max: 2 }],
    })
  })

  it('writes an override of differently-named parameters as one targets entry', () => {
    // The whole point of #208: the CSV could never say this.
    const doc = rowsToDoc([
      freeRow({ name: 'stiffness', qnames: ['ao/E', 'ven/R'], name_for_plotting: 'k' }),
    ])
    expect(doc.params[0].targets).toEqual(['ao/E', 'ven/R'])
    expect(doc.params[0].name).toBe('stiffness')
    expect(doc.params[0].name_for_plotting).toBe('k')
  })

  it('writes a modifier as modifies+operation and never targets', () => {
    const doc = rowsToDoc([
      freeRow({
        name: 'C_scale',
        kind: 'modifier',
        operation: 'scale',
        qnames: ['a/C', 'b/C'],
        min: 0.5,
        max: 2,
        name_for_plotting: 'C_scale',
        qname: 'a/C',
      }),
    ])
    expect(doc.params[0]).toEqual({
      name: 'C_scale',
      modifies: ['a/C', 'b/C'],
      operation: 'scale',
      min: 0.5,
      max: 2,
      name_for_plotting: 'C_scale',
    })
    expect(doc.params[0]).not.toHaveProperty('targets')
  })

  it('omits bounds on an unbounded row', () => {
    const doc = rowsToDoc([freeRow({ unbounded: true, prior: 'normal' })])
    expect(doc.params[0].unbounded).toBe(true)
    expect(doc.params[0]).not.toHaveProperty('min')
    expect(doc.params[0]).not.toHaveProperty('max')
    expect(doc.params[0].prior).toBe('normal')
  })

  it('never invents keys outside CA closed entry-key set', () => {
    // A key CA does not know makes the whole file unreadable by its resolver.
    const allowed = new Set([
      'name', 'targets', 'modifies', 'operation', 'param_type', 'min', 'max',
      'name_for_plotting', 'prior', 'prior_params', 'unbounded', 'comment',
    ])
    const doc = rowsToDoc([
      freeRow({
        comment: 'note',
        prior: 'normal',
        priorParams: { prior_mean: '7' },
        param_type: 'const',
        baselines: { 'v/a': 3 },
        selected: true,
        modifiedBy: null,
        groupedInto: null,
        initial_value: 3,
      }),
    ])
    for (const key of Object.keys(doc.params[0])) expect(allowed.has(key)).toBe(true)
    expect(doc.params[0].prior_params).toEqual({ prior_mean: '7' })
  })
})

describe('versionedJsonName', () => {
  const date = new Date(2026, 7, 10) // 2026-08-10

  it('keeps a CSV-loaded stem but saves as .json — the lineage stays visible', () => {
    expect(versionedJsonName('study_params_for_id.csv', 'm', date)).toBe(
      'study_params_for_id_260810.json',
    )
  })

  it('re-versions a JSON-loaded file', () => {
    expect(versionedJsonName('study_260801.json', 'm', date)).toBe('study_260801_260810.json')
  })

  it('falls back to the model name', () => {
    expect(versionedJsonName(null, 'heart', date)).toBe('heart_params_for_id_260810.json')
  })
})
