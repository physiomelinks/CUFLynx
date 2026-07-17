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

  it('emits run with the method + its schema settings seeded from defaults', async () => {
    const wrapper = mount(CalibrationPanel, {
      props: {
        canRun: true,
        defaults: {
          methods: [
            {
              value: 'genetic_algorithm',
              label: 'GA',
              gradient_based: false,
              options: [{ name: 'num_calls_to_function', type: 'int', default: 100 }],
            },
          ],
        },
      },
      global: { stubs },
    })
    await wrapper.find('[data-testid="run-calibration"]').trigger('click')
    const ev = wrapper.emitted('run')
    expect(ev).toBeTruthy()
    expect(ev[0][0].param_id_method).toBe('genetic_algorithm')
    // Per-method setting, seeded from the schema default (not hardcoded).
    expect(ev[0][0].num_calls_to_function).toBe(100)
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

  // Methods come from CA's PARAM_ID_METHODS schema (introspected, not hardcoded).
  const SCHEMA_METHODS = [
    { value: 'genetic_algorithm', label: 'Genetic algorithm', gradient_based: false },
    { value: 'sp_minimize', label: 'Gradient descent (L-BFGS-B)', gradient_based: true },
    { value: 'multi_start_sp_minimize', label: 'Multi-start gradient descent', gradient_based: true },
  ]

  it('shows every method from the introspected schema (incl. new ones)', () => {
    const w = mount(CalibrationPanel, {
      props: { defaults: { methods: SCHEMA_METHODS }, adAvailable: false },
      global: { stubs: selectStubs },
    })
    const text = w.find('[data-testid="calib-method"]').text()
    // Gradient methods are offered even without AD — they run with finite differences.
    expect(text).toContain('Genetic algorithm')
    expect(text).toContain('Gradient descent (L-BFGS-B)')
    expect(text).toContain('Multi-start gradient descent') // a method the panel never hardcoded
  })

  it('shows the FD/AD gradient dropdown only for gradient-based methods', () => {
    const ga = mount(CalibrationPanel, {
      props: {
        defaults: { methods: SCHEMA_METHODS, param_id_method: 'genetic_algorithm' },
        adAvailable: true,
      },
      global: { stubs: selectStubs },
    })
    expect(ga.find('[data-testid="calib-gradient-method"]').exists()).toBe(false)

    const sp = mount(CalibrationPanel, {
      props: {
        defaults: { methods: SCHEMA_METHODS, param_id_method: 'multi_start_sp_minimize' },
        adAvailable: true,
      },
      global: { stubs: selectStubs },
    })
    expect(sp.find('[data-testid="calib-gradient-method"]').exists()).toBe(true)
  })

  it('emits the gradient_method with the run settings', async () => {
    const wrapper = mount(CalibrationPanel, {
      props: {
        canRun: true,
        adAvailable: true,
        defaults: { methods: SCHEMA_METHODS, param_id_method: 'sp_minimize' },
      },
      global: { stubs: selectStubs },
    })
    await wrapper.find('[data-testid="run-calibration"]').trigger('click')
    const ev = wrapper.emitted('run')
    expect(ev[0][0].param_id_method).toBe('sp_minimize')
    expect(ev[0][0].gradient_method).toBe('FD')
  })

  // Per-method settings come from CA's schema — a gradient method must not show
  // max_patience, and a method's own options (num_starts) must show.
  const METHODS_WITH_OPTIONS = [
    {
      value: 'genetic_algorithm', label: 'GA', gradient_based: false,
      options: [
        { name: 'num_calls_to_function', type: 'int', default: 100 },
        { name: 'max_patience', type: 'int', default: 10 },
      ],
    },
    {
      value: 'multi_start_sp_minimize', label: 'Multi-start', gradient_based: true,
      options: [
        { name: 'num_starts', type: 'int', default: 10 },
        { name: 'cost_convergence', type: 'float', default: 1e-3 },
      ],
    },
  ]

  it('shows only the selected method\'s settings (no max_patience for gradient descent)', async () => {
    const ga = mount(CalibrationPanel, {
      props: { defaults: { methods: METHODS_WITH_OPTIONS, param_id_method: 'genetic_algorithm' } },
      global: { stubs: selectStubs },
    })
    expect(ga.find('[data-testid="calib-opt-max_patience"]').exists()).toBe(true)
    expect(ga.find('[data-testid="calib-opt-num_starts"]').exists()).toBe(false)

    const ms = mount(CalibrationPanel, {
      props: { defaults: { methods: METHODS_WITH_OPTIONS, param_id_method: 'multi_start_sp_minimize' } },
      global: { stubs: selectStubs },
    })
    // The reported bug: max_patience must NOT appear for multi-start gradient descent.
    expect(ms.find('[data-testid="calib-opt-max_patience"]').exists()).toBe(false)
    expect(ms.find('[data-testid="calib-opt-num_starts"]').exists()).toBe(true)
  })

  it('emits only the selected method\'s settings (num_starts, not max_patience)', async () => {
    const wrapper = mount(CalibrationPanel, {
      props: {
        canRun: true,
        defaults: { methods: METHODS_WITH_OPTIONS, param_id_method: 'multi_start_sp_minimize' },
      },
      global: { stubs: selectStubs },
    })
    await wrapper.find('[data-testid="run-calibration"]').trigger('click')
    const payload = wrapper.emitted('run')[0][0]
    expect(payload).toHaveProperty('num_starts', 10)
    expect(payload).not.toHaveProperty('max_patience')
  })

  it('populates the gradient menu from the backend (FSA visible for cellml_only)', () => {
    const wrapper = mount(CalibrationPanel, {
      props: {
        defaults: { methods: METHODS_WITH_OPTIONS, param_id_method: 'multi_start_sp_minimize' },
        gradientSources: [
          { value: 'FD', label: 'Finite difference' },
          { value: 'FSA', label: 'Forward sensitivity (Myokit CVODES)' },
        ],
      },
      global: { stubs: selectStubs },
    })
    const grad = wrapper.find('[data-testid="calib-gradient-method"]')
    expect(grad.exists()).toBe(true)
    expect(grad.text()).toContain('Forward sensitivity')
  })

  it('falls back the gradient source to FD when it is no longer offered', async () => {
    const wrapper = mount(CalibrationPanel, {
      props: {
        canRun: true,
        defaults: { methods: SCHEMA_METHODS, param_id_method: 'sp_minimize', gradient_method: 'AD' },
        gradientSources: [
          { value: 'FD', label: 'Finite difference' },
          { value: 'AD', label: 'AD' },
        ],
      },
      global: { stubs: selectStubs },
    })
    // The model changes and AD is no longer a source (e.g. switched to cellml_only).
    await wrapper.setProps({ gradientSources: [{ value: 'FD', label: 'Finite difference' }] })
    await wrapper.find('[data-testid="run-calibration"]').trigger('click')
    expect(wrapper.emitted('run').at(-1)[0].gradient_method).toBe('FD')
  })
})
