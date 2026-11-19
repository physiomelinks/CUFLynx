import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import IAPanel from './IAPanel.vue'

// A Select that can be driven: picks emit the option's value (or the option).
const SelectStub = {
  props: ['modelValue', 'options', 'optionLabel', 'optionValue'],
  emits: ['update:modelValue'],
  template: `<select v-bind="$attrs" :value="modelValue"
    @change="$emit('update:modelValue', $event.target.value)">
    <option v-for="(o, i) in options" :key="i" :value="o && o.value != null ? o.value : o">
      {{ o && o.label != null ? o.label : o }}</option></select>`,
}
const ButtonStub = {
  props: ['disabled', 'label'],
  template: '<button :disabled="disabled" v-bind="$attrs">{{ label }}</button>',
}
const RadioStub = {
  props: ['modelValue', 'value'],
  emits: ['update:modelValue'],
  template: `<input type="radio" v-bind="$attrs" :checked="modelValue === value"
    @change="$emit('update:modelValue', value)" />`,
}
const stubs = {
  Select: SelectStub,
  InputNumber: true,
  InputText: true,
  Checkbox: true,
  RadioButton: RadioStub,
  Button: ButtonStub,
}

// The shape GET /api/identifiability/defaults serves: CA's descriptors, each
// option marked with the methods that read it. The names here are test data,
// not anything the panel knows.
const DEFAULTS = {
  param_reduction: {
    options: [
      { name: 'method', type: 'enum', default: 'pca', choices: ['pca', 'sobol'] },
      { name: 'n_params', type: 'int', default: null, methods: ['pca', 'sobol'] },
      { name: 'criterion', type: 'enum', default: 'max', choices: ['max', 'mean'], methods: ['sobol'] },
      { name: 'min_mode_std', type: 'float', default: 1.0, methods: ['pca'] },
      { name: 'redundancy_check', type: 'bool', default: true, methods: ['pca'] },
      { name: 'sensitivity_dir', type: 'str', default: null },
      { name: 'output_dir', type: 'str', default: null },
    ],
  },
  ia_options: [
    { name: 'method', type: 'enum', default: 'Laplace', choices: ['Laplace', 'profile_likelihood'] },
    { name: 'gradient_source', type: 'enum', default: 'FD', choices: ['FD', 'AD', 'FSA'] },
    { name: 'sub_method', type: 'enum', default: 'parabola_fit',
      choices: ['parabola_fit', 'numdifftools_finite_diff'] },
    { name: 'num_points', type: 'int', default: 50 },
    { name: 'optimiser_options', type: 'dict', default: null },
  ],
}
const RUNS = [{ dir: '/out/sa', num_samples: 8, has_samples: true }]

function mountPanel(props = {}) {
  return mount(IAPanel, {
    props: { canRun: true, defaults: DEFAULTS, sobolRuns: RUNS, ...props },
    global: { stubs },
  })
}

