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
