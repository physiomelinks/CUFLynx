import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import ReductionResults from './ReductionResults.vue'
import AnalysisPanel from './AnalysisPanel.vue'

const ButtonStub = {
  props: ['disabled', 'label'],
  emits: ['click'],
  template: '<button :disabled="disabled" v-bind="$attrs" @click="$emit(\'click\')">{{ label }}</button>',
}

describe('ReductionResults', () => {
  const REDUCTION = {
    report_dir: '/out/sa/param_reduction',
    reduced_params_for_id: '/out/sa/param_reduction/reduced_params_for_id.json',
    heatmap: '/out/sa/param_reduction/pca_mode_sobol_ST.png',
    reduction: { method: 'pca' },
    selected: [{ parameter: 'm/alpha', score: 0.7, mode: 1 }],
    modes: [
      { mode: 1, std: 4.0, variance_share: 0.9, informative: true },
      { mode: 2, std: 0.5, variance_share: 0.1, informative: false },
    ],
    n_informative_modes: 1,
    unexplained_effects: { 'm/beta': 0.3 },
  }

  it('shows the kept parameters, the modes and the unexplained effects', () => {
    const wrapper = mount(ReductionResults, { props: { reduction: REDUCTION }, global: { stubs: { Button: ButtonStub } } })
    expect(wrapper.find('[data-testid="pr-kept"]').text()).toContain('m/alpha')
    const modes = wrapper.find('[data-testid="pr-modes"]').findAll('tbody tr')
    expect(modes).toHaveLength(2)
    expect(modes[1].text()).toContain('no')
    expect(wrapper.find('[data-testid="pr-unexplained"]').text()).toContain('m/beta')
  })

  it('loads the heatmap from the API, never a blob: URL', () => {
    const wrapper = mount(ReductionResults, { props: { reduction: REDUCTION }, global: { stubs: { Button: ButtonStub } } })
    const src = wrapper.find('[data-testid="pr-heatmap"]').attributes('src')
    expect(src).toBe(
      '/api/param_reduction/heatmap?report_dir=%2Fout%2Fsa%2Fparam_reduction',
    )
  })

  const COMPOSITION = [
    {
      mode: 1,
      features: [
        { label: 'x (max) (Exp0, Sub0)', loading: 0.3 },
        { label: 'y (max) (Exp0, Sub0)', loading: -0.9 },
        { label: 'z (mean) (Exp0, Sub0)', loading: null },
      ],
      parameters: [
        { parameter: 'm/alpha', S1: 0.1, ST: 0.2 },
        { parameter: 'm/beta', S1: 0.6, ST: 0.8 },
      ],
    },
    { mode: 2, features: [{ label: 'x (max) (Exp0, Sub0)', loading: 0.5 }], parameters: [] },
  ]

  it('shows a tab per PCA mode, with its signed feature loadings and parameter ST bars', async () => {
    const wrapper = mount(ReductionResults, {
      props: { reduction: { ...REDUCTION, mode_composition: COMPOSITION } },
      global: { stubs: { Button: ButtonStub } },
    })
    const tabs = wrapper.findAll('[role="tab"]')
    expect(tabs.map((t) => t.text())).toEqual(['Mode 1 (90.0%)', 'Mode 2 (10.0%)'])
    expect(tabs[0].attributes('aria-selected')).toBe('true')
    expect(tabs[1].classes()).toContain('dim') // not informative

    // Largest |loading| first; a negative one runs left of the centre line; nan is dropped.
    const loadings = wrapper.find('[data-testid="pr-mode-loadings"]')
    expect(loadings.findAll('.bar-label').map((l) => l.text())).toEqual([
      'y (max) (Exp0, Sub0)',
      'x (max) (Exp0, Sub0)',
    ])
    const bars = loadings.findAll('.bar')
    expect(bars[0].classes()).toContain('neg')
    expect(bars[0].attributes('style')).toContain('right: 50%')
    expect(bars[0].attributes('style')).toContain('width: 50%')
    expect(bars[1].attributes('style')).toContain('left: 50%')
    const st = wrapper.find('[data-testid="pr-mode-st"]')
    expect(st.findAll('.bar-label').map((l) => l.text())).toEqual(['m/beta', 'm/alpha'])
    expect(st.find('.bar').attributes('style')).toContain('width: 80%')

    await wrapper.find('[data-testid="pr-mode-tab-2"]').trigger('click')
    expect(wrapper.find('[data-testid="pr-mode-tab-2"]').attributes('aria-selected')).toBe('true')
    expect(wrapper.find('[data-testid="pr-mode-loadings"]').findAll('.bar-label')).toHaveLength(1)
    expect(wrapper.find('[data-testid="pr-mode-st"]').exists()).toBe(false)
  })

  it('has no mode section without CA\'s mode files', () => {
    const wrapper = mount(ReductionResults, { props: { reduction: REDUCTION }, global: { stubs: { Button: ButtonStub } } })
    expect(wrapper.find('[data-testid="pr-composition"]').exists()).toBe(false)
  })

  it('"Calibrate these" adopts the reduction\'s params_for_id', async () => {
    const wrapper = mount(ReductionResults, { props: { reduction: REDUCTION }, global: { stubs: { Button: ButtonStub } } })
    await wrapper.find('[data-testid="pr-adopt"]').trigger('click')
    expect(wrapper.emitted('adopt')[0]).toEqual(['/out/sa/param_reduction'])
  })
})


describe('the Analysis tab shows a reduction', () => {
  it('in its own section, whose "Calibrate these" adopts it', async () => {
    const empty = mount(AnalysisPanel)
    expect(empty.find('[data-testid="reduction-section"]').text()).toContain('Identifiability tab')
    const reduction = { report_dir: '/r', reduced_params_for_id: '/r/x.json', reduction: { method: 'sobol' },
      selected: [{ parameter: 'm/alpha', score: 0.5 }] }
    const w = mount(AnalysisPanel, { props: { reduction }, global: { stubs: { Button: ButtonStub } } })
    expect(w.find('[data-testid="reduction-section"] [data-testid="pr-kept"]').text()).toContain('m/alpha')
    await w.find('[data-testid="pr-adopt"]').trigger('click')
    expect(w.emitted('adopt-reduction')[0]).toEqual(['/r'])
  })

  it('is the open section when a reduction step is selected', () => {
    const w = mount(AnalysisPanel, { props: { focus: 'reduction' } })
    expect('folded' in w.find('[data-testid="reduction-section"]').element.dataset).toBe(false)
    expect(w.findAll('section.analysis-section').filter((s) => !('folded' in s.element.dataset))).toHaveLength(1)
  })
})