describe('IAPanel parameter reduction form', () => {
  it('is built from the descriptors, showing only the chosen method\'s options', async () => {
    const wrapper = mountPanel()
    const has = (name) => wrapper.find(`[data-testid="pr-opt-${name}"]`).exists()
    expect(has('method')).toBe(true)
    expect(has('n_params')).toBe(true)
    expect(has('min_mode_std')).toBe(true)
    expect(has('redundancy_check')).toBe(true)
    expect(has('criterion')).toBe(false)
    // no path boxes: the Sobol run has its own picker, and CA writes the reduction under
    // that run, inside the working dir
    expect(has('output_dir')).toBe(false)
    expect(has('sensitivity_dir')).toBe(false)
    // the PCA tuning is under Advanced settings (lib/advancedSettings.json)
    const advanced = wrapper.find('[data-testid="reduction-advanced"]')
    expect(advanced.find('[data-testid="pr-opt-min_mode_std"]').exists()).toBe(true)
    expect(advanced.find('[data-testid="pr-opt-n_params"]').exists()).toBe(false)

    await wrapper.find('[data-testid="pr-opt-method"]').setValue('sobol')
    expect(has('criterion')).toBe(true)
    expect(wrapper.find('[data-testid="reduction-advanced"] [data-testid="pr-opt-criterion"]').exists()).toBe(true)
    expect(has('min_mode_std')).toBe(false)
    expect(has('redundancy_check')).toBe(false)
  })

  it('does not offer the manual method: parameters chosen by hand are picked in the Workflow view', () => {
    const wrapper = mountPanel({ defaults: { param_reduction: { options: [
      { name: 'method', type: 'enum', default: 'pca', choices: ['pca', 'sobol', 'manual'] },
      { name: 'parameters', type: 'list', default: null, methods: ['manual'] },
    ] }, ia_options: [] } })
    const method = wrapper.find('[data-testid="pr-opt-method"]')
    expect(method.findAll('option').map((o) => o.text())).toEqual(['pca', 'sobol'])
  })

  it('says so when this CA has no parameter reduction', () => {
    const wrapper = mountPanel({ defaults: { ia_options: [] } })
    expect(wrapper.find('[data-testid="pr-unavailable"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="pr-run"]').exists()).toBe(false)
  })

  it('sends the chosen method and its options over the chosen Sobol run', async () => {
    const wrapper = mountPanel()
    expect(wrapper.find('[data-testid="pr-run"]').attributes('disabled')).toBeDefined()
    await wrapper.find('[data-testid="pr-sobol-run"]').setValue('/out/sa')
    await wrapper.find('[data-testid="pr-opt-method"]').setValue('sobol')
    await wrapper.find('[data-testid="pr-run"]').trigger('click')

    const payload = wrapper.emitted('run-reduction')[0][0]
    expect(payload.source).toBe('existing')
    // Only the sobol method's options, and none left blank.
    expect(payload.param_reduction_options).toEqual({
      method: 'sobol',
      criterion: 'max',
      sensitivity_dir: '/out/sa',
    })
  })

  it('runs Sobol first without naming a Sobol run', async () => {
    const wrapper = mountPanel()
    await wrapper.find('[data-testid="pr-source-sobol"]').trigger('change')
    expect(wrapper.find('[data-testid="pr-sobol-run"]').exists()).toBe(false)
    expect(wrapper.find('[data-testid="pr-opt-sensitivity_dir"]').exists()).toBe(false)
    await wrapper.find('[data-testid="pr-run"]').trigger('click')
    const payload = wrapper.emitted('run-reduction')[0][0]
    expect(payload.source).toBe('sobol_first')
    expect(payload.param_reduction_options).toEqual({
      method: 'pca',
      min_mode_std: 1.0,
      redundancy_check: true,
    })
  })
})

describe('IAPanel parameter reduction results', () => {
  const REDUCTION = {
    report_dir: '/out/sa/param_reduction',
    reduced_params_for_id: '/out/sa/param_reduction/reduced_params_for_id.json',
    heatmap: '/out/sa/param_reduction/pca_mode_sobol_ST.png',
    reduction: { method: 'pca' },
    selected: [{ parameter: 'm/alpha', score: 0.7, mode: 1 }],
    modes: [
      { mode: 1, std: 4.0, variance_share: 0.9, informative: true },
      { mode: 2, std: 0.5, variance_share: 0.1, informative: false },
    ],
    n_informative_modes: 1,
    unexplained_effects: { 'm/beta': 0.3 },
  }

  it('shows the kept parameters, the modes and the unexplained effects', () => {
    const wrapper = mountPanel({ reduction: REDUCTION })
    expect(wrapper.find('[data-testid="pr-kept"]').text()).toContain('m/alpha')
    const modes = wrapper.find('[data-testid="pr-modes"]').findAll('tbody tr')
    expect(modes).toHaveLength(2)
    expect(modes[1].text()).toContain('no')
    expect(wrapper.find('[data-testid="pr-unexplained"]').text()).toContain('m/beta')
  })

  it('loads the heatmap from the API, never a blob: URL', () => {
    const wrapper = mountPanel({ reduction: REDUCTION })
    const src = wrapper.find('[data-testid="pr-heatmap"]').attributes('src')
    expect(src).toBe(
      '/api/param_reduction/heatmap?report_dir=%2Fout%2Fsa%2Fparam_reduction',
    )
  })

  it('"Calibrate these" adopts the reduction\'s params_for_id', async () => {
    const wrapper = mountPanel({ reduction: REDUCTION })
    await wrapper.find('[data-testid="pr-adopt"]').trigger('click')
    expect(wrapper.emitted('adopt')[0]).toEqual(['/out/sa/param_reduction'])
  })
})

