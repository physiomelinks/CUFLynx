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
