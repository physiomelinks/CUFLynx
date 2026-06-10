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

  it('test_clear_obs_data_restores_manual_time_controls', () => {
    const o = useObsData()
    o.setObsData({ n_experiments: 1 })
    expect(o.useManualTime.value).toBe(false)
    o.clearObsData()
    expect(o.useManualTime.value).toBe(true)
    expect(o.experimentCount.value).toBe(0)
  })

  it('derives experiment count from protocol_info', () => {
    const o = useObsData()
    o.setObsData({ protocol_info: { sim_times: [[5]], pre_times: [0] } })
    expect(o.experimentCount.value).toBe(1)
  })
})
