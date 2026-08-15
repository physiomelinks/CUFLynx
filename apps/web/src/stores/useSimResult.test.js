import { describe, it, expect } from 'vitest'
import { useSimResult } from './useSimResult'

// Issue #175: a single run can carry warnings too. The backend forwards CA's
// stiffness check -- which is the only thing that says a plausible-looking trace
// is wrong -- and flags a partly-NaN trace. Protocol runs already surfaced
// warnings; a plain simulate dropped them on the floor.

const STIFF = 'AADC stiffness check: the model appears STIFF'

describe('useSimResult warnings', () => {
  it('takes a single run’s warnings from the payload', () => {
    const sim = useSimResult()
    sim.setResult({ time: [0], outputs: {}, warnings: [STIFF] })
    expect(sim.warnings.value).toEqual([STIFF])
  })

  it('reads them off the payload rather than a parameter', () => {
    // Deliberate: `cost` is read the same way, and the bug in #159 was a call
    // site that forgot to pass one. A warning nobody passes is a warning nobody
    // sees.
    const sim = useSimResult()
    sim.setResult({ warnings: [STIFF] }, 12)
    expect(sim.warnings.value).toEqual([STIFF])
    expect(sim.lastRunMs.value).toBe(12)
  })

  it('has none when the run was clean', () => {
    const sim = useSimResult()
    sim.setResult({ time: [0], outputs: {} })
    expect(sim.warnings.value).toEqual([])
  })

  it('does not keep the previous run’s warnings', () => {
    const sim = useSimResult()
    sim.setResult({ warnings: [STIFF] })
    sim.setResult({ time: [0], outputs: {} })
    expect(sim.warnings.value).toEqual([])
  })

  it('still takes a protocol run’s warnings as an argument', () => {
    const sim = useSimResult()
    sim.setExperiments([{ time: [0], outputs: {} }], ['pre_time ignored'])
    expect(sim.warnings.value).toEqual(['pre_time ignored'])
  })
})

// Workstream D: an external python model's `extra_plots()` figures are rendered
// server-side and arrive as `solver_plots` on the run's response. They are the
// only view of a 2D field a time series cannot show, so losing them (or keeping
// a stale one) is the failure mode worth testing.
const FIG = { index: 0, title: 'Temperature field', url: '/api/models/m/solver_plots/t1/0.png' }

describe('useSimResult solver plots', () => {
  it('takes a single run’s solver_plots off the payload', () => {
    const sim = useSimResult()
    sim.setResult({ time: [0], outputs: {}, solver_plots: [FIG] })
    expect(sim.solverPlots.value).toEqual([FIG])
  })

  it('has none when the run drew none', () => {
    const sim = useSimResult()
    sim.setResult({ time: [0], outputs: {} })
    expect(sim.solverPlots.value).toEqual([])
  })

  // A figure drawn from the previous parameters, shown beside the new run's
  // traces, is worse than no figure at all.
  it('does not keep the previous run’s figures', () => {
    const sim = useSimResult()
    sim.setResult({ solver_plots: [FIG] })
    sim.setResult({ time: [0], outputs: {} })
    expect(sim.solverPlots.value).toEqual([])
  })

  // A protocol run's payload is not handed to the store wholesale, so its
  // figures travel as an argument -- like the cost.
  it('takes a protocol run’s figures as an argument', () => {
    const sim = useSimResult()
    sim.setExperiments([{ time: [0], outputs: {} }], [], 5, null, [FIG])
    expect(sim.solverPlots.value).toEqual([FIG])
  })

  it('clears them on demand (a new model or obs_data)', () => {
    const sim = useSimResult()
    sim.setResult({ solver_plots: [FIG] })
    sim.clearSolverPlots()
    expect(sim.solverPlots.value).toEqual([])
  })
})
