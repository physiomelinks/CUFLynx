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
const stubs = { Select: SelectStub, InputNumber: true, Checkbox: true, Button: ButtonStub }

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
})
