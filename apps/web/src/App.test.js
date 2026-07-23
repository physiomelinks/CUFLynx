import { describe, it, expect, vi, beforeEach } from 'vitest'
import { nextTick } from 'vue'
import { shallowMount, flushPromises } from '@vue/test-utils'

// Mock the API so the onMounted bootstrap doesn't hit the network. shallowMount
// stubs every child component, so this test exercises App's own <script setup>
// — which is exactly where a "used before declaration" (TDZ) bug throws,
// blanking the whole app (the black screen). A setup-time error propagates out
// of mount(), so this test fails if such a bug is reintroduced.
vi.mock('./lib/api', () => ({
  getVariables: vi.fn().mockResolvedValue({}),
  simulate: vi.fn().mockResolvedValue({ time: [], outputs: {} }),
  runProtocol: vi.fn().mockResolvedValue({ experiments: [] }),
  getCalibrationDefaults: vi.fn().mockResolvedValue({}),
  getCalibrationPythons: vi.fn().mockResolvedValue({ pythons: [] }),
  getSensitivityDefaults: vi.fn().mockResolvedValue({}),
  getUQDefaults: vi.fn().mockResolvedValue({}),
  getConfig: vi.fn().mockResolvedValue({
    ca_dir: '',
    ca_exists: true,
    generated_model_format: 'cellml_only',
    solver: 'CVODE_myokit',
    solver_info: {},
    differentiable_operations: {},
  }),
  setConfig: vi.fn().mockResolvedValue({}),
}))

import { getConfig, setConfig } from './lib/api'
import App from './App.vue'

