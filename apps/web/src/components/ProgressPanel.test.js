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
})
