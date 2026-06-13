import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import CalibrationPanel from './CalibrationPanel.vue'

// Stub PrimeVue inputs; render Button as a real button so click/disabled work.
const ButtonStub = {
  props: ['disabled', 'label', 'icon', 'severity', 'size', 'text'],
  emits: ['click'],
  template:
    '<button :disabled="disabled" v-bind="$attrs" @click="$emit(\'click\')">{{ label }}</button>',
}
// Interactive <select> stub so v-model updates flow through.
const SelectStub = {
  props: ['modelValue', 'options', 'optionLabel', 'optionValue', 'size'],
  emits: ['update:modelValue'],
  template:
    '<select v-bind="$attrs" :value="modelValue" @change="$emit(\'update:modelValue\', $event.target.value)">' +
    '<option v-for="o in options" :key="o.value" :value="o.value">{{ o.label }}</option></select>',
}
const stubs = {
  Select: true,
  InputNumber: true,
  Checkbox: true,
  Button: ButtonStub,
  FileBrowserDialog: true,
}

describe('CalibrationPanel', () => {
  it('renders streamed terminal lines', () => {
    const wrapper = mount(CalibrationPanel, {
      props: { lines: ['generation 0', 'best cost: 0.25'], state: 'running' },
      global: { stubs },
    })
    const term = wrapper.find('[data-testid="cal-terminal"]')
    expect(term.text()).toContain('generation 0')
    expect(term.text()).toContain('best cost: 0.25')
  })

  it('emits run with the current settings when runnable', async () => {
    const wrapper = mount(CalibrationPanel, {
      props: { canRun: true, defaults: { methods: ['genetic_algorithm', 'CMA-ES'] } },
      global: { stubs },
    })
    await wrapper.find('[data-testid="run-calibration"]').trigger('click')
    const ev = wrapper.emitted('run')
    expect(ev).toBeTruthy()
    expect(ev[0][0].param_id_method).toBe('genetic_algorithm')
    expect(ev[0][0].num_calls_to_function).toBeGreaterThan(0)
    expect(ev[0][0]).toHaveProperty('python_path')
    expect(ev[0][0]).toHaveProperty('num_cores')
  })

  it('warns when the selected interpreter is missing deps', async () => {
    const wrapper = mount(CalibrationPanel, {
      props: {
        pythons: [
          { path: '/usr/bin/python3', version: '3.10.12', ready: false, missing: ['nevergrad'] },
        ],
      },
      global: { stubs: { ...stubs, Select: SelectStub } },
    })
    await wrapper.find('[data-testid="python-select"]').setValue('/usr/bin/python3')
    expect(wrapper.find('[data-testid="python-warning"]').text()).toContain('nevergrad')
  })

  it('disables run when not runnable', () => {
    const wrapper = mount(CalibrationPanel, {
      props: { canRun: false },
      global: { stubs },
    })
    expect(
      wrapper.find('[data-testid="run-calibration"]').attributes('disabled'),
    ).toBeDefined()
  })
})