describe('App.vue', () => {
  it('mounts without a setup-time error (guards against TDZ / use-before-declare)', () => {
    const wrapper = shallowMount(App)
    // Reaching here means <script setup> ran end-to-end; the layout rendered.
    expect(wrapper.find('.layout').exists()).toBe(true)
  })

  it('asks where outputs should go on open, and persists the choice', async () => {
    localStorage.removeItem('cuflynx-outputs-dir')
    const wrapper = shallowMount(App)
    await flushPromises() // onMounted bootstrap finishes, then opens the prompt
    const setup = wrapper
      .findAllComponents({ name: 'FileBrowserDialog' })
      .find((d) => d.props('title') === 'Where should outputs be saved?')
    expect(setup).toBeTruthy()
    expect(setup.props('visible')).toBe(true)
    setup.vm.$emit('select', '/data/outputs')
    await flushPromises()
    expect(localStorage.getItem('cuflynx-outputs-dir')).toBe('/data/outputs')
  })

  // Myokit JIT-compiles every model, so a missing C toolchain breaks all
  // simulation. The packaged desktop app can't ship a compiler, making this the
  // most likely first-run failure — warn instead of letting sims 500.
  describe('missing C compiler warning', () => {
    const BASE_CONFIG = {
      ca_dir: '',
      ca_exists: true,
      generated_model_format: 'cellml_only',
      solver: 'CVODE_myokit',
      solver_info: {},
      differentiable_operations: {},
    }

    const NO_COMPILER = {
      present: false,
      hint: 'xcode-select --install',
      affects: "CVODE_myokit (generated model format 'cellml_only')",
      alternatives: [
        { generated_model_format: 'python', solver: 'solve_ivp', label: 'Python (scipy solve_ivp)' },
        { generated_model_format: 'casadi_python', solver: 'casadi_integrator', label: 'CasADi' },
      ],
    }

    it('warns (not errors) and names the backends that still work', async () => {
      getConfig.mockResolvedValueOnce({ ...BASE_CONFIG, cpp_compiler: NO_COMPILER })
      // Render Message for real: shallowMount stubs it, and a stub drops the slot
      // content — which is where the message body lives.
      const wrapper = shallowMount(App, { global: { stubs: { Message: false } } })
      await flushPromises()

      const banner = wrapper.findComponent('[data-testid="no-compiler-warning"]')
      expect(banner.exists()).toBe(true)
      // A missing compiler only costs you Myokit/CVODE — it is not fatal.
      expect(banner.props('severity')).toBe('warn')
      expect(banner.text()).toContain('Myokit CVODE solver is unavailable')
      expect(banner.text()).toContain('Python (scipy solve_ivp)')
      expect(banner.text()).toContain('CasADi')
    })

    it('still offers the install hint for those who want CVODE_myokit', async () => {
      getConfig.mockResolvedValueOnce({ ...BASE_CONFIG, cpp_compiler: NO_COMPILER })
      const wrapper = shallowMount(App, { global: { stubs: { Message: false } } })
      await flushPromises()

      expect(wrapper.find('[data-testid="no-compiler-warning"]').text()).toContain(
        'xcode-select --install',
      )
    })

    it('stays quiet when a compiler is present', async () => {
      getConfig.mockResolvedValueOnce({
        ...BASE_CONFIG,
        cpp_compiler: { present: true, hint: '' },
      })
      const wrapper = shallowMount(App)
      await flushPromises()

      expect(wrapper.find('[data-testid="no-compiler-warning"]').exists()).toBe(false)
    })

    it('stays quiet when the backend omits cpp_compiler (older API)', async () => {
      getConfig.mockResolvedValueOnce({ ...BASE_CONFIG })
      const wrapper = shallowMount(App)
      await flushPromises()

      expect(wrapper.find('[data-testid="no-compiler-warning"]').exists()).toBe(false)
    })
  })


  // The RHS import column (model / obs_data / params uploads) is resized by a
  // draggable divider: drag it to resize, drag fully right to hide, drag the tab
  // (or double-click) to bring it back. Frees width for plots/analysis.
  describe('resizable RHS import column', () => {
    const dragTo = async (wrapper, clientX) => {
      await wrapper.find('[data-testid="rhs-handle"]').trigger('mousedown')
      window.dispatchEvent(new MouseEvent('mousemove', { clientX }))
      window.dispatchEvent(new MouseEvent('mouseup'))
      await nextTick()
    }

    beforeEach(() => localStorage.removeItem('cuflynx-rhs-width'))

    it('starts expanded, and the drag divider is always present', () => {
      const wrapper = shallowMount(App)
      expect(wrapper.find('[data-testid="rhs-handle"]').exists()).toBe(true)
      expect(wrapper.find('[data-testid="rhs-column"]').classes()).not.toContain('collapsed')
      expect(wrapper.vm.rhsWidth).toBeGreaterThan(0)
    })

    it('dragging the divider resizes the column width', async () => {
      const wrapper = shallowMount(App)
      await dragTo(wrapper, window.innerWidth - 350) // 350px from the right edge
      expect(wrapper.vm.rhsWidth).toBe(350)
      expect(wrapper.find('[data-testid="rhs-column"]').classes()).not.toContain('collapsed')
    })

    it('dragging fully to the right edge collapses (hides) the column', async () => {
      const wrapper = shallowMount(App)
      await dragTo(wrapper, window.innerWidth - 10) // past the snap threshold
      expect(wrapper.vm.rhsWidth).toBe(0)
      expect(wrapper.vm.rhsCollapsed).toBe(true)
      expect(wrapper.find('[data-testid="rhs-column"]').classes()).toContain('collapsed')
    })

    it('the tab drags the hidden column back out, and double-click restores it', async () => {
      const wrapper = shallowMount(App)
      await dragTo(wrapper, window.innerWidth - 10) // hide
      expect(wrapper.vm.rhsCollapsed).toBe(true)

      // drag the tab back out
      await dragTo(wrapper, window.innerWidth - 300)
      expect(wrapper.vm.rhsWidth).toBe(300)
      expect(wrapper.vm.rhsCollapsed).toBe(false)

      // hide again, then double-click the tab to restore the default width
      await dragTo(wrapper, window.innerWidth - 10)
      await wrapper.find('[data-testid="rhs-handle"]').trigger('dblclick')
      expect(wrapper.vm.rhsWidth).toBe(300)
    })
  })

  // The packaged desktop app has no default interpreter (its own executable is
  // the frozen bundle), so the choice must survive a restart or the user re-picks
  // it on every launch.
  describe('analysis interpreter persistence', () => {
    const BASE_CONFIG = {
      ca_dir: '',
      ca_exists: true,
      generated_model_format: 'cellml_only',
      solver: 'CVODE_myokit',
      solver_info: {},
      differentiable_operations: {},
    }

    it('hydrates the remembered interpreter without echoing it back', async () => {
      getConfig.mockResolvedValueOnce({
        ...BASE_CONFIG,
        python_path: '/venv/bin/python',
      })
      setConfig.mockClear()
      shallowMount(App)
      await flushPromises()

      // Hydration must not trigger a redundant write-back of the same value.
      expect(setConfig).not.toHaveBeenCalledWith(
        expect.objectContaining({ pythonPath: '/venv/bin/python' }),
      )
    })

    it('persists a reset to the bundled default (empty value POSTs)', async () => {
      // Start hydrated with a venv, then clear back to "" (Bundled). The watcher
      // must POST "" so the backend resets — not skip it as a no-op.
      getConfig.mockResolvedValueOnce({ ...BASE_CONFIG, python_path: '/venv/bin/python' })
      setConfig.mockClear()
      const wrapper = shallowMount(App)
      await flushPromises()

      wrapper.vm.pythonPath = ''
      await flushPromises()

      expect(setConfig).toHaveBeenCalledWith(expect.objectContaining({ pythonPath: '' }))
    })
  })
})

