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

describe('useParamsForId — scale modifier parameters (#208)', () => {
  function modifier() {
    return [
      {
        qname: 'a/C', qnames: ['a/C', 'b/C'], name: 'C_scale',
        modifies: ['a/C', 'b/C'], operation: 'scale',
        baselines: { 'a/C': 2e-8, 'b/C': 4e-8 },
        min: 0.5, max: 2.0, initial_value: 1.0, identity: 1.0,
        name_for_plotting: 'C_scale',
      },
    ]
  }

  it('one slider carrying θ, starting at the identity', () => {
    const sliders = useSliders()
    useParamsForId(sliders).importParams(modifier())
    expect(sliders.count.value).toBe(1)
    const s = sliders.sliders['a/C']
    expect(s.kind).toBe('modifier')
    expect(s.value).toBe(1.0) // θ = identity: every target at its baseline
    expect(s.min).toBe(0.5)
  })

  it('paramDict expands θ·baseline per target — the analogue of the grouped fan-out', () => {
    const sliders = useSliders()
    useParamsForId(sliders).importParams(modifier())
    expect(sliders.paramDict.value).toEqual({ 'a/C': 2e-8, 'b/C': 4e-8 })
    sliders.setValue('a/C', 1.5)
    expect(sliders.paramDict.value['a/C']).toBeCloseTo(3e-8)
    expect(sliders.paramDict.value['b/C']).toBeCloseTo(6e-8)
  })

  it('analysisDict carries θ at the anchor, never a physical value', () => {
    // The θ/anchor contract: calibration start points and SA nominals match by
    // param_names[i][0], which for a modifier IS modifies[0].
    const sliders = useSliders()
    useParamsForId(sliders).importParams(modifier())
    sliders.setValue('a/C', 1.4)
    expect(sliders.analysisDict.value).toEqual({ 'a/C': 1.4 })
  })

  it('a target with no resolved baseline is skipped, not handed θ', () => {
    const sliders = useSliders()
    const entries = modifier()
    delete entries[0].baselines['b/C']
    useParamsForId(sliders).importParams(entries)
    expect(sliders.paramDict.value).toEqual({ 'a/C': 2e-8 })
  })

  it('modifierSpecs describes the slider for the cost-sensitivity request', () => {
    const sliders = useSliders()
    useParamsForId(sliders).importParams(modifier())
    sliders.setValue('a/C', 1.2)
    expect(sliders.modifierSpecs.value).toEqual([
      {
        name: 'C_scale', anchor: 'a/C', targets: ['a/C', 'b/C'],
        operation: 'scale', baselines: { 'a/C': 2e-8, 'b/C': 4e-8 },
        value: 1.2, bounds: [0.5, 2.0],
      },
    ])
  })

  it('paramSpecs points every member at the modifier for calibration write-back', () => {
    const sliders = useSliders()
    const p = useParamsForId(sliders)
    p.importParams(modifier())
    expect(p.paramSpecs.value['b/C'].primary).toBe('a/C')
    expect(p.paramSpecs.value['b/C'].kind).toBe('modifier')
  })
})
