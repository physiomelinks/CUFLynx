import { describe, it, expect, vi, beforeEach } from 'vitest'

vi.mock('../lib/api', () => ({
  scanDatasets: vi.fn(),
  startObsExtract: vi.fn(),
  getObsExtractStatus: vi.fn(),
  cancelObsExtract: vi.fn(),
}))

import {
  scanDatasets,
  startObsExtract,
  getObsExtractStatus,
  cancelObsExtract,
} from '../lib/api'
import { useObsExtract } from './useObsExtract'

beforeEach(() => vi.clearAllMocks())

const RESULT = {
  obs_data: { data_items: [{ data_item_name: 'a' }] },
  n_data_items: 1, n_experiments: 1, warnings: [],
}

describe('useObsExtract', () => {
  it('accumulates the log by offset rather than replacing it', async () => {
    startObsExtract.mockResolvedValue({ job_id: 'j1' })
    getObsExtractStatus
      .mockResolvedValueOnce({ state: 'running', lines: ['a'], next_offset: 1 })
      .mockResolvedValueOnce({ state: 'done', lines: ['b', 'c'], next_offset: 3,
                               result: RESULT, error: '', warning: '' })

    const ox = useObsExtract({ intervalMs: 1 })
    await ox.start({}, {})
    await vi.waitFor(() => expect(ox.state.value).toBe('done'))

    expect(ox.lines.value).toEqual(['a', 'b', 'c'])
    expect(getObsExtractStatus).toHaveBeenLastCalledWith('j1', 1)
    expect(ox.result.value.n_data_items).toBe(1)
  })

  it('a cancelled run keeps the partial result', async () => {
    // Which is the point of cancelling rather than closing the dialog.
    startObsExtract.mockResolvedValue({ job_id: 'j1' })
    getObsExtractStatus.mockResolvedValue({
      state: 'cancelled', lines: [], next_offset: 0, result: RESULT,
      error: '', warning: 'cancelled part-way',
    })

    const ox = useObsExtract({ intervalMs: 1 })
    await ox.start({}, {})
    await vi.waitFor(() => expect(ox.state.value).toBe('cancelled'))
    expect(ox.result.value).toEqual(RESULT)
    expect(ox.warning.value).toContain('part-way')
  })

  it('reports a start that was refused', async () => {
    startObsExtract.mockRejectedValue({
      response: { data: { detail: 'an extraction is already running' } },
    })
    const ox = useObsExtract({ intervalMs: 1 })
    await ox.start({}, {})
    expect(ox.state.value).toBe('error')
    expect(ox.error.value).toContain('already running')
  })

  it('reports a failed run without losing the log', async () => {
    startObsExtract.mockResolvedValue({ job_id: 'j1' })
    getObsExtractStatus.mockResolvedValue({
      state: 'error', lines: ['[error] nothing to extract'], next_offset: 1,
      result: null, error: 'nothing to extract', warning: '',
    })
    const ox = useObsExtract({ intervalMs: 1 })
    await ox.start({}, {})
    await vi.waitFor(() => expect(ox.state.value).toBe('error'))
    expect(ox.error.value).toBe('nothing to extract')
    expect(ox.lines.value).toHaveLength(1)
  })

  it('scan stores what it found and clears the running state', async () => {
    scanDatasets.mockResolvedValue({ root: '/d', datasets: [], groups: [] })
    const ox = useObsExtract()
    const found = await ox.rescan({ root: '/d' })
    expect(found.root).toBe('/d')
    expect(ox.scan.value.root).toBe('/d')
    expect(ox.state.value).toBe('idle')
  })

  it('a failed scan is reported and leaves no half-state', async () => {
    scanDatasets.mockRejectedValue({ response: { data: { detail: 'not a directory' } } })
    const ox = useObsExtract()
    expect(await ox.rescan({ root: '/nope' })).toBeNull()
    expect(ox.state.value).toBe('error')
    expect(ox.error.value).toBe('not a directory')
  })

  it('cancel is forgiving of a job that already finished', async () => {
    startObsExtract.mockResolvedValue({ job_id: 'j1' })
    getObsExtractStatus.mockResolvedValue({ state: 'done', lines: [], next_offset: 0,
                                            result: RESULT, error: '', warning: '' })
    cancelObsExtract.mockRejectedValue(new Error('gone'))
    const ox = useObsExtract({ intervalMs: 1 })
    await ox.start({}, {})
    await expect(ox.cancel()).resolves.toBeUndefined()
  })

  it('reset clears a previous run before the next one', async () => {
    startObsExtract.mockResolvedValue({ job_id: 'j1' })
    getObsExtractStatus.mockResolvedValue({ state: 'done', lines: ['x'], next_offset: 1,
                                            result: RESULT, error: '', warning: '' })
    const ox = useObsExtract({ intervalMs: 1 })
    await ox.start({}, {})
    await vi.waitFor(() => expect(ox.state.value).toBe('done'))
    ox.reset()
    expect(ox.lines.value).toEqual([])
    expect(ox.result.value).toBeNull()
    expect(ox.state.value).toBe('idle')
  })
})
