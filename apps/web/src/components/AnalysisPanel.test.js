import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import AnalysisPanel from './AnalysisPanel.vue'

const UQ_PARAMS = [
  {
    qname: 'Lotka_Volterra_module/alpha',
    mean: 1.2,
    std: 0.3,
    q05: 0.7,
    q50: 1.2,
    q95: 1.8,
    bins: [0.5, 1.0, 1.5, 2.0],
    counts: [3, 8, 4],
  },
]

describe('AnalysisPanel UQ section', () => {
  it('shows an empty hint when there are no UQ results', () => {
    const wrapper = mount(AnalysisPanel)
    expect(wrapper.text()).toContain('Run a UQ analysis')
    expect(wrapper.find('[data-testid="uq-density"]').exists()).toBe(false)
  })

  it('renders a density plot + stats per parameter, with the LaTeX label', () => {
    const wrapper = mount(AnalysisPanel, {
      props: {
        uqParams: UQ_PARAMS,
        uqMethod: 'mcmc',
        paramLabels: { 'Lotka_Volterra_module/alpha': '\\alpha' },
      },
    })
    const rows = wrapper.findAll('[data-testid="uq-row"]')
    expect(rows).toHaveLength(1)
    // density SVG with a histogram polygon
    const density = wrapper.find('[data-testid="uq-density"]')
    expect(density.exists()).toBe(true)
    expect(density.find('polygon').exists()).toBe(true)
    // mean ± std and the 90% CI are shown
    expect(rows[0].text()).toContain('±')
    expect(rows[0].text()).toContain('90% CI')
    // LaTeX label rendered via KaTeX (not the raw backslash form)
    expect(rows[0].find('.uq-label').html()).toContain('katex')
  })

  it('puts a parameter scale under the posterior', () => {
    // Without it the density has a shape but no position: the mean is in the
    // header text and nothing says where the tails actually reach.
    const wrapper = mount(AnalysisPanel, { props: { uqParams: UQ_PARAMS, uqMethod: 'mcmc' } })
    const axis = wrapper.find('[data-testid="uq-axis"]')
    expect(axis.exists()).toBe(true)
    expect(axis.findAll('span').length).toBeGreaterThan(1)
    // Labels are HTML, positioned by percent: the plot is stretched to the row
    // width, which would distort text drawn inside the SVG.
    expect(axis.find('span').attributes('style')).toContain('left')
  })
})

const SENS = {
  indices: { local: { 'y^{0,0} [max]': { 'm/a': 0.5, 'm/b': -0.2 } } },
  paramNames: ['m/a', 'm/b'],
  outputNames: ['y^{0,0} [max]'],
}
const SAVED = [
  { id: 1, label: '#1 Sobol · saltelli · n256', at: '10:00:00' },
  { id: 2, label: '#2 Local · FD · current', at: '10:05:00' },
]

describe('AnalysisPanel sensitivity comparison', () => {
  it('lists saved runs and emits select / remove / clear', async () => {
    const wrapper = mount(AnalysisPanel, {
      props: { ...SENS, savedResults: SAVED, selectedResultId: 2 },
    })
    const chips = wrapper.findAll('[data-testid^="run-chip-"]')
    expect(chips).toHaveLength(2)
    expect(wrapper.find('[data-testid="run-chip-2"]').classes()).toContain('active')

    await wrapper.find('[data-testid="run-chip-1"]').trigger('click')
    expect(wrapper.emitted('select-result')[0]).toEqual([1])

    // the × removes that run without also selecting it (@click.stop)
    await wrapper.find('[data-testid="run-remove-1"]').trigger('click')
    expect(wrapper.emitted('remove-result')[0]).toEqual([1])
    expect(wrapper.emitted('select-result')).toHaveLength(1)

    await wrapper.find('[data-testid="clear-runs"]').trigger('click')
    expect(wrapper.emitted('clear-results')).toBeTruthy()
  })

  it('hides the run selector when nothing is saved', () => {
    const wrapper = mount(AnalysisPanel, { props: { ...SENS, savedResults: [] } })
    expect(wrapper.find('[data-testid="saved-runs"]').exists()).toBe(false)
  })

  it('typesets the var^{e,s} [op] output-name column header via KaTeX', () => {
    const wrapper = mount(AnalysisPanel, { props: { ...SENS } })
    const head = wrapper.find('[data-testid="heatmap-table"] thead .col-head')
    // The ^{0,0} superscript means the label is LaTeX, so the cell content is
    // typeset via KaTeX (the raw caret/brace form survives only in the title
    // tooltip, kept for accessibility / hover).
    expect(head.find('.katex').exists()).toBe(true)
    expect(head.attributes('title')).toBe('y^{0,0} [max]')
    // the [operation] suffix is plain text, NOT typeset by KaTeX
    const op = head.find('.op-label')
    expect(op.exists()).toBe(true)
    expect(op.text()).toBe('[max]')
    expect(op.find('.katex').exists()).toBe(false)
    // indices are still looked up by the (reformatted) output-name string key
    const cell = wrapper.find('[data-testid="heatmap-table"] tbody .cell')
    expect(cell.text()).toBe('0.50')
  })
})

