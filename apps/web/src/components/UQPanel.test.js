import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import UQPanel from './UQPanel.vue'

const SelectStub = {
  props: ['modelValue', 'options', 'optionLabel', 'optionValue'],
  template:
    '<select v-bind="$attrs"><option v-for="(o, i) in options" :key="i">{{ o && o.label != null ? o.label : o }}</option></select>',
}
const ButtonStub = {
  props: ['disabled', 'label'],
  template: '<button :disabled="disabled" v-bind="$attrs">{{ label }}</button>',
}
const stubs = {
  Select: SelectStub,
  InputNumber: true,
  InputText: true,
  Checkbox: true,
  Button: ButtonStub,
}

// The UQ settings come from CA's ANALYSIS_OPTIONS[uq] descriptors
// (introspected, not hardcoded), so new CA options surface here automatically.
describe('UQPanel UQ options from CA schema', () => {
  const MCMC_OPTIONS = [
    { name: 'num_steps', type: 'int', default: 1000 },
    { name: 'num_walkers', type: 'int', default: 64 },
    { name: 'thin', type: 'int', default: 5 }, // a future CA option
  ]

  it('renders the options CA now sends under uq_options', async () => {
    // CA renamed the mode from 'mcmc' to 'uq', so /api/uq/defaults now carries them as
    // `uq_options`. `mcmc_options` is still read (below) so a panel talking to an API that
    // has not been restarted yet keeps rendering its form.
    const wrapper = mount(UQPanel, {
      props: { canRun: true, defaults: { method: 'mcmc', uq_options: MCMC_OPTIONS } },
      global: { stubs },
    })
    expect(wrapper.find('[data-testid="mcmc-opt-num_steps"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="mcmc-opt-thin"]').exists()).toBe(true)
  })

  it('prefers uq_options when both spellings are present', async () => {
    const wrapper = mount(UQPanel, {
      props: {
        canRun: true,
        defaults: {
          method: 'mcmc',
          uq_options: [{ name: 'burn_in', type: 'float', default: 0.5 }],
          mcmc_options: MCMC_OPTIONS,
        },
      },
      global: { stubs },
    })
    expect(wrapper.find('[data-testid="mcmc-opt-burn_in"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="mcmc-opt-thin"]').exists()).toBe(false)
  })

  it('renders the schema options and seeds their defaults', async () => {
    const wrapper = mount(UQPanel, {
      props: { canRun: true, defaults: { method: 'mcmc', mcmc_options: MCMC_OPTIONS } },
      global: { stubs },
    })
    expect(wrapper.find('[data-testid="mcmc-opt-num_steps"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="mcmc-opt-num_walkers"]').exists()).toBe(true)
    // A new CA option appears without any panel change.
    expect(wrapper.find('[data-testid="mcmc-opt-thin"]').exists()).toBe(true)

    await wrapper.find('[data-testid="run-uq"]').trigger('click')
    const payload = wrapper.emitted('run')[0][0]
    expect(payload.num_steps).toBe(1000)
    expect(payload.num_walkers).toBe(64)
    expect(payload.thin).toBe(5)
  })

  it('hides the MCMC options for the Laplace method', () => {
    const wrapper = mount(UQPanel, {
      props: { defaults: { method: 'laplace', mcmc_options: MCMC_OPTIONS } },
      global: { stubs },
    })
    expect(wrapper.find('[data-testid="mcmc-opt-num_steps"]').exists()).toBe(false)
  })

  // CA's uq schema carries `method` too. Rendering it alongside the panel's own
  // Method select put two Method fields on the form -- and the schema copy won
  // the merge, so picking Laplace ran MCMC.
  it('does not render a second Method field for the schema copy', () => {
    const wrapper = mount(UQPanel, {
      props: {
        canRun: true,
        defaults: {
          method: 'mcmc',
          uq_options: [
            { name: 'method', type: 'enum', default: 'mcmc', choices: ['mcmc'] },
            ...MCMC_OPTIONS,
          ],
        },
      },
      global: { stubs },
    })
    expect(wrapper.find('[data-testid="mcmc-opt-method"]').exists()).toBe(false)
    // and the panel's own one is still there, with the options it offers
    expect(wrapper.findAll('.field').filter((f) => f.text().startsWith('Method'))).toHaveLength(1)
  })

  it('sends the method the user chose, not the schema default', async () => {
    // The panel offers laplace; CA's uq schema does not, because laplace runs
    // through IdentifiabilityAnalysis. The user's choice has to survive.
    const wrapper = mount(UQPanel, {
      props: {
        canRun: true,
        defaults: {
          method: 'laplace',
          uq_options: [
            { name: 'method', type: 'enum', default: 'mcmc', choices: ['mcmc'] },
            ...MCMC_OPTIONS,
          ],
        },
      },
      global: { stubs },
    })
    await wrapper.find('[data-testid="run-uq"]').trigger('click')
    expect(wrapper.emitted('run')[0][0].method).toBe('laplace')
  })

  it("renders a 'str' option as a text input, not a number input", async () => {
    // Regression: str descriptors fell through to InputNumber and displayed NaN.
    // CA's identifiability sub_method ('parabola_fit') is the real instance.
    const opts = [...MCMC_OPTIONS, { name: 'moves', type: 'str', default: 'stretch' }]
    const wrapper = mount(UQPanel, {
      props: { canRun: true, defaults: { method: 'mcmc', mcmc_options: opts } },
      global: { stubs },
    })
    const tag = (id) => wrapper.find(`[data-testid="mcmc-opt-${id}"]`).element.tagName.toLowerCase()
    expect(tag('moves')).toBe('input-text-stub')
    expect(tag('num_steps')).toBe('input-number-stub')

    // and the string default survives instead of becoming NaN
    await wrapper.find('[data-testid="run-uq"]').trigger('click')
    expect(wrapper.emitted('run')[0][0].moves).toBe('stretch')
  })
})

