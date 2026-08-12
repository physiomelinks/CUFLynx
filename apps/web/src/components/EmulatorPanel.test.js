import { describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'
import EmulatorPanel from './EmulatorPanel.vue'

const SelectStub = {
  props: ['modelValue', 'options', 'optionLabel', 'optionValue'],
  template:
    '<select v-bind="$attrs"><option v-for="(o, i) in options" :key="i">{{ o && o.label != null ? o.label : o }}</option></select>',
}
const ButtonStub = {
  props: ['disabled', 'label'],
  template: '<button :disabled="disabled" v-bind="$attrs">{{ label }}</button>',
}
const CheckboxStub = {
  props: ['modelValue', 'disabled'],
  emits: ['update:modelValue'],
  template:
    '<input type="checkbox" :disabled="disabled" :checked="modelValue" v-bind="$attrs" @change="$emit(\'update:modelValue\', !modelValue)" />',
}
const stubs = {
  Select: SelectStub,
  InputNumber: true,
  InputText: true,
  Checkbox: CheckboxStub,
  Button: ButtonStub,
}

// What the backend serves from CA's ANALYSIS_OPTIONS['emulation'].
const DEFAULTS = {
  supported: true,
  label: 'Emulator (surrogate model)',
  enable_flag: 'do_emulation',
  use_flag: 'use_emulator',
  models: ['GaussianProcessRBF', 'RadialBasisFunctions'],
  options: [
    { name: 'emulator_dir', type: 'str', default: null, required: false, description: 'where' },
    { name: 'models', type: 'str', default: 'default', required: false, description: 'which' },
    { name: 'num_train_samples', type: 'int', default: 128, required: false, description: 'n' },
    {
      name: 'sample_type', type: 'enum', default: 'sobol', required: false,
      choices: ['sobol', 'latin_hypercube', 'random'], description: 'doe',
    },
    { name: 'min_r2', type: 'float', default: 0.9, required: false, description: 'threshold' },
  ],
}

const METADATA = {
  feature_labels: ['x_{SS} (steady_state_avg benchmark/x)'],
  feature_r2: [0.9999],
  feature_rmse: [0.002],
  worst_r2: 0.9999,
  param_entry_labels: ['benchmark/p'],
  param_mins: [0],
  param_maxs: [6],
  model_name: 'GaussianProcessRBF',
  design: { num_train_samples: 64, num_used: 64, sample_type: 'sobol' },
}

function mountPanel(props = {}) {
  return mount(EmulatorPanel, {
    props: { defaults: DEFAULTS, canRun: true, ...props },
    global: { stubs },
  })
}

describe('EmulatorPanel', () => {
  it("builds its form from circulatory_autogen's emulation schema", () => {
    const wrapper = mountPanel()
    expect(wrapper.find('[data-testid="emu-opt-num_train_samples"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="emu-opt-sample_type"]').exists()).toBe(true)
    // emulator_dir is deliberately not offered: CUFLynx derives it on both sides,
    // and a second way to say where the bundle lives is a way to disagree.
    expect(wrapper.find('[data-testid="emu-opt-emulator_dir"]').exists()).toBe(false)
  })

  it('cannot be used until something has been trained', () => {
    const wrapper = mountPanel()
    const box = wrapper.find('[data-testid="use-emulator"]')
    expect(box.attributes('disabled')).toBeDefined()
    expect(wrapper.find('[data-testid="emu-none"]').exists()).toBe(true)
  })

  it('shows the held-out R2 per feature once one is trained', () => {
    const wrapper = mountPanel({
      metadata: METADATA,
      features: [{ label: METADATA.feature_labels[0], r2: 0.9999, rmse: 0.002 }],
    })
    const summary = wrapper.find('[data-testid="emu-summary"]')
    expect(summary.exists()).toBe(true)
    expect(summary.text()).toContain('0.9999')
    // And what the emulator is valid over, since outside it the answer is an
    // extrapolation with no error estimate.
    expect(summary.text()).toContain('benchmark/p')
    expect(wrapper.find('[data-testid="use-emulator"]').attributes('disabled')).toBeUndefined()
  })

  it('warns when the emulator is below the threshold CA will refuse at', () => {
    // The number that decides everything downstream. Saying it here, next to the
    // tick box, is the difference between "the run failed" and "here is why".
    const wrapper = mountPanel({
      metadata: { ...METADATA, worst_r2: 0.42 },
      features: [{ label: 'x', r2: 0.42, rmse: 1 }],
    })
    const warning = wrapper.find('[data-testid="emu-below-threshold"]')
    expect(warning.exists()).toBe(true)
    expect(warning.text()).toContain('0.42')
  })

  it('emits the tick box upward, because it governs the other tabs', async () => {
    const wrapper = mountPanel({ metadata: METADATA, modelValue: false })
    await wrapper.find('[data-testid="use-emulator"]').trigger('change')
    expect(wrapper.emitted('update:modelValue')?.[0]).toEqual([true])
  })

  it('emits the training settings on run', async () => {
    const wrapper = mountPanel()
    await wrapper.find('[data-testid="train-emulator"]').trigger('click')
    const settings = wrapper.emitted('run')?.[0]?.[0]
    expect(settings.num_train_samples).toBe(128)
    expect(settings.sample_type).toBe('sobol')
    expect(settings.num_cores).toBe(1)
  })

  it('says so plainly when circulatory_autogen has no emulator support', () => {
    const wrapper = mountPanel({ defaults: { supported: false, options: [] } })
    expect(wrapper.find('[data-testid="emu-unsupported"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="train-emulator"]').exists()).toBe(false)
  })

  it('blocks a multi-core run when there is no MPI launcher', async () => {
    const wrapper = mountPanel({ mpiexecAvailable: false })
    wrapper.vm.settings.num_cores = 4
    await wrapper.vm.$nextTick()
    expect(wrapper.find('[data-testid="emu-cores-invalid"]').exists()).toBe(true)
    expect(
      wrapper.find('[data-testid="train-emulator"]').attributes('disabled'),
    ).toBeDefined()
  })
})