describe('IAPanel: a step clicked in the Workflow view', () => {
  it("fills the analysis from the step's ia_options and runs with them", async () => {
    const wrapper = mountPanel()
    wrapper.vm.applySettings({ ia_options: { method: 'profile_likelihood', num_points: 7 },
                               run_calibration_first: true })
    await wrapper.vm.$nextTick()
    await wrapper.find('[data-testid="ia-run"]').trigger('click')
    expect(wrapper.emitted('run-ia')[0][0]).toEqual({
      method: 'laplace',
      run_calibration_first: true,
      ia_options: { method: 'profile_likelihood', num_points: 7 },
    })
  })
})

describe('IAPanel identifiability analysis', () => {
  it('sends CA\'s ia_options through the UQ runner\'s laplace method', async () => {
    const wrapper = mountPanel()
    await wrapper.find('[data-testid="ia-opt-method"]').setValue('profile_likelihood')
    expect(wrapper.find('[data-testid="ia-opt-num_points"]').exists()).toBe(true)
    // A dict option has no field; CA's default applies.
    expect(wrapper.find('[data-testid="ia-opt-optimiser_options"]').exists()).toBe(false)
    await wrapper.find('[data-testid="ia-run"]').trigger('click')
    expect(wrapper.emitted('run-ia')[0][0]).toEqual({
      method: 'laplace',
      run_calibration_first: false,
      ia_options: { method: 'profile_likelihood', num_points: 50 },
    })
  })

  it('shows and sends only the options the chosen method and gradient source read', async () => {
    const wrapper = mountPanel()
    const shown = (name) => wrapper.find(`[data-testid="ia-opt-${name}"]`).exists()
    // Laplace with FD: the FD Hessian method, no profile-likelihood settings.
    expect(shown('gradient_source')).toBe(true)
    expect(shown('sub_method')).toBe(true)
    expect(shown('num_points')).toBe(false)

    await wrapper.find('[data-testid="ia-opt-gradient_source"]').setValue('AD')
    expect(shown('sub_method')).toBe(false)
    await wrapper.find('[data-testid="ia-run"]').trigger('click')
    expect(wrapper.emitted('run-ia')[0][0].ia_options).toEqual({
      method: 'Laplace',
      gradient_source: 'AD',
    })

    await wrapper.find('[data-testid="ia-opt-gradient_source"]').setValue('FD')
    expect(shown('sub_method')).toBe(true)
    await wrapper.find('[data-testid="ia-opt-method"]').setValue('profile_likelihood')
    expect(shown('gradient_source')).toBe(false)
    expect(shown('sub_method')).toBe(false)
  })

  it('follows a when or methods key on CA\'s descriptor over its own table', async () => {
    const wrapper = mountPanel({
      defaults: {
        ...DEFAULTS,
        ia_options: [
          DEFAULTS.ia_options[0],
          { name: 'num_points', type: 'int', default: 50, methods: ['Laplace'] },
          { name: 'sub_method', type: 'str', default: 'x', when: { method: ['profile_likelihood'] } },
        ],
      },
    })
    expect(wrapper.find('[data-testid="ia-opt-num_points"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="ia-opt-sub_method"]').exists()).toBe(false)
  })

  it('shows profile-likelihood intervals, with an open side as unbounded', () => {
    const wrapper = mountPanel({
      iaProfiles: [{ param_name: 'm/a', best_val: 1, ci_95: { lower: 0.5, upper: null } }],
    })
    const text = wrapper.find('[data-testid="ia-profiles"]').text()
    expect(text).toContain('m/a')
    expect(text).toContain('unbounded')
  })
})
