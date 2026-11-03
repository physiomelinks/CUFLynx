import { beforeEach, describe, expect, it, vi } from 'vitest'

const getEmulatorInfo = vi.fn()
vi.mock('../lib/api', () => ({
  getEmulatorInfo: (...args) => getEmulatorInfo(...args),
  startEmulatorTraining: vi.fn(),
  getEmulatorStatus: vi.fn(),
  cancelEmulatorTraining: vi.fn(),
}))

const { useEmulator } = await import('./useEmulator')

const RBF = {
  dir: '/s/emulators/a_RBF',
  name: 'a_RBF',
  model_name: 'two_phase_RadialBasisFunctions',
  worst_r2: 0.31,
}
const MLP = {
  dir: '/s/emulators/a_MLP',
  name: 'a_MLP',
  model_name: 'two_phase_MLP',
  worst_r2: 0.28,
}

function info(overrides = {}) {
  return {
    emulator_dir: RBF.dir,
    metadata: { model_name: RBF.model_name, worst_r2: RBF.worst_r2 },
    error_points: null,
    reusable: true,
    choices: [RBF, MLP],
    chosen: false,
    ...overrides,
  }
}

describe('useEmulator bundle selection', () => {
  beforeEach(() => getEmulatorInfo.mockClear())

  it('carries the choices the backend offers', async () => {
    getEmulatorInfo.mockResolvedValue(info())
    const emu = useEmulator()
    await emu.refresh('m1', '/s')
    expect(emu.choices.value).toHaveLength(2)
    expect(emu.chosen.value).toBe(false)
  })

  it('sends the pick back and reloads from it', async () => {
    // The reload is the point: metadata, held-out points and reusability all
    // belong to one bundle, and a stale one on screen would describe an emulator
    // that is not the one selected.
    getEmulatorInfo.mockResolvedValue(info())
    const emu = useEmulator()
    await emu.refresh('m1', '/s')

    getEmulatorInfo.mockResolvedValue(
      info({ emulator_dir: MLP.dir, metadata: { model_name: MLP.model_name }, chosen: true }),
    )
    await emu.select(MLP.dir, 'm1', '/s')

    expect(getEmulatorInfo).toHaveBeenLastCalledWith('m1', '/s', MLP.dir)
    expect(emu.emulatorDir.value).toBe(MLP.dir)
    expect(emu.metadata.value.model_name).toBe('two_phase_MLP')
    expect(emu.chosen.value).toBe(true)
  })

  it('drops a selection the backend no longer recognises', async () => {
    // A study retrained into different directories leaves a stale pick that the
    // backend silently ignores. Keeping it would leave the panel claiming a
    // choice while the default was being served.
    getEmulatorInfo.mockResolvedValue(info({ chosen: true }))
    const emu = useEmulator()
    await emu.select(MLP.dir, 'm1', '/s')
    expect(emu.selectedDir.value).toBe(MLP.dir)

    getEmulatorInfo.mockResolvedValue(info({ chosen: false }))
    await emu.refresh('m1', '/s')
    expect(emu.selectedDir.value).toBe('')
  })

  it('returns to the default when the pick is cleared', async () => {
    getEmulatorInfo.mockResolvedValue(info())
    const emu = useEmulator()
    await emu.select('', 'm1', '/s')
    expect(getEmulatorInfo).toHaveBeenLastCalledWith('m1', '/s', '')
  })

  it('clears the choices when the study answers with nothing usable', async () => {
    // A malformed answer rather than a thrown one: the state-clearing path is the
    // same, and vitest reports an error thrown from inside a mock as a failure of
    // the test that armed it even when the code under test handles it.
    getEmulatorInfo.mockResolvedValue(undefined)
    const emu = useEmulator()
    // Must not propagate: no emulator is the normal starting state for a study,
    // and an unreadable one is the panel showing nothing, not an error banner.
    await expect(emu.refresh('m1', '/s')).resolves.toBeUndefined()
    expect(emu.choices.value).toEqual([])
    expect(emu.chosen.value).toBe(false)
    expect(emu.metadata.value).toBe(null)
  })
})
