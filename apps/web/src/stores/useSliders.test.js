import { describe, it, expect } from 'vitest'
import {
  useSliders,
  shouldUseLog,
  valueToSlider,
  sliderToValue,
  SLIDER_STEPS,
} from './useSliders'

describe('useSliders', () => {
  it('test_add_slider_increments_count', () => {
    const s = useSliders()
    expect(s.count.value).toBe(0)
    s.addSlider('a/x', { min: 0, max: 10 })
    s.addSlider('a/y', { min: 0, max: 10 })
    expect(Object.keys(s.sliders).length).toBe(2)
    expect(s.count.value).toBe(2)
  })

  it('test_remove_slider_removes_key', () => {
    const s = useSliders()
    s.addSlider('a/x', { min: 0, max: 10 })
    s.removeSlider('a/x')
    expect(s.sliders['a/x']).toBeUndefined()
  })

  it('test_slider_value_within_range', () => {
    const s = useSliders()
    s.addSlider('a/x', { min: 0, max: 10, value: 5 })
    s.setValue('a/x', 20)
    expect(s.sliders['a/x'].value).toBe(10)
    s.setValue('a/x', -5)
    expect(s.sliders['a/x'].value).toBe(0)
  })

  it('test_log_slider_heuristic', () => {
    const s = useSliders()
    const wide = s.addSlider('a/wide', { min: 1, max: 1e5 })
    expect(wide.log).toBe(true)
    const narrow = s.addSlider('a/narrow', { min: 0, max: 1 })
    expect(narrow.log).toBe(false)
    // Direct heuristic checks.
    expect(shouldUseLog(1e-4, 1)).toBe(true)
    expect(shouldUseLog(0, 10)).toBe(false)
  })

  it('maps linear values to the slider track and back', () => {
    const s = { qname: 'a/x', min: 0, max: 10, value: 5, log: false }
    expect(valueToSlider(s)).toBe(SLIDER_STEPS / 2)
    expect(sliderToValue(s, SLIDER_STEPS / 2)).toBeCloseTo(5)
  })

  it('log slider spreads a small value across the track (not stuck left)', () => {
    // value 1e-2 is the geometric centre of [1e-4, 1] -> 50% on a log track,
    // but only ~1% on a (buggy) linear track. This is the "stuck on LHS" fix.
    const s = { qname: 'a/g', min: 1e-4, max: 1, value: 1e-2, log: true }
    expect(valueToSlider(s)).toBe(SLIDER_STEPS / 2)
    expect(sliderToValue(s, SLIDER_STEPS / 2)).toBeCloseTo(1e-2)
    // round-trips at the extremes
    expect(valueToSlider({ ...s, value: 1e-4 })).toBe(0)
    expect(valueToSlider({ ...s, value: 1 })).toBe(SLIDER_STEPS)
  })

  it('falls back to linear when the range is non-positive', () => {
    const s = { qname: 'a/z', min: 0, max: 10, value: 5, log: true }
    expect(valueToSlider(s)).toBe(SLIDER_STEPS / 2) // log ignored, min<=0
  })

  it('paramDict reflects current values', () => {
    const s = useSliders()
    s.addSlider('a/x', { min: 0, max: 10, value: 3 })
    expect(s.paramDict.value).toEqual({ 'a/x': 3 })
  })

  it('resetToInit restores each slider to the value it was created with', () => {
    const s = useSliders()
    s.addSlider('a/x', { min: 0, max: 10, value: 3 })
    s.addSlider('a/y', { min: 0, max: 10, value: 7 })
    s.setValue('a/x', 8)
    s.setValue('a/y', 1)
    expect(s.sliders['a/x'].value).toBe(8)
    s.resetToInit()
    expect(s.sliders['a/x'].value).toBe(3)
    expect(s.sliders['a/y'].value).toBe(7)
  })

  describe('load saved values (issue #106)', () => {
    it('order reflects the current sliders (the .npy save/load order)', () => {
      const s = useSliders()
      s.addSlider('a/x', { min: 0, max: 10, value: 3 })
      s.addSlider('a/y', { min: 0, max: 10, value: 7 })
      expect(s.order.value).toEqual(['a/x', 'a/y'])
    })

    it('applyValues sets existing sliders, clamped, and ignores unknown qnames', () => {
      const s = useSliders()
      s.addSlider('a/x', { min: 0, max: 10, value: 5 })
      s.addSlider('a/y', { min: 0, max: 10, value: 5 })
      s.applyValues({ 'a/x': 999, 'a/y': 2, 'a/gone': 4 })
      expect(s.sliders['a/x'].value).toBe(10) // clamped to max
      expect(s.sliders['a/y'].value).toBe(2)
      expect(s.sliders['a/gone']).toBeUndefined() // unknown -> ignored, no throw
    })

    it('applyValues tolerates empty/undefined input', () => {
      const s = useSliders()
      s.addSlider('a/x', { min: 0, max: 10, value: 5 })
      s.applyValues(undefined)
      s.applyValues({})
      expect(s.sliders['a/x'].value).toBe(5)
    })
  })

  describe('grouped parameters (issue #193)', () => {
    it('a slider drives only itself unless told otherwise', () => {
      const s = useSliders()
      s.addSlider('a/x', { min: 0, max: 10, value: 3 })
      expect(s.sliders['a/x'].qnames).toEqual(['a/x'])
      expect(s.paramDict.value).toEqual({ 'a/x': 3 })
    })

    it('one grouped slider sets every component it names', () => {
      // The params_for_id row "a b c, E" is one parameter in three components;
      // the model has no variable for the group, so the run must be told all three.
      const s = useSliders()
      s.addSlider('a/E', { min: 0, max: 10, value: 4, qnames: ['a/E', 'b/E', 'c/E'] })
      expect(s.paramDict.value).toEqual({ 'a/E': 4, 'b/E': 4, 'c/E': 4 })
    })

    it('moving the handle moves every component with it', () => {
      const s = useSliders()
      s.addSlider('a/E', { min: 0, max: 10, value: 4, qnames: ['a/E', 'b/E'] })
      s.setValue('a/E', 9)
      expect(s.paramDict.value).toEqual({ 'a/E': 9, 'b/E': 9 })
    })

    it('the save/load order counts parameters, not components', () => {
      // A .npy is saved in this order and CA writes one value per params_for_id
      // row, so a group must occupy exactly one slot.
      const s = useSliders()
      s.addSlider('a/E', { min: 0, max: 10, value: 4, qnames: ['a/E', 'b/E'] })
      s.addSlider('a/R', { min: 0, max: 10, value: 1 })
      expect(s.order.value).toEqual(['a/E', 'a/R'])
    })
  })
})
