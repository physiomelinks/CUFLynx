import { describe, it, expect } from 'vitest'
import { versionedJsonName, withParameters } from './obsDataJson'

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

describe('withParameters', () => {
  const document = {
    protocol_info: { pre_times: [0, 0], sim_times: [[1, 2], [3]], params_to_change: { 'C/y': [[1, 2], [3]] } },
    data_items: [{ data_item_name: 'p' }],
    note: 'kept',
  }

  it('gives each unset parameter a lane with no value, and keeps the rest', () => {
    const out = withParameters(document, ['C/x', 'C/y'])
    expect(out.protocol_info.params_to_change).toEqual({
      'C/y': [[1, 2], [3]],
      'C/x': [[null, null], [null]],
    })
    expect(out.data_items).toEqual(document.data_items)
    expect(out.note).toBe('kept')
    // a copy: the document given is not changed
    expect(document.protocol_info.params_to_change['C/x']).toBeUndefined()
  })

  it('starts a protocol of one experiment for a document without one', () => {
    expect(withParameters(null, ['C/x']).protocol_info).toEqual({
      pre_times: [0],
      sim_times: [[1]],
      params_to_change: { 'C/x': [[null]] },
    })
  })
})
