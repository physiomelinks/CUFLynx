import { describe, it, expect, vi } from 'vitest'
import { mount } from '@vue/test-utils'

// Avoid chart.js touching a (jsdom-less) canvas; we only assert on chart data.
vi.mock('vue-chartjs', () => ({ Line: { name: 'Line', render: () => null } }))

import ProgressPanel from './ProgressPanel.vue'

describe('ProgressPanel', () => {
  it('test_plots_best_line_and_band_for_single_start_ga', () => {
    const wrapper = mount(ProgressPanel, {
      props: {
        costHistory: [
          [0.9, 1.0, 1.1],
          [0.4, 0.6, 0.8],
        ],
      },
    })
    const sets = wrapper.vm.costData.datasets
    // best line (col 0) + a filled band over the top-10 spread.
    expect(sets.map((d) => d.data)).toEqual([
      [0.9, 0.4],
      [1.1, 0.8],
    ])
    expect(sets.some((d) => d.fill === '-1')).toBe(true)
    expect(wrapper.vm.xLabel).toBe('generation')
  })

  it('test_plots_one_line_per_start_for_multi_start', () => {
    const wrapper = mount(ProgressPanel, {
      props: {
        costHistory: [],
        startCosts: [
          [1.5, 1.2, 1.0],
          [2.0, 1.1],
          [3.0],
        ],
      },
    })
    const sets = wrapper.vm.costData.datasets
    expect(sets).toHaveLength(3)
    expect(sets.map((d) => d.label)).toEqual(['start 0', 'start 1', 'start 2'])
    expect(sets.map((d) => d.data)).toEqual([
      [1.5, 1.2, 1.0],
      [2.0, 1.1],
      [3.0],
    ])
    // No fill band in multi-start mode.
    expect(sets.some((d) => d.fill)).toBe(false)
    expect(wrapper.vm.xLabel).toBe('iteration')
    // The panel renders even though costHistory is empty.
    expect(wrapper.find('[data-testid="progress-panel"]').exists()).toBe(true)
    expect(wrapper.text()).not.toContain('Run a calibration')
  })

  it('test_shows_empty_hint_without_any_data', () => {
    const wrapper = mount(ProgressPanel, { props: { costHistory: [], startCosts: [] } })
    expect(wrapper.text()).toContain('Run a calibration')
  })

  it('test_multi_start_cost_lines_are_shades_of_one_base_colour', () => {
    const wrapper = mount(ProgressPanel, {
      props: { costHistory: [], startCosts: [[1.5, 1.0], [2.0, 1.1], [3.0, 1.2]] },
    })
    const colours = wrapper.vm.costData.datasets.map((d) => d.borderColor)
    // start 0 is the base palette colour; later starts are distinct (shaded).
    expect(colours[0]).toBe('#5b9bd5')
    expect(new Set(colours).size).toBe(3)
  })

  it('test_plots_param_per_colour_and_start_per_shade_normalised_to_range', () => {
    const wrapper = mount(ProgressPanel, {
      props: {
        costHistory: [],
        startCosts: [[1.5], [2.0]],
        startParams: {
          param_names: ['well x', 'well y'],
          starts: [
            // start 0: two iterations, each [x, y]
            [
              [1.2, 3.4],
              [1.0, 3.0],
            ],
            // start 1
            [
              [2.2, 4.4],
              [1.9, 4.0],
            ],
          ],
        },
        // params_for_id ranges keyed by qname (slash form); labels come slashless.
        paramSpecs: {
          'well/x': { min: 0, max: 2 },
          'well/y': { min: 2, max: 4 },
        },
      },
    })
    expect(wrapper.vm.hasStartParams).toBe(true)
    const sets = wrapper.vm.startParamData.datasets
    // 2 params × 2 starts = 4 lines.
    expect(sets).toHaveLength(4)
    // Values are normalised to each param's [min, max]: x in [0,2] -> /2;
    // y in [2,4] -> (v-2)/2. Start 0's well x column [1.2, 1.0] -> [0.6, 0.5].
    const wellX_start0 = sets.find((d) => d.label === 'well x' && d._legend === true)
    expect(wellX_start0.data).toEqual([0.6, 0.5])
    const wellY_start0 = sets.find((d) => d.label === 'well y' && d._legend === true)
    expect(wellY_start0.data).toEqual([0.7, 0.5])
    // well x uses palette[0] as its base; well y uses palette[1].
    expect(sets.filter((d) => d.label === 'well x')[0].borderColor).toBe('#5b9bd5')
    expect(sets.filter((d) => d.label === 'well y')[0].borderColor).toBe('#ed7d31')
    // Within a param, start 0 is the base colour and start 1 a lighter shade.
    const wellX = sets.filter((d) => d.label === 'well x')
    expect(wellX[0].borderColor).not.toBe(wellX[1].borderColor)
    // One legend entry per param (the start-0 datasets).
    expect(sets.filter((d) => d._legend).length).toBe(2)
    // The per-start param chart renders.
    expect(wrapper.text()).toContain('Normalised parameter values vs iteration')
  })

  it('test_hides_metric_toggle_without_gradient_data', () => {
    const wrapper = mount(ProgressPanel, {
      props: { costHistory: [[0.9], [0.4]] },
    })
    expect(wrapper.vm.hasGradient).toBe(false)
    expect(wrapper.find('[data-testid="metric-toggle"]').exists()).toBe(false)
    // Falls back to cost even if asked for gradient.
    expect(wrapper.vm.activeMetric).toBe('cost')
  })

  it('test_shows_metric_toggle_and_switches_to_gradient_single_start', async () => {
    const wrapper = mount(ProgressPanel, {
      props: {
        // single-start gradient-based run: one cost per iteration + dJ/dp vectors.
        costHistory: [[0.9], [0.4], [0.1]],
        // gradient converges toward 0; |grad|_inf = max|component|.
        gradHistory: [
          [1.0, -2.0],
          [0.5, -1.0],
          [0.01, -0.02],
        ],
      },
    })
    expect(wrapper.vm.hasGradient).toBe(true)
    expect(wrapper.find('[data-testid="metric-toggle"]').exists()).toBe(true)
    // Default shows the cost series.
    expect(wrapper.vm.activeMetric).toBe('cost')
    expect(wrapper.vm.displayData.datasets[0].data).toEqual([0.9, 0.4, 0.1])
    // Switch to gradient: a single |grad|_inf line.
    await wrapper.find('[data-testid="metric-gradient"]').trigger('click')
    expect(wrapper.vm.activeMetric).toBe('gradient')
    expect(wrapper.vm.displayData.datasets).toHaveLength(1)
    expect(wrapper.vm.displayData.datasets[0].data).toEqual([2.0, 1.0, 0.02])
  })

  it('test_y_axis_toggle_switches_between_log_and_linear', async () => {
    const wrapper = mount(ProgressPanel, {
      props: {
        costHistory: [[0.9], [0.4], [0.1]],
        gradHistory: [[1.0], [0.5], [0.01]],
      },
    })
    // Cost defaults to a logarithmic y-axis.
    expect(wrapper.vm.costOptions.scales.y.type).toBe('logarithmic')
    // Toggle to linear.
    await wrapper.find('[data-testid="yscale-linear"]').trigger('click')
    expect(wrapper.vm.costOptions.scales.y.type).toBe('linear')
    // Back to log.
    await wrapper.find('[data-testid="yscale-log"]').trigger('click')
    expect(wrapper.vm.costOptions.scales.y.type).toBe('logarithmic')
  })

  it('test_gradient_metric_defaults_to_linear_y_but_can_be_toggled_to_log', async () => {
    const wrapper = mount(ProgressPanel, {
      props: { costHistory: [[0.9], [0.4]], gradHistory: [[1.0], [0.01]] },
    })
    await wrapper.find('[data-testid="metric-gradient"]').trigger('click')
    // Gradient decays toward 0, so linear by default.
    expect(wrapper.vm.costOptions.scales.y.type).toBe('linear')
    // The user can still force log.
    await wrapper.find('[data-testid="yscale-log"]').trigger('click')
    expect(wrapper.vm.costOptions.scales.y.type).toBe('logarithmic')
  })

  it('test_gradient_toggle_plots_one_line_per_start_for_multi_start', async () => {
    const wrapper = mount(ProgressPanel, {
      props: {
        costHistory: [],
        startCosts: [
          [1.5, 1.2],
          [2.0, 1.1],
        ],
        startGrads: {
          param_names: ['well x', 'well y'],
          starts: [
            // start 0: two iterations, each [dJ/dx, dJ/dy]
            [
              [1.0, -2.0],
              [0.5, -1.0],
            ],
            // start 1
            [
              [3.0, -4.0],
              [1.5, -2.0],
            ],
          ],
        },
      },
    })
    expect(wrapper.vm.hasGradient).toBe(true)
    await wrapper.find('[data-testid="metric-gradient"]').trigger('click')
    const sets = wrapper.vm.displayData.datasets
    // One |grad|_inf curve per start, mirroring the multi-start cost plot.
    expect(sets).toHaveLength(2)
    expect(sets.map((d) => d.label)).toEqual(['start 0', 'start 1'])
    expect(sets.map((d) => d.data)).toEqual([
      [2.0, 1.0],
      [4.0, 2.0],
    ])
    // No fill band in multi-start gradient mode.
    expect(sets.some((d) => d.fill)).toBe(false)
  })

  it('test_param_plot_falls_back_to_raw_when_range_unknown', () => {
    const wrapper = mount(ProgressPanel, {
      props: {
        costHistory: [],
        startCosts: [[1.5]],
        startParams: {
          param_names: ['well x'],
          starts: [[[1.2], [1.0]]],
        },
        // No matching spec -> values left un-normalised.
        paramSpecs: {},
      },
    })
    const [set] = wrapper.vm.startParamData.datasets
    expect(set.data).toEqual([1.2, 1.0])
  })
})
