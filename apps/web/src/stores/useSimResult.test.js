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
