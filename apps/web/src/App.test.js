import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
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
  costSensitivity: vi.fn().mockResolvedValue({
    cost: 3,
    rel_step: 0.001,
    n_simulations: 3,
    params: [{ name: 'a/alpha', value: 1, derivative: 2, elasticity: 0.7, reason: null }],
    unavailable: null,
  }),
  getCalibrationDefaults: vi.fn().mockResolvedValue({}),
  getCalibrationPythons: vi.fn().mockResolvedValue({ pythons: [] }),
  startCalibration: vi.fn().mockResolvedValue({ job_id: 'job-1' }),
  getCalibrationStatus: vi.fn().mockResolvedValue({ state: 'running', log: '' }),
  getCalibrationProgress: vi.fn().mockResolvedValue({}),
  cancelCalibration: vi.fn().mockResolvedValue({}),
  calibratedModelUrl: vi.fn((id) => `/api/calibration/${id}/calibrated_model`),
  startSensitivity: vi.fn().mockResolvedValue({ job_id: 'job-2' }),
  getSensitivityStatus: vi.fn().mockResolvedValue({ state: 'running', log: '' }),
  cancelSensitivity: vi.fn().mockResolvedValue({}),
  startUQ: vi.fn().mockResolvedValue({ job_id: 'job-3' }),
  // Emulator training: started, then polled once to a terminal state so the
  // store's poll loop does not leave a timer behind.
  startEmulatorTraining: vi.fn().mockResolvedValue({ job_id: 'job-4' }),
  getEmulatorStatus: vi.fn().mockResolvedValue({ state: 'error', lines: [], error: '' }),
  cancelEmulatorTraining: vi.fn().mockResolvedValue({}),
  getUQStatus: vi.fn().mockResolvedValue({ state: 'running', log: '' }),
  getUQProgress: vi.fn().mockResolvedValue({}),
  cancelUQ: vi.fn().mockResolvedValue({}),
  getSensitivityDefaults: vi.fn().mockResolvedValue({}),
  getUQDefaults: vi.fn().mockResolvedValue({}),
  getEmulatorDefaults: vi.fn().mockResolvedValue({}),
  getEmulatorInfo: vi.fn().mockResolvedValue({}),
  predictEmulator: vi.fn().mockResolvedValue({ labels: [], values: [], cost: null }),
  costAtParams: vi.fn().mockResolvedValue({ cost: null, emulator_cost: null }),
  getConfig: vi.fn().mockResolvedValue({
    ca_dir: '',
    ca_exists: true,
    generated_model_format: 'cellml',
    solver: 'CVODE_myokit',
    solver_info: {},
    differentiable_operations: {},
  }),
  setConfig: vi.fn().mockResolvedValue({}),
  saveParams: vi.fn().mockResolvedValue({ path: '/out/run_a.npy', outputs_path: null }),
  loadParams: vi.fn().mockResolvedValue({ values: {} }),
  listSavedRuns: vi.fn().mockResolvedValue({ runs: [] }),
  loadSavedRun: vi.fn().mockResolvedValue({}),
  exportPipeline: vi.fn().mockResolvedValue({}),
  exportPlotting: vi.fn().mockResolvedValue({}),
  // The real one; a failure path that reports "undefined is not a function"
  // instead of the server's reason would pass a test and fail a user.
  errorMessage: (e) => String(e?.response?.data?.detail || e?.message || e),
}))

import {
  getConfig,
  setConfig,
  getEmulatorDefaults,
  getCalibrationPythons,
  saveParams,
  listSavedRuns,
  loadSavedRun,
  simulate,
  runProtocol,
  startEmulatorTraining,
  costSensitivity,
  predictEmulator,
  costAtParams,
  getEmulatorInfo,
} from './lib/api'
import { setNotificationCtor } from './lib/notify'
import App from './App.vue'
import TourOverlay from './components/TourOverlay.vue'
import { TOUR_STEPS } from './lib/tourSteps'

