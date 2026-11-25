import { describe, it, expect, vi, beforeEach } from 'vitest'

vi.mock('../lib/api', () => ({
  startParamReduction: vi.fn(),
  getParamReductionStatus: vi.fn(),
  cancelParamReduction: vi.fn(),
  startUQ: vi.fn(),
  getUQStatus: vi.fn(),
  cancelUQ: vi.fn(),
  getSobolProgress: vi.fn(),
  errorMessage: (e) => String(e),
}))

import {
  startParamReduction,
  getParamReductionStatus,
  startUQ,
  getUQStatus,
  getSobolProgress,
} from '../lib/api'
import { useParamReduction, useIdentifiabilityAnalysis } from './useIdentifiability'

const flush = () => new Promise((resolve) => setTimeout(resolve, 0))

beforeEach(() => {
  vi.clearAllMocks()
})

describe('identifiability jobs carry their progress files (CA #702)', () => {
  it("a reduction keeps its Sobol run's progress and reads that run's design", async () => {
    const progress = { kind: 'sobol', state: 'done', phases: [] }
    startParamReduction.mockResolvedValue({ job_id: 'r1' })
    getParamReductionStatus.mockResolvedValue({ state: 'done', lines: [], progress })
    getSobolProgress.mockResolvedValue({ design: { names: ['a'] }, convergence: null })
    const job = useParamReduction({ intervalMs: 0 })
    await job.start('m1', {})
    await flush()
    expect(job.progress.value).toEqual(progress)
    expect(getSobolProgress).toHaveBeenCalledWith('r1', 'param_reduction')
    expect(job.sobolProgress.value.design.names).toEqual(['a'])
  })

  it('a profile likelihood keeps its progress and reads no Sobol design', async () => {
    const progress = { kind: 'profile_likelihood', state: 'running', phases: [] }
    startUQ.mockResolvedValue({ job_id: 'u1' })
    getUQStatus.mockResolvedValue({ state: 'error', lines: [], progress, error: 'x' })
    const job = useIdentifiabilityAnalysis({ intervalMs: 0 })
    await job.start('m1', {})
    expect(job.progress.value).toEqual(progress)
    expect(job.sobolProgress.value).toBe(null)
    expect(getSobolProgress).not.toHaveBeenCalled()
    job.reset()
    expect(job.progress.value).toBe(null)
  })
})
