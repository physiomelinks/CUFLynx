import { describe, it, expect, vi, beforeEach } from 'vitest'

vi.mock('../lib/api', () => ({
  startSensitivity: vi.fn(),
  getSensitivityStatus: vi.fn(),
  cancelSensitivity: vi.fn(),
}))

import { startSensitivity, getSensitivityStatus } from '../lib/api'
import { useSensitivity } from './useSensitivity'

const INDICES = { local: { 'y^{0,0} [max]': { 'a/x': 0.5 } } }

/** A finished local-SA status payload, as the manager reports it. */
function done(extra = {}) {
  return {
    state: 'done',
    lines: [],
    next_offset: 0,
    indices: INDICES,
    param_names: ['a/x'],
    output_names: ['y^{0,0} [max]'],
    nominal: [1.0],
    nominal_source: 'current parameter values (from sliders)',
    ...extra,
  }
}

async function runWith(settings, statusExtra) {
  startSensitivity.mockResolvedValue({ job_id: 'j1' })
  getSensitivityStatus.mockResolvedValue(done(statusExtra))
  const sa = useSensitivity({ intervalMs: 0 })
  await sa.start('m1', settings)
  return sa
}

beforeEach(() => {
  vi.clearAllMocks()
})

describe('the saved run label names the gradient method that actually ran', () => {
  it('uses the resolved arm, not the request, when the request said auto', async () => {
    // circulatory_autogen's own default spelling is 'auto', which names no arm.
    // Labelling the run "Local · auto" tells the user nothing about what
    // produced the numbers; the backend reports what it resolved to.
    const sa = await runWith(
      { method: 'local', gradient_method: 'auto', nominal: 'current' },
      { gradient_method: 'FSA' },
    )
    expect(sa.results.value).toHaveLength(1)
    expect(sa.results.value[0].label).toContain('FSA')
    expect(sa.results.value[0].label).not.toContain('auto')
  })

  it.each(['FD', 'AD', 'FSA'])('names %s when that is what ran', async (arm) => {
    const sa = await runWith(
      { method: 'local', gradient_method: 'auto', nominal: 'current' },
      { gradient_method: arm },
    )
    expect(sa.results.value[0].label).toBe(`#1 Local · ${arm} · current`)
  })

  it('falls back to the request when the run reported nothing', async () => {
    // An older backend, or a run that failed before reporting: the request is
    // still better than inventing a default.
    const sa = await runWith(
      { method: 'local', gradient_method: 'FD', nominal: 'midpoint' },
      { gradient_method: undefined },
    )
    expect(sa.results.value[0].label).toBe('#1 Local · FD · midpoint')
  })

  it('leaves the Sobol label alone', async () => {
    const sa = await runWith(
      { method: 'sobol', sample_type: 'saltelli', num_samples: 64 },
      { gradient_method: undefined },
    )
    expect(sa.results.value[0].label).toBe('#1 Sobol · saltelli · n64')
  })
})
