import { describe, it, expect } from 'vitest'
import {
  obsModelVar,
  isPlottableOverlay,
  derivePlotVariables,
  overlayItemsFor,
  buildChartData,
} from './plot'

// Mirrors the SN_simple obs_data shape (3 experiments, predictions + overlays).
const obs = {
  n_experiments: 3,
  experiment_labels: ['SHR', 'SHR M', 'I_ramp'],
  prediction_items: [
    { variable: 'var_SN/Cai', name_for_plotting: 'Ca_{ter}', experiment_idx: 0 },
    { variable: 'soma_SN/V', name_for_plotting: 'V', experiment_idx: 0 },
  ],
  data_items: [
    { variable: 'soma_SN/V', operands: ['soma_SN/V'], data_type: 'constant', plot_type: 'horizontal', value: 20, experiment_idx: 2 },
    { variable: 'soma_SN/V', operands: ['time', 'soma_SN/V'], data_type: 'constant', plot_type: 'vertical', value: 2.02, experiment_idx: 0 },
    { variable: 'soma_SN/V', operands: ['time', 'soma_SN/V'], data_type: 'constant', plot_type: 'None', value: 0, experiment_idx: 0 },
  ],
}

describe('obs plot helpers', () => {
  it('obsModelVar picks the non-time operand', () => {
    expect(obsModelVar({ operands: ['time', 'soma_SN/V'] })).toBe('soma_SN/V')
    expect(obsModelVar({ operands: ['Lotka_Volterra_module/x'], variable: 'x_max' })).toBe(
      'Lotka_Volterra_module/x',
    )
    expect(obsModelVar({ variable: 'var_SN/Cai' })).toBe('var_SN/Cai')
  })

  it('isPlottableOverlay skips frequency and plot_type None', () => {
    expect(isPlottableOverlay({ plot_type: 'horizontal' })).toBe(true)
    expect(isPlottableOverlay({ plot_type: 'vertical' })).toBe(true)
    expect(isPlottableOverlay({ plot_type: 'None' })).toBe(false)
    expect(isPlottableOverlay({ plot_type: 'horizontal', data_type: 'frequency' })).toBe(false)
  })

  it('derivePlotVariables unions predictions + plottable data items', () => {
    const vars = derivePlotVariables(obs)
    expect(vars.map((v) => v.qname)).toEqual(['var_SN/Cai', 'soma_SN/V'])
    expect(vars.find((v) => v.qname === 'var_SN/Cai').label).toBe('Ca_{ter}')
  })

  it('overlayItemsFor filters by experiment + variable, skipping None', () => {
    const e0 = overlayItemsFor(obs, 0, 'soma_SN/V')
    expect(e0).toHaveLength(1)
    expect(e0[0].plot_type).toBe('vertical')

    const e2 = overlayItemsFor(obs, 2, 'soma_SN/V')
    expect(e2).toHaveLength(1)
    expect(e2[0].plot_type).toBe('horizontal')

    expect(overlayItemsFor(obs, 1, 'soma_SN/V')).toHaveLength(0)
  })
})

describe('buildChartData reference lines', () => {
  const simResult = { time: [0, 1, 2], outputs: { 'soma_SN/V': [-80, -50, -79] } }

  it('renders a vertical line spanning the y-range at x=value', () => {
    const { datasets } = buildChartData(simResult, {
      dataItems: [{ name_for_plotting: 't_peak', plot_type: 'vertical', value: 2.02, data_type: 'constant' }],
    })
    const v = datasets.find((d) => d.kind === 'obs-vertical')
    expect(v).toBeTruthy()
    expect(v.data[0]).toEqual({ x: 2.02, y: -80 })
    expect(v.data[1]).toEqual({ x: 2.02, y: -50 })
  })

  it('renders a horizontal line for a constant overlay', () => {
    const { datasets } = buildChartData(simResult, {
      dataItems: [{ name_for_plotting: 'V_max', plot_type: 'horizontal', value: 20, data_type: 'constant' }],
    })
    expect(datasets.some((d) => d.kind === 'obs-constant' && Array.isArray(d.borderDash))).toBe(true)
  })
})
