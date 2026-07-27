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

  // Scientific notation in the cursor tooltips (issue #107)
  describe('tooltip formatting', () => {
    const optionsOf = () =>
      mount(PlotPanel, { props: { simResult }, global: { stubs } }).vm.chartOptions

    const labelFor = (y, label = 'x') =>
      optionsOf().plugins.tooltip.callbacks.label({
        dataset: { label },
        parsed: { x: 0, y },
      })

    it('formats tiny and huge y-values in scientific notation', () => {
      expect(labelFor(1.5e-9)).toBe('x: 1.5e-9')
      expect(labelFor(1.5e6)).toBe('x: 1.5e6')
    })

    it('leaves mid-range y-values plain', () => {
      expect(labelFor(2.5)).toBe('x: 2.5')
    })

    it('keeps the plain dataset label, falling back to the bare value', () => {
      expect(labelFor(2.5, 'Lotka_Volterra_module/x')).toBe(
        'Lotka_Volterra_module/x: 2.5',
      )
      expect(labelFor(2.5, '')).toBe('2.5')
    })

    it('formats the hovered x-value in the tooltip title', () => {
      const title = optionsOf().plugins.tooltip.callbacks.title
      expect(title([{ parsed: { x: 1.5e-9, y: 1 } }])).toBe('1.5e-9')
      expect(title([{ parsed: { x: 2.5, y: 1 } }])).toBe('2.5')
      expect(title([])).toBe('')
    })

    it('shows the time to 3 significant figures, unlike the y-value', () => {
      const options = optionsOf()
      const title = options.plugins.tooltip.callbacks.title
      // The time only has to locate the sample, so it is cut short...
      expect(title([{ parsed: { x: 0.123456, y: 1 } }])).toBe('0.123')
      expect(title([{ parsed: { x: 1.23456e-9, y: 1 } }])).toBe('1.23e-9')
      expect(title([{ parsed: { x: 0.35000000000000003, y: 1 } }])).toBe('0.35')
      // ...while the y-value it sits above keeps fmtSci's fuller precision.
      expect(
        options.plugins.tooltip.callbacks.label({
          dataset: { label: '' },
          parsed: { x: 0, y: 1.23456e-9 },
        }),
      ).toBe('1.2346e-9')
    })

    it('formats both axis ticks with the concise scientific formatter', () => {
      const { x, y } = optionsOf().scales
      expect(y.ticks.callback(1.5e-9)).toBe('1.5e-9')
      expect(y.ticks.callback(2.5)).toBe('2.5')
      expect(x.ticks.callback(1.5e6)).toBe('1.5e6')
    })

    it('reports the nearest sample without requiring the cursor to hit it', () => {
      // Hit-testing is per sample, not per segment, so on a steep stretch (worse
      // on a maximized plot) the gap between consecutive samples is dead space
      // however wide hitRadius is. intersect:false makes Chart.js fall back to
      // plain nearest-by-distance, so the tooltip tracks the curve continuously.
      expect(optionsOf().interaction).toMatchObject({
        mode: 'nearest',
        intersect: false,
      })
    })

    it('widens the point hit radius so the cursor need not be exactly on the line', () => {
      // Traces draw with pointRadius 0 and Chart.js hit-tests with
      // distance^2 < (hitRadius + radius)^2, so the stock hitRadius of 1 gives a
      // 1px target. Anything meaningfully larger makes the value easy to read off.
      const hit = optionsOf().elements.point.hitRadius
      expect(hit).toBeGreaterThan(1)
      expect(hit).toBeLessThanOrEqual(20)
    })
  })

  it('shows no remove button by default', () => {
    const wrapper = mount(PlotPanel, {
      props: { simResult, title: 'x' },
      global: { stubs },
    })
    expect(wrapper.find('[data-testid="plot-remove"]').exists()).toBe(false)
  })

  it('emits remove when the removable ✕ is clicked', async () => {
    const wrapper = mount(PlotPanel, {
      props: { simResult, title: 'x', removable: true },
      global: { stubs },
    })
    const btn = wrapper.find('[data-testid="plot-remove"]')
    expect(btn.exists()).toBe(true)
    await btn.trigger('click')
    expect(wrapper.emitted('remove')).toHaveLength(1)
  })

  // Individual-plot maximize (issue #115)
  it('shows a maximize button only when maximizable', () => {
    const off = mount(PlotPanel, { props: { simResult, title: 'x' }, global: { stubs } })
    expect(off.find('[data-testid="plot-maximize"]').exists()).toBe(false)
    const on = mount(PlotPanel, {
      props: { simResult, title: 'x', maximizable: true },
      global: { stubs },
    })
    expect(on.find('[data-testid="plot-maximize"]').exists()).toBe(true)
  })

  it('emits toggle-maximize and reflects the maximized state in the button', async () => {
    const wrapper = mount(PlotPanel, {
      props: { simResult, title: 'x', maximizable: true, maximized: false },
      global: { stubs },
    })
    const btn = wrapper.find('[data-testid="plot-maximize"]')
    expect(btn.attributes('aria-pressed')).toBe('false')
    expect(btn.attributes('title')).toBe('Maximize plot')
    expect(btn.find('.pi-window-maximize').exists()).toBe(true)

    await btn.trigger('click')
    expect(wrapper.emitted('toggle-maximize')).toHaveLength(1)

    await wrapper.setProps({ maximized: true })
    expect(btn.attributes('aria-pressed')).toBe('true')
    expect(btn.attributes('title')).toBe('Restore plot')
    expect(btn.find('.pi-window-minimize').exists()).toBe(true)
  })

  // Axis units (issue #125)
  describe('axis units', () => {
    const opts = (props) =>
      mount(PlotPanel, { props: { simResult, ...props }, global: { stubs } }).vm.chartOptions

    it('labels the y-axis with the variable name and its units', () => {
      const y = opts({ varLabel: 'p_o2', yUnit: 'kPa' }).scales.y.title
      expect(y.display).toBe(true)
      expect(y.text).toBe('p_o2 (kPa)')
    })

    it('shows the units alone when there is no variable label', () => {
      const y = opts({ varLabel: '', yUnit: 'mmHg' }).scales.y.title
      expect(y.display).toBe(true)
      expect(y.text).toBe('mmHg')
    })

    it('falls back to an unlabelled y-axis when the units are unknown', () => {
      expect(opts({ varLabel: 'x' }).scales.y.title.display).toBe(false)
    })

    it('suppresses the y-axis title for dimensionless variables', () => {
      expect(opts({ varLabel: 'x', yUnit: 'dimensionless' }).scales.y.title.display).toBe(
        false,
      )
    })

    it('labels the time axis with the model time units', () => {
      expect(opts({ xUnit: 'second' }).scales.x.title.text).toBe('time (second)')
    })

    it('keeps the bare time label when the time units are unknown', () => {
      expect(opts({}).scales.x.title.text).toBe('time')
      expect(opts({ xUnit: 'dimensionless' }).scales.x.title.text).toBe('time')
    })

    it('reacts to a units change', async () => {
      const wrapper = mount(PlotPanel, {
        props: { simResult, varLabel: 'x' },
        global: { stubs },
      })
      expect(wrapper.vm.chartOptions.scales.y.title.display).toBe(false)
      await wrapper.setProps({ yUnit: 'mM' })
      expect(wrapper.vm.chartOptions.scales.y.title.text).toBe('x (mM)')
    })
  })

  it('remounts the chart when maximize toggles so Chart.js resizes (issue #115)', async () => {
    // Chart.js keeps the enlarged canvas on restore, leaving the axis stretched;
    // a key tied to `maximized` forces a fresh chart. Assert the Line instance is
    // recreated (new vm) each time the maximize state flips.
    const wrapper = mount(PlotPanel, {
      props: { simResult, title: 'x', maximizable: true, maximized: false },
      global: { stubs },
    })
    const uid = () => wrapper.findComponent({ name: 'Line' }).vm.$.uid
    const before = uid()
    await wrapper.setProps({ maximized: true }) // maximize -> remount
    const maximized = uid()
    await wrapper.setProps({ maximized: false }) // restore -> remount again
    const restored = uid()
    expect(maximized).not.toBe(before)
    expect(restored).not.toBe(maximized)
  })
})
