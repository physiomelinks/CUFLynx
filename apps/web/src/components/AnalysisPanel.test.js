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