describe('AnalysisPanel nominal (local SA)', () => {
  it('shows the nominal parameter values between Runs and Index for a local run', () => {
    const wrapper = mount(AnalysisPanel, {
      props: {
        ...SENS,
        nominal: [1.5, 2.5e-8],
        nominalSource: 'current parameter values (from sliders)',
        paramLabels: { 'm/a': 'a', 'm/b': 'b' },
      },
    })
    const row = wrapper.find('[data-testid="nominal-row"]')
    expect(row.exists()).toBe(true)
    const chips = row.findAll('.nominal-chip')
    expect(chips).toHaveLength(2)
    // Values formatted (plain + scientific), aligned with paramNames.
    expect(chips[0].find('.nominal-val').text()).toBe('1.5')
    expect(chips[1].find('.nominal-val').text()).toBe('2.5e-8')
    // The source of the nominal point is shown.
    expect(row.text()).toContain('from current parameter values')
  })

  it('hides the nominal row for a Sobol (global) run even if nominal is passed', () => {
    const sobol = {
      indices: { S1: { y: { 'm/a': 0.3 } }, ST: { y: { 'm/a': 0.5 } } },
      paramNames: ['m/a'],
      outputNames: ['y'],
    }
    const wrapper = mount(AnalysisPanel, { props: { ...sobol, nominal: [1.0] } })
    expect(wrapper.find('[data-testid="nominal-row"]').exists()).toBe(false)
  })

  it('hides the nominal row for a local run when no nominal is provided', () => {
    const wrapper = mount(AnalysisPanel, { props: { ...SENS } })
    expect(wrapper.find('[data-testid="nominal-row"]').exists()).toBe(false)
  })
})


