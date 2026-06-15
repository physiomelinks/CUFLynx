import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import ParamInputPlot from './ParamInputPlot.vue'

// Stub the canvas chart; assert the exposed chartData/chartOptions instead.
function mountPlot(props) {
  return mount(ParamInputPlot, { props, global: { stubs: { Line: true } } })
}

const dashedBoundaries = (datasets) =>
  datasets.filter((d) => Array.isArray(d.borderDash) && d.borderDash[0] === 6)

describe('ParamInputPlot', () => {
  it('holds the warmup value left of t=0 and sets the time x-range', () => {
    const wrapper = mountPlot({
      series: { time: [0, 1, 2, 3], values: [5, 5, 2, 2] },
      preTime: 2,
      totalSim: 3,
      boundaries: [1, 2],
      title: 'a/x',
    })
    const { datasets } = wrapper.vm.chartData
    // input line starts with the warmup hold at x = -preTime, y = first value
    expect(datasets[0].data[0]).toEqual({ x: -2, y: 5 })
    // one dashed line per interior subexp boundary
    const dashed = dashedBoundaries(datasets)
    expect(dashed.map((d) => d.data[0].x).sort()).toEqual([1, 2])
    // x-axis spans [-preTime, totalSim]
    const opts = wrapper.vm.chartOptions
    expect(opts.scales.x.min).toBe(-2)
    expect(opts.scales.x.max).toBe(3)
  })

  it('empty series → only vertical lines (no data line)', () => {
    const wrapper = mountPlot({ series: null, preTime: 1, totalSim: 5, boundaries: [2] })
    const { datasets } = wrapper.vm.chartData
    // every dataset is a two-point vertical line; none is a data series
    for (const d of datasets) expect(d.data).toHaveLength(2)
    expect(dashedBoundaries(datasets)).toHaveLength(1) // the one subexp boundary
    expect(wrapper.vm.chartOptions.scales.x.min).toBe(-1)
    expect(wrapper.vm.chartOptions.scales.x.max).toBe(5)
  })
})
