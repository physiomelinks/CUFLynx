import { describe, expect, it } from 'vitest'
import { ref } from 'vue'
import { applyReopenedProgress } from './runProgress'

function stores({ saRunning = false, emuRunning = false, iaRunning = false } = {}) {
  return {
    sa: { running: ref(saRunning), progress: ref(null), sobolProgress: ref(null) },
    emu: { running: ref(emuRunning), progress: ref(null) },
    ia: { running: ref(iaRunning), progress: ref(null) },
  }
}

const SOBOL = { kind: 'sobol', state: 'done', phases: [] }
const FOUND = {
  sobol: { progress: SOBOL, design: { rows: [] }, convergence: { n: [4] } },
  emulator: { kind: 'emulation', state: 'stopped', phases: [] },
  profile: { kind: 'profile_likelihood', state: 'done', phases: [] },
}

describe('applyReopenedProgress', () => {
  it('fills each section from the reopened runs', () => {
    const s = stores()
    applyReopenedProgress(FOUND, s)
    expect(s.sa.progress.value).toEqual(SOBOL)
    expect(s.sa.sobolProgress.value).toEqual({ design: { rows: [] }, convergence: { n: [4] } })
    expect(s.emu.progress.value.kind).toBe('emulation')
    expect(s.ia.progress.value.kind).toBe('profile_likelihood')
  })

  it('leaves a running store its live progress', () => {
    const s = stores({ saRunning: true, emuRunning: true, iaRunning: true })
    s.sa.progress.value = 'live'
    applyReopenedProgress(FOUND, s)
    expect(s.sa.progress.value).toBe('live')
    expect(s.sa.sobolProgress.value).toBeNull()
    expect(s.emu.progress.value).toBeNull()
    expect(s.ia.progress.value).toBeNull()
  })

  it('changes nothing when the working dir has no progress files', () => {
    const s = stores()
    applyReopenedProgress(undefined, s)
    applyReopenedProgress({ sobol: null, emulator: null, profile: null }, s)
    expect(s.sa.progress.value).toBeNull()
    expect(s.emu.progress.value).toBeNull()
    expect(s.ia.progress.value).toBeNull()
  })
})