// Issue #159: manual exploration had a picture and no number. The cost of the
// current parameters belongs where fit is judged, and next to the best fit so
// "am I winning" is answerable.
describe('AnalysisPanel cost (#159)', () => {
  const CURRENT = {
    cost: 1363.2,
    items: [
      { label: 'u_{AR}', percent_error: 5.4, std_error: 0.54, cost: 900 },
      { label: 'v_{AR}', percent_error: -2.9, std_error: -0.29, cost: 463.2 },
    ],
  }
  const BASELINE = {
    label: 'calibration best fit',
    cost: 12.5,
    items: [
      { label: 'u_{AR}', percent_error: 0.9, std_error: 0.09 },
      { label: 'v_{AR}', percent_error: -0.4, std_error: -0.04 },
    ],
  }

  const mountIt = (props = {}) => mount(AnalysisPanel, { props: { ...props } })

  it('shows the current cost even with no calibration to compare against', () => {
    const w = mountIt({ currentCost: CURRENT })
    expect(w.find('[data-testid="analysis-cost-current"]').text()).toBe('1363')
  })

  it('shows the baseline beside it once there is one', () => {
    const w = mountIt({ currentCost: CURRENT, baselineCost: BASELINE })
    expect(w.find('[data-testid="analysis-cost-baseline"]').text()).toBe('12.5')
    expect(w.text()).toContain('calibration best fit')
  })

  it('colours each cost figure as its own series (#221)', () => {
    // The two numbers used to sit in body text, so which cost belonged to
    // which series had to be read from the caption. They now carry the same
    // colours their bars and legend swatches do.
    const w = mountIt({ currentCost: CURRENT, baselineCost: BASELINE })
    const current = w.find('[data-testid="analysis-cost-current"]').attributes('style')
    const baseline = w.find('[data-testid="analysis-cost-baseline"]').attributes('style')

    expect(current).toContain('rgb(91, 155, 213)') // CURRENT_COLOUR  #5b9bd5
    expect(baseline).toContain('rgb(161, 66, 244)') // BASELINE_COLOUR #a142f4
    expect(current).not.toBe(baseline)
  })

  it('offers no comparison when there is nothing to compare with', () => {
    const w = mountIt({ currentCost: CURRENT })
    expect(w.find('[data-testid="compare-costs"]').exists()).toBe(false)
  })

  it('keeps the comparison charts off until asked', () => {
    const w = mountIt({ currentCost: CURRENT, baselineCost: BASELINE })
    expect(w.find('[data-testid="compare-percent-chart"]').exists()).toBe(false)
  })

  it('draws both series once the box is ticked', async () => {
    const w = mountIt({ currentCost: CURRENT, baselineCost: BASELINE })
    await w.find('[data-testid="compare-costs"]').setValue(true)
    const rows = w.findAll('[data-testid="compare-percent-chart"] .bar-row')
    expect(rows).toHaveLength(2)
    // Two fills per row: the current parameters and the baseline.
    expect(rows[0].findAll('.bar-fill')).toHaveLength(2)
  })

  it('gives the two series different colours, since they must be told apart', async () => {
    const w = mountIt({ currentCost: CURRENT, baselineCost: BASELINE })
    await w.find('[data-testid="compare-costs"]').setValue(true)
    const fills = w.findAll('[data-testid="compare-percent-chart"] .bar-fill')
    expect(fills[0].attributes('style')).not.toBe(fills[1].attributes('style'))
  })

  it('says nothing rather than zero when the cost is unknown', () => {
    const w = mountIt({ currentCost: { cost: null, items: [{ label: 'x', percent_error: 1 }] } })
    expect(w.find('[data-testid="analysis-cost-current"]').text()).toBe('—')
  })

  it('shows a cost spanning orders of magnitude without losing it to rounding', () => {
    const w = mountIt({ currentCost: { cost: 0.000123, items: CURRENT.items } })
    expect(w.find('[data-testid="analysis-cost-current"]').text()).toContain('e-4')
  })

  it('skips an observable that could not be scored rather than drawing it at zero', async () => {
    const w = mountIt({
      currentCost: {
        cost: 5,
        items: [
          { label: 'a', percent_error: 5, cost: 5 },
          { label: 'b', percent_error: null, cost: null },
        ],
      },
      baselineCost: BASELINE,
    })
    await w.find('[data-testid="compare-costs"]').setValue(true)
    expect(w.findAll('[data-testid="compare-percent-chart"] .bar-row')).toHaveLength(1)
  })
})


// Found by using it: ticking the box hid the calibration bars instead of adding
// a series beside them, and the best fit's own cost read as a dash.
describe('AnalysisPanel comparison (#159 follow-up)', () => {
  const CURRENT = {
    cost: 1363,
    items: [{ label: 'u', percent_error: 5.4, std_error: 0.54, cost: 900 }],
  }
  const BASELINE = {
    label: 'calibration best fit',
    cost: 12.5,
    items: [{ label: 'u', percent_error: 0.9, std_error: 0.09 }],
  }

  it('replaces the calibration charts rather than adding below them', async () => {
    const w = mount(AnalysisPanel, {
      props: {
        percentError: [1.2],
        stdError: [0.12],
        errorLabels: ['u'],
        currentCost: CURRENT,
        baselineCost: BASELINE,
      },
    })
    expect(w.find('[data-testid="percent-error-chart"]').exists()).toBe(true)
    await w.find('[data-testid="compare-costs"]').setValue(true)
    // The comparison already carries the best fit as its second series, so
    // keeping the originals below would plot the same numbers twice.
    expect(w.find('[data-testid="compare-percent-chart"]').exists()).toBe(true)
    expect(w.find('[data-testid="percent-error-chart"]').exists()).toBe(false)
  })

  it('goes back to the best-fit bars alone when unticked', async () => {
    const w = mount(AnalysisPanel, {
      props: {
        percentError: [1.2],
        stdError: [0.12],
        errorLabels: ['u'],
        currentCost: CURRENT,
        baselineCost: BASELINE,
      },
    })
    const box = w.find('[data-testid="compare-costs"]')
    await box.setValue(true)
    await box.setValue(false)
    expect(w.find('[data-testid="percent-error-chart"]').exists()).toBe(true)
    expect(w.find('[data-testid="compare-percent-chart"]').exists()).toBe(false)
  })

  it('shows the best fit as a number, not a dash', () => {
    const w = mount(AnalysisPanel, { props: { currentCost: CURRENT, baselineCost: BASELINE } })
    expect(w.find('[data-testid="analysis-cost-baseline"]').text()).toBe('12.5')
  })
})

