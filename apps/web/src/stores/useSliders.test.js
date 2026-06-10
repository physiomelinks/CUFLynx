import { describe, it, expect } from 'vitest'
import { useSliders, shouldUseLog } from './useSliders'

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

  it('paramDict reflects current values', () => {
    const s = useSliders()
    s.addSlider('a/x', { min: 0, max: 10, value: 3 })
    expect(s.paramDict.value).toEqual({ 'a/x': 3 })
  })
})