// Auto-run is the only way a run starts, and it is debounced by 300ms. A test
// that seeds its own displayed run has to let the run its study triggers happen
// first, or that run lands mid-test and replaces what was seeded.
const drainAutoRun = async () => {
  await new Promise((resolve) => setTimeout(resolve, 350))
  await flushPromises()
}

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
      generated_model_format: 'cellml',
      solver: 'CVODE_myokit',
      solver_info: {},
      differentiable_operations: {},
    }

    const NO_COMPILER = {
      present: false,
      hint: 'xcode-select --install',
      affects: "CVODE_myokit (generated model format 'cellml')",
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

  // The left column (params / sensitivity / calibration / uq) resizes the same
  // way, via a divider on its right edge (width = the pointer's x from the left).
  describe('resizable LHS column', () => {
    const dragTo = async (wrapper, clientX) => {
      await wrapper.find('[data-testid="lhs-handle"]').trigger('mousedown')
      window.dispatchEvent(new MouseEvent('mousemove', { clientX }))
      window.dispatchEvent(new MouseEvent('mouseup'))
      await nextTick()
    }

    beforeEach(() => localStorage.removeItem('cuflynx-lhs-width'))

    it('starts expanded with the drag divider present', () => {
      const wrapper = shallowMount(App)
      expect(wrapper.find('[data-testid="lhs-handle"]').exists()).toBe(true)
      expect(wrapper.vm.lhsWidth).toBeGreaterThan(0)
      expect(wrapper.find('[data-testid="lhs-column"]').classes()).not.toContain('collapsed')
    })

    it('drag resizes; dragging fully left collapses; the tab / dblclick restores', async () => {
      const wrapper = shallowMount(App)
      await dragTo(wrapper, 360) // 360px from the left edge
      expect(wrapper.vm.lhsWidth).toBe(360)

      await dragTo(wrapper, 10) // past the snap threshold -> hide
      expect(wrapper.vm.lhsWidth).toBe(0)
      expect(wrapper.vm.lhsCollapsed).toBe(true)
      expect(wrapper.find('[data-testid="lhs-column"]').classes()).toContain('collapsed')

      await dragTo(wrapper, 320) // drag the tab back out
      expect(wrapper.vm.lhsWidth).toBe(320)
      expect(wrapper.vm.lhsCollapsed).toBe(false)

      await dragTo(wrapper, 10)
      await wrapper.find('[data-testid="lhs-handle"]').trigger('dblclick')
      expect(wrapper.vm.lhsWidth).toBe(320)
    })
  })

  // The packaged desktop app has no default interpreter (its own executable is
  // the frozen bundle), so the choice must survive a restart or the user re-picks
  // it on every launch.
  describe('analysis interpreter persistence', () => {
    const BASE_CONFIG = {
      ca_dir: '',
      ca_exists: true,
      generated_model_format: 'cellml',
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

  // The global random seed (Settings popup) makes analysis runs reproducible; it
  // persists server-side like the interpreter choice, and defaults to none.
  describe('global random seed', () => {
    const BASE_CONFIG = {
      ca_dir: '',
      ca_exists: true,
      generated_model_format: 'cellml',
      solver: 'CVODE_myokit',
      solver_info: {},
      differentiable_operations: {},
    }

    it('hydrates the remembered seed without echoing it back', async () => {
      getConfig.mockResolvedValueOnce({ ...BASE_CONFIG, seed: 42 })
      setConfig.mockClear()
      const wrapper = shallowMount(App)
      await flushPromises()

      expect(wrapper.vm.seed).toBe(42)
      expect(setConfig).not.toHaveBeenCalledWith(expect.objectContaining({ seed: expect.anything() }))
    })

    it('captures and sends the seed when set', async () => {
      getConfig.mockResolvedValueOnce({ ...BASE_CONFIG, seed: null })
      setConfig.mockClear()
      const wrapper = shallowMount(App)
      await flushPromises()

      wrapper.vm.seed = 7
      await flushPromises()

      expect(setConfig).toHaveBeenCalledWith(expect.objectContaining({ seed: 7 }))
    })

    it('clears the seed by POSTing an empty value', async () => {
      getConfig.mockResolvedValueOnce({ ...BASE_CONFIG, seed: 7 })
      setConfig.mockClear()
      const wrapper = shallowMount(App)
      await flushPromises()

      wrapper.vm.seed = null
      await flushPromises()

      expect(setConfig).toHaveBeenCalledWith(expect.objectContaining({ seed: '' }))
    })
  })
})

// Regression for #84: switching the backend solver must update the LOCAL-SA
// gradient sources shown in the Sensitivity panel (cellml+CVODE_myokit -> FSA;
// casadi_python -> AD). PR #95 made the panel read the reactive /api/config
// gradient_sources; this drives the real App reactive path through a backend switch.
describe('App.vue sensitivity gradient sources track the backend (#84)', () => {
  const cellml = {
    ca_dir: '', ca_exists: true, generated_model_format: 'cellml',
    solver: 'CVODE_myokit', solver_info: {}, differentiable_operations: {},
    model_formats: ['cellml', 'python', 'casadi_python'],
    solvers_by_format: { cellml: ['CVODE_myokit'], python: ['solve_ivp'], casadi_python: ['casadi_integrator'] },
    default_solver_by_format: { cellml: 'CVODE_myokit', python: 'solve_ivp', casadi_python: 'casadi_integrator' },
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

  it('swaps FSA for AD when switching cellml -> casadi_python (and back)', async () => {
    getConfig.mockResolvedValue({ ...cellml })
    setConfig.mockImplementation(async (payload) =>
      payload && payload.generatedModelFormat === 'casadi_python' ? { ...casadi } : { ...cellml },
    )
    const wrapper = shallowMount(App)
    await flushPromises()
    // initial: cellml -> FD + FSA
    expect(wrapper.vm.gradientSources.map((s) => s.value)).toEqual(['FD', 'FSA'])

    // user switches backend to casadi_python in Settings
    wrapper.vm.onFormatChange('casadi_python')
    await flushPromises()
    expect(wrapper.vm.gradientSources.map((s) => s.value)).toEqual(['FD', 'AD'])

    // and back
    wrapper.vm.onFormatChange('cellml')
    await flushPromises()
    expect(wrapper.vm.gradientSources.map((s) => s.value)).toEqual(['FD', 'FSA'])
  })
})

// #298: warn (orange) when the selected integrator can't produce the backend's
// analytic gradient (AD/FSA), and drop that source from the menus.
describe('App.vue gradient integrator suitability warning (#298)', () => {
  const base = {
    ca_dir: '', ca_exists: true, differentiable_operations: {},
    model_formats: ['cellml', 'casadi_python'],
    ad_suitable_methods: { casadi_integrator: ['collocation', 'rk', 'semi_implicit_euler', 'bdf'] },
    fsa_suitable_methods: { CVODE_myokit: ['CVODE'] },
    default_method_by_solver: { casadi_integrator: 'bdf' },
  }

  it('warns for an AD-unsuitable integrator (cvodes) and not for a suitable one (bdf)', async () => {
    getConfig.mockResolvedValue({
      ...base, generated_model_format: 'casadi_python', solver: 'casadi_integrator',
      solver_info: { method: 'cvodes' },
    })
    const wrapper = shallowMount(App)
    await flushPromises()
    expect(wrapper.vm.gradientIntegratorWarning).toContain('not available')
    expect(wrapper.vm.gradientIntegratorWarning).toContain('cvodes')

    // switching the integrator to a suitable one clears the warning
    wrapper.vm.solverInfo.method = 'bdf'
    await wrapper.vm.$nextTick()
    expect(wrapper.vm.gradientIntegratorWarning).toBe('')
  })

  it('warns for FSA-unsuitable cellml integrators', async () => {
    getConfig.mockResolvedValue({
      ...base, generated_model_format: 'cellml', solver: 'CVODE_myokit',
      solver_info: { method: 'other' },
    })
    const wrapper = shallowMount(App)
    await flushPromises()
    expect(wrapper.vm.gradientIntegratorWarning).toContain('FSA')
  })
  // Individual-plot maximize (issue #115): a per-plot button expands one output
  // plot to fill the middle window; a stale key (removed/regenerated plot) falls
  // back to the normal grid.
  describe('individual plot maximize', () => {
    it('toggles the maximized plot key on and off', () => {
      const wrapper = shallowMount(App)
      expect(wrapper.vm.maximizedPlot).toBe(null)
      wrapper.vm.toggleMaximizePlot('exp0:x')
      expect(wrapper.vm.maximizedPlot).toBe('exp0:x')
      // clicking the same plot restores the grid
      wrapper.vm.toggleMaximizePlot('exp0:x')
      expect(wrapper.vm.maximizedPlot).toBe(null)
      // switching directly to another plot
      wrapper.vm.toggleMaximizePlot('exp0:x')
      wrapper.vm.toggleMaximizePlot('exp0:y')
      expect(wrapper.vm.maximizedPlot).toBe('exp0:y')
    })

    it('effectiveMaximized ignores a key with no matching plot cell', () => {
      const wrapper = shallowMount(App)
      // No sim has run, so there are no plot cells: a set key must not blank the view.
      wrapper.vm.toggleMaximizePlot('does-not-exist')
      expect(wrapper.vm.maximizedPlot).toBe('does-not-exist')
      expect(wrapper.vm.effectiveMaximized).toBe(null)
    })
  })
})

// Issue #124: "Add plot" can put another variable on the x axis instead of time
// (the PV-loop use case), so a phase-plane cell renders and both variables are
// requested from the engine.
describe('App.vue plot one variable against another (#124)', () => {
  const VARS = {
    params: [],
    odes: ['heart/V_lv', 'heart/P_lv'],
    algebraic: [],
    all_names: [],
  }

  // No model id: runSimulation() short-circuits, so the seeded result survives.
  const mountWithResult = async () => {
    const wrapper = shallowMount(App)
    await flushPromises()
    wrapper.vm.model.variables.value = { ...VARS }
    wrapper.vm.sim.setResult({
      time: [0, 1, 2],
      outputs: { 'heart/V_lv': [1, 2, 3], 'heart/P_lv': [4, 5, 6] },
    })
    await nextTick()
    return wrapper
  }

  const addPlot = async (wrapper, qname, xqname) => {
    wrapper.vm.openAddPlot({ key: 'single', expIdx: 0, label: '' })
    wrapper.vm.addPlotVar = qname
    wrapper.vm.addPlotXVar = xqname
    await nextTick()
    wrapper.vm.confirmAddPlot()
    await flushPromises()
  }

  it('requests both variables and renders a "y vs x" cell', async () => {
    const wrapper = await mountWithResult()
    await addPlot(wrapper, 'heart/P_lv', 'heart/V_lv')

    // Both axes must be requested, else the engine never returns the x series.
    expect([...wrapper.vm.extraOutputNames].sort()).toEqual([
      'heart/P_lv',
      'heart/V_lv',
    ])

    // The title names the y variable only — it reads as the y-axis label, and
    // the x variable is named under the x axis instead.
    const cell = wrapper.vm.plotGroups[0].cells.find((c) => c.removeId)
    expect(cell.title).toBe('heart/P_lv')
    expect(cell.xLabel).toBe('heart/V_lv')
    expect(cell.simResult.xValues).toEqual([1, 2, 3])

    const panel = wrapper
      .findAllComponents({ name: 'PlotPanel' })
      .find((p) => p.props('xLabel') === 'heart/V_lv')
    expect(panel).toBeTruthy()
    expect(panel.props('title')).toBe('heart/P_lv')
  })

  it('defaults the x axis to time, keeping the plain time-series plot', async () => {
    const wrapper = await mountWithResult()
    wrapper.vm.openAddPlot({ key: 'single', expIdx: 0, label: '' })
    expect(wrapper.vm.addPlotXVar).toBe('time')
    expect(wrapper.vm.addPlotXChoices.map((c) => c.value)).toEqual([
      'time',
      'heart/V_lv',
      'heart/P_lv',
    ])

    await addPlot(wrapper, 'heart/P_lv', 'time')
    expect(wrapper.vm.extraOutputNames).toEqual(['heart/P_lv'])
    const cell = wrapper.vm.plotGroups[0].cells.find((c) => c.removeId)
    expect(cell.title).toBe('heart/P_lv')
    expect(cell.xLabel).toBeUndefined()
    expect(cell.simResult.xValues).toBeUndefined()
  })

  it('still offers the same y against a different x axis', async () => {
    const wrapper = await mountWithResult()
    await addPlot(wrapper, 'heart/P_lv', 'heart/V_lv')

    wrapper.vm.openAddPlot({ key: 'single', expIdx: 0, label: '' })
    // Same x axis: that plot already exists.
    wrapper.vm.addPlotXVar = 'heart/V_lv'
    await nextTick()
    expect(wrapper.vm.addPlotChoices.map((c) => c.value)).not.toContain('heart/P_lv')
    // Against time it is a different plot, so it is offered again.
    wrapper.vm.addPlotXVar = 'time'
    await nextTick()
    expect(wrapper.vm.addPlotChoices.map((c) => c.value)).toContain('heart/P_lv')
  })

  describe('switching the axes', () => {
    it('swaps which variable is on which axis', async () => {
      const wrapper = await mountWithResult()
      await addPlot(wrapper, 'heart/P_lv', 'heart/V_lv')
      const id = wrapper.vm.plotGroups[0].cells.find((c) => c.removeId).removeId

      wrapper.vm.switchExtraPlotAxes(id)
      await nextTick()

      const cell = wrapper.vm.plotGroups[0].cells.find((c) => c.removeId)
      expect(cell.title).toBe('heart/V_lv')
      expect(cell.xLabel).toBe('heart/P_lv')
      expect(cell.simResult.outputs).toEqual({ 'heart/V_lv': [1, 2, 3] })
      expect(cell.simResult.xValues).toEqual([4, 5, 6])
    })

    it('is its own inverse', async () => {
      const wrapper = await mountWithResult()
      await addPlot(wrapper, 'heart/P_lv', 'heart/V_lv')
      const id = wrapper.vm.plotGroups[0].cells.find((c) => c.removeId).removeId
      wrapper.vm.switchExtraPlotAxes(id)
      wrapper.vm.switchExtraPlotAxes(id)
      await nextTick()
      const cell = wrapper.vm.plotGroups[0].cells.find((c) => c.removeId)
      expect(cell.title).toBe('heart/P_lv')
      expect(cell.xLabel).toBe('heart/V_lv')
    })

    // Both series were already requested for the phase-plane plot, so swapping
    // them is a relabelling — it must not need another run.
    it('needs no re-run, since both series are already requested', async () => {
      const wrapper = await mountWithResult()
      await addPlot(wrapper, 'heart/P_lv', 'heart/V_lv')
      const id = wrapper.vm.plotGroups[0].cells.find((c) => c.removeId).removeId
      wrapper.vm.switchExtraPlotAxes(id)
      await nextTick()
      expect([...wrapper.vm.extraOutputNames].sort()).toEqual([
        'heart/P_lv',
        'heart/V_lv',
      ])
    })

    // A time series has nothing to swap in for time, so it is not offered.
    it('does nothing for a plain time-series plot', async () => {
      const wrapper = await mountWithResult()
      await addPlot(wrapper, 'heart/P_lv', 'time')
      const id = wrapper.vm.plotGroups[0].cells.find((c) => c.removeId).removeId
      wrapper.vm.switchExtraPlotAxes(id)
      await nextTick()
      const cell = wrapper.vm.plotGroups[0].cells.find((c) => c.removeId)
      expect(cell.title).toBe('heart/P_lv')
      expect(cell.xLabel).toBeUndefined()
    })
  })
})

// Issue #196: overlay several variables on one plot, added from a button on the
// plot itself, and taken off again the same way.
describe('App.vue several variables on one plot (#196)', () => {
  const VARS = {
    params: [],
    odes: ['heart/V_lv', 'heart/P_lv'],
    algebraic: ['heart/V_rv'],
    all_names: [],
    units: { 'heart/V_lv': 'mL', 'heart/P_lv': 'mmHg', 'heart/V_rv': 'mL' },
  }
  const OUTPUTS = {
    'heart/V_lv': [1, 2, 3],
    'heart/P_lv': [4, 5, 6],
    'heart/V_rv': [7, 8, 9],
  }

  // No model id: runSimulation() short-circuits, so the seeded result survives.
  const mountWithResult = async () => {
    const wrapper = shallowMount(App)
    await flushPromises()
    wrapper.vm.model.variables.value = { ...VARS }
    wrapper.vm.sim.setResult({ time: [0, 1, 2], outputs: { ...OUTPUTS } })
    await nextTick()
    return wrapper
  }

  // A plot of one variable, which is where overlaying is worth testing: the
  // combined manual cell already draws everything.
  const addPlot = async (wrapper, qname) => {
    wrapper.vm.openAddPlot({ key: 'single', expIdx: 0, label: '' })
    wrapper.vm.addPlotVar = qname
    await nextTick()
    wrapper.vm.confirmAddPlot()
    await flushPromises()
  }

  const extraCell = (wrapper) =>
    wrapper.vm.plotGroups[0].cells.find((c) => c.removeId)

  const overlay = async (wrapper, cell, qname) => {
    wrapper.vm.openPlotVars(cell)
    wrapper.vm.plotVarsPick = qname
    await nextTick()
    wrapper.vm.confirmPlotVar()
    await flushPromises()
  }

  it('draws the added variable alongside the plot\'s own', async () => {
    const wrapper = await mountWithResult()
    await addPlot(wrapper, 'heart/V_lv')
    await overlay(wrapper, extraCell(wrapper), 'heart/V_rv')

    const cell = extraCell(wrapper)
    expect(Object.keys(cell.simResult.outputs)).toEqual(['heart/V_lv', 'heart/V_rv'])
    expect(cell.simResult.outputs['heart/V_rv']).toEqual([7, 8, 9])
    // The plot keeps its identity: the overlay is an addition, not a rename.
    expect(cell.title).toBe('heart/V_lv')
  })

  // Without this the cell would draw an empty trace: the engine only returns
  // what the run asked for.
  it('asks the engine for the overlaid variable', async () => {
    const wrapper = await mountWithResult()
    await addPlot(wrapper, 'heart/V_lv')
    await overlay(wrapper, extraCell(wrapper), 'heart/V_rv')
    expect([...wrapper.vm.extraOutputNames].sort()).toEqual([
      'heart/V_lv',
      'heart/V_rv',
    ])
  })

  // The comparison is the whole point of overlaying; silently losing it on the
  // next slider drag would make the feature useless.
  it('survives a re-run', async () => {
    const wrapper = await mountWithResult()
    await addPlot(wrapper, 'heart/V_lv')
    await overlay(wrapper, extraCell(wrapper), 'heart/V_rv')

    wrapper.vm.sim.setResult({
      time: [0, 1, 2],
      outputs: { ...OUTPUTS, 'heart/V_rv': [70, 80, 90] },
    })
    await nextTick()

    expect(extraCell(wrapper).simResult.outputs['heart/V_rv']).toEqual([70, 80, 90])
  })

  it('takes the variable off again without touching the plot', async () => {
    const wrapper = await mountWithResult()
    await addPlot(wrapper, 'heart/V_lv')
    const cell = extraCell(wrapper)
    await overlay(wrapper, cell, 'heart/V_rv')

    wrapper.vm.removePlotVar('heart/V_rv')
    await nextTick()

    expect(Object.keys(extraCell(wrapper).simResult.outputs)).toEqual(['heart/V_lv'])
    expect(wrapper.vm.extraOutputNames).toEqual(['heart/V_lv'])
  })

  it('only offers variables the plot is not already drawing', async () => {
    const wrapper = await mountWithResult()
    await addPlot(wrapper, 'heart/V_lv')
    wrapper.vm.openPlotVars(extraCell(wrapper))
    await nextTick()
    expect(wrapper.vm.plotVarsChoices).not.toContain('heart/V_lv')
    expect(wrapper.vm.plotVarsChoices).toContain('heart/V_rv')

    await overlay(wrapper, extraCell(wrapper), 'heart/V_rv')
    expect(wrapper.vm.plotVarsChoices).not.toContain('heart/V_rv')
  })

  // Only what the user added can be removed: taking away the cell's own
  // variable would leave an empty plot where "remove plot" was meant.
  it('lists what is drawn, marking only the overlays as removable', async () => {
    const wrapper = await mountWithResult()
    await addPlot(wrapper, 'heart/V_lv')
    await overlay(wrapper, extraCell(wrapper), 'heart/V_rv')
    expect(wrapper.vm.plotVarsDrawn).toEqual(['heart/V_lv', 'heart/V_rv'])
    expect(wrapper.vm.plotVarsAdded).toEqual(['heart/V_rv'])
  })

  describe('units', () => {
    it('keeps the axis unit when the variables agree', async () => {
      const wrapper = await mountWithResult()
      await addPlot(wrapper, 'heart/V_lv')
      await overlay(wrapper, extraCell(wrapper), 'heart/V_rv')
      const cell = extraCell(wrapper)
      expect(cell.yUnit).toBe('mL')
      expect(cell.mixedUnits).toBe(false)
    })

    // Allowed, not forbidden — but the plot must stop claiming a unit, and say
    // why rather than looking like a model that declares none.
    it('drops the axis unit and flags the plot when they do not', async () => {
      const wrapper = await mountWithResult()
      await addPlot(wrapper, 'heart/V_lv')
      await overlay(wrapper, extraCell(wrapper), 'heart/P_lv')
      const cell = extraCell(wrapper)
      expect(cell.yUnit).toBe('')
      expect(cell.mixedUnits).toBe(true)
    })

    // Warn before the line is drawn: afterwards the only signal is an axis that
    // has quietly lost its label.
    it('warns in the picker, naming both units', async () => {
      const wrapper = await mountWithResult()
      await addPlot(wrapper, 'heart/V_lv')
      wrapper.vm.openPlotVars(extraCell(wrapper))
      wrapper.vm.plotVarsPick = 'heart/P_lv'
      await nextTick()
      expect(wrapper.vm.plotVarsUnitWarning).toContain('mmHg')
      expect(wrapper.vm.plotVarsUnitWarning).toContain('mL')

      wrapper.vm.plotVarsPick = 'heart/V_rv'
      await nextTick()
      expect(wrapper.vm.plotVarsUnitWarning).toBe('')
    })
  })

  it('offers the affordance on every output plot, controlled inputs aside', async () => {
    const wrapper = await mountWithResult()
    await addPlot(wrapper, 'heart/V_lv')
    // Both the combined manual cell and the added one can take an overlay.
    for (const cell of wrapper.vm.plotGroups[0].cells) expect(cell.addable).toBe(true)

    // A controlled (params_to_change) cell draws its input on a synthesised
    // time base, not the run's, so a model variable would land on the wrong x.
    const controlled = wrapper.vm.withUserVars(
      { key: 'exp0:ctrl:heart/HR', controlled: true, simResult: { outputs: {} } },
      OUTPUTS,
    )
    expect(controlled.addable).toBeUndefined()
  })

  // A new model has different variables, so overlays naming the old one's are
  // meaningless — and the cell keys they hang off are gone too.
  it('forgets the overlays when a new model is loaded', async () => {
    const wrapper = await mountWithResult()
    await addPlot(wrapper, 'heart/V_lv')
    await overlay(wrapper, extraCell(wrapper), 'heart/V_rv')
    expect(wrapper.vm.plotVars).not.toEqual({})

    await wrapper.vm.onModelLoaded({ model_id: 'm2', name: 'other' })
    await flushPromises()
    expect(wrapper.vm.plotVars).toEqual({})
  })
})

// Multi-core runs need an MPI launcher from the selected interpreter's own env
// (#75). Nothing used to say which interpreter provides one, so the capability
// was undiscoverable — the picker now marks the ones that enable Cores > 1.
describe('App.vue interpreter MPI marker', () => {
  const CONFIG = {
    ca_dir: '',
    ca_exists: true,
    generated_model_format: 'cellml',
    solver: 'CVODE_myokit',
    solver_info: {},
    differentiable_operations: {},
  }
  const MPI_PY = {
    path: '/venv/bin/python',
    version: '3.11.4',
    ready: true,
    missing: [],
    mpi: true,
    mpiexec: '/venv/bin/mpiexec',
  }
  const PLAIN_PY = {
    path: '/usr/bin/python3',
    version: '3.10.6',
    ready: true,
    missing: [],
    mpi: false,
    mpiexec: null,
  }

  // A launcher on PATH but not in the interpreter's own environment: runs still
  // work (resolve_mpiexec falls back), so this is neither ✓ nor ✗.
  const PATH_PY = {
    path: '/usr/bin/python3.12',
    version: '3.12.1',
    ready: true,
    missing: [],
    mpi: false,
    mpiexec: '/usr/bin/mpiexec',
  }

  const mountWith = async (pythons, selected, extra = {}) => {
    getCalibrationPythons.mockResolvedValueOnce({ pythons })
    getConfig.mockResolvedValueOnce({ ...CONFIG, python_path: selected, ...extra })
    const wrapper = shallowMount(App)
    await flushPromises()
    return wrapper
  }
  const labelFor = (wrapper, path) =>
    wrapper.vm.pythonOptions.find((o) => o.value === path).label

  it('marks only the MPI-capable interpreter in the picker', async () => {
    const wrapper = await mountWith([MPI_PY, PLAIN_PY], '')
    expect(labelFor(wrapper, MPI_PY.path)).toContain('MPI ✓')
    expect(labelFor(wrapper, PLAIN_PY.path)).not.toContain('MPI')
  })

  it('shows the launcher path in the chip tooltip for the selected interpreter', async () => {
    const wrapper = await mountWith([MPI_PY, PLAIN_PY], MPI_PY.path)
    const chip = wrapper.find('[data-testid="python-mpi"]')
    expect(chip.text()).toContain('MPI ✓')
    expect(chip.attributes('title')).toContain('/venv/bin/mpiexec')
  })

  it('flags an interpreter without a launcher of its own', async () => {
    const wrapper = await mountWith([MPI_PY, PLAIN_PY], PLAIN_PY.path)
    const chip = wrapper.find('[data-testid="python-mpi"]')
    expect(chip.text()).toContain('MPI ✗')
    expect(chip.attributes('title')).toContain('Cores > 1 unavailable')
  })

  it('says nothing about MPI for an interpreter that was never probed', async () => {
    // A browsed path the server didn't discover: unknown, not "no MPI".
    const wrapper = await mountWith([MPI_PY], '/elsewhere/python')
    expect(wrapper.find('[data-testid="python-mpi"]').exists()).toBe(false)
  })

  // A PATH launcher still runs (resolve_mpiexec falls back to it), so reporting
  // "Cores > 1 unavailable" would contradict a machine where multi-core works.
  // It is a distinct state because it is the one that can mismatch mpi4py.
  it('distinguishes a PATH launcher from one in the interpreter itself', async () => {
    const wrapper = await mountWith([PATH_PY], PATH_PY.path)
    const chip = wrapper.find('[data-testid="python-mpi"]')
    expect(chip.text()).toContain('MPI (system)')
    expect(chip.attributes('title')).toContain('/usr/bin/mpiexec')
    expect(chip.attributes('title')).toContain('PATH')
    // Not offered as the recommended pick in the dropdown, though.
    expect(labelFor(wrapper, PATH_PY.path)).not.toContain('MPI ✓')
  })

  it('says Cores > 1 is unavailable only when no launcher resolves at all', async () => {
    const wrapper = await mountWith([PLAIN_PY], PLAIN_PY.path)
    expect(wrapper.find('[data-testid="python-mpi"]').attributes('title')).toContain(
      'Cores > 1 unavailable',
    )
  })

  // Regression (#75 follow-up): the server resolves "" to a concrete
  // interpreter and reports that path back, so choosing "Server default" used
  // to (a) bounce the picker onto that path on the next load and (b) drop the
  // MPI chip, making an MPI-capable default look like it had lost MPI.
  describe('server default', () => {
    it('stays selected when the server reports the path it resolves to', async () => {
      const wrapper = await mountWith([MPI_PY], MPI_PY.path, {
        python_default: MPI_PY.path,
      })
      expect(wrapper.vm.pythonPath).toBe('')
    })

    it('keeps an explicit pick that is not the default', async () => {
      const wrapper = await mountWith([MPI_PY, PLAIN_PY], MPI_PY.path, {
        python_default: PLAIN_PY.path,
      })
      expect(wrapper.vm.pythonPath).toBe(MPI_PY.path)
    })

    it('reports the MPI support of the default interpreter, not silence', async () => {
      const wrapper = await mountWith([MPI_PY], MPI_PY.path, {
        python_default: MPI_PY.path,
      })
      const chip = wrapper.find('[data-testid="python-mpi"]')
      expect(chip.exists()).toBe(true)
      expect(chip.text()).toContain('MPI ✓')
      expect(chip.attributes('title')).toContain('/venv/bin/mpiexec')
    })

    it('names the interpreter the default resolves to, and marks its MPI', async () => {
      const wrapper = await mountWith([MPI_PY], '', { python_default: MPI_PY.path })
      const label = labelFor(wrapper, '')
      expect(label).toContain('Server default')
      expect(label).toContain(MPI_PY.path)
      expect(label).toContain('MPI ✓')
    })

    it('stays a bare label when the default is unknown (packaged: no external)', async () => {
      const wrapper = await mountWith([MPI_PY], '', { python_default: '' })
      expect(labelFor(wrapper, '')).toBe('Server default')
      expect(wrapper.find('[data-testid="python-mpi"]').exists()).toBe(false)
    })
  })
})

// Issue #105: a Settings toggle (default OFF, per the issue discussion) that pops
// a browser notification when a calibration / sensitivity / UQ run ends. The
// preference is client-side only, so it lives in localStorage like the theme.
describe('App.vue notify-when-long-runs-finish setting (#105)', () => {
  beforeEach(() => {
    localStorage.removeItem('cuflynx-notify-on-finish')
    setNotificationCtor(undefined)
  })
  afterEach(() => setNotificationCtor(undefined))

  it('defaults to OFF for a fresh user', async () => {
    const wrapper = shallowMount(App)
    await flushPromises()
    expect(wrapper.vm.notifyOnFinish).toBe(false)
    // …and nothing is written to localStorage just by mounting.
    expect(localStorage.getItem('cuflynx-notify-on-finish')).toBe(null)
  })

  it('restores a remembered "on" from localStorage', async () => {
    localStorage.setItem('cuflynx-notify-on-finish', '1')
    const wrapper = shallowMount(App)
    await flushPromises()
    expect(wrapper.vm.notifyOnFinish).toBe(true)
  })

  it('persists the choice and requests permission when switched on', async () => {
    const Ctor = function () {}
    Ctor.permission = 'default'
    Ctor.requestPermission = vi.fn().mockResolvedValue('granted')
    setNotificationCtor(Ctor)

    const wrapper = shallowMount(App)
    await flushPromises()
    wrapper.vm.notifyOnFinish = true
    await flushPromises()

    expect(Ctor.requestPermission).toHaveBeenCalled()
    expect(localStorage.getItem('cuflynx-notify-on-finish')).toBe('1')
    expect(wrapper.vm.notifyWarning).toBe('')
  })

  it('surfaces a denial inline instead of leaving a dead toggle', async () => {
    const Ctor = function () {}
    Ctor.permission = 'default'
    Ctor.requestPermission = vi.fn().mockResolvedValue('denied')
    setNotificationCtor(Ctor)

    const wrapper = shallowMount(App)
    await flushPromises()
    wrapper.vm.notifyOnFinish = true
    await flushPromises()

    // notifyWarning drives the [data-testid="notify-warning"] row in Settings.
    expect(wrapper.vm.notifyWarning).toContain('blocked')
    // The toggle itself stays on — the user asked for it; we just say why it can't work.
    expect(wrapper.vm.notifyOnFinish).toBe(true)
  })

  it('explains an unsupported browser (no Notification API)', async () => {
    setNotificationCtor(null)
    const wrapper = shallowMount(App)
    await flushPromises()
    wrapper.vm.notifyOnFinish = true
    await flushPromises()
    expect(wrapper.vm.notifyWarning).toContain('does not support')
  })

  it('clears the warning and persists "off" when switched back off', async () => {
    setNotificationCtor(null)
    const wrapper = shallowMount(App)
    await flushPromises()
    wrapper.vm.notifyOnFinish = true
    await flushPromises()
    wrapper.vm.notifyOnFinish = false
    await flushPromises()
    expect(wrapper.vm.notifyWarning).toBe('')
    expect(localStorage.getItem('cuflynx-notify-on-finish')).toBe('0')
  })
})

// Issue #126: "Save current" also stores the traces those values produced, and
// the saved runs can be ticked back on to compare against the live one.
describe('App.vue saved-run overlays (#126)', () => {
  const VARS = { params: [], odes: ['m/x'], algebraic: [], all_names: [] }
  const RUN = {
    prefix: 'run_a',
    path: '/out/run_a_outputs.json',
    saved_at: '2026-01-02T00:00:00+00:00',
    params: { 'm/x': 1 },
    variables: ['m/x'],
  }

  const mountWithResult = async () => {
    const wrapper = shallowMount(App)
    await flushPromises()
    wrapper.vm.model.variables.value = { ...VARS }
    wrapper.vm.sim.setResult({ time: [0, 1, 2], outputs: { 'm/x': [1, 2, 3] } })
    await nextTick()
    return wrapper
  }

  it('saves the traces alongside the parameters', async () => {
    const wrapper = await mountWithResult()
    wrapper.vm.model.modelId.value = 'model-1'
    simulate.mockResolvedValue({ time: [0, 1, 2], outputs: { 'm/x': [1, 2, 3] } })
    await wrapper.vm.onSaveParams({ filename: 'run_a.npy' })
    await flushPromises()

    const [, , filename, , result] = saveParams.mock.calls.at(-1)
    expect(filename).toBe('run_a.npy')
    expect(result.outputs['m/x']).toEqual([1, 2, 3])
  })

  // Issue #148: the displayed run only holds what was on screen, so a saved run
  // built from it has nothing to show on a plot added afterwards.
  describe('covering plots that do not exist yet', () => {
    const ALL_VARS = { params: [], odes: ['m/x'], algebraic: ['m/y'], all_names: [] }

    // A run is a protocol run now -- the window is the protocol's -- so the
    // displayed run is one experiment and the wider re-run is another.
    const mountForSave = async () => {
      const wrapper = shallowMount(App)
      await flushPromises()
      wrapper.vm.model.modelId.value = 'model-1'
      wrapper.vm.model.variables.value = { ...ALL_VARS }
      wrapper.vm.obs.setObsData({
        protocol_info: { pre_times: [0], sim_times: [[1]] },
        data_items: [],
      })
      await drainAutoRun()
      // On screen: states only, which is what a live run asks for.
      wrapper.vm.sim.setExperiments([{ time: [0, 1], outputs: { 'm/x': [1, 2] } }])
      await nextTick()
      return wrapper
    }

    it('re-runs asking for every plottable variable', async () => {
      const wrapper = await mountForSave()
      runProtocol.mockClear()
      runProtocol.mockResolvedValue({
        experiments: [{ time: [0, 1], outputs: { 'm/x': [1, 2], 'm/y': [3, 4] } }],
      })

      await wrapper.vm.onSaveParams({ filename: 'run_a.npy' })
      await flushPromises()

      expect(runProtocol).toHaveBeenCalledTimes(1)
      expect(runProtocol.mock.calls[0][2].outputs).toEqual(
        expect.arrayContaining(['m/x', 'm/y']),
      )
      // ...and the algebraic variable, absent from the displayed run, is saved.
      expect(saveParams.mock.calls.at(-1)[4].experiments[0].outputs['m/y']).toEqual([3, 4])
    })

    // A solver failure while widening the outputs must not cost the save.
    it('falls back to the displayed run when the wider run fails', async () => {
      const wrapper = await mountForSave()
      runProtocol.mockRejectedValue(new Error('solver failed'))

      await wrapper.vm.onSaveParams({ filename: 'run_a.npy' })
      await flushPromises()

      expect(saveParams).toHaveBeenCalled()
      expect(saveParams.mock.calls.at(-1)[4].experiments[0].outputs['m/x']).toEqual([1, 2])
      runProtocol.mockResolvedValue({ experiments: [] })
    })

    it('saves the displayed run when no model is loaded', async () => {
      const wrapper = shallowMount(App)
      await flushPromises()
      wrapper.vm.sim.setResult({ time: [0], outputs: { 'm/x': [1] } })
      simulate.mockClear()
      runProtocol.mockClear()
      await wrapper.vm.onSaveParams({ filename: 'run_a.npy' })
      await flushPromises()
      expect(runProtocol).not.toHaveBeenCalled()
      expect(simulate).not.toHaveBeenCalled()
    })
  })

  it('saves a protocol run as its experiments, not a flattened trace', async () => {
    const wrapper = await mountWithResult()
    wrapper.vm.sim.setExperiments([
      { time: [0, 1], outputs: { 'm/x': [1, 2] } },
      { time: [0, 1], outputs: { 'm/x': [3, 4] } },
    ])
    await nextTick()
    await wrapper.vm.onSaveParams({ filename: 'run_a.npy' })
    await flushPromises()

    const result = saveParams.mock.calls.at(-1)[4]
    expect(result.experiments).toHaveLength(2)
  })

  // The parameters are what the user asked to save; the traces ride along.
  it('reports a failed outputs write without pretending nothing saved', async () => {
    saveParams.mockResolvedValueOnce({
      path: '/out/run_a.npy',
      outputs_path: null,
      outputs_error: 'could not write the saved outputs to /out: disk full',
    })
    const wrapper = await mountWithResult()
    await wrapper.vm.onSaveParams({ filename: 'run_a.npy' })
    await flushPromises()
    expect(wrapper.vm.sim.message.value).toContain('Parameters saved, but')
    expect(wrapper.vm.sim.message.value).toContain('disk full')
  })

  it('refreshes the saved list after a save', async () => {
    listSavedRuns.mockResolvedValue({ runs: [RUN] })
    const wrapper = await mountWithResult()
    await wrapper.vm.onSaveParams({ filename: 'run_a.npy' })
    await flushPromises()
    expect(wrapper.vm.savedRuns.items.value.map((r) => r.prefix)).toEqual(['run_a'])
  })

  it('a ticked run reaches the plot cell as an overlay', async () => {
    listSavedRuns.mockResolvedValue({ runs: [RUN] })
    loadSavedRun.mockResolvedValue({
      prefix: 'run_a',
      params: { 'm/x': 1 },
      time: [0, 1, 2],
      outputs: { 'm/x': [9, 9, 9] },
    })
    const wrapper = await mountWithResult()
    await wrapper.vm.savedRuns.refresh('/out')
    await wrapper.vm.onToggleSavedRun('run_a')
    await nextTick()

    const cell = wrapper.vm.plotGroups[0].cells[0]
    expect(cell.savedSeries).toHaveLength(1)
    expect(cell.savedSeries[0]).toMatchObject({ prefix: 'run_a', values: [9, 9, 9] })
    expect(cell.savedSeries[0].color).toBeTruthy()
  })

  it('unticking it removes the overlay again', async () => {
    listSavedRuns.mockResolvedValue({ runs: [RUN] })
    loadSavedRun.mockResolvedValue({
      prefix: 'run_a',
      params: {},
      time: [0],
      outputs: { 'm/x': [9] },
    })
    const wrapper = await mountWithResult()
    await wrapper.vm.savedRuns.refresh('/out')
    await wrapper.vm.onToggleSavedRun('run_a')
    await wrapper.vm.onToggleSavedRun('run_a')
    await nextTick()
    expect(wrapper.vm.plotGroups[0].cells[0].savedSeries).toEqual([])
  })
})

// The calibration best fit is tickable in the same list (#126): its values are
// known as soon as calibration finishes, its traces only once the model is run
// at them.
describe('App.vue best-fit overlay (#126)', () => {
  const VARS = { params: [], odes: ['m/x'], algebraic: [], all_names: [] }

  const mountWithFit = async (best = { 'm/alpha': 9 }) => {
    listSavedRuns.mockResolvedValue({ runs: [] })
    const wrapper = shallowMount(App)
    await flushPromises()
    // A model id is what makes hasModel true, and a protocol_info is what gives
    // the run a window -- without both there is nothing to run the fit against.
    wrapper.vm.model.modelId.value = 'model-1'
    wrapper.vm.model.variables.value = { ...VARS }
    wrapper.vm.obs.setObsData({
      protocol_info: { pre_times: [0], sim_times: [[1]] },
      data_items: [{ variable: 'm/x', operands: ['m/x'], plot_type: 'horizontal' }],
    })
    await drainAutoRun()
    wrapper.vm.sim.setExperiments([{ time: [0, 1], outputs: { 'm/x': [1, 2] } }])
    wrapper.vm.calib.bestParams.value = best
    await flushPromises()
    return wrapper
  }

  it('offers the best fit as soon as a calibration produces one', async () => {
    const wrapper = await mountWithFit()
    const items = wrapper.vm.savedRuns.items.value
    expect(items[0]).toMatchObject({ prefix: 'best fit', virtual: true })
    expect(items[0].params).toEqual({ 'm/alpha': 9 })
  })

  it('offers nothing before a calibration has run', async () => {
    listSavedRuns.mockResolvedValue({ runs: [] })
    const wrapper = shallowMount(App)
    await flushPromises()
    expect(wrapper.vm.savedRuns.items.value).toEqual([])
  })

  it('runs the model at the fitted values only when ticked', async () => {
    const wrapper = await mountWithFit()
    runProtocol.mockClear()
    runProtocol.mockResolvedValue({
      experiments: [{ time: [0, 1], outputs: { 'm/x': [5, 6] } }],
    })

    await wrapper.vm.onToggleSavedRun('best fit')
    await flushPromises()

    expect(runProtocol).toHaveBeenCalledTimes(1)
    // The fit only names calibrated params; the rest stay where the sliders are.
    expect(runProtocol.mock.calls[0][1]).toMatchObject({ 'm/alpha': 9 })
  })

  it('the fitted trace reaches the plot cell as an overlay', async () => {
    const wrapper = await mountWithFit()
    runProtocol.mockResolvedValue({
      experiments: [{ time: [0, 1], outputs: { 'm/x': [5, 6] } }],
    })
    await wrapper.vm.onToggleSavedRun('best fit')
    await nextTick()

    const cell = wrapper.vm.plotGroups[0].cells[0]
    expect(cell.savedSeries[0]).toMatchObject({ prefix: 'best fit', values: [5, 6] })
  })

  // A second calibration under the same name is a different run.
  it('takes down a shown best fit when a new one arrives', async () => {
    const wrapper = await mountWithFit()
    runProtocol.mockResolvedValue({
      experiments: [{ time: [0, 1], outputs: { 'm/x': [5, 6] } }],
    })
    await wrapper.vm.onToggleSavedRun('best fit')
    expect(wrapper.vm.savedRuns.isShown('best fit')).toBe(true)

    wrapper.vm.calib.bestParams.value = { 'm/alpha': 12 }
    await flushPromises()
    expect(wrapper.vm.savedRuns.isShown('best fit')).toBe(false)
    expect(wrapper.vm.savedRuns.items.value[0].params).toEqual({ 'm/alpha': 12 })
  })
})

// Issue #145: plots in a window should share a y-axis width so they line up.
describe('App.vue shared plot alignment (#145)', () => {
  const VARS = { params: [], odes: ['m/x', 'm/y'], algebraic: [], all_names: [] }

  const mountWithPlots = async () => {
    listSavedRuns.mockResolvedValue({ runs: [] })
    const wrapper = shallowMount(App)
    await flushPromises()
    wrapper.vm.model.variables.value = { ...VARS }
    wrapper.vm.sim.setResult({
      time: [0, 1],
      outputs: { 'm/x': [1, 2], 'm/y': [3, 4] },
    })
    await nextTick()
    return wrapper
  }

  it('shares the widest reported width across the window', async () => {
    const wrapper = await mountWithPlots()
    wrapper.vm.axisAlign.report('single', 47)
    wrapper.vm.axisAlign.report('other', 62)
    await nextTick()
    expect(wrapper.vm.sharedAxisWidth).toBe(62)
  })

  // A maximized plot is alone in the window: nothing to line it up with, and
  // forcing the shared width on it would only waste margin.
  it('aligns nothing while a plot is maximized', async () => {
    const wrapper = await mountWithPlots()
    wrapper.vm.axisAlign.report('single', 62)
    await nextTick()
    expect(wrapper.vm.sharedAxisWidth).toBe(62)

    wrapper.vm.toggleMaximizePlot('single')
    await nextTick()
    expect(wrapper.vm.sharedAxisWidth).toBe(0)
  })

  // A phase-plane cell's x is another variable, so aligning it against time
  // plots would line up axes that have nothing to do with each other.
  it('excludes phase-plane cells', async () => {
    const wrapper = await mountWithPlots()
    expect(wrapper.vm.alignsWithTime({ key: 'a' })).toBe(true)
    expect(wrapper.vm.alignsWithTime({ key: 'b', xLabel: 'm/y' })).toBe(false)

    wrapper.vm.onAxisWidth({ key: 'b', xLabel: 'm/y' }, 99)
    await nextTick()
    expect(wrapper.vm.sharedAxisWidth).toBe(0)
  })

  it('forgets a plot that has gone, so it stops padding the rest', async () => {
    const wrapper = await mountWithPlots()
    wrapper.vm.axisAlign.report('single', 40)
    wrapper.vm.axisAlign.report('removed-cell', 90)
    await nextTick()
    expect(wrapper.vm.sharedAxisWidth).toBe(90)

    // The pruning watch fires on the set of live cell keys.
    wrapper.vm.sim.setResult({ time: [0, 1], outputs: { 'm/x': [1, 2] } })
    await nextTick()
    expect(wrapper.vm.axisAlign.widths['removed-cell']).toBeUndefined()
  })
})

// Issue #27: a Myokit .mmt with a bare `time = 0 bind time` declares no time
// unit, so the converted CellML reports `dimensionless` and the time axis had
// nothing to label itself with.
describe('App.vue time unit (#27)', () => {
  const withUnits = async (units) => {
    listSavedRuns.mockResolvedValue({ runs: [] })
    const wrapper = shallowMount(App)
    await flushPromises()
    wrapper.vm.model.variables.value = {
      params: [], odes: ['m/x'], algebraic: [], all_names: [], units,
    }
    await nextTick()
    return wrapper
  }

  beforeEach(() => localStorage.removeItem('cuflynx-time-unit'))

  it('takes the unit the model declares', async () => {
    const wrapper = await withUnits({ 'environment/time': 'second', 'm/x': 'mM' })
    expect(wrapper.vm.modelTimeUnit).toBe('second')
    expect(wrapper.vm.timeUnitLabel).toBe('second')
  })

  // `dimensionless` is what "never declared" looks like — not a unit, so the
  // axis must not be labelled with it.
  it('treats dimensionless as undeclared', async () => {
    const wrapper = await withUnits({ 'engine/time': 'dimensionless', 'm/x': 'mV' })
    expect(wrapper.vm.modelTimeUnit).toBe('')
    expect(wrapper.vm.timeUnitLabel).toBe('')
  })

  it('uses the user unit when the model declares none', async () => {
    const wrapper = await withUnits({ 'engine/time': 'dimensionless' })
    wrapper.vm.timeUnitOverride = 'ms'
    await nextTick()
    expect(wrapper.vm.timeUnitLabel).toBe('ms')
  })

  // The model is the authority: a user unit must not be able to make the axis
  // disagree with the equations.
  it('never lets a user unit override the model own', async () => {
    const wrapper = await withUnits({ 'environment/time': 'second' })
    wrapper.vm.timeUnitOverride = 'ms'
    await nextTick()
    expect(wrapper.vm.timeUnitLabel).toBe('second')
  })

  it('remembers the unit across sessions', async () => {
    const wrapper = await withUnits({ 'engine/time': 'dimensionless' })
    wrapper.vm.timeUnitOverride = 'ms'
    await nextTick()
    expect(localStorage.getItem('cuflynx-time-unit')).toBe('ms')
  })

  it('nothing is guessed when neither supplies one', async () => {
    const wrapper = await withUnits({ 'engine/time': 'dimensionless' })
    expect(wrapper.vm.timeUnitLabel).toBe('')
  })
})

// Issue #122: aadc_python is only offered when Matlogica's AADC is importable,
// so Settings must explain a missing format rather than leaving a silent gap.
describe('App.vue AADC availability (#122)', () => {
  const CONFIG = {
    ca_dir: '',
    ca_exists: true,
    generated_model_format: 'cellml',
    solver: 'CVODE_myokit',
    solver_info: {},
    differentiable_operations: {},
  }

  const mountWith = async (aadc) => {
    getConfig.mockResolvedValueOnce({ ...CONFIG, aadc })
    const wrapper = shallowMount(App)
    await flushPromises()
    return wrapper
  }

  it('says why the format is missing, and how to get it', async () => {
    const wrapper = await mountWith({
      available: false,
      in_app: false,
      in_analysis_python: null,
      hint: 'Request a licence at https://matlogica.com/',
      licence_url: 'https://matlogica.com/',
    })
    expect(wrapper.vm.aadcNotice).toContain('not installed')
    expect(wrapper.vm.aadcNotice).toContain('matlogica.com')
  })

  it('confirms it when the library is there', async () => {
    const wrapper = await mountWith({
      available: true,
      in_app: true,
      in_analysis_python: true,
      hint: '',
      licence_url: '',
    })
    expect(wrapper.vm.aadcNotice).toContain('available')
  })

  // Analysis runs happen in the user's own Python, so having it in only the app
  // is a run waiting to fail.
  it('warns when it is missing from the analysis interpreter', async () => {
    const wrapper = await mountWith({
      available: true,
      in_app: true,
      in_analysis_python: false,
      hint: '',
      licence_url: '',
    })
    expect(wrapper.vm.aadcNotice).toContain('analysis runs')
  })

  it('stays quiet when the backend says nothing about AADC (older API)', async () => {
    const wrapper = await mountWith(undefined)
    expect(wrapper.vm.aadcNotice).toBe('')
  })
})

// The run window has ONE source: the obs_data's protocol_info. The t₁/pre
// spinners were a second one, and a calibration then ran over one window while
// the live cost ran over the other -- the same parameters, two different costs.
// So the spinners are gone, and with them the Run button (auto-run is the only
// way to run) and the Clear obs data buttons.
describe('App.vue top bar run window', () => {
  const PROTOCOL = {
    protocol_info: { pre_times: [1, 1], sim_times: [[2, 3], [4]] },
    n_experiments: 2,
    data_items: [],
  }

  const mounted = async ({ model = true, obsData = null } = {}) => {
    const wrapper = shallowMount(App)
    await flushPromises()
    if (model) wrapper.vm.model.modelId.value = 'abc'
    if (obsData) wrapper.vm.obs.setObsData(obsData)
    await nextTick()
    return wrapper
  }

  it('offers no t1/pre spinners, at any point', async () => {
    const empty = await mounted({ model: false })
    expect(empty.find('[data-testid="time-controls"]').exists()).toBe(false)
    const withModel = await mounted()
    expect(withModel.find('[data-testid="time-controls"]').exists()).toBe(false)
    const withProtocol = await mounted({ obsData: PROTOCOL })
    expect(withProtocol.find('[data-testid="time-controls"]').exists()).toBe(false)
  })

  it('offers no Run button and no Clear obs data', async () => {
    const wrapper = await mounted({ obsData: PROTOCOL })
    const labels = wrapper
      .findAllComponents({ name: 'Button' })
      .map((b) => b.props('label'))
    expect(labels).not.toContain('Run')
    expect(labels).not.toContain('Clear obs data')
  })

  // The protocol readout is now the only visible statement of the run window, so
  // it says the window and not only the shape of the protocol.
  it('states the window the protocol sets', async () => {
    const wrapper = await mounted({ obsData: PROTOCOL })
    const text = wrapper.find('[data-testid="protocol-summary"]').text()
    expect(text).toContain('2 experiment(s)')
    expect(text).toContain('9') // total sim_times
    expect(text).toContain('2') // total pre_times
    expect(wrapper.vm.simTime).toBe(9)
    expect(wrapper.vm.preTime).toBe(2)
  })

  it('says what is missing when a model has no protocol to run over', async () => {
    const wrapper = await mounted()
    expect(wrapper.find('[data-testid="protocol-summary"]').exists()).toBe(false)
    expect(wrapper.find('[data-testid="no-protocol"]').text()).toContain('protocol_info')
    // ...and a data-only obs_data is the same state: overlays, but no window.
    wrapper.vm.obs.setObsData({ has_protocol: false, data_items: [] })
    await nextTick()
    expect(wrapper.find('[data-testid="no-protocol"]').exists()).toBe(true)
  })

  it('says nothing at all before a model is loaded', async () => {
    const wrapper = await mounted({ model: false })
    expect(wrapper.find('[data-testid="no-protocol"]').exists()).toBe(false)
    expect(wrapper.find('[data-testid="protocol-summary"]').exists()).toBe(false)
  })
})

// No protocol, no run: a request with no window to run over could not mean
// anything, so nothing is sent and the top bar says why (above).
describe('App.vue blocks running without a protocol', () => {
  // Its own model id, so "was this app run?" is answered by the calls' model id
  // rather than by a call count another still-mounted app could add to.
  const MODEL = 'no-protocol-model'
  const ranThisModel = () =>
    [...simulate.mock.calls, ...runProtocol.mock.calls].some((c) => c[0] === MODEL)

  const withModel = async () => {
    const wrapper = shallowMount(App)
    await flushPromises()
    wrapper.vm.model.modelId.value = MODEL
    await nextTick()
    return wrapper
  }

  it('runs nothing for a model with no obs_data', async () => {
    const wrapper = await withModel()
    await wrapper.vm.runSimulation()
    await flushPromises()
    expect(ranThisModel()).toBe(false)
    // No spinner left turning, and no error banner: nothing has gone wrong.
    expect(wrapper.vm.sim.status.value).toBe('idle')
    expect(wrapper.vm.sim.message.value).toBe('')
  })

  it('runs nothing for a data-only obs_data either', async () => {
    const wrapper = await withModel()
    wrapper.vm.obs.setObsData({ has_protocol: false, data_items: [] })
    await flushPromises()
    await wrapper.vm.runSimulation()
    await flushPromises()
    expect(ranThisModel()).toBe(false)
  })

  it('does not debounce one into existence when a slider moves', async () => {
    vi.useFakeTimers()
    try {
      const wrapper = await withModel()
      wrapper.vm.sliders.addSlider('a/alpha', { min: 0, max: 2, value: 1 })
      wrapper.vm.sliders.setValue('a/alpha', 1.5)
      await nextTick()
      vi.advanceTimersByTime(1000)
      await flushPromises()
      expect(ranThisModel()).toBe(false)
    } finally {
      vi.useRealTimers()
    }
  })

  // Auto-run is the only way to run, so loading the protocol is what starts one.
  it('runs as soon as an obs_data with a protocol arrives', async () => {
    vi.useFakeTimers()
    try {
      const wrapper = await withModel()
      wrapper.vm.onObsDataLoaded({
        protocol_info: { pre_times: [0], sim_times: [[5]] },
        data_items: [],
      })
      await nextTick()
      vi.advanceTimersByTime(1000)
      await flushPromises()
      expect(runProtocol.mock.calls.filter((c) => c[0] === MODEL)).toHaveLength(1)
    } finally {
      vi.useRealTimers()
    }
  })
})

// Issue #159: the cost belongs at the top of the panel where the parameters are
// being changed, not only in the Analysis tab.
describe('App.vue cost line (#159)', () => {
  // Through the store's own setters, not by assigning `sim.result` -- which is
  // how the first version of these tests passed while the feature did not.
  // setExperiments (the protocol path, and the only path that has a cost worth
  // showing) nulls `result`, so a cost read from there was always null.
  const withCost = async (cost, { protocol = true } = {}) => {
    const wrapper = shallowMount(App)
    await flushPromises()
    if (protocol) {
      wrapper.vm.sim.setExperiments([{ time: [0, 1], outputs: { 'a/x': [1, 2] } }], [], 1, cost)
    } else {
      wrapper.vm.sim.setResult({ time: [0, 1], outputs: { 'a/x': [1, 2] }, cost })
    }
    await nextTick()
    return wrapper
  }

  it('shows the cost of the current parameters above the plots', async () => {
    const wrapper = await withCost({
      cost: 1363.2,
      items: [{ label: 'u', cost: 900 }, { label: 'v', cost: 463.2 }],
    })
    expect(wrapper.find('[data-testid="cost-value"]').text()).toBe('1363')
  })

  it('says how many observables it could score, not just the total', async () => {
    const wrapper = await withCost({
      cost: 900,
      items: [{ label: 'u', cost: 900 }, { label: 'v', cost: null }],
    })
    expect(wrapper.find('[data-testid="cost-line"]').text()).toContain('1 of 2')
  })

  // Issue #181: the cost is a mean over *weighted* observables, so the note has
  // to count the same ones -- a weight-0 item is switched off, not unscored.
  it('counts weighted observables, not every data_item', async () => {
    const wrapper = await withCost({
      cost: 4,
      n_weighted: 2,
      incomplete: false,
      items: [{ label: 'a', cost: 4 }, { label: 'b', cost: 4 }, { label: 'off', cost: null }],
    })
    expect(wrapper.find('[data-testid="cost-line"]').text()).toContain('2 of 2')
  })

  it('says when the number is not comparable with the calibration', async () => {
    // A weighted observable we could not score leaves the numerator short of
    // CA's, so the mean is lower -- flattering, and silently so.
    const wrapper = await withCost({
      cost: 2,
      n_weighted: 2,
      incomplete: true,
      items: [{ label: 'a', cost: 4 }, { label: 'b', cost: null }],
    })
    expect(wrapper.find('[data-testid="cost-line"]').text()).toContain('not comparable')
  })

  it('shows nothing at all when there is no cost to show', async () => {
    const wrapper = await withCost(null)
    expect(wrapper.find('[data-testid="cost-line"]').exists()).toBe(false)
  })

  it('shows the cost of a plain run too, not only a protocol one', async () => {
    const wrapper = await withCost(
      { cost: 42, items: [{ label: 'u', cost: 42 }] },
      { protocol: false },
    )
    expect(wrapper.find('[data-testid="cost-value"]').text()).toBe('42')
  })
})

// Issue #188: the cost line says the parameters are worth 36.8; this says which
// parameter the 36.8 is about, and updates as the sliders settle.
describe('App.vue cost sensitivities (#188)', () => {
  beforeEach(() => {
    localStorage.removeItem('cuflynx-cost-sensitivity')
    costSensitivity.mockClear()
  })

  /** A mounted app with a model, obs_data, one slider and a scored run. */
  const ready = async () => {
    const wrapper = shallowMount(App)
    await flushPromises()
    wrapper.vm.model.modelId.value = 'm1'
    wrapper.vm.obs.setObsData({
      // With a protocol: without one nothing runs at all, gradient included.
      protocol_info: { pre_times: [0], sim_times: [[1]] },
      data_items: [{ variable: 'a/x', operands: ['a/x'] }],
    })
    wrapper.vm.sliders.addSlider('a/alpha', { min: 0, max: 2, value: 1 })
    wrapper.vm.sim.setResult({
      time: [0, 1],
      outputs: { 'a/x': [1, 2] },
      cost: { cost: 3, items: [{ label: 'x', cost: 3 }] },
    })
    await nextTick()
    return wrapper
  }

  it('is off until asked for: a gradient is 2M+1 simulations', async () => {
    const wrapper = await ready()
    expect(wrapper.find('[data-testid="cost-sens-toggle"]').exists()).toBe(true)
    expect(wrapper.findComponent({ name: 'CostSensitivityBar' }).exists()).toBe(false)
    await wrapper.vm.runCostSensitivity()
    expect(costSensitivity).not.toHaveBeenCalled()
  })

  it('measures once the parameters settle, and remembers the choice', async () => {
    vi.useFakeTimers()
    try {
      const wrapper = await ready()
      await wrapper.find('[data-testid="cost-sens-toggle"]').trigger('click')
      expect(localStorage.getItem('cuflynx-cost-sensitivity')).toBe('1')
      // Nothing yet: the debounce is what keeps a drag from queueing gradients.
      expect(costSensitivity).not.toHaveBeenCalled()
      vi.advanceTimersByTime(600)
      await flushPromises()
      expect(costSensitivity).toHaveBeenCalledTimes(1)
      const [modelId, params, opts] = costSensitivity.mock.calls[0]
      expect(modelId).toBe('m1')
      expect(params).toEqual({ 'a/alpha': 1 })
      // The slider range travels with it, for a parameter sitting at exactly 0.
      expect(opts.bounds).toEqual({ 'a/alpha': [0, 2] })
      expect(wrapper.findComponent({ name: 'CostSensitivityBar' }).exists()).toBe(true)
    } finally {
      vi.useRealTimers()
    }
  })

  it('drops a pending gradient the moment a slider moves again', async () => {
    // Otherwise 2M+1 simulations queue ahead of the plot the user is watching.
    vi.useFakeTimers()
    try {
      const wrapper = await ready()
      await wrapper.find('[data-testid="cost-sens-toggle"]').trigger('click')
      vi.advanceTimersByTime(400)
      wrapper.vm.scheduleRun()
      vi.advanceTimersByTime(400)
      await flushPromises()
      expect(costSensitivity).not.toHaveBeenCalled()
    } finally {
      vi.useRealTimers()
    }
  })

  it('marks the ranking stale rather than dropping it when parameters change', async () => {
    vi.useFakeTimers()
    try {
      const wrapper = await ready()
      await wrapper.find('[data-testid="cost-sens-toggle"]').trigger('click')
      vi.advanceTimersByTime(600)
      await flushPromises()
      const bar = wrapper.findComponent({ name: 'CostSensitivityBar' })
      expect(bar.props('status')).toBe('ready')
      wrapper.vm.sliders.setValue('a/alpha', 1.5)
      await nextTick()
      expect(bar.props('status')).toBe('stale')
    } finally {
      vi.useRealTimers()
    }
  })

  it('reports a failure instead of leaving the last numbers looking current', async () => {
    vi.useFakeTimers()
    try {
      costSensitivity.mockRejectedValueOnce(new Error('CVODE gave up'))
      const wrapper = await ready()
      await wrapper.find('[data-testid="cost-sens-toggle"]').trigger('click')
      vi.advanceTimersByTime(600)
      await flushPromises()
      const bar = wrapper.findComponent({ name: 'CostSensitivityBar' })
      expect(bar.props('status')).toBe('error')
      expect(bar.props('error')).toContain('CVODE gave up')
    } finally {
      vi.useRealTimers()
    }
  })
})

// External python models (a dropped .py holding the solver class) run as CA's
// `external_python` model_type and nothing else can run them, so the backend is
// a property of the model rather than a setting -- and `external_python` cannot
// be generated from a CellML model, so it is not on offer for one.
describe('App.vue external python models own the backend', () => {
  const BASE = {
    ca_dir: '', ca_exists: true, generated_model_format: 'cellml',
    solver: 'CVODE_myokit', solver_info: {}, differentiable_operations: {},
    model_formats: ['cellml', 'python', 'casadi_python', 'external_python'],
    solvers_by_format: {
      cellml: ['CVODE_myokit'], python: ['solve_ivp'],
      casadi_python: ['casadi_integrator'], external_python: ['external'],
    },
    default_solver_by_format: {
      cellml: 'CVODE_myokit', python: 'solve_ivp',
      casadi_python: 'casadi_integrator', external_python: 'external',
    },
  }

  beforeEach(() => {
    getConfig.mockResolvedValue({ ...BASE })
    // The server echoes back what it was set to, as the real one does; taking a
    // bare {} would make every save read as "reset to cellml".
    setConfig.mockImplementation(async (payload) => ({
      ...BASE,
      generated_model_format: payload?.generatedModelFormat ?? BASE.generated_model_format,
      solver: payload?.solver ?? BASE.solver,
      solver_info: payload?.solverInfo ?? {},
    }))
  })
  afterEach(() => {
    setConfig.mockReset().mockResolvedValue({})
    getConfig.mockResolvedValue({ ...BASE, model_formats: ['cellml'] })
  })

  // Render the Settings dialog's slot: the format selector and its hint live
  // inside it, and a stubbed Dialog drops the whole body.
  const DialogSlotStub = { template: '<div><slot /></div>' }

  const loadModel = async (data) => {
    const wrapper = shallowMount(App, { global: { stubs: { Dialog: DialogSlotStub } } })
    await flushPromises()
    await wrapper.vm.onModelLoaded(data)
    await flushPromises()
    return wrapper
  }

  it('switches the backend to external_python when a .py is loaded', async () => {
    const wrapper = await loadModel({
      model_id: 'py1', name: 'heat_fenics', model_format: 'external_python',
    })
    expect(wrapper.vm.generatedModelFormat).toBe('external_python')
    expect(wrapper.vm.solver).toBe('external')
    // Persisted, so the runners agree with the sliders about what is running.
    expect(setConfig).toHaveBeenCalledWith(
      expect.objectContaining({ generatedModelFormat: 'external_python' }),
    )
  })

  it('locks the format selector while that model is loaded', async () => {
    const wrapper = await loadModel({
      model_id: 'py1', name: 'heat_fenics', model_format: 'external_python',
    })
    expect(wrapper.vm.isExternalPythonModel).toBe(true)
    expect(wrapper.vm.formatChoices).toEqual(['external_python'])
    const select = wrapper.find('[data-testid="model-format-select"]')
    expect(select.attributes('disabled')).toBe('true')
    // A greyed-out control with no explanation is a puzzle, not a lock.
    expect(wrapper.find('[data-testid="external-python-format-hint"]').exists()).toBe(true)
  })

  // It cannot be generated from a CellML model, so offering it would only move
  // the failure to the next simulation -- the OpenCOR/AADC rule.
  it('hides external_python from the menu for a CellML model', async () => {
    const wrapper = await loadModel({ model_id: 'c1', name: 'lotka' })
    expect(wrapper.vm.formatChoices).not.toContain('external_python')
    expect(wrapper.vm.formatChoices).toContain('cellml')
    expect(
      wrapper.find('[data-testid="model-format-select"]').attributes('disabled'),
    ).toBe('false')
    expect(wrapper.find('[data-testid="external-python-format-hint"]').exists()).toBe(false)
  })

  // external_python has nothing to run once the .py is gone.
  it('leaves external_python when a CellML model replaces the .py', async () => {
    const wrapper = await loadModel({
      model_id: 'py1', name: 'heat_fenics', model_format: 'external_python',
    })
    await wrapper.vm.onModelLoaded({ model_id: 'c1', name: 'lotka' })
    await flushPromises()
    expect(wrapper.vm.generatedModelFormat).toBe('cellml')
    expect(wrapper.vm.isExternalPythonModel).toBe(false)
  })
})

// Workstream D: figures the solver drew for the run (an external python model's
// `extra_plots()`), rendered to PNG server-side and shown beside the traces.
describe('App.vue solver plots (external python extra_plots)', () => {
  const FIG = {
    index: 0, title: 'Temperature field', url: '/api/models/m1/solver_plots/tok1/0.png',
  }
  const FIG2 = {
    index: 1, title: 'Mesh', url: '/api/models/m1/solver_plots/tok1/1.png',
  }

  const mountApp = async () => {
    const wrapper = shallowMount(App)
    await flushPromises()
    return wrapper
  }

  it('adds an image cell per figure after a manual run', async () => {
    const wrapper = await mountApp()
    wrapper.vm.sim.setResult({
      time: [0, 1], outputs: { 'heat/T_p1': [1, 2] }, solver_plots: [FIG, FIG2],
    })
    await nextTick()
    const cells = wrapper.vm.plotGroups[0].cells
    // Appended after the model's own plots: the figure is about the whole run.
    expect(cells.slice(-2).map((c) => c.key)).toEqual(['solver:0', 'solver:1'])
    expect(cells.at(-2)).toMatchObject({
      kind: 'image', title: 'Temperature field', url: FIG.url,
    })
  })

  it('renders those cells with ImagePanel, not a chart', async () => {
    const wrapper = await mountApp()
    wrapper.vm.sim.setResult({
      time: [0, 1], outputs: { 'heat/T_p1': [1, 2] }, solver_plots: [FIG],
    })
    await nextTick()
    const panels = wrapper.findAllComponents({ name: 'ImagePanel' })
    expect(panels).toHaveLength(1)
    expect(panels[0].props('url')).toBe(FIG.url)
    expect(panels[0].props('title')).toBe('Temperature field')
    expect(panels[0].props('maximizable')).toBe(true)
  })

  // A new run means a new token, so the url is its own cache-buster -- nothing
  // here has to version it, but the stale one must not survive.
  it('replaces them when the next run draws different ones', async () => {
    const wrapper = await mountApp()
    wrapper.vm.sim.setResult({ time: [0], outputs: {}, solver_plots: [FIG] })
    await nextTick()
    const next = { ...FIG, url: '/api/models/m1/solver_plots/tok2/0.png' }
    wrapper.vm.sim.setResult({ time: [0], outputs: {}, solver_plots: [next] })
    await nextTick()
    const images = wrapper.vm.plotGroups[0].cells.filter((c) => c.kind === 'image')
    expect(images).toHaveLength(1)
    expect(images[0].url).toBe(next.url)
  })

  it('drops them when a new model is loaded', async () => {
    const wrapper = await mountApp()
    wrapper.vm.sim.setResult({ time: [0], outputs: {}, solver_plots: [FIG] })
    await nextTick()
    await wrapper.vm.onModelLoaded({ model_id: 'm2', name: 'other' })
    await flushPromises()
    expect(wrapper.vm.sim.solverPlots.value).toEqual([])
  })

  // One run, one set of figures: on a protocol run they go on the last group so
  // they appear once, at the end, rather than under every experiment.
  it('puts a protocol run’s figures on the last experiment group', async () => {
    const wrapper = await mountApp()
    wrapper.vm.obs.setObsData({
      protocol_info: { sim_times: [[1], [1]] },
      data_items: [{ variable: 'heat/T_p1', operation: 'series' }],
    })
    wrapper.vm.sim.setExperiments(
      [
        { time: [0, 1], outputs: { 'heat/T_p1': [1, 2] } },
        { time: [0, 1], outputs: { 'heat/T_p1': [3, 4] } },
      ],
      [], 5, null, [FIG],
    )
    await nextTick()
    const groups = wrapper.vm.plotGroups
    expect(groups).toHaveLength(2)
    expect(groups[0].cells.some((c) => c.kind === 'image')).toBe(false)
    expect(groups.at(-1).cells.at(-1)).toMatchObject({ kind: 'image', url: FIG.url })
  })

  it('adds nothing when the run drew no figures', async () => {
    const wrapper = await mountApp()
    wrapper.vm.sim.setResult({ time: [0, 1], outputs: { 'heat/T_p1': [1, 2] } })
    await nextTick()
    expect(wrapper.vm.plotGroups[0].cells.some((c) => c.kind === 'image')).toBe(false)
    expect(wrapper.findAllComponents({ name: 'ImagePanel' })).toHaveLength(0)
  })
})

// CA types a solver_info field `json` when it is a free-form object handed to
// the backend untouched -- external_python's `user_config`, which is the whole
// of an external solver's configuration. Rendered as a number input (the form's
// fallback) it would be unusable, so it gets a text field of its own.
describe('App.vue free-form (JSON) solver_info fields', () => {
  const CONFIG = {
    ca_dir: '', ca_exists: true, generated_model_format: 'external_python',
    solver: 'external', solver_info: {}, differentiable_operations: {},
    model_formats: ['cellml', 'external_python'],
    solvers_by_format: { external_python: ['external'] },
    default_solver_by_format: { external_python: 'external' },
    solver_info_schema: {
      external: [
        { key: 'dt', label: 'Time step (dt)', type: 'number', default: 0.01 },
        { key: 'user_config', label: 'User config (JSON)', type: 'json', default: null },
      ],
    },
  }

  beforeEach(() => {
    getConfig.mockResolvedValue({ ...CONFIG })
    // The server echoes the solver_info it was given, as the real one does.
    setConfig.mockImplementation(async (payload) => ({
      ...CONFIG, solver_info: payload?.solverInfo ?? {},
    }))
  })
  afterEach(() => {
    setConfig.mockReset().mockResolvedValue({})
    getConfig.mockResolvedValue({
      ca_dir: '', ca_exists: true, generated_model_format: 'cellml',
      solver: 'CVODE_myokit', solver_info: {}, differentiable_operations: {},
    })
  })

  const mountApp = async () => {
    const wrapper = shallowMount(App, {
      global: { stubs: { Dialog: { template: '<div><slot /></div>' } } },
    })
    await flushPromises()
    return wrapper
  }

  it('renders it as text, not as a number input', async () => {
    const wrapper = await mountApp()
    const field = wrapper.find('[data-testid="solver-info-user_config"]')
    expect(field.exists()).toBe(true)
    expect(field.element.tagName.toLowerCase()).toContain('input-text')
  })

  it('parses what was typed into solver_info', async () => {
    const wrapper = await mountApp()
    wrapper.vm.onJsonFieldInput('user_config', '{"nx": 32}')
    await nextTick()
    expect(wrapper.vm.solverInfo.user_config).toEqual({ nx: 32 })
    expect(setConfig).toHaveBeenCalledWith(
      expect.objectContaining({ solverInfo: expect.objectContaining({ user_config: { nx: 32 } }) }),
    )
  })

  // Half-typed JSON is the normal state of a text field being filled in; it must
  // not wipe the value currently in force.
  it('keeps the last value that parsed while the text is incomplete', async () => {
    const wrapper = await mountApp()
    wrapper.vm.onJsonFieldInput('user_config', '{"nx": 32}')
    wrapper.vm.onJsonFieldInput('user_config', '{"nx":')
    await nextTick()
    expect(wrapper.vm.solverInfo.user_config).toEqual({ nx: 32 })
    expect(wrapper.vm.jsonFieldErrors.user_config).toContain('Not valid JSON')
    expect(
      wrapper.find('[data-testid="solver-info-user_config-error"]').exists(),
    ).toBe(true)
  })

  it('treats an empty field as no config at all', async () => {
    const wrapper = await mountApp()
    wrapper.vm.onJsonFieldInput('user_config', '{"nx": 32}')
    wrapper.vm.onJsonFieldInput('user_config', '  ')
    await nextTick()
    expect(wrapper.vm.solverInfo.user_config).toBeNull()
    expect(wrapper.vm.jsonFieldErrors.user_config).toBe('')
  })
})

// The protocol's window must travel with every analysis run: a runner handed
// none falls back to sim_time=2.0 while the Output-plots cost runs over the
// protocol's window -- the same best-fit parameters then score two different
// costs (heat1d: calibration said 24.66, the plots said otherwise). Now that the
// window has one source, both sides are that source.
describe('analysis runs carry the protocol window', () => {
  // 7 simulated, 1.5 of warm-up, spread over two experiments -- the payload
  // carries the totals, not any one experiment's slice.
  const PROTOCOL = {
    protocol_info: { pre_times: [1, 0.5], sim_times: [[3, 1], [3]] },
    data_items: [],
  }

  const mountApp = async () => {
    const wrapper = shallowMount(App)
    await flushPromises()
    wrapper.vm.model.modelId.value = 'abc'
    wrapper.vm.obs.setObsData(PROTOCOL)
    await flushPromises()
    return wrapper
  }

  it('calibration payload includes sim_time/pre_time from the protocol', async () => {
    const { startCalibration } = await import('./lib/api')
    startCalibration.mockClear()
    const wrapper = await mountApp()
    wrapper.vm.onRunCalibration({ param_id_method: 'genetic_algorithm' })
    await flushPromises()
    expect(startCalibration).toHaveBeenCalledTimes(1)
    const settings = startCalibration.mock.calls[0][1]
    expect(settings.sim_time).toBe(7)
    expect(settings.pre_time).toBe(1.5)
  })

  it('sensitivity payload includes sim_time/pre_time from the protocol', async () => {
    const { startSensitivity } = await import('./lib/api')
    startSensitivity.mockClear()
    const wrapper = await mountApp()
    wrapper.vm.onRunSensitivity({ analysis_type: 'global' })
    await flushPromises()
    expect(startSensitivity).toHaveBeenCalledTimes(1)
    const settings = startSensitivity.mock.calls[0][1]
    expect(settings.sim_time).toBe(7)
    expect(settings.pre_time).toBe(1.5)
  })

  it('UQ payload includes sim_time/pre_time from the protocol', async () => {
    const { startUQ } = await import('./lib/api')
    startUQ.mockClear()
    const wrapper = await mountApp()
    wrapper.vm.onRunUQ({ method: 'mcmc' })
    await flushPromises()
    expect(startUQ).toHaveBeenCalledTimes(1)
    const settings = startUQ.mock.calls[0][1]
    expect(settings.sim_time).toBe(7)
    expect(settings.pre_time).toBe(1.5)
  })

  // CA's emulator staleness fingerprint covers protocol_info's times, so an
  // emulator trained on a different window than the calibration uses is rejected
  // as stale. One source is what keeps them equal.
  it('emulator training uses the same window', async () => {
    startEmulatorTraining.mockClear()
    const wrapper = await mountApp()
    wrapper.vm.onTrainEmulator({ model: 'GaussianProcess' })
    await flushPromises()
    expect(startEmulatorTraining).toHaveBeenCalledTimes(1)
    const settings = startEmulatorTraining.mock.calls[0][1]
    expect(settings.sim_time).toBe(7)
    expect(settings.pre_time).toBe(1.5)
  })

  // The exported pipeline is the study written down; it has to state the same
  // window the GUI ran.
  it('the exported pipeline states the same window', async () => {
    const wrapper = await mountApp()
    expect(wrapper.vm.exportPayload()).toMatchObject({ sim_time: 7, pre_time: 1.5 })
  })
})

// Pointing Settings -> Python at an environment without autoemulate (a FEniCSx
// conda env, say) leaves every other tab working and emulation impossible. The
// tab has to say so from the outside -- the panel's form quietly degrading is
// what the user reported as a bug (#261).
describe('Emulator tab when emulation is unavailable', () => {
  const UNAVAILABLE = {
    supported: true,
    label: 'Emulator (surrogate model)',
    enable_flag: 'do_emulation',
    use_flag: 'use_emulator',
    options: [],
    models: [],
    available: false,
    interpreter: '/envs/fenicsx/bin/python',
    unavailable_reason:
      'The analysis interpreter /envs/fenicsx/bin/python cannot import autoemulate, ' +
      'which is what provides the emulator models, so there is nothing to train. ' +
      'Install it there with: /envs/fenicsx/bin/python -m pip install ' +
      '"autoemulate>=2.1,<3".',
  }

  it('warns on the tab, in words as well as in colour', async () => {
    getEmulatorDefaults.mockResolvedValueOnce(UNAVAILABLE)
    const wrapper = shallowMount(App)
    await flushPromises()
    const tab = wrapper.find('[data-testid="tab-emulator"]')
    expect(tab.classes()).toContain('warn')
    // Colour is never the only signal: a mark, a tooltip and a name for it.
    expect(wrapper.find('[data-testid="tab-emulator-warn"]').exists()).toBe(true)
    expect(tab.attributes('title')).toContain('autoemulate')
    expect(tab.attributes('aria-label')).toContain('unavailable')
  })

  it('leaves the tab alone when emulation is available', async () => {
    getEmulatorDefaults.mockResolvedValueOnce({ ...UNAVAILABLE, available: true })
    const wrapper = shallowMount(App)
    await flushPromises()
    const tab = wrapper.find('[data-testid="tab-emulator"]')
    expect(tab.classes()).not.toContain('warn')
    expect(wrapper.find('[data-testid="tab-emulator-warn"]').exists()).toBe(false)
    expect(tab.attributes('aria-label')).toBeUndefined()
  })

  // A circulatory_autogen with no emulators at all is the same story from the
  // user's side, and the backend reports it as unavailable too.
  it('warns for a circulatory_autogen with no emulator support', async () => {
    getEmulatorDefaults.mockResolvedValueOnce({ supported: false, options: [] })
    const wrapper = shallowMount(App)
    await flushPromises()
    expect(wrapper.find('[data-testid="tab-emulator"]').classes()).toContain('warn')
  })

  // The tick box is gone while unavailable, so leaving the flag on would be a
  // setting with no control -- and sensitivity / calibration / UQ would keep
  // asking circulatory_autogen for an emulator it cannot load.
  it('switches the analyses back off the emulator', async () => {
    getEmulatorDefaults.mockResolvedValueOnce(UNAVAILABLE)
    const wrapper = shallowMount(App)
    // Ticked before the interpreter was changed under it.
    wrapper.vm.emu.useEmulator.value = true
    await flushPromises()
    expect(wrapper.vm.emu.useEmulator.value).toBe(false)
  })

  it('keeps the flag when emulation is available', async () => {
    getEmulatorDefaults.mockResolvedValueOnce({ ...UNAVAILABLE, available: true })
    const wrapper = shallowMount(App)
    wrapper.vm.emu.useEmulator.value = true
    await flushPromises()
    expect(wrapper.vm.emu.useEmulator.value).toBe(true)
  })

  // Availability is answered by probing the interpreter chosen in Settings, so
  // it is not a constant of the session: changing the interpreter re-asks.
  it('re-reads availability when the interpreter changes', async () => {
    const wrapper = shallowMount(App)
    await flushPromises()
    getEmulatorDefaults.mockClear()
    getEmulatorDefaults.mockResolvedValueOnce(UNAVAILABLE)
    wrapper.vm.pythonPath = '/envs/fenicsx/bin/python'
    await flushPromises()
    expect(getEmulatorDefaults).toHaveBeenCalled()
    expect(wrapper.find('[data-testid="tab-emulator"]').classes()).toContain('warn')
  })
})

// Issue #333: a calibration with "use the emulator" on minimises the emulator's
// cost, while the line above the plots has always shown the solver's. The two
// are different functions of the same parameters, and nothing said so -- so the
// gap read as a bug. Both numbers, side by side, turn it into a quantity.
describe('App.vue cost line with the emulator in use (#333)', () => {
  const SOLVER = {
    cost: 1234.5,
    n_weighted: 2,
    incomplete: false,
    items: [{ label: 'u', cost: 600 }, { label: 'v', cost: 400 }],
    computed_by: 'circulatory_autogen',
  }
  const EMULATED = {
    cost: 987.6,
    n_weighted: 2,
    incomplete: false,
    items: [{ label: 'u', cost: 500 }, { label: 'v', cost: 400 }],
    computed_by: 'circulatory_autogen',
  }

  // Driven through the real path -- the prediction request, then a run -- rather
  // than by assigning the refs: the two costs are only comparable because they
  // are asked for at the same parameters, and that is the part worth testing.
  const shown = async ({ useEmulator = true, emulatorCost = EMULATED } = {}) => {
    // A trained emulator has to be found for the study, or the tick box unticks
    // itself -- there would be nothing to evaluate.
    getEmulatorInfo.mockResolvedValue({
      emulator_dir: '/out/emulators/m_obs',
      metadata: { feature_labels: ['u'] },
    })
    predictEmulator.mockResolvedValue({ labels: ['u'], values: [1], cost: emulatorCost })
    runProtocol.mockResolvedValue({ experiments: [{ time: [0, 1], outputs: {} }], cost: SOLVER })
    const wrapper = shallowMount(App)
    await flushPromises()
    wrapper.vm.model.modelId.value = 'abc'
    // A run needs a window, and the protocol is where it comes from.
    wrapper.vm.obs.setObsData({
      protocol_info: { pre_times: [0], sim_times: [[1]] },
      data_items: [],
    })
    await flushPromises()
    wrapper.vm.emu.useEmulator.value = useEmulator
    await flushPromises()
    await wrapper.vm.runSimulation()
    await nextTick()
    return wrapper
  }

  it('shows the model cost and the emulator cost side by side', async () => {
    const wrapper = await shown()
    expect(wrapper.find('[data-testid="cost-value"]').text()).toBe('1235')
    expect(wrapper.find('[data-testid="em-cost-value"]').text()).toBe('987.6')
    expect(wrapper.find('[data-testid="em-cost-label"]').text()).toBe('em cost')
  })

  it('says which cost is over how many observables', async () => {
    const wrapper = await shown()
    const note = wrapper.find('[data-testid="cost-note"]').text()
    expect(note).toContain('cost: 2 of 2 observables')
    expect(note).toContain('em cost: 2 of 2')
  })

  // The answer to "why don't these match?" has to be *here*, where the question
  // is asked, not in a tab the user has no reason to open.
  it('explains that the calibration minimises the em cost', async () => {
    const wrapper = await shown()
    expect(wrapper.find('[data-testid="em-cost-value"]').attributes('title')).toContain(
      'minimises the em cost',
    )
  })

  it('looks exactly as it did when the emulator is not in use', async () => {
    const wrapper = await shown({ useEmulator: false })
    expect(wrapper.find('[data-testid="cost-value"]').text()).toBe('1235')
    expect(wrapper.find('[data-testid="em-cost-value"]').exists()).toBe(false)
    expect(wrapper.find('[data-testid="cost-note"]').text()).toContain('2 of 2 observables')
  })

  // A bundle that cannot be scored (no obs_data CA can parse, labels that no
  // longer match) sends no cost. That is a silence, not an error banner.
  it('shows only the model cost when the emulator cannot be scored', async () => {
    const wrapper = await shown({ emulatorCost: null })
    expect(wrapper.find('[data-testid="cost-value"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="em-cost-value"]').exists()).toBe(false)
  })

  // Two costs of two different parameter sets are not a comparison. The
  // prediction and the run are separate requests, so mid-drag one can be a
  // slider position ahead of the other -- and then the em cost waits.
  it('withholds the em cost until it is of the same parameters as the run', async () => {
    const wrapper = await shown()
    wrapper.vm.emulatorCostAt = '{"a/alpha":2}'
    await nextTick()
    expect(wrapper.find('[data-testid="em-cost-value"]').exists()).toBe(false)
  })
})

// The same capability at the calibration's best fit: which model the Analysis
// tab's per-observable errors and cost describe (#333).
describe('App.vue calibration errors against model or emulator (#333)', () => {
  const BEST = { 'a/alpha': 1.5 }
  const MODEL_COST = { cost: 12, n_weighted: 1, items: [{ label: 'u', percent_error: 5, std_error: 1, cost: 12 }] }
  const EMU_COST = { cost: 9, n_weighted: 1, items: [{ label: 'u', percent_error: 3, std_error: 0.5, cost: 9 }] }

  const withBestFit = async ({ emulator = true } = {}) => {
    getEmulatorInfo.mockResolvedValue(
      emulator
        ? { emulator_dir: '/out/emulators/m_obs', metadata: { feature_labels: ['u'] } }
        : {},
    )
    costAtParams.mockResolvedValue({ cost: MODEL_COST, emulator_cost: EMU_COST })
    const wrapper = shallowMount(App)
    await flushPromises()
    wrapper.vm.model.modelId.value = 'abc'
    wrapper.vm.obs.setObsData({ data_items: [], has_protocol: false })
    await flushPromises()
    wrapper.vm.calib.bestParams.value = { ...BEST }
    await flushPromises()
    return wrapper
  }

  it('scores the best fit both ways, in one request', async () => {
    costAtParams.mockClear()
    const wrapper = await withBestFit()
    expect(costAtParams).toHaveBeenCalledTimes(1)
    // Physical values for the solver, theta for the emulator -- one point.
    const [, params, options] = costAtParams.mock.calls[0]
    expect(params).toEqual(BEST)
    expect(options.analysisParams).toEqual(BEST)
    expect(wrapper.vm.bestFitScores).toEqual({ model: MODEL_COST, emulator: EMU_COST })
  })

  // The tick box switches between two payloads that are already here. A solver
  // run per click would make it unusable.
  it('does not re-measure when nothing about the best fit changed', async () => {
    const wrapper = await withBestFit()
    costAtParams.mockClear()
    wrapper.vm.calib.bestParams.value = { ...BEST }
    await flushPromises()
    expect(costAtParams).not.toHaveBeenCalled()
  })

  it('asks again for a new best fit', async () => {
    const wrapper = await withBestFit()
    costAtParams.mockClear()
    wrapper.vm.calib.bestParams.value = { 'a/alpha': 2.5 }
    await flushPromises()
    expect(costAtParams).toHaveBeenCalledTimes(1)
  })

  it('offers nothing to compare when the study has no emulator', async () => {
    costAtParams.mockClear()
    const wrapper = await withBestFit({ emulator: false })
    expect(costAtParams).not.toHaveBeenCalled()
    expect(wrapper.vm.bestFitScores).toBe(null)
  })
})

// The emulator's predicted features still draw their dotted overlay when the cost
// cannot be computed, so a silent null left lines on the plot with no number beside
// them and nothing to act on -- which is how this was reported.
describe('em cost says why it is missing', () => {
  const shownWithReason = async (predictResponse) => {
    getEmulatorInfo.mockResolvedValue({
      emulator_dir: '/out/emulators/m_obs',
      metadata: { feature_labels: ['u'] },
    })
    predictEmulator.mockResolvedValue(predictResponse)
    runProtocol.mockResolvedValue({
      experiments: [{ time: [0, 1], outputs: {} }],
      cost: { cost: 1.0, items: [] },
    })
    const wrapper = shallowMount(App)
    await flushPromises()
    wrapper.vm.model.modelId.value = 'abc'
    wrapper.vm.obs.setObsData({
      protocol_info: { pre_times: [0], sim_times: [[1]] },
      data_items: [],
    })
    await flushPromises()
    wrapper.vm.emu.useEmulator.value = true
    await flushPromises()
    await wrapper.vm.runSimulation()
    await nextTick()
    return wrapper
  }

  const REASON =
    'the emulator has no prediction for "min(T_{p3}) (min heat/T_p3)". Its features were ' +
    'fixed when it was trained, so an observable added or renamed since is not among them ' +
    '-- retrain the emulator for this obs_data'

  it('renders the backend reason where the number would be', async () => {
    const wrapper = await shownWithReason({
      labels: ['u'], values: [1], cost: null, cost_unavailable: REASON,
    })
    const note = wrapper.find('[data-testid="em-cost-unavailable"]')
    expect(note.exists()).toBe(true)
    expect(note.text()).toContain('min(T_{p3})')
    // ...and no em cost value, since there is none.
    expect(wrapper.find('[data-testid="em-cost-value"]').exists()).toBe(false)
  })

  it('says nothing once a cost is available', async () => {
    const wrapper = await shownWithReason({
      labels: ['u'], values: [1], cost: { cost: 2.5, items: [] },
    })
    expect(wrapper.find('[data-testid="em-cost-unavailable"]').exists()).toBe(false)
    expect(wrapper.find('[data-testid="em-cost-value"]').text()).toBe('2.5')
  })
})

// The guided tour anchors on data-testid, so the anchors are asserted the same
// way tested elements are -- a rename that breaks the tour breaks a test first.
describe('tour anchors', () => {
  it('marks the Output plots pane', () => {
    const wrapper = shallowMount(App)
    expect(wrapper.find('[data-testid="plot-groups"]').exists()).toBe(true)
  })
})

// The tour itself: App owns the step index and the "have they seen it" flag,
// and the overlay is a stub under shallowMount -- so these assert on the stub's
// props, not on rendered bubble DOM (TourOverlay.test.js covers that).
describe('guided tour', () => {
  beforeEach(() => {
    localStorage.clear()
  })
  afterEach(() => {
    localStorage.clear()
  })

  const overlay = (wrapper) => wrapper.findComponent(TourOverlay)

  it('offers a Tutorial button in the top bar', () => {
    const wrapper = shallowMount(App)
    expect(wrapper.find('[data-testid="tour-start"]').exists()).toBe(true)
    // Nothing runs until it is asked for.
    expect(overlay(wrapper).exists()).toBe(false)
  })

  it('pulses only until the tour has been met once', async () => {
    const fresh = shallowMount(App)
    expect(fresh.find('[data-testid="tour-start"]').classes()).toContain('tour-pulse')

    localStorage.setItem('cuflynx-tour-seen', '1')
    const seen = shallowMount(App)
    expect(seen.find('[data-testid="tour-start"]').classes()).not.toContain('tour-pulse')
  })

  it('opens the overlay at step 0 with the whole step list', async () => {
    const wrapper = shallowMount(App)
    await wrapper.find('[data-testid="tour-start"]').trigger('click')
    await nextTick()
    const tour = overlay(wrapper)
    expect(tour.exists()).toBe(true)
    expect(tour.props('step')).toBe(0)
    expect(tour.props('steps')).toBe(TOUR_STEPS)
    expect(tour.props('steps').length).toBe(37)
    // The ctx is getters over App's own state, read on the overlay's tick.
    expect(typeof tour.props('ctx').hasModel).toBe('function')
    expect(tour.props('ctx').hasModel()).toBe(false)
    // Starting it is itself an answer to "have they met it".
    expect(localStorage.getItem('cuflynx-tour-seen')).toBe('1')
  })

  it('closes on the overlay saying so, and remembers', async () => {
    const wrapper = shallowMount(App)
    await wrapper.find('[data-testid="tour-start"]').trigger('click')
    await nextTick()
    overlay(wrapper).vm.$emit('close', 'skip')
    await nextTick()
    expect(overlay(wrapper).exists()).toBe(false)
    expect(localStorage.getItem('cuflynx-tour-seen')).toBe('1')
    expect(wrapper.find('[data-testid="tour-start"]').classes()).not.toContain('tour-pulse')
  })

  // The step index is deliberately not persisted: resuming at step 19 into a
  // reloaded, empty app is worse than starting again.
  it('starts from the beginning every time', async () => {
    const wrapper = shallowMount(App)
    await wrapper.find('[data-testid="tour-start"]').trigger('click')
    await nextTick()
    overlay(wrapper).vm.$emit('update:step', 19)
    await nextTick()
    expect(overlay(wrapper).props('step')).toBe(19)

    overlay(wrapper).vm.$emit('close', 'finish')
    await nextTick()
    await wrapper.find('[data-testid="tour-start"]').trigger('click')
    await nextTick()
    expect(overlay(wrapper).props('step')).toBe(0)
  })
})
