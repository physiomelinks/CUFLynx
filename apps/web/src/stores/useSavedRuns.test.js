import { describe, it, expect, vi, beforeEach } from 'vitest'

vi.mock('../lib/api', () => ({
  listSavedRuns: vi.fn(),
  loadSavedRun: vi.fn(),
}))

import { listSavedRuns, loadSavedRun } from '../lib/api'
import { useSavedRuns } from './useSavedRuns'
import { PALETTE, SAVED_PALETTE } from '../lib/plot'

const RUN_A = {
  prefix: 'run_a',
  path: '/out/run_a_outputs.json',
  saved_at: '2026-01-02T00:00:00+00:00',
  params: { 'm/alpha': 1.5 },
  variables: ['m/x'],
}
const RUN_B = { ...RUN_A, prefix: 'run_b', path: '/out/run_b_outputs.json' }

const RECORD_A = {
  prefix: 'run_a',
  params: { 'm/alpha': 1.5 },
  time: [0, 1, 2],
  outputs: { 'm/x': [1, 2, 3] },
}

beforeEach(() => {
  listSavedRuns.mockReset()
  loadSavedRun.mockReset()
  listSavedRuns.mockResolvedValue({ runs: [RUN_A, RUN_B] })
  loadSavedRun.mockResolvedValue(RECORD_A)
})

describe('useSavedRuns (#126)', () => {
  it('lists what is on disk, none shown to begin with', async () => {
    const s = useSavedRuns()
    await s.refresh('/out')
    expect(s.items.value.map((r) => r.prefix)).toEqual(['run_a', 'run_b'])
    expect(s.items.value.every((r) => !r.shown)).toBe(true)
  })

  it('an unreadable directory lists nothing rather than throwing', async () => {
    listSavedRuns.mockRejectedValue(new Error('nope'))
    const s = useSavedRuns()
    await s.refresh('/out')
    expect(s.runs.value).toEqual([])
  })

  // Series are the bulk of a run; the list only needs metadata to draw boxes.
  it('fetches the traces only when a run is first shown', async () => {
    const s = useSavedRuns()
    await s.refresh('/out')
    expect(loadSavedRun).not.toHaveBeenCalled()

    await s.toggle('run_a')
    expect(loadSavedRun).toHaveBeenCalledTimes(1)

    // Untick and re-tick: already loaded, so no second fetch.
    await s.toggle('run_a')
    await s.toggle('run_a')
    expect(loadSavedRun).toHaveBeenCalledTimes(1)
  })

  it('reports a failed load instead of showing an empty run', async () => {
    loadSavedRun.mockRejectedValue(new Error('gone'))
    const s = useSavedRuns()
    await s.refresh('/out')
    expect(await s.toggle('run_a')).toBe(false)
    expect(s.isShown('run_a')).toBe(false)
    expect(s.error.value).toContain('run_a')
  })

  describe('colours', () => {
    it('gives a shown run a colour and an unshown one none', async () => {
      const s = useSavedRuns()
      await s.refresh('/out')
      expect(s.colorFor('run_a')).toBe('')
      await s.toggle('run_a')
      expect(SAVED_PALETTE).toContain(s.colorFor('run_a'))
    })

    // A saved trace once came out the same green as a `max` obs line, so the
    // colour stopped telling you which kind of series you were looking at.
    it('never reuses a colour the live traces or obs overlays draw with', async () => {
      for (const c of SAVED_PALETTE) expect(PALETTE).not.toContain(c)
    })

    it('walks its own palette, so a second run is not the first colour again', async () => {
      const s = useSavedRuns()
      await s.refresh('/out')
      loadSavedRun.mockResolvedValue({ ...RECORD_A, prefix: 'run_b' })
      await s.toggle('run_a')
      await s.toggle('run_b')
      expect(s.colorFor('run_a')).toBe(SAVED_PALETTE[0])
      expect(s.colorFor('run_b')).toBe(SAVED_PALETTE[1])
    })

    it('gives two shown runs different colours', async () => {
      const s = useSavedRuns()
      await s.refresh('/out')
      loadSavedRun.mockResolvedValue({ ...RECORD_A, prefix: 'run_b' })
      await s.toggle('run_a')
      await s.toggle('run_b')
      expect(s.colorFor('run_a')).not.toBe(s.colorFor('run_b'))
    })

    // The colour ties the tick box, the trace and the slider marker together, so
    // it must not shuffle under the user when an unrelated run is unticked.
    it('keeps a run its colour when another is unticked', async () => {
      const s = useSavedRuns()
      await s.refresh('/out')
      await s.toggle('run_a')
      const before = s.colorFor('run_a')
      await s.toggle('run_b')
      await s.toggle('run_b')
      expect(s.colorFor('run_a')).toBe(before)
    })
  })

  describe('seriesFor', () => {
    it('returns the shown runs trace for a variable, with its colour', async () => {
      const s = useSavedRuns()
      await s.refresh('/out')
      await s.toggle('run_a')
      const series = s.seriesFor('m/x')
      expect(series).toHaveLength(1)
      expect(series[0]).toMatchObject({
        prefix: 'run_a',
        values: [1, 2, 3],
        time: [0, 1, 2],
      })
      expect(series[0].color).toBe(s.colorFor('run_a'))
    })

    it('returns nothing for a variable the run did not record', async () => {
      const s = useSavedRuns()
      await s.refresh('/out')
      await s.toggle('run_a')
      expect(s.seriesFor('m/other')).toEqual([])
    })

    it('returns nothing while no run is shown', async () => {
      const s = useSavedRuns()
      await s.refresh('/out')
      expect(s.seriesFor('m/x')).toEqual([])
    })

    it('picks the matching experiment for a protocol run', async () => {
      loadSavedRun.mockResolvedValue({
        prefix: 'run_a',
        params: {},
        experiments: [
          { time: [0, 1], outputs: { 'm/x': [1, 2] } },
          { time: [0, 1], outputs: { 'm/x': [3, 4] } },
        ],
      })
      const s = useSavedRuns()
      await s.refresh('/out')
      await s.toggle('run_a')
      expect(s.seriesFor('m/x', 0)[0].values).toEqual([1, 2])
      expect(s.seriesFor('m/x', 1)[0].values).toEqual([3, 4])
    })

    // Otherwise experiment 0's trace would be drawn on experiment 2's axes.
    it('contributes nothing to an experiment the run does not have', async () => {
      loadSavedRun.mockResolvedValue({
        prefix: 'run_a',
        params: {},
        experiments: [{ time: [0, 1], outputs: { 'm/x': [1, 2] } }],
      })
      const s = useSavedRuns()
      await s.refresh('/out')
      await s.toggle('run_a')
      expect(s.seriesFor('m/x', 2)).toEqual([])
    })
  })

  describe('markersFor', () => {
    it('gives the saved value and colour for a shown run', async () => {
      const s = useSavedRuns()
      await s.refresh('/out')
      await s.toggle('run_a')
      expect(s.markersFor('m/alpha')).toEqual([
        { prefix: 'run_a', color: s.colorFor('run_a'), value: 1.5 },
      ])
    })

    it('skips a parameter the run has no value for', async () => {
      const s = useSavedRuns()
      await s.refresh('/out')
      await s.toggle('run_a')
      expect(s.markersFor('m/absent')).toEqual([])
    })

    it('skips a non-finite saved value rather than marking zero', async () => {
      loadSavedRun.mockResolvedValue({ ...RECORD_A, params: { 'm/alpha': null } })
      const s = useSavedRuns()
      await s.refresh('/out')
      await s.toggle('run_a')
      expect(s.markersFor('m/alpha')).toEqual([])
    })
  })

  // A deleted file must not keep holding a colour slot.
  it('drops a tick whose run has disappeared on refresh', async () => {
    const s = useSavedRuns()
    await s.refresh('/out')
    await s.toggle('run_a')
    listSavedRuns.mockResolvedValue({ runs: [RUN_B] })
    await s.refresh('/out')
    expect(s.isShown('run_a')).toBe(false)
  })

  it('clear forgets everything', async () => {
    const s = useSavedRuns()
    await s.refresh('/out')
    await s.toggle('run_a')
    s.clear()
    expect(s.runs.value).toEqual([])
    expect(s.shown.value).toEqual([])
  })
})

