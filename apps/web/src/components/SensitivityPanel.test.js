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
const stubs = {
  Select: SelectStub,
  InputNumber: true,
  InputText: true,
  Checkbox: true,
  Button: ButtonStub,
}

function gradientOptions(wrapper) {
  return wrapper.find('[data-testid="gradient-method"]').findAll('option')
}

// The backend's CA-sourced gradient list for a casadi_python model: FD + CasADi AD
// (requires_all_differentiable). AD is gated client-side by adAvailable.
const GRAD_CASADI = [
  { value: 'FD', label: 'Finite difference', requires_all_differentiable: false },
  { value: 'AD', label: 'Automatic differentiation (CasADi)', requires_all_differentiable: true },
]
// For a cellml_only + CVODE_myokit model: FD + Myokit CVODES FSA (never AD-gated).
const GRAD_CELLML = [
  { value: 'FD', label: 'Finite difference', requires_all_differentiable: false },
  { value: 'FSA', label: 'Forward sensitivity (Myokit CVODES)', requires_all_differentiable: false },
]

describe('SensitivityPanel AD gating', () => {
  it('disables the AD gradient source when AD is unavailable', () => {
    const wrapper = mount(SensitivityPanel, {
      props: { defaults: { method: 'local', gradient_methods: GRAD_CASADI }, adAvailable: false },
      global: { stubs },
    })
    const ad = gradientOptions(wrapper).find((o) => o.text().includes('Automatic'))
    expect(ad).toBeTruthy()
    expect(ad.attributes('disabled')).toBeDefined()
  })

  it('enables the AD gradient source when AD is available', () => {
    const wrapper = mount(SensitivityPanel, {
      props: { defaults: { method: 'local', gradient_methods: GRAD_CASADI }, adAvailable: true },
      global: { stubs },
    })
    const ad = gradientOptions(wrapper).find((o) => o.text().includes('Automatic'))
    expect(ad.attributes('disabled')).toBeUndefined()
  })

  it('offers FSA (Myokit CVODES) enabled for cellml_only, regardless of adAvailable', () => {
    const wrapper = mount(SensitivityPanel, {
      props: { defaults: { method: 'local', gradient_methods: GRAD_CELLML }, adAvailable: false },
      global: { stubs },
    })
    const fsa = gradientOptions(wrapper).find((o) => o.text().includes('Forward sensitivity'))
    expect(fsa).toBeTruthy()
    expect(fsa.attributes('disabled')).toBeUndefined()
  })

  it('resets a selected AD gradient source when AD becomes unavailable', async () => {
    const wrapper = mount(SensitivityPanel, {
      props: {
        defaults: { method: 'local', gradient_method: 'AD', gradient_methods: GRAD_CASADI },
        adAvailable: true,
        canRun: true,
      },
      global: { stubs },
    })
    await wrapper.setProps({ adAvailable: false })
    await wrapper.find('[data-testid="run-sensitivity"]').trigger('click')
    expect(wrapper.emitted('run')[0][0].gradient_method).toBe('FD')
  })

  // Regression for #84: the gradient list must track the reactive
  // `gradientSources` prop (GET /api/config, re-fetched on every backend change),
  // not the one-time sensitivity defaults. Switching cellml_only (FSA) ->
  // casadi_python (AD) must swap FSA out for AD without a defaults refetch.
  it('tracks the reactive gradientSources prop when the backend solver switches', async () => {
    const wrapper = mount(SensitivityPanel, {
      // defaults.gradient_methods is the stale one-time list (cellml_only / FSA);
      // the reactive prop is what drives the menu.
      props: {
        defaults: { method: 'local', gradient_methods: GRAD_CELLML },
        gradientSources: GRAD_CELLML,
        adAvailable: false,
      },
      global: { stubs },
    })
    // cellml_only + CVODE_myokit: FSA present, AD absent.
    expect(gradientOptions(wrapper).find((o) => o.text().includes('Forward sensitivity'))).toBeTruthy()
    expect(gradientOptions(wrapper).find((o) => o.text().includes('Automatic'))).toBeFalsy()

    // Switch backend to casadi_python: /api/config now yields FD + AD, and AD is
    // available. AD must appear (enabled) and FSA must disappear.
    await wrapper.setProps({ gradientSources: GRAD_CASADI, adAvailable: true })
    const ad = gradientOptions(wrapper).find((o) => o.text().includes('Automatic'))
    expect(ad).toBeTruthy()
    expect(ad.attributes('disabled')).toBeUndefined()
    expect(gradientOptions(wrapper).find((o) => o.text().includes('Forward sensitivity'))).toBeFalsy()
  })

  // In SA, DEBUG does NOT reduce the sample count (num_samples is a separate
  // field) — it enables extra debug output. The label must describe that, not
  // the calibration-only "fewer/fast" behaviour.
  it('describes the DEBUG option as adding output, not reducing samples', () => {
    const wrapper = mount(SensitivityPanel, {
      props: { defaults: {} },
      global: { stubs },
    })
    const text = wrapper.text()
    expect(text).toContain('more output info')
    expect(text).not.toContain('fewer samples')
  })
})

