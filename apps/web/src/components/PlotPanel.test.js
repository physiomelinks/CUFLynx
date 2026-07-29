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

    // The x label is HTML, not a canvas axis title, so it can be typeset like
    // the heading and its unit can be clicked.
    it('never titles the x-axis on the canvas either', () => {
      expect(opts({ xUnit: 'second' }).scales.x.title).toBeUndefined()
    })

    it('labels the time axis below the plot with the model time units', () => {
      const w = chip({ xUnit: 'second' })
      expect(w.find('[data-testid="plot-xlabel"]').text()).toBe('time')
      expect(w.find('[data-testid="plot-x-unit"]').text()).toBe('[second]')
    })

    it('shows no x unit chip when the time units are unknown or dimensionless', () => {
      expect(chip({}).find('[data-testid="plot-x-unit"]').exists()).toBe(false)
      expect(
        chip({ xUnit: 'dimensionless' }).find('[data-testid="plot-x-unit"]').exists(),
      ).toBe(false)
      // The label itself still names the axis.
      expect(chip({}).find('[data-testid="plot-xlabel"]').text()).toBe('time')
    })

    // On a phase-plane cell (issue #124) the x axis is a variable, not the time,
    // so the unit annotates whatever that axis is named.
    it('names a phase-plane x variable with its own units', () => {
      const w = chip({ xLabel: 'heart/V_lv', xUnit: 'mL' })
      expect(w.find('[data-testid="plot-xlabel"]').text()).toBe('heart/V_lv')
      expect(w.find('[data-testid="plot-x-unit"]').text()).toBe('[mL]')
    })

    // Both labels are the same heading type: the title reads as the y-axis
    // label, the foot as the x-axis one.
    it('gives the x label the same class-driven type as the title', () => {
      const w = chip({ varLabel: 'p_o2', xLabel: 'heart/V_lv' })
      expect(w.find('[data-testid="plot-title"]').classes()).toContain('plot-title')
      expect(w.find('[data-testid="plot-xlabel"]').classes()).toContain('plot-xlabel')
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

  // The x axis converts the same way — on a phase-plane cell it carries a model
  // variable just as the y axis does (issues #124 + #125).
  describe('x-axis unit conversion', () => {
    const mountWithUnits = (props) =>
      mount(PlotPanel, {
        props: {
          simResult,
          title: 'p',
          varLabel: 'p',
          yUnit: 'J_per_m3',
          xLabel: 'heart/V_lv',
          xUnit: 'm3',
          ...props,
        },
        global: { stubs },
      })

    const convertX = async (wrapper, unit, factor) => {
      await wrapper.find('[data-testid="plot-x-unit"]').trigger('click')
      await wrapper.find('[data-testid="convert-unit-name"]').setValue(unit)
      await wrapper.find('[data-testid="convert-unit-factor"]').setValue(factor)
      await wrapper.find('[data-testid="convert-unit-apply"]').trigger('click')
    }

    it('scales every x value and relabels the x chip', async () => {
      const wrapper = mountWithUnits()
      const before = wrapper.vm.displayData.datasets[0].data.map((p) => p.x)
      await convertX(wrapper, 'mL', '1e6')
      expect(wrapper.vm.xDisplayUnit).toBe('mL')
      expect(wrapper.find('[data-testid="plot-x-unit"]').text()).toBe('[mL]')
      expect(wrapper.vm.displayData.datasets[0].data.map((p) => p.x)).toEqual(
        before.map((x) => x * 1e6),
      )
    })

    it('leaves the y values alone', async () => {
      const wrapper = mountWithUnits()
      const ys = wrapper.vm.displayData.datasets[0].data.map((p) => p.y)
      await convertX(wrapper, 'mL', 1e6)
      expect(wrapper.vm.displayData.datasets[0].data.map((p) => p.y)).toEqual(ys)
      expect(wrapper.vm.displayUnit).toBe('J_per_m3')
    })

    // One dialog serves both axes, so it must report the axis it is editing
    // rather than whichever was converted last.
    it('reports the x axis units, not the y axis ones', async () => {
      const wrapper = mountWithUnits()
      await wrapper.find('[data-testid="plot-x-unit"]').trigger('click')
      expect(wrapper.find('[data-testid="convert-original-unit"]').text()).toBe('m3')
      await wrapper.find('[data-testid="convert-unit-name"]').setValue('mL')
      await wrapper.find('[data-testid="convert-unit-factor"]').setValue('1e6')
      await wrapper.find('[data-testid="convert-unit-apply"]').trigger('click')
      await wrapper.find('[data-testid="plot-x-unit"]').trigger('click')
      expect(wrapper.find('[data-testid="convert-current-summary"]').text()).toBe(
        '1 m3 = 1e6 mL',
      )
      // Reopening on the y axis shows the y axis's own (unconverted) state.
      await wrapper.find('[data-testid="convert-unit-apply"]').trigger('click')
      await wrapper.find('[data-testid="plot-unit"]').trigger('click')
      expect(wrapper.find('[data-testid="convert-original-unit"]').text()).toBe('J_per_m3')
      expect(wrapper.find('[data-testid="convert-current-summary"]').text()).toContain(
        'No conversion applied',
      )
    })

    it('converts each axis independently', async () => {
      const wrapper = mountWithUnits()
      const raw = wrapper.vm.chartData.datasets[0].data.map((p) => ({ ...p }))
      await convertX(wrapper, 'mL', 1e6)
      await wrapper.find('[data-testid="plot-unit"]').trigger('click')
      await wrapper.find('[data-testid="convert-unit-name"]').setValue('mmHg')
      await wrapper.find('[data-testid="convert-unit-factor"]').setValue(0.0075)
      await wrapper.find('[data-testid="convert-unit-apply"]').trigger('click')
      expect(wrapper.vm.displayData.datasets[0].data).toEqual(
        raw.map((p) => ({ ...p, x: p.x * 1e6, y: p.y * 0.0075 })),
      )
    })

    // Switching axes (issue #124): the parent owns the variables, so the panel
    // only asks — but the conversions are the panel's and must follow the
    // variable they were set on.
    it('offers a switch-axes button only on a phase-plane plot', () => {
      expect(
        mountWithUnits({ switchable: true }).find('[data-testid="plot-switch-axes"]').exists(),
      ).toBe(true)
      expect(mountWithUnits().find('[data-testid="plot-switch-axes"]').exists()).toBe(false)
    })

    it('emits switch-axes when the button is clicked', async () => {
      const wrapper = mountWithUnits({ switchable: true })
      await wrapper.find('[data-testid="plot-switch-axes"]').trigger('click')
      expect(wrapper.emitted('switch-axes')).toHaveLength(1)
    })

    it('carries each conversion across to the axis its variable moved to', async () => {
      const wrapper = mountWithUnits({ switchable: true })
      await convertX(wrapper, 'mL', 1e6)
      await wrapper.find('[data-testid="plot-switch-axes"]').trigger('click')
      // The factor set for the x variable now applies to the y axis, where that
      // variable went — it must not stay behind rescaling the incoming one.
      expect(wrapper.vm.conversion).toEqual({ unit: 'mL', factor: 1e6 })
      expect(wrapper.vm.xConversion).toBe(null)
    })

    it('resets the x axis without touching the y axis', async () => {
      const wrapper = mountWithUnits()
      const before = wrapper.vm.displayData.datasets[0].data.map((p) => p.x)
      await convertX(wrapper, 'mL', 1e6)
      await wrapper.find('[data-testid="plot-x-unit"]').trigger('click')
      await wrapper.find('[data-testid="convert-unit-reset"]').trigger('click')
      expect(wrapper.vm.xConversion).toBe(null)
      expect(wrapper.vm.xDisplayUnit).toBe('m3')
      expect(wrapper.vm.displayData.datasets[0].data.map((p) => p.x)).toEqual(before)
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

// Issue #146: ticking a saved run adds a legend row. That used to grow the cell,
// and Chart.js (maintainAspectRatio:false) enlarges its canvas to fill but never
// shrinks it back — so unticking left the plot permanently taller.
describe('plot height across legend changes', () => {
  const withSaved = (savedSeries) =>
    mount(PlotPanel, {
      props: { simResult, title: 'x', savedSeries },
      global: { stubs },
    })

  it('a saved overlay adds a legend entry', () => {
    const before = withSaved([]).findAll('.legend-item').length
    const after = withSaved([
      { prefix: 'run_a', color: '#7f7f7f', time: [0, 1, 2], values: [4, 5, 6] },
    ]).findAll('.legend-item').length
    expect(after).toBe(before + 1)
  })

  // The plot area is pinned in CSS so the legend can only move itself; the class
  // is what switches that off when the cell does have a definite height.
  it('only takes the flexible plot area when maximized', async () => {
    const wrapper = mount(PlotPanel, {
      props: { simResult, title: 'x', maximizable: true, maximized: false },
      global: { stubs },
    })
    expect(wrapper.find('[data-testid="plot-panel"]').classes()).not.toContain('maximized')
    await wrapper.setProps({ maximized: true })
    expect(wrapper.find('[data-testid="plot-panel"]').classes()).toContain('maximized')
  })

  it('keeps the chart wrapper a single element as the legend changes', () => {
    // Remounting the canvas on a legend change would reset zoom/state; only the
    // maximize toggle is allowed to do that.
    const wrapper = withSaved([])
    const uid = () => wrapper.findComponent({ name: 'Line' }).vm.$.uid
    const before = uid()
    wrapper.setProps({
      savedSeries: [{ prefix: 'r', color: '#000', time: [0], values: [1] }],
    })
    expect(uid()).toBe(before)
  })
})

// Issue #145: Chart.js sizes each y axis to its own tick labels, so plots in a
// grid start their plot areas at different x and traces sharing a time axis fail
// to line up.
describe('shared y-axis width', () => {
  const panel = (props = {}) =>
    mount(PlotPanel, { props: { simResult, title: 'x', ...props }, global: { stubs } })

  const fitWith = (wrapper, scale) => {
    wrapper.vm.chartOptions.scales.y.afterFit(scale)
    return scale
  }

  it('reports the width its own labels need', () => {
    const wrapper = panel()
    fitWith(wrapper, { width: 47 })
    expect(wrapper.emitted('axis-width').at(-1)).toEqual([47])
  })

  it('widens to the shared width', () => {
    const wrapper = panel({ alignWidth: 62 })
    expect(fitWith(wrapper, { width: 47 }).width).toBe(62)
  })

  // Squeezing a plot below what its labels need would clip them.
  it('never narrows below its own natural width', () => {
    const wrapper = panel({ alignWidth: 30 })
    expect(fitWith(wrapper, { width: 47 }).width).toBe(47)
  })

  // The override must not be read back as the natural width on the next layout,
  // or each pass would widen from the last and ratchet upward.
  it('keeps reporting its natural width after being widened', () => {
    const wrapper = panel({ alignWidth: 62 })
    fitWith(wrapper, { width: 47 })
    // Chart.js re-fits, now seeing the width we forced.
    fitWith(wrapper, { width: 62 })
    expect(wrapper.emitted('axis-width').at(-1)).toEqual([47])
  })

  it('goes back to its natural width when alignment is switched off', async () => {
    const wrapper = panel({ alignWidth: 62 })
    fitWith(wrapper, { width: 47 })
    await wrapper.setProps({ alignWidth: 0 })
    expect(fitWith(wrapper, { width: 47 }).width).toBe(47)
  })
})