// Regression for #84: switching the backend solver must update the LOCAL-SA
// gradient sources shown in the Sensitivity panel (cellml_only+CVODE_myokit -> FSA;
// casadi_python -> AD). PR #95 made the panel read the reactive /api/config
// gradient_sources; this drives the real App reactive path through a backend switch.
describe('App.vue sensitivity gradient sources track the backend (#84)', () => {
  const cellml = {
    ca_dir: '', ca_exists: true, generated_model_format: 'cellml_only',
    solver: 'CVODE_myokit', solver_info: {}, differentiable_operations: {},
    model_formats: ['cellml_only', 'python', 'casadi_python'],
    solvers_by_format: { cellml_only: ['CVODE_myokit'], python: ['solve_ivp'], casadi_python: ['casadi_integrator'] },
    default_solver_by_format: { cellml_only: 'CVODE_myokit', python: 'solve_ivp', casadi_python: 'casadi_integrator' },
    gradient_sources: [
      { value: 'FD', label: 'Finite difference', requires_all_differentiable: false },
      { value: 'FSA', label: 'Forward sensitivity (Myokit CVODES)', requires_all_differentiable: false },
    ],
  }
  const casadi = {
    ...cellml, generated_model_format: 'casadi_python', solver: 'casadi_integrator',
    gradient_sources: [
      { value: 'FD', label: 'Finite difference', requires_all_differentiable: false },
      { value: 'AD', label: 'Automatic differentiation (CasADi)', requires_all_differentiable: true },
    ],
  }

  it('swaps FSA for AD when switching cellml_only -> casadi_python (and back)', async () => {
    getConfig.mockResolvedValue({ ...cellml })
    setConfig.mockImplementation(async (payload) =>
      payload && payload.generatedModelFormat === 'casadi_python' ? { ...casadi } : { ...cellml },
    )
    const wrapper = shallowMount(App)
    await flushPromises()
    // initial: cellml_only -> FD + FSA
    expect(wrapper.vm.gradientSources.map((s) => s.value)).toEqual(['FD', 'FSA'])

    // user switches backend to casadi_python in Settings
    wrapper.vm.onFormatChange('casadi_python')
    await flushPromises()
    expect(wrapper.vm.gradientSources.map((s) => s.value)).toEqual(['FD', 'AD'])

    // and back
    wrapper.vm.onFormatChange('cellml_only')
    await flushPromises()
    expect(wrapper.vm.gradientSources.map((s) => s.value)).toEqual(['FD', 'FSA'])
  })
})
