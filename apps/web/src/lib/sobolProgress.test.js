import { describe, it, expect, vi, afterEach } from 'vitest'
import { useSobolFollow } from './sobolProgress'

const flush = () => new Promise((resolve) => setTimeout(resolve, 0))

afterEach(() => {
  vi.useRealTimers()
})

describe('useSobolFollow', () => {
  it('reads on a timer while following, and once more when finished', async () => {
    vi.useFakeTimers()
    let n = 0
    const fetch = vi.fn(async () => ({ design: { n: ++n }, convergence: null }))
    const f = useSobolFollow(fetch, { sobolIntervalMs: 1000 })
    f.follow('j')
    await vi.advanceTimersByTimeAsync(0)
    expect(f.data.value.design.n).toBe(1)
    await vi.advanceTimersByTimeAsync(1000)
    expect(fetch).toHaveBeenCalledTimes(2)
    await f.finish('j')
    expect(fetch).toHaveBeenCalledTimes(3)
    await vi.advanceTimersByTimeAsync(5000)
    expect(fetch).toHaveBeenCalledTimes(3)
    expect(f.data.value.design.n).toBe(3)
  })

  it('drops a reply that arrives after a reset', async () => {
    let release
    const fetch = vi.fn(() => new Promise((resolve) => { release = resolve }))
    const f = useSobolFollow(fetch)
    f.follow('j')
    f.reset()
    release({ design: {}, convergence: null })
    await flush()
    expect(f.data.value).toBe(null)
  })

  it('keeps the last answer when a read fails', async () => {
    const fetch = vi.fn()
      .mockResolvedValueOnce({ design: { ok: 1 }, convergence: null })
      .mockRejectedValueOnce(new Error('gone'))
    const f = useSobolFollow(fetch)
    f.follow('j')
    await flush()
    await f.finish('j')
    expect(f.data.value.design.ok).toBe(1)
  })
})
