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
// Render Select as a real <select> so we can read its options + visibility.
const SelectStub = {
  props: ['modelValue', 'options', 'optionLabel', 'optionValue'],
  template:
    '<select v-bind="$attrs"><option v-for="(o, i) in options" :key="i">{{ o && o.label != null ? o.label : o }}</option></select>',
}
const stubs = {
  Select: true,
  InputNumber: true,
  Checkbox: true,
  Button: ButtonStub,
}
const selectStubs = { ...stubs, Select: SelectStub }

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
    expect(ev[0][0]).toHaveProperty('num_cores')
    // Python interpreter is chosen in the top bar, not this panel.
    expect(ev[0][0]).not.toHaveProperty('python_path')
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

  it('offers sp_minimize only when AD is available', () => {
    const without = mount(CalibrationPanel, {
      props: { adAvailable: false },
      global: { stubs: selectStubs },
    })
    expect(without.find('[data-testid="calib-method"]').text()).not.toContain('SciPy minimize')

    const withAd = mount(CalibrationPanel, {
      props: { adAvailable: true },
      global: { stubs: selectStubs },
    })
    expect(withAd.find('[data-testid="calib-method"]').text()).toContain('SciPy minimize')
  })

  it('shows the FD/AD gradient dropdown only for sp_minimize', () => {
    const ga = mount(CalibrationPanel, {
      props: { adAvailable: true },
      global: { stubs: selectStubs },
    })
    expect(ga.find('[data-testid="calib-gradient-method"]').exists()).toBe(false)

    // Seeding param_id_method via defaults selects sp_minimize on mount.
    const sp = mount(CalibrationPanel, {
      props: { adAvailable: true, defaults: { param_id_method: 'sp_minimize' } },
      global: { stubs: selectStubs },
    })
    const grad = sp.find('[data-testid="calib-gradient-method"]')
    expect(grad.exists()).toBe(true)
    expect(grad.text()).toContain('Automatic differentiation')
  })

  it('emits the gradient_method with the run settings', async () => {
    const wrapper = mount(CalibrationPanel, {
      props: { canRun: true, adAvailable: true, defaults: { param_id_method: 'sp_minimize' } },
      global: { stubs: selectStubs },
    })
    await wrapper.find('[data-testid="run-calibration"]').trigger('click')
    const ev = wrapper.emitted('run')
    expect(ev[0][0].param_id_method).toBe('sp_minimize')
    expect(ev[0][0].gradient_method).toBe('FD')
  })
})
