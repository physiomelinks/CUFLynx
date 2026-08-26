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
// A real <input>, so a number field's disabled state is an absent attribute
// rather than the string "false" an auto-stub renders for a false prop.
const InputNumberStub = {
  // `size` is absorbed: PrimeVue's "small" is not a valid <input size>.
  props: ['modelValue', 'disabled', 'size', 'min', 'max', 'invalid'],
  emits: ['update:modelValue'],
  template: '<input type="number" :disabled="disabled" :value="modelValue" v-bind="$attrs" />',
}
const stubs = {
  Select: SelectStub,
  InputNumber: InputNumberStub,
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
    // Sits between num_train_samples and sample_type in CA's schema, and is
    // rendered as a checkbox by the generic bool arm — the tick box is free, and
    // the work is making it honest.
    { name: 'reuse_samples', type: 'bool', default: false, required: false, description: 'refit' },
    {
      name: 'sample_type', type: 'enum', default: 'sobol', required: false,
      choices: ['sobol', 'latin_hypercube', 'random'], description: 'doe',
    },
    { name: 'log_scale_params', type: 'bool', default: false, required: false, description: 'log' },
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

  // The interpreter chosen in Settings cannot import autoemulate. What the panel
  // used to do was degrade quietly -- the models menu became a text box, because
  // the registry could not be read -- which reads as a bug rather than as a
  // missing package (#261). Now it explains, and offers nothing else.
  describe('when the interpreter cannot emulate', () => {
    // Verbatim from apps/api/solver_options.py's emulator_availability(): it is
    // written for display, and it carries *two* commands mid-sentence.
    const REASON =
      'The analysis interpreter /envs/fenicsx/bin/python cannot import autoemulate, ' +
      'which is what provides the emulator models, so there is nothing to train. ' +
      'Install it there with: /envs/fenicsx/bin/python -m pip install ' +
      '"autoemulate>=2.1,<3" (autoemulate requires Python >=3.10,<3.13). Installing ' +
      'circulatory_autogen itself with its optional emulation extra does the same: ' +
      'pip install -e "/src/circulatory_autogen[emulation]". Or choose an interpreter ' +
      'that already has it in Settings.'
    const UNAVAILABLE = {
      ...DEFAULTS,
      models: [],
      available: false,
      interpreter: '/envs/fenicsx/bin/python',
      unavailable_reason: REASON,
    }

    it('renders the explanation and nothing else', () => {
      const wrapper = mountPanel({ defaults: UNAVAILABLE })
      expect(wrapper.find('[data-testid="emu-unavailable"]').exists()).toBe(true)
      // No settings, no Train, no tick box: every one would be a control that
      // cannot do anything.
      expect(wrapper.find('[data-testid="emu-opt-num_train_samples"]').exists()).toBe(false)
      expect(wrapper.find('[data-testid="emu-opt-models"]').exists()).toBe(false)
      expect(wrapper.find('[data-testid="emu-cores"]').exists()).toBe(false)
      expect(wrapper.find('[data-testid="train-emulator"]').exists()).toBe(false)
      expect(wrapper.find('[data-testid="use-emulator"]').exists()).toBe(false)
      expect(wrapper.find('[data-testid="emu-terminal"]').exists()).toBe(false)
    })

    it("shows the backend's reason verbatim, with the commands set apart", () => {
      const wrapper = mountPanel({ defaults: UNAVAILABLE })
      const reason = wrapper.find('[data-testid="emu-unavailable-reason"]')
      // Verbatim: the sentence is written for display, and naming the
      // interpreter is the half that tells the user *where* to install.
      expect(reason.text()).toBe(REASON)
      // ...but the commands are to be copied, so each is a <code> run of its
      // own — ending where the command ends, not where the sentence does.
      const codes = reason.findAll('code').map((c) => c.text())
      expect(codes).toEqual([
        '/envs/fenicsx/bin/python -m pip install "autoemulate>=2.1,<3"',
        'pip install -e "/src/circulatory_autogen[emulation]"',
      ])
      // And the tutorial section that says which environment it belongs in.
      expect(wrapper.find('[data-testid="emu-install-link"]').attributes('href')).toContain(
        'external_python.md#installing-the-models-dependencies',
      )
    })

    // The backend's other worded-for-display case: nothing configured at all.
    it('sets the commands apart in the "no interpreter configured" reason too', () => {
      const reason =
        "CUFLynx's own environment cannot import autoemulate, which is what provides " +
        'the emulator models, and no analysis interpreter is configured. Choose one in ' +
        'Settings that has autoemulate installed, or install it there with: pip install ' +
        '"autoemulate>=2.1,<3" (autoemulate requires Python >=3.10,<3.13). Installing ' +
        'circulatory_autogen itself with its optional emulation extra does the same: ' +
        'pip install -e "/src/circulatory_autogen[emulation]".'
      const wrapper = mountPanel({
        defaults: { ...UNAVAILABLE, interpreter: null, unavailable_reason: reason },
      })
      const shown = wrapper.find('[data-testid="emu-unavailable-reason"]')
      expect(shown.text()).toBe(reason)
      expect(shown.findAll('code').map((c) => c.text())).toEqual([
        'pip install "autoemulate>=2.1,<3"',
        'pip install -e "/src/circulatory_autogen[emulation]"',
      ])
    })

    it('still shows the whole reason when it carries no command', () => {
      const wrapper = mountPanel({
        defaults: { ...UNAVAILABLE, unavailable_reason: 'No Python interpreter is configured.' },
      })
      expect(wrapper.find('[data-testid="emu-unavailable-reason"]').text()).toBe(
        'No Python interpreter is configured.',
      )
    })

    // `available` is false whenever `supported` is, so both would otherwise
    // render. The one that names the interpreter and the command wins.
    it('wins over the older "this CA has no emulators" message', () => {
      const wrapper = mountPanel({ defaults: { ...UNAVAILABLE, supported: false } })
      expect(wrapper.find('[data-testid="emu-unavailable"]').exists()).toBe(true)
      expect(wrapper.find('[data-testid="emu-unsupported"]').exists()).toBe(false)
    })

    // A circulatory_autogen (or a backend) that predates the field says nothing
    // about availability, and must behave exactly as it did before.
    it('treats a missing `available` as available', () => {
      const wrapper = mountPanel({ defaults: DEFAULTS })
      expect(wrapper.find('[data-testid="emu-unavailable"]').exists()).toBe(false)
      expect(wrapper.find('[data-testid="train-emulator"]').exists()).toBe(true)
    })
  })

  // The degradation that started this: an available emulator whose model
  // registry could not be listed still offers the field, as free text.
  it('offers models as a menu when there are any and as free text when not', () => {
    const menu = mountPanel()
    expect(menu.find('[data-testid="emu-opt-models"]').element.tagName).toBe('SELECT')
    const free = mountPanel({ defaults: { ...DEFAULTS, models: [], available: true } })
    const field = free.find('[data-testid="emu-opt-models"]')
    expect(field.exists()).toBe(true)
    expect(field.element.tagName).not.toBe('SELECT')
    expect(free.find('[data-testid="train-emulator"]').exists()).toBe(true)
  })

  // `emulator_settings.reuse_samples` refits the design and simulated features a
  // previous run saved, running no simulations. Two things the form has to show
  // or the tick box lies: it needs those files, and it makes three settings moot.
  describe('reuse samples', () => {
    const reuseBox = (w) => w.find('[data-testid="emu-opt-reuse_samples"]')

    async function tickReuse(wrapper) {
      await reuseBox(wrapper).trigger('change')
      await wrapper.vm.$nextTick()
    }

    it('is disabled, with the reason, when there is nothing to reuse', () => {
      const wrapper = mountPanel({ metadata: METADATA, reusable: false })
      expect(reuseBox(wrapper).attributes('disabled')).toBeDefined()
      const hint = wrapper.find('[data-testid="emu-reuse-unavailable"]')
      expect(hint.exists()).toBe(true)
      expect(hint.text()).toContain('Train an emulator first')
    })

    it('is enabled once both the metadata and the saved samples are there', () => {
      const wrapper = mountPanel({ metadata: METADATA, reusable: true })
      expect(reuseBox(wrapper).attributes('disabled')).toBeUndefined()
      expect(wrapper.find('[data-testid="emu-reuse-unavailable"]').exists()).toBe(false)
    })

    it('greys exactly the three settings circulatory_autogen will ignore', async () => {
      const wrapper = mountPanel({ metadata: METADATA, reusable: true })
      const disabled = (name) =>
        wrapper.find(`[data-testid="emu-opt-${name}"]`).attributes('disabled') !== undefined

      for (const name of ['num_train_samples', 'sample_type', 'log_scale_params']) {
        expect(disabled(name), `${name} before`).toBe(false)
      }

      await tickReuse(wrapper)

      // Ignored on a reuse run: the saved design is what gets fitted.
      expect(disabled('num_train_samples')).toBe(true)
      expect(disabled('sample_type')).toBe(true)
      expect(disabled('log_scale_params')).toBe(true)
      // Still applied — trying these without paying for the simulations again is
      // the whole point of reusing.
      expect(disabled('models')).toBe(false)
      expect(disabled('min_r2')).toBe(false)
      expect(wrapper.find('[data-testid="emu-reuse-on"]').exists()).toBe(true)
    })

    it('carries reuse_samples in the run payload', async () => {
      const wrapper = mountPanel({ metadata: METADATA, reusable: true })
      await wrapper.find('[data-testid="train-emulator"]').trigger('click')
      expect(wrapper.emitted('run')[0][0].reuse_samples).toBe(false)

      await tickReuse(wrapper)
      await wrapper.find('[data-testid="train-emulator"]').trigger('click')
      expect(wrapper.emitted('run')[1][0].reuse_samples).toBe(true)
    })

    it('unticks itself when the emulator directory loses its samples', async () => {
      const wrapper = mountPanel({ metadata: METADATA, reusable: true })
      await tickReuse(wrapper)
      // A different study, or a bundle from a CA that never saved the samples:
      // leaving it ticked would ask for a run circulatory_autogen refuses.
      await wrapper.setProps({ reusable: false })
      await wrapper.vm.$nextTick()
      expect(reuseBox(wrapper).element.checked).toBe(false)
      await wrapper.find('[data-testid="train-emulator"]').trigger('click')
      const emitted = wrapper.emitted('run')
      expect(emitted[emitted.length - 1][0].reuse_samples).toBe(false)
    })
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

describe('tour anchors', () => {
  it('marks the training settings and the use-emulator row', () => {
    const wrapper = mountPanel()
    expect(wrapper.find('[data-testid="emu-settings"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="emu-use-row"]').exists()).toBe(true)
  })
})

describe('opening the tab before anything is loaded', () => {
  // Reported against v0.4.1: opening the Emulator tab on a fresh app showed only the
  // backend's environment diagnosis ("the emulator options could not be read ... the
  // server log has the import error"). It trained fine once a model was loaded, so the
  // message sent the user hunting an import failure in an app that worked. What is
  // actually blocking them is that they have loaded nothing yet, and that is the only
  // step they can take.
  const open = (props) => mountPanel({ canRun: false, ...props })

  it('says what to load, even while the backend calls emulation unavailable', () => {
    const w = open({
      canRun: false,
      defaults: {
        supported: false,
        available: false,
        unavailable_reason: 'The emulator options could not be read from libcuflynx.',
      },
    })

    const needs = w.find('[data-testid="emu-needs-study"]')
    expect(needs.exists()).toBe(true)
    expect(needs.text()).toMatch(/load a model/i)
    // The diagnosis is still shown -- underneath, as context, not as the instruction.
    expect(w.find('[data-testid="emu-unavailable"]').exists()).toBe(true)
  })

  it('says what to load when emulation is perfectly fine', () => {
    const w = open({ canRun: false, defaults: { supported: true, available: true } })
    expect(w.find('[data-testid="emu-needs-study"]').exists()).toBe(true)
  })

  it('says nothing once a study is loaded', () => {
    const w = open({ canRun: true, defaults: { supported: true, available: true } })
    expect(w.find('[data-testid="emu-needs-study"]').exists()).toBe(false)
  })

  it('calls the engine libcuflynx, not circulatory_autogen', () => {
    // The packaged app bundles the engine; there is no circulatory_autogen checkout
    // involved, so naming one sends a packaged-app user looking for a directory they
    // do not have.
    const w = open({ canRun: true, defaults: { supported: false } })
    const text = w.find('[data-testid="emu-unsupported"]').text()
    // The *subject* of the sentence is the engine, and it is libcuflynx. Asserting only
    // that "circulatory_autogen" is absent would not work here -- the second sentence
    // mentions a checkout on purpose, for the user who has pointed Settings at one.
    expect(text).toMatch(/^This libcuflynx has no emulator support/)
  })
})

/**
 * Multi-stage sampling.
 *
 * CA draws the design in one stage by default and can draw it in several, each with a
 * share of the sample budget and its own method. The two per-stage settings are lists
 * that must be exactly `num_stages` long, with fractions summing to 1 — CA refuses the
 * plan otherwise, after the model has been generated. So the form's job is to make an
 * invalid plan hard to express and an unused one invisible.
 */
describe('sampling stages', () => {
  const STAGE_OPTIONS = [
    { name: 'num_stages', type: 'int', default: 1, required: false, description: 'stages' },
    {
      name: 'frac_per_stage', type: 'str', default: null, required: false,
      per_stage: true, description: 'shares',
    },
    {
      name: 'method_per_stage', type: 'str', default: null, required: false,
      per_stage: true,
      item_choices: ['sobol', 'latin_hypercube', 'random', 'gradient_weighted',
                     'error_weighted'],
      description: 'methods',
    },
    {
      name: 'weight_per_stage', type: 'str', default: null, required: false,
      per_stage: true, description: 'weights',
    },
  ]

  const withStages = { ...DEFAULTS, options: [...DEFAULTS.options, ...STAGE_OPTIONS] }

  function mountStages(props = {}) {
    return mount(EmulatorPanel, {
      props: { defaults: withStages, canRun: true, ...props },
      global: { stubs },
    })
  }

  async function setStages(wrapper, count) {
    wrapper.vm.optionValues.num_stages = count
    await wrapper.vm.$nextTick()
    await wrapper.vm.$nextTick()
  }

  it('shows nothing about stages for the single-stage default', async () => {
    const wrapper = mountStages()
    await wrapper.vm.$nextTick()
    expect(wrapper.find('[data-testid="emu-stages"]').exists()).toBe(false)
    // The lists mean nothing with one stage, and sending a one-element list would
    // override CA's own default with this form's guess at it.
    expect(wrapper.vm.optionValues.frac_per_stage).toBe(null)
    expect(wrapper.vm.optionValues.method_per_stage).toBe(null)
  })

  it('reveals one row per stage as soon as there is more than one', async () => {
    const wrapper = mountStages()
    await setStages(wrapper, 3)
    expect(wrapper.find('[data-testid="emu-stages"]').exists()).toBe(true)
    for (const i of [0, 1, 2]) {
      expect(wrapper.find(`[data-testid="emu-stage-frac-${i}"]`).exists()).toBe(true)
      expect(wrapper.find(`[data-testid="emu-stage-method-${i}"]`).exists()).toBe(true)
    }
    expect(wrapper.find('[data-testid="emu-stage-frac-3"]').exists()).toBe(false)
  })

  it('defaults to an even split that adds up, and to adapting after the first stage', async () => {
    const wrapper = mountStages()
    await setStages(wrapper, 2)
    const { frac_per_stage: fractions, method_per_stage: methods } = wrapper.vm.optionValues
    expect(fractions).toEqual([0.5, 0.5])
    expect(fractions.reduce((a, b) => a + b, 0)).toBeCloseTo(1)
    // Space-filling first — an adaptive stage has no earlier features to place points
    // from — then the adaptive one, which is the reason to ask for two stages at all.
    expect(methods).toEqual(['sobol', 'gradient_weighted'])
  })

  it('keeps the lists the same length as the count when it changes', async () => {
    const wrapper = mountStages()
    await setStages(wrapper, 2)
    await setStages(wrapper, 4)
    expect(wrapper.vm.optionValues.frac_per_stage).toHaveLength(4)
    expect(wrapper.vm.optionValues.method_per_stage).toHaveLength(4)
    expect(wrapper.vm.optionValues.frac_per_stage.reduce((a, b) => a + b, 0)).toBeCloseTo(1)
  })

  it('keeps a method the user chose when another stage is added', async () => {
    const wrapper = mountStages()
    await setStages(wrapper, 2)
    wrapper.vm.optionValues.method_per_stage[1] = 'latin_hypercube'
    await setStages(wrapper, 3)
    expect(wrapper.vm.optionValues.method_per_stage[1]).toBe('latin_hypercube')
  })

  it('offers the methods CA advertises, not a list of its own', async () => {
    const wrapper = mountStages()
    await setStages(wrapper, 2)
    expect(wrapper.vm.stageMethods).toEqual(STAGE_OPTIONS[2].item_choices)
  })

  it('says so when the shares do not add up, instead of leaving CA to refuse', async () => {
    const wrapper = mountStages()
    await setStages(wrapper, 2)
    expect(wrapper.find('[data-testid="emu-stages-sum"]').exists()).toBe(false)
    wrapper.vm.optionValues.frac_per_stage[1] = 0.2
    await wrapper.vm.$nextTick()
    expect(wrapper.find('[data-testid="emu-stages-sum"]').text()).toContain('0.700')
  })

  it('says so when the first stage is the adaptive one', async () => {
    const wrapper = mountStages()
    await setStages(wrapper, 2)
    expect(wrapper.find('[data-testid="emu-stages-first"]').exists()).toBe(false)
    wrapper.vm.optionValues.method_per_stage[0] = 'gradient_weighted'
    await wrapper.vm.$nextTick()
    expect(wrapper.find('[data-testid="emu-stages-first"]').exists()).toBe(true)
  })

  it('drops the stage lists again on the way back to a single stage', async () => {
    const wrapper = mountStages()
    await setStages(wrapper, 3)
    await setStages(wrapper, 1)
    expect(wrapper.find('[data-testid="emu-stages"]').exists()).toBe(false)
    expect(wrapper.vm.optionValues.frac_per_stage).toBe(null)
  })

  it('greys the stage settings out when the design is being reused', async () => {
    // Reuse refits samples already on disk, so nothing about the design applies —
    // the same reason num_train_samples and sample_type are greyed.
    const wrapper = mountStages({ reusable: true })
    await setStages(wrapper, 2)
    wrapper.vm.optionValues.reuse_samples = true
    await wrapper.vm.$nextTick()
    expect(wrapper.find('[data-testid="emu-stage-frac-0"]').attributes('disabled')).toBeDefined()
    expect(wrapper.find('[data-testid="emu-stage-method-0"]').attributes('disabled')).toBeDefined()
  })

  it('sends the stage plan to the runner as lists', async () => {
    const wrapper = mountStages()
    await setStages(wrapper, 2)
    await wrapper.vm.$nextTick()
    const emitted = wrapper.emitted('change')
    const latest = emitted[emitted.length - 1][0]
    expect(latest.num_stages).toBe(2)
    expect(latest.frac_per_stage).toEqual([0.5, 0.5])
    expect(latest.method_per_stage).toEqual(['sobol', 'gradient_weighted'])
    expect(latest.weight_per_stage).toEqual([1, 1])
  })

  it('offers a weight only for a stage that has scores to follow', async () => {
    // A space-filling stage has none, and CA ignores the value — a box for it would be
    // asking for a number that does nothing.
    const wrapper = mountStages()
    await setStages(wrapper, 2)
    expect(wrapper.find('[data-testid="emu-stage-weight-0"]').exists()).toBe(false)
    expect(wrapper.find('[data-testid="emu-stage-weight-1"]').exists()).toBe(true)

    wrapper.vm.optionValues.method_per_stage[0] = 'gradient_weighted'
    await wrapper.vm.$nextTick()
    expect(wrapper.find('[data-testid="emu-stage-weight-0"]').exists()).toBe(true)
  })

  it('treats error_weighted as an adaptive stage too', async () => {
    const wrapper = mountStages()
    await setStages(wrapper, 2)
    wrapper.vm.optionValues.method_per_stage[1] = 'error_weighted'
    await wrapper.vm.$nextTick()
    expect(wrapper.find('[data-testid="emu-stage-weight-1"]').exists()).toBe(true)

    // ...including when it is put first, which CA refuses.
    wrapper.vm.optionValues.method_per_stage[0] = 'error_weighted'
    await wrapper.vm.$nextTick()
    expect(wrapper.find('[data-testid="emu-stages-first"]').exists()).toBe(true)
  })
})