describe('UQPanel cores gating (no MPI launcher)', () => {
  const mountPanel = (mpiexecAvailable, num_cores) =>
    mount(UQPanel, {
      props: { canRun: true, mpiexecAvailable, defaults: { method: 'mcmc', num_cores } },
      global: { stubs },
    })
  const msg = (w) => w.find('[data-testid="uq-cores-invalid"]')
  const runBtn = (w) => w.find('[data-testid="run-uq"]')

  it('marks Cores invalid and disables Run for >1 core with no launcher', () => {
    const w = mountPanel(false, 4)
    expect(msg(w).exists()).toBe(true)
    expect(msg(w).text()).toContain('no MPI launcher')
    expect(runBtn(w).attributes('disabled')).toBeDefined()
  })

  // Saying it is unavailable is not enough: the message has to name the fix
  // (pick an MPI-capable Python interpreter; on Windows install MS-MPI), #75.
  it('points at the fix: an MPI-marked Python interpreter, or MS-MPI on Windows', () => {
    const text = msg(mountPanel(false, 4)).text().replace(/\s+/g, ' ')
    expect(text).toContain('Python interpreter marked MPI ✓')
    expect(text).toContain('Microsoft MPI')
  })

  it('does not emit run while Cores is invalid', async () => {
    const w = mountPanel(false, 4)
    await runBtn(w).trigger('click')
    expect(w.emitted('run')).toBeFalsy()
  })

  it('does not gate when a launcher is available', () => {
    const w = mountPanel(true, 4)
    expect(msg(w).exists()).toBe(false)
    expect(runBtn(w).attributes('disabled')).toBeUndefined()
  })

  it('does not gate a single-core run', () => {
    expect(msg(mountPanel(false, 1)).exists()).toBe(false)
  })
})

// CA marks the options only some samplers read (`libraries` on the descriptor). The panel
// offers what the chosen sampler actually reads: num_tune and pymc_method are pyMC's, and
// under emcee they are a tuning count nothing reads and an algorithm that will not run.
describe('UQPanel option visibility follows the sampler library', () => {
  const options = (library) => [
    { name: 'library', type: 'enum', default: library, choices: ['emcee', 'pymc'] },
    { name: 'num_steps', type: 'int', default: 1000 },
    { name: 'chain_save_every', type: 'int', default: 50 }, // both backends honour it
    { name: 'num_tune', type: 'int', default: 1000, libraries: ['pymc'] },
    {
      name: 'pymc_method',
      type: 'enum',
      default: 'mcmc',
      choices: ['mcmc', 'smc'],
      libraries: ['pymc'],
    },
  ]

  const mountWith = (uq_options) =>
    mount(UQPanel, {
      props: { canRun: true, defaults: { method: 'mcmc', uq_options } },
      global: { stubs },
    })

  const shown = (w, name) => w.find(`[data-testid="mcmc-opt-${name}"]`).exists()

  it('hides the pyMC-only settings when emcee is selected', () => {
    const w = mountWith(options('emcee'))
    expect(shown(w, 'num_steps')).toBe(true)
    expect(shown(w, 'chain_save_every')).toBe(true)
    expect(shown(w, 'num_tune')).toBe(false)
    expect(shown(w, 'pymc_method')).toBe(false)
  })

  it('shows them when pymc is selected', () => {
    const w = mountWith(options('pymc'))
    expect(shown(w, 'num_tune')).toBe(true)
    expect(shown(w, 'pymc_method')).toBe(true)
  })

  it('does not send a hidden option in the run payload', async () => {
    // Not merely cosmetic: a hidden setting that still travelled would put a value in
    // UQ_options that the run's own sampler never reads, and the exported pipeline would
    // carry it too -- a setting the user cannot see and did not choose.
    const w = mountWith(options('emcee'))
    await w.find('[data-testid="run-uq"]').trigger('click')
    const payload = w.emitted('run')[0][0]
    expect(payload.num_steps).toBe(1000)
    expect(payload.num_tune).toBeUndefined()
    expect(payload.pymc_method).toBeUndefined()
  })

  it('renders every option when CA sends no libraries annotation', () => {
    // A CA older than the annotation says nothing about which sampler reads what. Showing
    // everything is what this panel did before, and is the right degradation: hiding a
    // setting CA has not classified would remove the only way to set it.
    const w = mountWith([
      { name: 'library', type: 'enum', default: 'emcee', choices: ['emcee', 'pymc'] },
      { name: 'num_tune', type: 'int', default: 1000 },
      { name: 'pymc_method', type: 'enum', default: 'mcmc', choices: ['mcmc', 'smc'] },
    ])
    expect(shown(w, 'num_tune')).toBe(true)
    expect(shown(w, 'pymc_method')).toBe(true)
  })
})

describe('tour anchors', () => {
  it('marks the settings form', () => {
    const wrapper = mount(UQPanel, {
      props: { canRun: true, defaults: { method: 'mcmc' } },
      global: { stubs },
    })
    expect(wrapper.find('[data-testid="uq-settings"]').exists()).toBe(true)
  })
})
