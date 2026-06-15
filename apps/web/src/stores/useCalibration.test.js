import { describe, it, expect, vi, beforeEach } from 'vitest'
import { watch, nextTick } from 'vue'

vi.mock('../lib/api', () => ({
  startCalibration: vi.fn(),
  getCalibrationStatus: vi.fn(),
  cancelCalibration: vi.fn(),
}))

import { startCalibration, getCalibrationStatus } from '../lib/api'
import { useCalibration, applyBestParams } from './useCalibration'
import { useSliders } from './useSliders'

beforeEach(() => {
  startCalibration.mockReset()
  getCalibrationStatus.mockReset()
})

describe('applyBestParams', () => {
  it('updates an existing slider', () => {
    const s = useSliders()
    s.addSlider('a/x', { min: 0, max: 10, value: 1 })
    applyBestParams(s, {}, { 'a/x': 6 })
    expect(s.sliders['a/x'].value).toBe(6)
  })

  it('adds a missing slider using its params_for_id spec', () => {
    const s = useSliders()
    applyBestParams(s, { 'a/y': { min: 1, max: 9 } }, { 'a/y': 4 })
    expect(s.sliders['a/y'].min).toBe(1)
    expect(s.sliders['a/y'].max).toBe(9)
    expect(s.sliders['a/y'].value).toBe(4)
  })

  it('adds a missing slider with a fallback range when no spec', () => {
    const s = useSliders()
    applyBestParams(s, {}, { 'a/z': 5 })
    expect(s.sliders['a/z'].value).toBe(5)
    expect(s.sliders['a/z'].min).toBe(0)
    expect(s.sliders['a/z'].max).toBe(10)
  })
})

describe('useCalibration', () => {
  it('start polls once and resolves to done', async () => {
    startCalibration.mockResolvedValue({ job_id: 'j1' })
    getCalibrationStatus.mockResolvedValue({
      state: 'done',
      lines: ['generation 0', 'best cost: 0.25'],
      next_offset: 2,
      best_params: { 'a/x': 1.5 },
      cost: 0.25,
      error: null,
    })
    const c = useCalibration()
    await c.start('m1', { param_id_method: 'genetic_algorithm' })
    expect(c.state.value).toBe('done')
    expect(c.bestParams.value).toEqual({ 'a/x': 1.5 })
    expect(c.lines.value).toEqual(['generation 0', 'best cost: 0.25'])
  })

  it('accumulates lines across running -> done polls', async () => {
    vi.useFakeTimers()
    startCalibration.mockResolvedValue({ job_id: 'j2' })
    getCalibrationStatus
      .mockResolvedValueOnce({ state: 'running', lines: ['gen 0'], next_offset: 1 })
      .mockResolvedValueOnce({
        state: 'done',
        lines: ['best cost: 0.1'],
        next_offset: 2,
        best_params: { 'a/x': 2 },
        cost: 0.1,
        error: null,
      })
    const c = useCalibration({ intervalMs: 10 })
    await c.start('m1', {})
    expect(c.state.value).toBe('running')
    await vi.advanceTimersByTimeAsync(20)
    expect(c.state.value).toBe('done')
    expect(c.lines.value).toEqual(['gen 0', 'best cost: 0.1'])
    expect(c.bestParams.value).toEqual({ 'a/x': 2 })
    vi.useRealTimers()
  })

  // Regression: when calibration finishes, the best-fit values must reach the
  // sliders. App.vue does this with a watcher on `state` that calls
  // applyBestParams once done. This only works if poll() sets `bestParams`
  // BEFORE flipping `state` to 'done' — otherwise the watcher fires with a
  // stale (null) bestParams and the sliders are never updated.
  it('applies best-fit params to the sliders when calibration finishes', async () => {
    startCalibration.mockResolvedValue({ job_id: 'j3' })
    getCalibrationStatus.mockResolvedValue({
      state: 'done',
      lines: [],
      next_offset: 0,
      best_params: { 'a/x': 6 },
      cost: 0.1,
      error: null,
    })
    const s = useSliders()
    s.addSlider('a/x', { min: 0, max: 10, value: 1 })
    const c = useCalibration()
    watch(
      () => c.state.value,
      (state) => {
        if (state === 'done' && c.bestParams.value) {
          applyBestParams(s, {}, c.bestParams.value)
        }
      },
    )
    await c.start('m1', {})
    await nextTick()
    expect(s.sliders['a/x'].value).toBe(6)
  })
})
