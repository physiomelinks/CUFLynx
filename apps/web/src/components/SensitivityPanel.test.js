import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import SensitivityPanel from './SensitivityPanel.vue'

// Render Select as a real <select> whose <option>s carry the disabled flag, so
// we can assert the AD gating. Button is a real button for click/disabled.
const SelectStub = {
  props: ['modelValue', 'options', 'optionLabel', 'optionValue', 'optionDisabled'],
  template:
    '<select v-bind="$attrs"><option v-for="(o, i) in options" :key="i" :disabled="o && o.disabled">{{ o && o.label != null ? o.label : o }}</option></select>',
}
const ButtonStub = {
  props: ['disabled', 'label'],
  template: '<button :disabled="disabled" v-bind="$attrs">{{ label }}</button>',
}
const stubs = { Select: SelectStub, InputNumber: true, Checkbox: true, Button: ButtonStub }

function gradientOptions(wrapper) {
  return wrapper.find('[data-testid="gradient-method"]').findAll('option')
}

describe('SensitivityPanel AD gating', () => {
  it('disables the AD gradient source when AD is unavailable', () => {
    const wrapper = mount(SensitivityPanel, {
      props: { defaults: { method: 'local' }, adAvailable: false },
      global: { stubs },
    })
    const ad = gradientOptions(wrapper).find((o) => o.text().includes('Automatic'))
    expect(ad).toBeTruthy()
    expect(ad.attributes('disabled')).toBeDefined()
  })

  it('enables the AD gradient source when AD is available', () => {
    const wrapper = mount(SensitivityPanel, {
      props: { defaults: { method: 'local' }, adAvailable: true },
      global: { stubs },
    })
    const ad = gradientOptions(wrapper).find((o) => o.text().includes('Automatic'))
    expect(ad.attributes('disabled')).toBeUndefined()
  })

  it('resets a selected AD gradient source when AD becomes unavailable', async () => {
    const wrapper = mount(SensitivityPanel, {
      props: { defaults: { method: 'local', gradient_method: 'AD' }, adAvailable: true, canRun: true },
      global: { stubs },
    })
    await wrapper.setProps({ adAvailable: false })
    await wrapper.find('[data-testid="run-sensitivity"]').trigger('click')
    expect(wrapper.emitted('run')[0][0].gradient_method).toBe('FD')
  })
})
