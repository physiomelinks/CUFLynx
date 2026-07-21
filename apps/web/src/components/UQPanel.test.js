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

// The MCMC settings come from CA's ANALYSIS_OPTIONS[mcmc] descriptors
// (introspected, not hardcoded), so new CA options surface here automatically.
describe('UQPanel MCMC options from CA schema', () => {
  const MCMC_OPTIONS = [
    { name: 'num_steps', type: 'int', default: 1000 },
    { name: 'num_walkers', type: 'int', default: 64 },
    { name: 'thin', type: 'int', default: 5 }, // a future CA option
  ]

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
