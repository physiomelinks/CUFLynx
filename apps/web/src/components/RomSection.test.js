import { beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'

vi.mock('../lib/api', () => ({
  getRomAvailability: vi.fn(),
  checkRomSetup: vi.fn(),
  startRomRun: vi.fn(),
  getRomStatus: vi.fn(),
  cancelRomRun: vi.fn(),
  getRomResults: vi.fn(),
  errorMessage: (e) => e?.message ?? String(e),
}))

import * as api from '../lib/api'
import RomSection from './RomSection.vue'

const ButtonStub = {
  props: ['disabled', 'label'],
  emits: ['click'],
  template: '<button :disabled="disabled" v-bind="$attrs" @click="$emit(\'click\')">{{ label }}</button>',
}
const InputTextStub = {
  // `size` is absorbed: PrimeVue's "small" is not a valid <input size>.
  props: ['modelValue', 'size'],
  emits: ['update:modelValue'],
  template: '<input :value="modelValue" v-bind="$attrs" @input="$emit(\'update:modelValue\', $event.target.value)" />',
}
const stubs = { Button: ButtonStub, InputText: InputTextStub, FileBrowserDialog: true }

const DESCRIPTION = {
  ok: true,
  name: 'toy_heart',
  label: 'A toy heart',
  fitted_states: ['lv/q', 'arterial/q', 'venous/q'],
  run_variables: [{ names: ['R'], varies: 'schedule' }],
  W: ['R'],
  beat: { kind: 'clock', period: 1 },
  sizes: { train: [2, 2], cal: [1, 2], test: [1, 3] },
  runnable: true,
}
const ROWS = [
  { method: 'beat map', test_score: 0.97, selection_score: 0.38, rank: 3, diverged: 0, forecasts: 1, fit_s: 0.2, best: true },
  { method: 'discrete T/50', test_score: 1.13, selection_score: 0.11, rank: null, diverged: 0, forecasts: 1, fit_s: 0.7, best: false },
]

async function mounted() {
  const w = mount(RomSection, { global: { stubs } })
  await flushPromises()
  return w
}

async function checked(answer) {
  api.checkRomSetup.mockResolvedValue(answer)
  const w = await mounted()
  await w.find('[data-testid="rom-setup-path"]').setValue('/s/toy.rom_setup.json')
  await w.find('[data-testid="rom-check"]').trigger('click')
  await flushPromises()
  return w
}

describe('RomSection', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    window.localStorage.clear()
    api.getRomAvailability.mockResolvedValue({ available: true, reason: null })
  })

  it('sits under its own heading in the Surrogate tab', async () => {
    const w = await mounted()
    expect(w.find('[data-testid="surrogate-rom-heading"]').text()).toBe('Reduced-order model (ROM)')
  })

  it('shows only the reason when this libcuflynx has no ROMs, and can ask again', async () => {
    api.getRomAvailability.mockResolvedValue({ available: false, reason: 'not in the public release yet' })
    const w = await mounted()
    expect(w.find('[data-testid="rom-unavailable-reason"]').text()).toBe('not in the public release yet')
    expect(w.find('[data-testid="rom-setup-path"]').exists()).toBe(false)
    await w.find('[data-testid="rom-refresh"]').trigger('click')
    expect(api.getRomAvailability).toHaveBeenLastCalledWith(true)
  })

  it('lists a setup\'s problems by where they are, and offers no run', async () => {
    const w = await checked({
      ok: false,
      problems: [{ where: 'beat.clock.period', message: 'not a variable' }],
      description: null,
      default_root: '/s/rom_toy',
      has_results: false,
    })
    expect(w.find('[data-testid="rom-problems"]').text()).toContain('beat.clock.period')
    expect(w.find('[data-testid="rom-fit"]').exists()).toBe(false)
  })

  it('describes a good setup, fills the default folder and remembers the file', async () => {
    const w = await checked({ ok: true, problems: [], description: DESCRIPTION, default_root: '/s/rom_toy', has_results: false })
    const d = w.find('[data-testid="rom-description"]').text()
    expect(d).toContain('A toy heart')
    expect(d).toContain('R')
    expect(w.find('[data-testid="rom-root"]').element.value).toBe('/s/rom_toy')
    expect(window.localStorage.getItem('cuflynx.rom.setup')).toBe('/s/toy.rom_setup.json')
    expect(w.find('[data-testid="rom-fit"]').attributes('disabled')).toBeUndefined()
  })

  it('reads back results already in the folder', async () => {
    api.getRomResults.mockResolvedValue({ rows: ROWS })
    const w = await checked({ ok: true, problems: [], description: DESCRIPTION, default_root: '/s/rom_toy', has_results: true })
    expect(api.getRomResults).toHaveBeenCalledWith('/s/rom_toy')
    expect(w.findAll('[data-testid="rom-results"] tbody tr')).toHaveLength(2)
  })

  it('fits the named methods, streams the log and shows the scores', async () => {
    api.startRomRun.mockResolvedValue({ job_id: 'j1', root: '/s/rom_toy' })
    api.getRomStatus.mockResolvedValue({
      state: 'done', lines: ['rom setup toy_heart: true runs'], next_offset: 1, rows: ROWS, error: null,
    })
    const w = await checked({ ok: true, problems: [], description: DESCRIPTION, default_root: '/s/rom_toy', has_results: false })
    await w.find('[data-testid="rom-methods"]').setValue('beat map, discrete T/50')
    await w.find('[data-testid="rom-fit"]').trigger('click')
    await flushPromises()
    expect(api.startRomRun).toHaveBeenCalledWith('/s/toy.rom_setup.json', {
      action: 'fit', root: '/s/rom_toy', methods: ['beat map', 'discrete T/50'],
    })
    expect(w.find('[data-testid="rom-terminal"]').text()).toContain('true runs')
    const first = w.findAll('[data-testid="rom-results"] tbody tr')[0].text()
    expect(first).toContain('beat map')
    expect(first).toContain('0.970')
    expect(w.find('[data-testid="rom-state"]').text()).toBe('done')
  })

  it('shows why a run failed', async () => {
    api.startRomRun.mockResolvedValue({ job_id: 'j2', root: '/s/rom_toy' })
    api.getRomStatus.mockResolvedValue({ state: 'error', lines: [], next_offset: 0, rows: [], error: 'CVODES failed at t = 3' })
    const w = await checked({ ok: true, problems: [], description: DESCRIPTION, default_root: '/s/rom_toy', has_results: false })
    await w.find('[data-testid="rom-runs"]').trigger('click')
    await flushPromises()
    expect(api.startRomRun.mock.calls[0][1].action).toBe('runs')
    expect(w.find('[data-testid="rom-error"]').text()).toBe('CVODES failed at t = 3')
  })

  it('checks but does not fit a setup that cannot run yet', async () => {
    const w = await checked({
      ok: true, problems: [], description: { ...DESCRIPTION, runnable: false }, default_root: '/s/rom_toy', has_results: false,
    })
    expect(w.find('[data-testid="rom-not-runnable"]').exists()).toBe(true)
    expect(w.find('[data-testid="rom-fit"]').attributes('disabled')).toBeDefined()
  })
})
