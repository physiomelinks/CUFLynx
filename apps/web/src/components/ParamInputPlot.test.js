import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import ParamInputPlot from './ParamInputPlot.vue'

// Stub the canvas chart; assert the exposed chartData computed instead.
function mountPlot(props) {
  return mount(ParamInputPlot, { props, global: { stubs: { Line: true } } })
}

describe('ParamInputPlot', () => {
  it('renders the series line + one dashed dataset per boundary', () => {
    const wrapper = mountPlot({
      series: { time: [0, 1, 2, 3], values: [0, 1, 1, 0] },
      boundaries: [1, 2],
      title: 'a/x',
    })
    const { datasets } = wrapper.vm.chartData
    expect(datasets).toHaveLength(3) // 1 series + 2 boundaries
    const dashed = datasets.filter((d) => Array.isArray(d.borderDash))
    expect(dashed).toHaveLength(2)
    expect(dashed[0].data.map((p) => p.x)).toEqual([1, 1]) // vertical span at x=1
    expect(dashed[1].data.map((p) => p.x)).toEqual([2, 2])
  })

  it('handles an empty series without throwing', () => {
    const wrapper = mountPlot({ series: null, boundaries: [], title: '' })
    const { datasets } = wrapper.vm.chartData
    expect(datasets).toHaveLength(1)
    expect(datasets[0].data).toEqual([])
  })
})
