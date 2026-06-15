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
})
