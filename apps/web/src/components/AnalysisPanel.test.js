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
