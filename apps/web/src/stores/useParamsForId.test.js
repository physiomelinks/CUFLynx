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
