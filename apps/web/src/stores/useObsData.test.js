import { describe, it, expect } from 'vitest'
import { useObsData } from './useObsData'

describe('useObsData', () => {
  it('test_set_obs_data_updates_experiment_count', () => {
    const o = useObsData()
    expect(o.experimentCount.value).toBe(0)
    o.setObsData({ n_experiments: 2, data_items: [] })
    expect(o.experimentCount.value).toBe(2)
    expect(o.hasObsData.value).toBe(true)
  })

  it('test_clear_obs_data_drops_the_protocol_and_its_run_window', () => {
    const o = useObsData()
    o.setObsData({ protocol_info: { pre_times: [1], sim_times: [[5]] } })
    expect(o.hasProtocol.value).toBe(true)
    expect(o.protocolSimTime.value).toBe(5)
    o.clearObsData()
    expect(o.hasProtocol.value).toBe(false)
    expect(o.protocolSimTime.value).toBe(null)
    expect(o.protocolPreTime.value).toBe(null)
    expect(o.experimentCount.value).toBe(0)
  })

  it('derives experiment count from protocol_info', () => {
    const o = useObsData()
    o.setObsData({ protocol_info: { sim_times: [[5]], pre_times: [0] } })
    expect(o.hasProtocol.value).toBe(true)
    expect(o.experimentCount.value).toBe(1)
  })

  // The run window: the protocol's totals, and nothing else can state them.
  it('totals the protocol times over every experiment and sub-experiment', () => {
    const o = useObsData()
    o.setObsData({
      protocol_info: { pre_times: [1, 0.5], sim_times: [[2, 3], [4]] },
    })
    expect(o.protocolPreTime.value).toBe(1.5)
    expect(o.protocolSimTime.value).toBe(9)
  })

  it('has no run window for a data-only obs_data (3compartment)', () => {
    const o = useObsData()
    o.setObsData({ has_protocol: false, n_data_items: 6, data_items: [] })
    expect(o.hasObsData.value).toBe(true)
    expect(o.hasProtocol.value).toBe(false)
    expect(o.protocolSimTime.value).toBe(null)
    expect(o.protocolPreTime.value).toBe(null)
  })

  // The times are user-authored JSON: a missing or unreadable entry counts as 0
  // rather than making the whole window NaN.
  it('reads a partial or malformed protocol as zeros, not NaN', () => {
    const o = useObsData()
    o.setObsData({ protocol_info: { sim_times: [[2, 'x'], 3] } })
    expect(o.protocolPreTime.value).toBe(0)
    expect(o.protocolSimTime.value).toBe(5)
  })
})
