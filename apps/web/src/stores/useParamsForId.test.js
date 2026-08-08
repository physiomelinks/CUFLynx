import { describe, it, expect } from 'vitest'
import { useSliders } from './useSliders'
import { useParamsForId } from './useParamsForId'

function paramsFixture() {
  return [
    { qname: 'm/alpha', min: 0.1, max: 7, initial_value: 5, name_for_plotting: '\\alpha' },
    { qname: 'm/beta', min: 0.01, max: 2, initial_value: null, name_for_plotting: '\\beta' },
    { qname: 'm/wide', min: 1, max: 1e6, initial_value: null },
  ]
}

describe('useParamsForId', () => {
  it('test_import_creates_slider_for_each_param', () => {
    const sliders = useSliders()
    const p = useParamsForId(sliders)
    p.importParams(paramsFixture())
    expect(sliders.count.value).toBe(3)
  })

  it('test_initial_value_uses_model_default_when_available', () => {
    const sliders = useSliders()
    useParamsForId(sliders).importParams(paramsFixture())
    expect(sliders.sliders['m/alpha'].value).toBe(5)
  })

  it('test_initial_value_uses_midpoint_when_model_default_null', () => {
    const sliders = useSliders()
    useParamsForId(sliders).importParams(paramsFixture())
    // (0.01 + 2) / 2
    expect(sliders.sliders['m/beta'].value).toBeCloseTo(1.005)
  })

  it('test_log_scale_enabled_when_range_exceeds_threshold', () => {
    const sliders = useSliders()
    useParamsForId(sliders).importParams(paramsFixture())
    expect(sliders.sliders['m/wide'].log).toBe(true)
  })

  it('test_clear_removes_all_imported_sliders', () => {
    const sliders = useSliders()
    const p = useParamsForId(sliders)
    p.importParams(paramsFixture())
    p.clear()
    expect(sliders.count.value).toBe(0)
  })
})

describe('useParamsForId — grouped parameters (issue #193)', () => {
  function grouped() {
    return [
      {
        qname: 'ao_A/E',
        qnames: ['ao_A/E', 'ao_B/E', 'ao_C/E', 'ao_D/E'],
        min: 3e5,
        max: 1.3e6,
        initial_value: 4e5,
        name_for_plotting: 'E_{AR}',
      },
    ]
  }

  it('makes one slider for a row naming four vessels, not four', () => {
    // The bug: four handles for one quantity, so moving one and not the others
    // put the model in a state it never has.
    const sliders = useSliders()
    useParamsForId(sliders).importParams(grouped())
    expect(sliders.count.value).toBe(1)
    expect(sliders.sliders['ao_A/E'].qnames).toEqual([
      'ao_A/E',
      'ao_B/E',
      'ao_C/E',
      'ao_D/E',
    ])
  })

  it('sets all four components from the one handle', () => {
    const sliders = useSliders()
    useParamsForId(sliders).importParams(grouped())
    expect(Object.keys(sliders.paramDict.value)).toHaveLength(4)
    expect(sliders.paramDict.value['ao_D/E']).toBe(4e5)
  })

  it('indexes every member in paramSpecs, pointing back at the one slider', () => {
    // CA's best fit names each member separately; without this back-pointer the
    // write-back would hand each of them a new slider of its own.
    const sliders = useSliders()
    const p = useParamsForId(sliders)
    p.importParams(grouped())
    expect(p.paramSpecs.value['ao_C/E'].primary).toBe('ao_A/E')
    expect(p.paramSpecs.value['ao_C/E'].name_for_plotting).toBe('E_{AR}')
  })

  it('carries the backend warning onto the slider', () => {
    const sliders = useSliders()
    useParamsForId(sliders).importParams([
      { ...grouped()[0], warning: 'components disagree' },
    ])
    expect(sliders.sliders['ao_A/E'].warning).toBe('components disagree')
  })

  it('clearing removes the group by its one key', () => {
    const sliders = useSliders()
    const p = useParamsForId(sliders)
    p.importParams(grouped())
    p.clear()
    expect(sliders.count.value).toBe(0)
  })
})
