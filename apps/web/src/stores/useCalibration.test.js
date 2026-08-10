import { describe, it, expect, vi, beforeEach } from 'vitest'
import { watch, nextTick } from 'vue'

vi.mock('../lib/api', () => ({
  startCalibration: vi.fn(),
  getCalibrationStatus: vi.fn(),
  getCalibrationProgress: vi.fn(),
  cancelCalibration: vi.fn(),
  calibratedModelUrl: vi.fn((id) => `/api/calibration/${id}/calibrated_model`),
}))

import { startCalibration, getCalibrationStatus } from '../lib/api'
import { useCalibration, applyBestParams, expandBestFitParams } from './useCalibration'
import { useSliders } from './useSliders'

beforeEach(() => {
  startCalibration.mockReset()
  getCalibrationStatus.mockReset()
})

describe('expandBestFitParams (#208)', () => {
  it('expands a modifier slot from θ to θ·baseline per target', () => {
    // bestParams carries θ at every member (right for the anchor slider, wrong
    // for a simulation); the run's modifiers metadata carries the baselines.
    const best = { 'x/k': 7, 'a/C': 1.5, 'b/C': 1.5 }
    const modifiers = [
      { name: 'C_scale', anchor: 'a/C', targets: ['a/C', 'b/C'],
        operation: 'scale', baselines: [2, 4], theta: 1.5 },
    ]
    expect(expandBestFitParams(best, modifiers)).toEqual({ 'x/k': 7, 'a/C': 3, 'b/C': 6 })
  })

  it('drops a target with no resolved baseline rather than passing θ through', () => {
    const best = { 'a/C': 1.5 }
    const modifiers = [
      { name: 's', anchor: 'a/C', targets: ['a/C'], operation: 'scale',
        baselines: null, theta: 1.5 },
    ]
    expect(expandBestFitParams(best, modifiers)).toEqual({})
  })

  it('modifier θ still lands on the anchor slider through applyBestParams', () => {
    // The write-back path needs no expansion: the anchor slider *holds* θ.
    const s = useSliders()
    s.addSlider('a/C', {
      min: 0.5, max: 2, value: 1, kind: 'modifier',
      qnames: ['a/C', 'b/C'], baselines: { 'a/C': 2, 'b/C': 4 },
    })
    const spec = { min: 0.5, max: 2, qnames: ['a/C', 'b/C'], primary: 'a/C', kind: 'modifier' }
    applyBestParams(s, { 'a/C': spec, 'b/C': spec }, { 'a/C': 1.5, 'b/C': 1.5 })
    expect(s.sliders['a/C'].value).toBe(1.5)
    // And the live expansion follows: θ=1.5 over baselines [2, 4].
    expect(s.paramDict.value).toEqual({ 'a/C': 3, 'b/C': 6 })
  })
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

  describe('grouped parameters (issue #193)', () => {
    // CA reports the best fit per *member* qname (one value repeated), so a
    // grouped parameter arrives as several entries for one slider.
    const specs = () => {
      const spec = { min: 0, max: 10, qnames: ['a/E', 'b/E'], primary: 'a/E' }
      return { 'a/E': spec, 'b/E': spec }
    }

    it('lands every member on the one slider instead of splitting it apart', () => {
      const s = useSliders()
      s.addSlider('a/E', { min: 0, max: 10, value: 1, qnames: ['a/E', 'b/E'] })
      applyBestParams(s, specs(), { 'a/E': 6, 'b/E': 6 })
      expect(s.count.value).toBe(1)
      expect(s.sliders['a/E'].value).toBe(6)
      expect(s.sliders['b/E']).toBeUndefined()
    })

    it('re-creates a removed group as a group, not as its members', () => {
      const s = useSliders()
      applyBestParams(s, specs(), { 'a/E': 6, 'b/E': 6 })
      expect(s.count.value).toBe(1)
      expect(s.sliders['a/E'].qnames).toEqual(['a/E', 'b/E'])
    })
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

  it('exposes a calibrated-model download URL when the run saved one (#114)', async () => {
    startCalibration.mockResolvedValue({ job_id: 'jcal' })
    getCalibrationStatus.mockResolvedValue({
      state: 'done',
      lines: [],
      next_offset: 0,
      best_params: { 'a/x': 1.5 },
      cost: 0.25,
      calibrated_model_path: '/out/model_calibrated.cellml',
      error: null,
    })
    const c = useCalibration()
    await c.start('m1', { param_id_method: 'genetic_algorithm' })
    expect(c.calibratedModelPath.value).toBe('/out/model_calibrated.cellml')
    expect(c.calibratedModelUrl.value).toBe('/api/calibration/jcal/calibrated_model')
  })

  it('has no download URL when the run saved no calibrated model', async () => {
    startCalibration.mockResolvedValue({ job_id: 'j0' })
    getCalibrationStatus.mockResolvedValue({
      state: 'done', lines: [], next_offset: 0, best_params: {}, cost: 1, error: null,
    })
    const c = useCalibration()
    await c.start('m1', { param_id_method: 'genetic_algorithm' })
    expect(c.calibratedModelUrl.value).toBeNull()
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
