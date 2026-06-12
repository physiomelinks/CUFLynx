import { describe, it, expect, vi } from 'vitest'
import { mount } from '@vue/test-utils'

// Avoid chart.js touching a (jsdom-less) canvas; we only assert on chartData.
vi.mock('vue-chartjs', () => ({ Line: { name: 'Line', render: () => null } }))

import PlotPanel from './PlotPanel.vue'

const stubs = { Select: true }

const simResult = {
  time: [0, 1, 2],
  outputs: { 'Lotka_Volterra_module/x': [20, 22, 19] },
}

describe('PlotPanel', () => {
  it('test_renders_horizontal_line_for_constant_data_item', () => {
    const dataItems = [
      {
        variable: 'x_max',
        name_for_plotting: 'x_{max}',
        data_type: 'constant',
        value: 30,
      },
    ]
    const wrapper = mount(PlotPanel, {
      props: { simResult, dataItems },
      global: { stubs },
    })
    const sets = wrapper.vm.chartData.datasets
    expect(sets.some((d) => Array.isArray(d.borderDash))).toBe(true)
  })

  it('test_renders_series_overlay_for_series_data_item', () => {
    const dataItems = [
      {
        variable: 'x_trace',
        data_type: 'series',
        obs_dt: 0.5,
        value: [20, 21, 22, 23],
      },
    ]
    const wrapper = mount(PlotPanel, {
      props: { simResult, dataItems },
      global: { stubs },
    })
    const sets = wrapper.vm.chartData.datasets
    const series = sets.find((d) => d.kind === 'obs-series')
    expect(series).toBeTruthy()
    expect(series.type).toBe('scatter')
    expect(series.data[1]).toEqual({ x: 0.5, y: 21 })
  })

  it('builds a dataset per simulation output', () => {
    const wrapper = mount(PlotPanel, {
      props: { simResult, dataItems: [] },
      global: { stubs },
    })
    const sim = wrapper.vm.chartData.datasets.filter((d) => d.kind === 'simulation')
    expect(sim.length).toBe(1)
    expect(sim[0].label).toBe('Lotka_Volterra_module/x')
  })
})
