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

// Loading a run off disk (#255). The panel reads its heatmap out of the selected
// *saved* result -- `indices` is a computed over it -- so a loader that assigned
// to `indices` wrote to a read-only ref and left the panel empty while every
// other panel filled.
describe('a run loaded from an outputs directory', () => {
  const LOADED = {
    indices: { S1: { 'y [max]': { 'a/x': 0.4 } } },
    param_names: ['a/x'],
    output_names: ['y [max]'],
  }

  it('reaches the panel, which reads the selected saved run', () => {
    const sa = useSensitivity()
    sa.addLoadedResult(LOADED, { label: 'Loaded · Sobol', source: '/out:sobol' })
    expect(sa.indices.value).toEqual(LOADED.indices)
    expect(sa.paramNames.value).toEqual(['a/x'])
    expect(sa.outputNames.value).toEqual(['y [max]'])
    expect(sa.results.value).toHaveLength(1)
    expect(sa.results.value[0].label).toBe('Loaded · Sobol')
  })

  it('reloading one directory refreshes its entry instead of stacking copies', () => {
    const sa = useSensitivity()
    sa.addLoadedResult(LOADED, { source: '/out:sobol' })
    sa.addLoadedResult(LOADED, { source: '/out:sobol' })
    expect(sa.results.value).toHaveLength(1)
  })

  it('a different run is kept beside it, which is what saved runs are for', () => {
    const sa = useSensitivity()
    sa.addLoadedResult(LOADED, { source: '/out/run-a:sobol', label: 'A' })
    sa.addLoadedResult(LOADED, { source: '/out/run-b:sobol', label: 'B' })
    expect(sa.results.value.map((r) => r.label)).toEqual(['A', 'B'])
    // The newest is the one shown.
    expect(sa.results.value.at(-1).id).toBe(sa.selectedId.value)
  })

  it('a payload with no indices adds nothing to compare against', () => {
    const sa = useSensitivity()
    expect(sa.addLoadedResult({ param_names: [] }, {})).toBeNull()
    expect(sa.results.value).toHaveLength(0)
  })
})
