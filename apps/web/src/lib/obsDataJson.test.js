import { describe, it, expect } from 'vitest'
import {
  splitItems,
  itemToRow,
  rowToItem,
  newRow,
  buildObsData,
  versionedJsonName,
  experimentIdxMax,
} from './obsDataJson'

const OPS = ['', 'max', 'min', 'mean']

describe('splitItems', () => {
  it('editable = constant with a known operation; everything else preserved', () => {
    const items = [
      { variable: 'a', data_type: 'constant', operation: 'max', value: 1, std: 0.1 },
      { variable: 's', data_type: 'series', obs_dt: 0.1, value: [1, 2], std: 0.1 },
      { variable: 'c', data_type: 'constant', operation: 'calc_spike', value: 0, std: 0.1 },
    ]
    const { editable, preserved } = splitItems(items, OPS)
    expect(editable.map((r) => r.variable)).toEqual(['a'])
    expect(preserved.map((i) => i.variable)).toEqual(['s', 'c'])
  })

  it('treats a missing data_type/operation as an editable constant', () => {
    const { editable } = splitItems([{ variable: 'x', value: 1, std: 1 }], OPS)
    expect(editable).toHaveLength(1)
  })
})

describe('itemToRow / rowToItem round-trip', () => {
  it('preserves extra keys and round-trips cost_type', () => {
    const item = {
      variable: 'a',
      data_type: 'constant',
      operation: 'max',
      operands: ['m/x'],
      unit: 'u',
      value: 1,
      std: 0.1,
      weight: 2,
      experiment_idx: 1,
      subexperiment_idx: 3,
      cost_type: 'MSE',
      operation_kwargs: { p: 1 },
      plot_type: 'horizontal',
    }
    const back = rowToItem(itemToRow(item))
    expect(back.data_type).toBe('constant')
    expect(back.value).toBe(1)
    expect(back.weight).toBe(2)
    expect(back.subexperiment_idx).toBe(3) // extra key preserved
    expect(back.operation_kwargs).toEqual({ p: 1 }) // extra key preserved
    expect(back.cost_type).toBe('MSE')
    expect(back.operation).toBe('max')
  })

  it('round-trips operation_kwargs and reflects edited values', () => {
    const row = itemToRow({
      variable: 'a', data_type: 'constant', operation: 'peak_above', operands: ['m/x'],
      value: 1, std: 0.1, operation_kwargs: { threshold: 0.5, window: 10 },
    })
    expect(row.operation_kwargs).toEqual({ threshold: 0.5, window: 10 })
    row.operation_kwargs.threshold = 0.9 // edit persists on save
    expect(rowToItem(row).operation_kwargs).toEqual({ threshold: 0.9, window: 10 })
  })

  it('drops operation_kwargs when empty or when the operation is cleared', () => {
    const row = itemToRow({
      variable: 'a', data_type: 'constant', operation: 'peak_above', operands: ['m/x'],
      value: 1, std: 0.1, operation_kwargs: { threshold: 0.5 },
    })
    row.operation_kwargs = {}
    expect('operation_kwargs' in rowToItem(row)).toBe(false)
    row.operation_kwargs = { threshold: 0.5 }
    row.operation = '' // no operation -> kwargs have no meaning
    expect('operation_kwargs' in rowToItem(row)).toBe(false)
  })

  it('newRow has an empty operation_kwargs map', () => {
    expect(newRow().operation_kwargs).toEqual({})
  })

  it('drops blank operation/cost_type instead of emitting empty strings', () => {
    const row = newRow()
    row.operation = ''
    row.cost_type = ''
    row.operands = ['m/x']
    const item = rowToItem(row)
    expect('operation' in item).toBe(false)
    expect('cost_type' in item).toBe(false)
    expect(item.plot_type).toBe('horizontal')
  })

  it('round-trips a text source and drops it when cleared', () => {
    const row = itemToRow({
      variable: 'a', data_type: 'constant', operation: 'max', operands: ['m/x'],
      value: 1, std: 0.1, source: 'Smith et al. 2020, fig 3',
    })
    expect(row.source).toBe('Smith et al. 2020, fig 3')
    expect(rowToItem(row).source).toBe('Smith et al. 2020, fig 3')
    row.source = ''
    expect('source' in rowToItem(row)).toBe(false)
  })

  it('round-trips a comment and drops it when cleared', () => {
    const row = itemToRow({
      variable: 'a', data_type: 'constant', operation: 'max', operands: ['m/x'],
      value: 1, std: 0.1, comment: 'noisy near t=0',
    })
    expect(row.comment).toBe('noisy near t=0')
    expect(rowToItem(row).comment).toBe('noisy near t=0')
    row.comment = ''
    expect('comment' in rowToItem(row)).toBe(false)
  })

  it('never clobbers a legacy dict source (file paths)', () => {
    const item = {
      variable: 'a', data_type: 'constant', operation: 'max', operands: ['m/x'],
      value: 1, std: 0.1, source: { value_path: 'x.npy' },
    }
    const row = itemToRow(item)
    expect(row.source).toBe('') // dict not surfaced as editable text
    expect(rowToItem(row).source).toEqual({ value_path: 'x.npy' }) // preserved
  })
})

describe('newRow defaults', () => {
  it('is a constant with sensible defaults', () => {
    expect(newRow()).toMatchObject({
      data_type: 'constant',
      operation: 'max',
      value: 0,
      std: 1,
      weight: 1.0,
      experiment_idx: 0,
      unit: 'dimensionless',
      plot_type: 'horizontal',
    })
  })
})

describe('buildObsData', () => {
  const editableRows = [
    itemToRow({ variable: 'a', data_type: 'constant', operation: 'max', operands: ['m/x'], value: 1, std: 0.1 }),
  ]
  const preservedItems = [{ variable: 's', data_type: 'series', obs_dt: 0.1 }]

  it('object form with protocol_info (verbatim) + preserved items appended', () => {
    const protocolInfo = { pre_times: [0], sim_times: [[5]] }
    const out = buildObsData({ protocolInfo, editableRows, preservedItems, predictionRows: [] })
    expect(Array.isArray(out)).toBe(false)
    expect(out.protocol_info).toBe(protocolInfo)
    expect(out.data_items).toHaveLength(2)
    expect(out.data_items[1]).toEqual({ variable: 's', data_type: 'series', obs_dt: 0.1 })
    expect(out.prediction_items).toEqual([])
  })

  it('bare array (data-only) when protocol_info is null', () => {
    const out = buildObsData({ protocolInfo: null, editableRows, preservedItems: [], predictionRows: [] })
    expect(Array.isArray(out)).toBe(true)
    expect(out).toHaveLength(1)
  })
})

describe('versionedJsonName', () => {
  const d = new Date(2026, 5, 15) // 260615

  it('appends _yymmdd to the loaded JSON stem', () => {
    expect(versionedJsonName('Lotka_Volterra_obs_data.json', 'M', d)).toBe(
      'Lotka_Volterra_obs_data_260615.json',
    )
  })
  it('falls back to <model>_obs_data when no file was loaded', () => {
    expect(versionedJsonName(null, 'M', d)).toBe('M_obs_data_260615.json')
  })
})

describe('experimentIdxMax', () => {
  it('is experimentCount-1, floored at 0', () => {
    expect(experimentIdxMax(3)).toBe(2)
    expect(experimentIdxMax(0)).toBe(0)
  })
})