// The Sobol settings come from CA's ANALYSIS_OPTIONS[sensitivity_analysis]
// descriptors (introspected, not hardcoded), so new CA options surface here.
describe('SensitivityPanel Sobol options from CA schema', () => {
  const SA_OPTIONS = [
    { name: 'method', type: 'enum', default: 'sobol', choices: ['sobol', 'naive'] },
    { name: 'sample_type', type: 'str', default: 'saltelli' },
    { name: 'num_samples', type: 'int', default: 256 },
    { name: 'confidence_level', type: 'float', default: 0.95 }, // a future CA option
  ]

  it('renders the schema options (excluding method) and seeds their defaults', async () => {
    const wrapper = mount(SensitivityPanel, {
      props: { canRun: true, defaults: { method: 'sobol', options: SA_OPTIONS } },
      global: { stubs },
    })
    // Every descriptor except `method` (the top-level Sobol/local selector covers that).
    expect(wrapper.find('[data-testid="sa-opt-num_samples"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="sa-opt-sample_type"]').exists()).toBe(true)
    // A new CA option appears without any panel change.
    expect(wrapper.find('[data-testid="sa-opt-confidence_level"]').exists()).toBe(true)
    // CA's own `method` option is not a second control.
    expect(wrapper.find('[data-testid="sa-opt-method"]').exists()).toBe(false)

    await wrapper.find('[data-testid="run-sensitivity"]').trigger('click')
    const payload = wrapper.emitted('run')[0][0]
    expect(payload.num_samples).toBe(256) // seeded from the schema default
    expect(payload.sample_type).toBe('saltelli')
    expect(payload.confidence_level).toBe(0.95)
  })

  it('hides the Sobol options for the local (finite-difference) method', () => {
    const wrapper = mount(SensitivityPanel, {
      props: { defaults: { method: 'local', options: SA_OPTIONS } },
      global: { stubs },
    })
    expect(wrapper.find('[data-testid="sa-opt-num_samples"]').exists()).toBe(false)
  })

  it("renders a 'str' option as a text input, not a number input", () => {
    // Regression: the template had branches for bool and enum then fell through to
    // InputNumber, so a str descriptor (CA's sample_type, default 'saltelli') was
    // bound to a numeric control and displayed as NaN. Asserting the control's
    // existence is not enough — the old code rendered a control too, the wrong one.
    const wrapper = mount(SensitivityPanel, {
      props: { canRun: true, defaults: { method: 'sobol', options: SA_OPTIONS } },
      global: { stubs },
    })
    const tag = (id) => wrapper.find(`[data-testid="sa-opt-${id}"]`).element.tagName.toLowerCase()
    expect(tag('sample_type')).toBe('input-text-stub')
    // numeric descriptors must keep the number input
    expect(tag('num_samples')).toBe('input-number-stub')
    expect(tag('confidence_level')).toBe('input-number-stub')
  })
})

describe('SensitivityPanel cores gating (no MPI launcher)', () => {
  const mountPanel = (mpiexecAvailable, num_cores, method = 'sobol') =>
    mount(SensitivityPanel, {
      props: { canRun: true, mpiexecAvailable, defaults: { method, num_cores } },
      global: { stubs },
    })
  const msg = (w) => w.find('[data-testid="sa-cores-invalid"]')
  const runBtn = (w) => w.find('[data-testid="run-sensitivity"]')

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

  it('does not gate the local method (cores unused there)', () => {
    expect(msg(mountPanel(false, 4, 'local')).exists()).toBe(false)
  })
})

describe('SensitivityPanel reacts to a backend switch (#84)', () => {
  it('swaps FSA for AD when gradientSources prop updates (and back)', async () => {
    const wrapper = mount(SensitivityPanel, {
      props: {
        defaults: { method: 'local', gradient_methods: GRAD_CELLML },
        gradientSources: GRAD_CELLML,
        adAvailable: false,
      },
      global: { stubs },
    })
    let opts = gradientOptions(wrapper).map((o) => o.text())
    expect(opts.some((t) => t.includes('Forward sensitivity'))).toBe(true)
    expect(opts.some((t) => t.includes('Automatic'))).toBe(false)

    // backend switched to casadi_python: reactive gradientSources becomes FD+AD
    await wrapper.setProps({ gradientSources: GRAD_CASADI, adAvailable: true })
    opts = gradientOptions(wrapper).map((o) => o.text())
    expect(opts.some((t) => t.includes('Automatic'))).toBe(true)
    expect(opts.some((t) => t.includes('Forward sensitivity'))).toBe(false)
    // AD is enabled (adAvailable true), not faded
    const ad = gradientOptions(wrapper).find((o) => o.text().includes('Automatic'))
    expect(ad.attributes('disabled')).toBeUndefined()

    // switch back to cellml_only
    await wrapper.setProps({ gradientSources: GRAD_CELLML, adAvailable: false })
    opts = gradientOptions(wrapper).map((o) => o.text())
    expect(opts.some((t) => t.includes('Forward sensitivity'))).toBe(true)
    expect(opts.some((t) => t.includes('Automatic'))).toBe(false)
  })
})
