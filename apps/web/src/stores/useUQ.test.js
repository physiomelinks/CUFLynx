import { describe, it, expect, vi, beforeEach } from 'vitest'

vi.mock('../lib/api', () => ({
  startUQ: vi.fn(async () => ({ job_id: 'j1' })),
  getUQStatus: vi.fn(),
  getUQProgress: vi.fn(),
  cancelUQ: vi.fn(async () => ({ cancelled: true })),
}))

import { getUQStatus, getUQProgress } from '../lib/api'
import { useUQ } from './useUQ'

const CHAIN = {
  steps: 120,
  walkers: 4,
  walkers_shown: 4,
  num_params: 1,
  param_labels: ['a'],
  trace_steps: [0, 1],
  traces: [[[0, 1]]],
  windowed_mean: null,
  autocorrelation: null,
}

beforeEach(() => vi.clearAllMocks())

describe('useUQ chain polling', () => {
  it('fetches the chain once more after the run stops', async () => {
    // CA writes the finished chain as the last thing it does — after the poll that saw the run
    // still going. With an emulator the whole run can be shorter than one chain-poll interval,
    // so without this final fetch there was nothing to draw at all, and the Progress section
    // disappeared the moment `running` went false.
    getUQStatus.mockResolvedValueOnce({
      state: 'done', lines: [], next_offset: 0, method: 'mcmc', params: [],
    })
    // The real sequence: nothing on disk when the run starts, the whole chain only once it
    // has finished. The opening poll must not be what the assertion rests on.
    getUQProgress
      .mockResolvedValueOnce({ ...CHAIN, steps: 0 })
      .mockResolvedValueOnce(CHAIN)

    const uq = useUQ()
    await uq.start('m1', {})

    expect(getUQProgress).toHaveBeenCalledTimes(2)
    expect(uq.progress.value?.steps).toBe(120)
    expect(uq.state.value).toBe('done')
  })

  it('keeps the chain a cancelled run had already sampled', async () => {
    // Half a chain is still a chain — which is the other half of what CA #418 is for.
    getUQStatus.mockResolvedValue({ state: 'running', lines: [], next_offset: 0 })
    getUQProgress.mockResolvedValue(CHAIN)

    const uq = useUQ({ intervalMs: 10000, chainIntervalMs: 10000 })
    await uq.start('m1', {})
    getUQProgress.mockClear()
    await uq.cancel()

    expect(getUQProgress).toHaveBeenCalled()
    expect(uq.progress.value?.steps).toBe(120)
  })

  it('a chain that cannot be fetched does not fail the run', async () => {
    // The chain is a picture of a run that is otherwise fine; a stale plot beats a false error.
    getUQStatus.mockResolvedValueOnce({
      state: 'done', lines: [], next_offset: 0, method: 'mcmc', params: [],
    })
    getUQProgress.mockRejectedValue(new Error('boom'))

    const uq = useUQ()
    await uq.start('m1', {})

    expect(uq.state.value).toBe('done')
    expect(uq.error.value).toBe('')
  })

  it('does not carry one run’s chain into the next', async () => {
    getUQStatus.mockResolvedValue({
      state: 'done', lines: [], next_offset: 0, method: 'mcmc', params: [],
    })
    getUQProgress.mockResolvedValueOnce(CHAIN)

    const uq = useUQ()
    await uq.start('m1', {})
    expect(uq.progress.value?.steps).toBe(120)

    // The next run has written nothing yet: the panel must go back to empty, not show the
    // previous run's chain as if it were this one's.
    getUQProgress.mockResolvedValueOnce({ ...CHAIN, steps: 0 })
    await uq.start('m1', {})
    expect(uq.progress.value).toBeNull()
  })
})

describe('a job the server has forgotten', () => {
  it('says the run was lost, not that it failed', async () => {
    // A restarted server 404s every poll. "AxiosError: status code 404" sends someone looking
    // for a bug in their model; the run was fine, the server went away underneath it.
    getUQStatus.mockRejectedValue({ response: { status: 404, data: { detail: 'not found' } } })

    const uq = useUQ()
    await uq.start('m1', {})

    expect(uq.state.value).toBe('error')
    expect(uq.error.value).toContain('no longer tracking this run')
    expect(uq.error.value).toContain('still in the run directory')
  })
})
