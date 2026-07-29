import { describe, it, expect, vi, beforeEach } from 'vitest'

vi.mock('../lib/api', () => ({
  listSavedRuns: vi.fn(),
  loadSavedRun: vi.fn(),
}))

import { listSavedRuns, loadSavedRun } from '../lib/api'
import { useSavedRuns } from './useSavedRuns'
import { PALETTE } from '../lib/plot'

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
      expect(PALETTE).toContain(s.colorFor('run_a'))
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
