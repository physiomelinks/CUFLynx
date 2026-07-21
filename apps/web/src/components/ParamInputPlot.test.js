import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import ParamInputPlot, { PRE_FRAC } from './ParamInputPlot.vue'

// Stub the canvas chart; assert the exposed chartData/chartOptions instead.
function mountPlot(props) {
  return mount(ParamInputPlot, { props, global: { stubs: { Line: true } } })
}

const dashedBoundaries = (datasets) =>
  datasets.filter((d) => Array.isArray(d.borderDash) && d.borderDash[0] === 6)

describe('ParamInputPlot', () => {
  it('compresses pre_time into a fixed slot and sets the x-range', () => {
    const wrapper = mountPlot({
      series: { time: [0, 1, 2, 3], values: [5, 5, 2, 2] },
      preTime: 2,
      totalSim: 3,
      boundaries: [1, 2],
      title: 'a/x',
    })
    const slot = 3 * PRE_FRAC // pre_time is shown in a small fixed slot, not -preTime
    const { datasets } = wrapper.vm.chartData
    // warmup hold starts at -preSlot (compressed), holding the first value
    expect(datasets[0].data[0]).toEqual({ x: -slot, y: 5 })
    // one dashed line per interior subexp boundary
    const dashed = dashedBoundaries(datasets)
    expect(dashed.map((d) => d.data[0].x).sort()).toEqual([1, 2])
    // x-axis spans [-preSlot, totalSim]
    const opts = wrapper.vm.chartOptions
    expect(opts.scales.x.min).toBe(-slot)
    expect(opts.scales.x.max).toBe(3)
    // negative ticks (the compressed pre slot) are hidden
    expect(opts.scales.x.ticks.callback(-1)).toBe('')
    expect(opts.scales.x.ticks.callback(2)).toBe(2)
  })

  it('formats large/small y-axis ticks in scientific notation', () => {
    const cb = mountPlot({
      series: { time: [0, 1], values: [1e-8, 5e-8] },
      preTime: 0,
      totalSim: 1,
      boundaries: [],
    }).vm.chartOptions.scales.y.ticks.callback
    expect(cb(1.5e-8)).toBe('1.5e-8')
    expect(cb(2e6)).toBe('2e6')
    expect(cb(50)).toBe('50') // moderate values stay plain
  })

  it('no pre_time → x starts at 0; empty series → only vertical lines', () => {
    const wrapper = mountPlot({ series: null, preTime: 0, totalSim: 5, boundaries: [2] })
    const { datasets } = wrapper.vm.chartData
    for (const d of datasets) expect(d.data).toHaveLength(2) // vertical lines only
    expect(dashedBoundaries(datasets)).toHaveLength(1)
    expect(wrapper.vm.preSlot).toBe(0)
    expect(wrapper.vm.chartOptions.scales.x.min + 0).toBe(0) // no pre slot (normalize -0)
    expect(wrapper.vm.chartOptions.scales.x.max).toBe(5)
  })
})