// ---------------------------------------------------------------------------
// Legend for the comparison bars (issue #178)
//
// Ticking "compare" draws two bars per observable in two colours, and nothing
// on screen said which colour was which. The bars are the only thing telling
// the current parameters from the best fit, so an unlabelled colour makes the
// chart unreadable rather than merely terse.
// ---------------------------------------------------------------------------
describe('AnalysisPanel comparison legend (#178)', () => {
  const CURRENT = {
    cost: 1363.2,
    items: [{ label: 'u_{AR}', percent_error: 5.4, std_error: 0.54, cost: 900 }],
  }
  const BASELINE = {
    label: 'calibration best fit',
    cost: 12.5,
    items: [{ label: 'u_{AR}', percent_error: 0.9, std_error: 0.09 }],
  }
  const mountIt = (props = {}) => mount(AnalysisPanel, { props: { ...props } })

  it('names both series once the charts are comparing', async () => {
    const w = mountIt({ currentCost: CURRENT, baselineCost: BASELINE })
    await w.find('[data-testid="compare-costs"]').setValue(true)
    const legends = w.findAll('[data-testid="compare-legend"]')
    // One per chart: the two charts scroll independently, so a single legend at
    // the top is off screen exactly when it is needed.
    expect(legends).toHaveLength(2)
    expect(legends[0].text()).toContain('current parameters')
    expect(legends[0].text()).toContain('calibration best fit')
  })

  it('uses the same colours as the bars it explains', async () => {
    const w = mountIt({ currentCost: CURRENT, baselineCost: BASELINE })
    await w.find('[data-testid="compare-costs"]').setValue(true)
    const swatches = w.find('[data-testid="compare-legend"]').findAll('.legend-swatch')
    const bars = w.find('[data-testid="compare-percent-chart"]').findAll('.bar-fill')
    // Bound from the same constants as the bars, so the two cannot drift.
    expect(swatches[0].attributes('style')).toContain(
      bars[0].attributes('style').match(/background:[^;]+/)[0],
    )
    expect(swatches[1].attributes('style')).toContain(
      bars[1].attributes('style').match(/background:[^;]+/)[0],
    )
  })

  it('is not shown when there is nothing to compare against', () => {
    const w = mountIt({ currentCost: CURRENT })
    expect(w.find('[data-testid="compare-legend"]').exists()).toBe(false)
  })

  it('takes the baseline’s own name, so it is not always “baseline”', async () => {
    const w = mountIt({
      currentCost: CURRENT,
      baselineCost: { ...BASELINE, label: 'bounds centre' },
    })
    await w.find('[data-testid="compare-costs"]').setValue(true)
    expect(w.find('[data-testid="compare-legend"]').text()).toContain('bounds centre')
  })
})

