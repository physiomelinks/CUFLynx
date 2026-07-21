import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
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
  startSensitivity: vi.fn().mockResolvedValue({ job_id: 'j1' }),
  getSensitivityStatus: vi.fn().mockResolvedValue({ state: 'done', lines: [] }),
  cancelSensitivity: vi.fn().mockResolvedValue({}),
}))

import { getConfig, setConfig, startSensitivity } from './lib/api'
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

  // When >1 cores are requested but no MPI launcher exists, the backend silently
  // runs on a single core. Warn and confirm first instead of running silently.
  describe('multi-core run without an MPI launcher', () => {
    const BASE_CONFIG = {
      ca_dir: '',
      ca_exists: true,
      generated_model_format: 'cellml_only',
      solver: 'CVODE_myokit',
      solver_info: {},
      differentiable_operations: {},
    }

    // PrimeVue's Dialog teleports its DOM (jsdom doesn't surface it), so stub it
    // with one that renders its default + footer slots inline. Then the real
    // confirm/cancel buttons are in the wrapper and clickable.
    const DialogStub = {
      name: 'Dialog',
      props: { visible: Boolean },
      emits: ['update:visible'],
      template:
        '<div v-if="visible" v-bind="$attrs"><slot /><slot name="footer" /></div>',
    }

    let wrapper
    beforeEach(() => vi.clearAllMocks())
    afterEach(() => wrapper?.unmount())

    async function mountWith(mpiexecAvailable) {
      getConfig.mockResolvedValueOnce({ ...BASE_CONFIG, mpiexec_available: mpiexecAvailable })
      wrapper = shallowMount(App, {
        global: { stubs: { Dialog: DialogStub, Button: false } },
      })
      await flushPromises()
      return wrapper
    }

    function runSensitivity(num_cores) {
      wrapper.findComponent({ name: 'SensitivityPanel' }).vm.$emit('run', { method: 'sobol', num_cores })
      return flushPromises()
    }

    const warnDialog = () => wrapper.find('[data-testid="cores-warning"]')

    it('warns and defers the run instead of silently using one core', async () => {
      await mountWith(false)
      await runSensitivity(4)

      expect(startSensitivity).not.toHaveBeenCalled() // deferred, not run
      expect(warnDialog().exists()).toBe(true)
      expect(warnDialog().text()).toContain('single core')
    })

    it('runs (on one core, server-side) when the user confirms', async () => {
      await mountWith(false)
      await runSensitivity(4)
      await wrapper.find('[data-testid="cores-warning-confirm"]').trigger('click')
      await flushPromises()

      expect(startSensitivity).toHaveBeenCalledTimes(1)
      // num_cores is sent as-is; the backend does the single-core fallback.
      expect(startSensitivity.mock.calls[0][1]).toMatchObject({ num_cores: 4 })
      expect(warnDialog().exists()).toBe(false)
    })

    it('does not run when the user cancels', async () => {
      await mountWith(false)
      await runSensitivity(4)
      await wrapper.find('[data-testid="cores-warning-cancel"]').trigger('click')
      await flushPromises()

      expect(startSensitivity).not.toHaveBeenCalled()
      expect(warnDialog().exists()).toBe(false)
    })

    it('runs directly (no warning) for a single core', async () => {
      await mountWith(false)
      await runSensitivity(1)

      expect(startSensitivity).toHaveBeenCalledTimes(1)
      expect(warnDialog().exists()).toBe(false)
    })

    it('runs directly (no warning) when a launcher is available', async () => {
      await mountWith(true)
      await runSensitivity(4)

      expect(startSensitivity).toHaveBeenCalledTimes(1)
      expect(warnDialog().exists()).toBe(false)
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