// The calibration best fit is tickable alongside the saved files (#126), but it
// is not a file: its parameter values exist as soon as a calibration finishes,
// while its traces have to be produced by running the model at them.
describe('useSavedRuns virtual runs (best fit)', () => {
  const BEST = {
    prefix: 'best fit',
    title: 'Latest calibration best fit',
    params: { 'm/alpha': 9 },
  }
  const record = { prefix: 'best fit', params: { 'm/alpha': 9 }, time: [0, 1], outputs: { 'm/x': [7, 8] } }

  const withBestFit = async (load = vi.fn().mockResolvedValue(record)) => {
    const s = useSavedRuns()
    await s.refresh('/out')
    s.setVirtualRun({ ...BEST, load })
    return { s, load }
  }

  it('appears in the list, ahead of the files, flagged as virtual', async () => {
    const { s } = await withBestFit()
    expect(s.items.value[0]).toMatchObject({ prefix: 'best fit', virtual: true })
    expect(s.items.value.map((r) => r.prefix)).toEqual(['best fit', 'run_a', 'run_b'])
  })

  it('offers its parameters before anything has been run', async () => {
    const { s } = await withBestFit()
    expect(s.items.value[0].params).toEqual({ 'm/alpha': 9 })
  })

  it('runs the model only when it is first ticked', async () => {
    const { s, load } = await withBestFit()
    expect(load).not.toHaveBeenCalled()
    await s.toggle('best fit')
    expect(load).toHaveBeenCalledTimes(1)
    expect(s.seriesFor('m/x')[0].values).toEqual([7, 8])
  })

  it('does not re-run it on a re-tick', async () => {
    const { s, load } = await withBestFit()
    await s.toggle('best fit')
    await s.toggle('best fit')
    await s.toggle('best fit')
    expect(load).toHaveBeenCalledTimes(1)
  })

  it('never asks the file endpoint for it', async () => {
    const { s } = await withBestFit()
    await s.toggle('best fit')
    expect(loadSavedRun).not.toHaveBeenCalled()
  })

  it('takes a colour from the saved palette like any other run', async () => {
    const { s } = await withBestFit()
    await s.toggle('best fit')
    expect(SAVED_PALETTE).toContain(s.colorFor('best fit'))
  })

  // A new calibration under the same name is a different run.
  it('drops the old traces when the fit is replaced', async () => {
    const { s, load } = await withBestFit()
    await s.toggle('best fit')
    expect(s.isShown('best fit')).toBe(true)

    s.setVirtualRun({ ...BEST, params: { 'm/alpha': 11 }, load })
    // Taken down rather than left showing traces from the previous fit.
    expect(s.isShown('best fit')).toBe(false)
    expect(s.series.value['best fit']).toBeUndefined()

    await s.toggle('best fit')
    expect(load).toHaveBeenCalledTimes(2)
  })

  it('survives a refresh, having no file to disappear', async () => {
    const { s } = await withBestFit()
    await s.toggle('best fit')
    await s.refresh('/out')
    expect(s.isShown('best fit')).toBe(true)
    expect(s.items.value[0].prefix).toBe('best fit')
  })

  it('is removed when the calibration is cleared', async () => {
    const { s } = await withBestFit()
    await s.toggle('best fit')
    s.removeVirtualRun('best fit')
    expect(s.items.value.map((r) => r.prefix)).toEqual(['run_a', 'run_b'])
    expect(s.isShown('best fit')).toBe(false)
  })

  it('reports a failed run instead of showing an empty overlay', async () => {
    const { s } = await withBestFit(vi.fn().mockRejectedValue(new Error('solver failed')))
    expect(await s.toggle('best fit')).toBe(false)
    expect(s.isShown('best fit')).toBe(false)
    expect(s.error.value).toContain('best fit')
  })

  it('stays unticked when the loader yields nothing (no model loaded)', async () => {
    const { s } = await withBestFit(vi.fn().mockResolvedValue(null))
    expect(await s.toggle('best fit')).toBe(false)
    expect(s.isShown('best fit')).toBe(false)
  })

  it('marks the sliders with the fitted values', async () => {
    const { s } = await withBestFit()
    await s.toggle('best fit')
    expect(s.markersFor('m/alpha')).toEqual([
      { prefix: 'best fit', color: s.colorFor('best fit'), value: 9 },
    ])
  })
})

