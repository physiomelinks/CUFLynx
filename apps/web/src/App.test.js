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
  saveParams: vi.fn().mockResolvedValue({ path: '/out/run_a.npy', outputs_path: null }),
  loadParams: vi.fn().mockResolvedValue({ values: {} }),
  listSavedRuns: vi.fn().mockResolvedValue({ runs: [] }),
  loadSavedRun: vi.fn().mockResolvedValue({}),
  exportPipeline: vi.fn().mockResolvedValue({}),
  exportPlotting: vi.fn().mockResolvedValue({}),
}))

import {
  getConfig,
  setConfig,
  getCalibrationPythons,
  saveParams,
  listSavedRuns,
  loadSavedRun,
  simulate,
} from './lib/api'
import { setNotificationCtor } from './lib/notify'
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

  // The global random seed (Settings popup) makes analysis runs reproducible; it
  // persists server-side like the interpreter choice, and defaults to none.
  describe('global random seed', () => {
    const BASE_CONFIG = {
      ca_dir: '',
      ca_exists: true,
      generated_model_format: 'cellml_only',
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

// #298: warn (orange) when the selected integrator can't produce the backend's
// analytic gradient (AD/FSA), and drop that source from the menus.
describe('App.vue gradient integrator suitability warning (#298)', () => {
  const base = {
    ca_dir: '', ca_exists: true, differentiable_operations: {},
    model_formats: ['cellml_only', 'casadi_python'],
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

  it('warns for FSA-unsuitable cellml_only integrators', async () => {
    getConfig.mockResolvedValue({
      ...base, generated_model_format: 'cellml_only', solver: 'CVODE_myokit',
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

// Multi-core runs need an MPI launcher from the selected interpreter's own env
// (#75). Nothing used to say which interpreter provides one, so the capability
// was undiscoverable — the picker now marks the ones that enable Cores > 1.
describe('App.vue interpreter MPI marker', () => {
  const CONFIG = {
    ca_dir: '',
    ca_exists: true,
    generated_model_format: 'cellml_only',
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
    await wrapper.vm.onSaveParams({ filename: 'run_a.npy' })
    await flushPromises()

    const [, , filename, , result] = saveParams.mock.calls.at(-1)
    expect(filename).toBe('run_a.npy')
    expect(result.outputs['m/x']).toEqual([1, 2, 3])
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
    // A model id is what makes hasModel true; the best fit has to be simulated,
    // so without one there is nothing to run it against.
    wrapper.vm.model.modelId.value = 'model-1'
    wrapper.vm.model.variables.value = { ...VARS }
    wrapper.vm.sim.setResult({ time: [0, 1], outputs: { 'm/x': [1, 2] } })
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
    simulate.mockClear()
    simulate.mockResolvedValue({ time: [0, 1], outputs: { 'm/x': [5, 6] } })

    await wrapper.vm.onToggleSavedRun('best fit')
    await flushPromises()

    expect(simulate).toHaveBeenCalledTimes(1)
    // The fit only names calibrated params; the rest stay where the sliders are.
    expect(simulate.mock.calls[0][1]).toMatchObject({ 'm/alpha': 9 })
  })

  it('the fitted trace reaches the plot cell as an overlay', async () => {
    const wrapper = await mountWithFit()
    simulate.mockResolvedValue({ time: [0, 1], outputs: { 'm/x': [5, 6] } })
    await wrapper.vm.onToggleSavedRun('best fit')
    await nextTick()

    const cell = wrapper.vm.plotGroups[0].cells[0]
    expect(cell.savedSeries[0]).toMatchObject({ prefix: 'best fit', values: [5, 6] })
  })

  // A second calibration under the same name is a different run.
  it('takes down a shown best fit when a new one arrives', async () => {
    const wrapper = await mountWithFit()
    simulate.mockResolvedValue({ time: [0, 1], outputs: { 'm/x': [5, 6] } })
    await wrapper.vm.onToggleSavedRun('best fit')
    expect(wrapper.vm.savedRuns.isShown('best fit')).toBe(true)

    wrapper.vm.calib.bestParams.value = { 'm/alpha': 12 }
    await flushPromises()
    expect(wrapper.vm.savedRuns.isShown('best fit')).toBe(false)
    expect(wrapper.vm.savedRuns.items.value[0].params).toEqual({ 'm/alpha': 12 })
  })
})