describe('emulator error', () => {
  const METADATA = {
    feature_labels: ['x (max a/x)', 'y (max a/y)'],
    feature_r2: [0.999, 0.42],
    feature_rmse: [0.01, 0.9],
    feature_mae: [0.008, 0.7],
    feature_bias: [0.0, -0.6],
    feature_max_abs_error: [0.02, 1.4],
    feature_nrmse: [0.002, 0.31],
  }
  const POINTS = {
    theta: [[0.1, 1.0], [0.9, 2.0]],
    y_true: [[1.0, 5.0], [2.0, 6.0]],
    y_pred: [[1.05, 4.0], [1.90, 6.9]],
    residual: [[0.05, -1.0], [-0.10, 0.9]],
    feature_labels: METADATA.feature_labels,
    param_entry_labels: ['a/p', 'a/q'],
  }

  it('says what to do when no emulator has been trained', () => {
    const wrapper = mount(AnalysisPanel)
    expect(wrapper.find('[data-testid="emulator-error-table"]').exists()).toBe(false)
    expect(wrapper.text()).toContain('Train an emulator')
  })

  it('shows every statistic, because R2 alone cannot rank features', () => {
    const wrapper = mount(AnalysisPanel, { props: { emulatorMetadata: METADATA } })
    const table = wrapper.find('[data-testid="emulator-error-table"]')
    expect(table.exists()).toBe(true)
    const text = table.text()
    // The second feature scores badly and is biased low; both must be visible,
    // since a good R2 with a bias shifts every downstream cost.
    expect(text).toContain('0.4200')
    expect(text).toContain('-0.600')
  })

  it('distinguishes "no emulator" from "no held-out points"', () => {
    // An emulator trained before CA saved them is still a usable emulator; the
    // summary is real and only the plots are missing.
    const wrapper = mount(AnalysisPanel, { props: { emulatorMetadata: METADATA } })
    expect(wrapper.find('[data-testid="emu-no-points"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="emu-parity"]').exists()).toBe(false)
  })

  it('plots predicted against simulated on a shared range', () => {
    const wrapper = mount(AnalysisPanel, {
      props: { emulatorMetadata: METADATA, emulatorErrorPoints: POINTS },
    })
    const parity = wrapper.find('[data-testid="emu-parity"]')
    expect(parity.exists()).toBe(true)
    // One point per held-out sample.
    expect(parity.findAll('.parity-point')).toHaveLength(2)
    // And a residual panel per parameter, which is what says *where* it is wrong.
    expect(wrapper.findAll('[data-testid="emu-residual"]')).toHaveLength(2)
  })

  it('gives every emulator plot named, ticked axes', () => {
    // A scatter with no axes shows the shape of the error but not its size.
    const wrapper = mount(AnalysisPanel, {
      props: { emulatorMetadata: METADATA, emulatorErrorPoints: POINTS },
    })
    const parity = wrapper.find('[data-testid="emu-parity"]')
    expect(parity.text()).toContain('simulated')
    expect(parity.text()).toContain('emulated')
    expect(parity.findAll('.tick').length).toBeGreaterThan(0)

    for (const res of wrapper.findAll('[data-testid="emu-residual"]')) {
      expect(res.text()).toContain('residual')
      expect(res.findAll('.tick').length).toBeGreaterThan(0)
    }
  })

  it('normalises the residual by the truth, so the axis carries a size', () => {
    // A raw residual axis whose only labelled value is the zero line says the
    // error has a shape but not how big it is. Dividing by the ground truth
    // makes 0.05 mean "5% out" without knowing the feature's units.
    const wrapper = mount(AnalysisPanel, {
      props: { emulatorMetadata: METADATA, emulatorErrorPoints: POINTS },
    })
    const res = wrapper.find('[data-testid="emu-residual"]')
    expect(res.text()).toContain('residual / truth')
    // POINTS feature 0: residuals 0.05 and -0.10 against truths 1.0 and 2.0.
    const ys = res.findAll('.parity-point').map((p) => Number(p.attributes('cy')))
    const guideY = Number(res.find('[data-testid="chart-guide"]').attributes('y1'))
    // 0.05/1.0 = +0.05 and -0.10/2.0 = -0.05 are equal and opposite, so the two
    // points sit the same distance either side of zero -- which the raw
    // residuals (0.05, -0.10) would not do.
    expect(guideY - ys[0]).toBeCloseTo(ys[1] - guideY, 6)
    // and the ticks show real fractions, not just the zero line
    const labels = res.findAll('.tick').map((t) => t.text())
    expect(labels.filter((t) => t !== '0').length).toBeGreaterThan(0)
  })

  it('brackets zero on the residual axis, for real emulator error', () => {
    // Straight from a trained CardiovascularSystem emulator: truths of order
    // 1e-4 with residuals a few percent of them. The normalised half-range comes
    // out at ±0.0447, where the tick step used to round up to 0.05 -- wider than
    // the axis -- leaving the zero line as the only labelled value, on the one
    // plot whose job is to say how large the error is.
    const real = {
      theta: [[0.1, 1.0], [0.5, 1.5], [0.9, 2.0]],
      y_true: [[7.585e-5, 5.0], [1.02e-4, 6.0], [1.383e-4, 7.0]],
      y_pred: [[7.24e-5, 5.0], [1.05e-4, 6.0], [1.4e-4, 7.0]],
      residual: [[-3.393e-6, 0.0], [3.0e-6, 0.0], [1.7e-6, 0.0]],
      feature_labels: METADATA.feature_labels,
      param_entry_labels: ['a/p', 'a/q'],
    }
    const wrapper = mount(AnalysisPanel, {
      props: { emulatorMetadata: METADATA, emulatorErrorPoints: real },
    })
    const res = wrapper.find('[data-testid="emu-residual"]')
    // The y ticks are the ones left of the plot area; take their text.
    const labels = res.findAll('.tick').map((t) => t.text())
    const numbers = labels.map(Number).filter((n) => !Number.isNaN(n))
    expect(numbers.some((n) => n < 0), `no tick below zero: ${labels}`).toBe(true)
    expect(numbers.some((n) => n > 0), `no tick above zero: ${labels}`).toBe(true)
  })

  it('falls back to the truth spread when a truth is zero, and says so', () => {
    // Dividing by zero would plot Infinity; dropping the point would hide error.
    const zeroTruth = {
      ...POINTS,
      y_true: [[0.0, 5.0], [2.0, 6.0]],
    }
    const wrapper = mount(AnalysisPanel, {
      props: { emulatorMetadata: METADATA, emulatorErrorPoints: zeroTruth },
    })
    const res = wrapper.find('[data-testid="emu-residual"]')
    expect(res.text()).toContain('residual / range')
    expect(wrapper.text()).toContain('at least one of them is zero')
    for (const p of res.findAll('.parity-point')) {
      expect(Number.isFinite(Number(p.attributes('cy')))).toBe(true)
    }
  })

  it('scales the parity plot with the data, not with the box', () => {
    // The 1:1 line used to be a CSS-rotated div sized 141.4% of the box, which
    // is the diagonal only when the box is square; at any other width it left
    // exact predictions sitting off the line. It is drawn in the points' own
    // coordinates now, so this holds whatever width the panel is given.
    const exact = {
      ...POINTS,
      y_true: [[1.0, 5.0], [2.0, 6.0]],
      y_pred: [[1.0, 5.0], [2.0, 6.0]],
    }
    const wrapper = mount(AnalysisPanel, {
      props: { emulatorMetadata: METADATA, emulatorErrorPoints: exact },
    })
    const guide = wrapper.find('[data-testid="emu-parity"] [data-testid="chart-guide"]')
    const at = (a) => Number(guide.attributes(a))
    const points = wrapper.findAll('[data-testid="emu-parity"] .parity-point')
    expect(points.length).toBe(2)
    for (const pt of points) {
      const t = (Number(pt.attributes('cx')) - at('x1')) / (at('x2') - at('x1'))
      expect(Number(pt.attributes('cy'))).toBeCloseTo(at('y1') + t * (at('y2') - at('y1')), 6)
    }
  })

  it('heads the section "Emulator", not "Emulator error"', () => {
    // It carries the error statistics, but the section is the emulator's: the
    // plots and whether it is in use for analyses live here too.
    const wrapper = mount(AnalysisPanel, { props: { emulatorMetadata: METADATA } })
    const heads = wrapper.findAll('h2').map((h) => h.text())
    expect(heads.some((h) => h.startsWith('Emulator'))).toBe(true)
    expect(heads.some((h) => h.includes('Emulator error'))).toBe(false)
  })
})