// Issue #150: a saved run has its own x series, so a phase-plane cell overlays
// it against that -- and every ticked run must be drawn, not just one.
describe('useSavedRuns phase-plane overlays (#150)', () => {
  const RECORD = (prefix) => ({
    prefix,
    params: {},
    time: [0, 1, 2],
    outputs: { 'm/y': [1, 2, 3], 'm/x': [7, 8, 9] },
  })

  const withTwoShown = async () => {
    const s = useSavedRuns()
    await s.refresh('/out')
    loadSavedRun.mockImplementation(async () => RECORD('r'))
    await s.toggle('run_a')
    await s.toggle('run_b')
    return s
  }

  it('returns every shown run for a variable', async () => {
    const s = await withTwoShown()
    expect(s.seriesFor('m/y')).toHaveLength(2)
  })

  it('returns the run own x series when asked for a phase-plane cell', async () => {
    const s = await withTwoShown()
    const series = s.seriesFor('m/y', null, 'm/x')
    expect(series).toHaveLength(2)
    expect(series[0].xValues).toEqual([7, 8, 9])
  })

  // Better nothing than a curve pinned to another run's x.
  it('omits a run with no x series for that cell', async () => {
    const s = useSavedRuns()
    await s.refresh('/out')
    loadSavedRun.mockResolvedValue({
      prefix: 'run_a',
      params: {},
      time: [0, 1],
      outputs: { 'm/y': [1, 2] },
    })
    await s.toggle('run_a')
    expect(s.seriesFor('m/y', null, 'm/x')).toEqual([])
    // ...but it is still fine as a time series.
    expect(s.seriesFor('m/y')).toHaveLength(1)
  })

  it('leaves xValues off a plain time-series lookup', async () => {
    const s = await withTwoShown()
    expect('xValues' in s.seriesFor('m/y')[0]).toBe(false)
  })
})
