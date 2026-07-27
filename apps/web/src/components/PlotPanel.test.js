import { describe, it, expect, vi } from 'vitest'
import { mount } from '@vue/test-utils'

// Avoid chart.js touching a (jsdom-less) canvas; we only assert on chartData.
vi.mock('vue-chartjs', () => ({ Line: { name: 'Line', render: () => null } }))

import PlotPanel from './PlotPanel.vue'

// PrimeVue components need the plugin installed; stub them the way the other
// dialog tests do so the convert-unit dialog can be driven through the DOM.
const DialogStub = {
  props: ['visible'],
  template: '<div v-if="visible"><slot /><slot name="footer" /></div>',
}
const ButtonStub = {
  props: ['label', 'text'],
  emits: ['click'],
  template: '<button v-bind="$attrs" @click="$emit(\'click\')">{{ label }}</button>',
}
const InputTextStub = {
  props: ['modelValue'],
  emits: ['update:modelValue'],
  template:
    '<input v-bind="$attrs" :value="modelValue" @input="$emit(\'update:modelValue\', $event.target.value)" />',
}
const stubs = {
  Select: true,
  Dialog: DialogStub,
  Button: ButtonStub,
  InputText: InputTextStub,
  // SciNumberInput is a plain local <input>, so it is exercised for real.
}

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

    const chip = (props) =>
      mount(PlotPanel, {
        props: { simResult, title: 'p_o2', ...props },
        global: { stubs },
      })

    it('never titles the y-axis: the variable is already named above in LaTeX', () => {
      expect(opts({ varLabel: 'p_o2', yUnit: 'kPa' }).scales.y.title).toBeUndefined()
    })

    it('shows the unit beside the variable in square brackets', () => {
      const unit = chip({ varLabel: 'p_o2', yUnit: 'kPa' }).find('[data-testid="plot-unit"]')
      expect(unit.exists()).toBe(true)
      expect(unit.text()).toBe('[kPa]')
    })

    it('shows no unit chip when the units are unknown or dimensionless', () => {
      expect(chip({ varLabel: 'x' }).find('[data-testid="plot-unit"]').exists()).toBe(false)
      expect(
        chip({ varLabel: 'x', yUnit: 'dimensionless' })
          .find('[data-testid="plot-unit"]')
          .exists(),
      ).toBe(false)
    })

    it('labels the time axis with the model time units', () => {
      expect(opts({ xUnit: 'second' }).scales.x.title.text).toBe('time [second]')
    })

    it('keeps the bare time label when the time units are unknown', () => {
      expect(opts({}).scales.x.title.text).toBe('time')
      expect(opts({ xUnit: 'dimensionless' }).scales.x.title.text).toBe('time')
    })

    // On a phase-plane cell (issue #124) the x axis is a variable, not the time,
    // so the unit annotates whatever that axis is named.
    it('annotates a phase-plane x label with its own units', () => {
      const o = opts({ xLabel: 'heart/V_lv', xUnit: 'mL' })
      expect(o.scales.x.title.text).toBe('heart/V_lv [mL]')
      expect(opts({ xLabel: 'heart/V_lv' }).scales.x.title.text).toBe('heart/V_lv')
    })

    it('reacts to a units change', async () => {
      const wrapper = chip({ varLabel: 'x' })
      expect(wrapper.find('[data-testid="plot-unit"]').exists()).toBe(false)
      await wrapper.setProps({ yUnit: 'mM' })
      expect(wrapper.find('[data-testid="plot-unit"]').text()).toBe('[mM]')
    })
  })

  // Clicking the unit converts the plot (issue #125)
  describe('unit conversion', () => {
    const mountWithUnit = (props) =>
      mount(PlotPanel, {
        props: { simResult, title: 'p', varLabel: 'p', yUnit: 'J_per_m3', ...props },
        global: { stubs },
      })

    // Drive the real controls: click the unit, fill the dialog, press Apply.
    const convert = async (wrapper, unit, factor) => {
      await wrapper.find('[data-testid="plot-unit"]').trigger('click')
      await wrapper.find('[data-testid="convert-unit-name"]').setValue(unit)
      await wrapper.find('[data-testid="convert-unit-factor"]').setValue(factor)
      await wrapper.find('[data-testid="convert-unit-apply"]').trigger('click')
    }

    it('opens the dialog when the unit is clicked', async () => {
      const wrapper = mountWithUnit()
      expect(wrapper.find('[data-testid="convert-unit-dialog"]').exists()).toBe(false)
      await wrapper.find('[data-testid="plot-unit"]').trigger('click')
      expect(wrapper.find('[data-testid="convert-unit-dialog"]').exists()).toBe(true)
    })

    it('closes the dialog on apply', async () => {
      const wrapper = mountWithUnit()
      await convert(wrapper, 'mmHg', 0.0075)
      expect(wrapper.find('[data-testid="convert-unit-dialog"]').exists()).toBe(false)
    })

    it('scales every plotted value and relabels the chip', async () => {
      const wrapper = mountWithUnit()
      const before = wrapper.vm.displayData.datasets[0].data.map((p) => p.y)
      await convert(wrapper, 'mmHg', 0.0075)
      expect(wrapper.vm.displayUnit).toBe('mmHg')
      expect(wrapper.find('[data-testid="plot-unit"]').text()).toBe('[mmHg]')
      expect(wrapper.vm.displayData.datasets[0].data.map((p) => p.y)).toEqual(
        before.map((y) => y * 0.0075),
      )
    })

    it('converts the obs overlays too, so the comparison stays valid', async () => {
      const wrapper = mountWithUnit({
        dataItems: [
          { variable: 'x_max', name_for_plotting: 'x', data_type: 'constant', value: 30 },
        ],
      })
      const before = wrapper.vm.displayData.datasets.map((d) => d.data.map((p) => p.y))
      await convert(wrapper, 'kPa', 2)
      const after = wrapper.vm.displayData.datasets.map((d) => d.data.map((p) => p.y))
      expect(after.length).toBe(before.length)
      after.forEach((ys, i) => expect(ys).toEqual(before[i].map((y) => y * 2)))
    })

    it('leaves the x values alone', async () => {
      const wrapper = mountWithUnit()
      const xs = wrapper.vm.displayData.datasets[0].data.map((p) => p.x)
      await convert(wrapper, 'mmHg', 0.0075)
      expect(wrapper.vm.displayData.datasets[0].data.map((p) => p.x)).toEqual(xs)
    })

    it('applies the factor to the original unit, replacing rather than compounding', async () => {
      const wrapper = mountWithUnit()
      const original = wrapper.vm.displayData.datasets[0].data.map((p) => p.y)
      await convert(wrapper, 'a', 2)
      await convert(wrapper, 'b', 5)
      // 5x the model's values, not 5x the already-doubled ones.
      expect(wrapper.vm.conversion.factor).toBe(5)
      expect(wrapper.vm.displayData.datasets[0].data.map((p) => p.y)).toEqual(
        original.map((y) => y * 5),
      )
    })

    it('names the original unit and the unit on screen', async () => {
      const wrapper = mountWithUnit()
      await convert(wrapper, 'mmHg', 0.0075)
      await wrapper.find('[data-testid="plot-unit"]').trigger('click')
      expect(wrapper.find('[data-testid="convert-original-unit"]').text()).toBe('J_per_m3')
      expect(wrapper.find('[data-testid="convert-current-unit"]').text()).toBe('mmHg')
    })

    it('states the conversion between the original and displayed units', async () => {
      const wrapper = mountWithUnit()
      await wrapper.find('[data-testid="plot-unit"]').trigger('click')
      expect(wrapper.find('[data-testid="convert-current-summary"]').text()).toContain(
        'No conversion applied',
      )
      await wrapper.find('[data-testid="convert-unit-name"]').setValue('mmHg')
      await wrapper.find('[data-testid="convert-unit-factor"]').setValue(0.0075)
      await wrapper.find('[data-testid="convert-unit-apply"]').trigger('click')
      await wrapper.find('[data-testid="plot-unit"]').trigger('click')
      expect(wrapper.find('[data-testid="convert-current-summary"]').text()).toBe(
        '1 J_per_m3 = 7.5e-3 mmHg',
      )
    })

    it('accepts a factor typed in scientific notation', async () => {
      const wrapper = mountWithUnit()
      const original = wrapper.vm.displayData.datasets[0].data.map((p) => p.y)
      await convert(wrapper, 'mmHg', '7.5e-3')
      expect(wrapper.vm.conversion.factor).toBe(0.0075)
      expect(wrapper.vm.displayData.datasets[0].data.map((p) => p.y)).toEqual(
        original.map((y) => y * 0.0075),
      )
    })

    it('reopens prefilled with the conversion in force', async () => {
      const wrapper = mountWithUnit()
      await convert(wrapper, 'mmHg', 0.0075)
      await wrapper.find('[data-testid="plot-unit"]').trigger('click')
      expect(wrapper.find('[data-testid="convert-unit-name"]').element.value).toBe('mmHg')
      expect(
        Number(wrapper.find('[data-testid="convert-unit-factor"]').element.value),
      ).toBe(0.0075)
    })

    it('resets to the model unit', async () => {
      const wrapper = mountWithUnit()
      const before = wrapper.vm.displayData.datasets[0].data.map((p) => p.y)
      await convert(wrapper, 'mmHg', 0.0075)
      await wrapper.find('[data-testid="plot-unit"]').trigger('click')
      await wrapper.find('[data-testid="convert-unit-reset"]').trigger('click')
      expect(wrapper.vm.conversion).toBe(null)
      expect(wrapper.vm.displayUnit).toBe('J_per_m3')
      expect(wrapper.vm.displayData.datasets[0].data.map((p) => p.y)).toEqual(before)
    })

    it('ignores a blank unit or a zero / non-numeric factor', async () => {
      const wrapper = mountWithUnit()
      await convert(wrapper, '', 2)
      expect(wrapper.vm.conversion).toBe(null)
      await convert(wrapper, 'mmHg', 0)
      expect(wrapper.vm.conversion).toBe(null)
      await convert(wrapper, 'mmHg', NaN)
      expect(wrapper.vm.conversion).toBe(null)
    })

    it('leaves the underlying chartData in the model units', async () => {
      // The conversion is display-only: exports and the sim keep model units.
      const wrapper = mountWithUnit()
      const raw = wrapper.vm.chartData.datasets[0].data.map((p) => p.y)
      await convert(wrapper, 'mmHg', 0.0075)
      expect(wrapper.vm.chartData.datasets[0].data.map((p) => p.y)).toEqual(raw)
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
