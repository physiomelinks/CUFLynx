import { describe, it, expect, vi, beforeEach } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'

vi.mock('../lib/api', () => ({ uploadParamsForId: vi.fn(), getConfig: vi.fn() }))

import EditParamsDialog from './EditParamsDialog.vue'
import { uploadParamsForId, getConfig } from '../lib/api'

// The prior vocabulary as CA reports it through /api/config.
const PRIOR_TYPES = {
  default: 'uniform',
  types: [
    { value: 'uniform', label: 'Uniform', description: 'flat across [min, max]',
      supports_unbounded: false, params: [] },
    {
      value: 'normal', label: 'Normal', description: 'centred on the range',
      supports_unbounded: true,
      params: [
        { name: 'prior_mean', type: 'float', default: null, positive: false, role: 'location',
          default_expr: '(min + max) / 2', description: 'Centre of the Gaussian.' },
        { name: 'prior_std', type: 'float', default: null, positive: true, role: 'scale',
          default_expr: '(max - min) / 6', description: 'Standard deviation.' },
      ],
    },
    {
      value: 'exponential', label: 'Exponential', description: 'decays',
      supports_unbounded: false,
      params: [{ name: 'prior_lambda', type: 'float', default: 1.0, positive: true, role: 'rate',
                 description: 'Decay rate.' }],
    },
  ],
}

/** jsdom's File has no .text(); read it the way the other tests do. */
function readFile(file) {
  return new Promise((resolve) => {
    const reader = new FileReader()
    reader.onload = () => resolve(reader.result)
    reader.readAsText(file)
  })
}

// Inline stubs so the dialog + footer render without teleport.
const DialogStub = {
  props: ['visible'],
  template: '<div v-if="visible"><slot /><slot name="footer" /></div>',
}
const ButtonStub = {
  props: ['label', 'disabled', 'icon', 'size', 'text', 'title'],
  emits: ['click'],
  template:
    '<button :disabled="disabled" :title="title" v-bind="$attrs" @click="$emit(\'click\')">{{ label }}</button>',
}
const CheckboxStub = {
  props: ['modelValue', 'binary'],
  emits: ['update:modelValue'],
  template:
    '<input type="checkbox" :checked="modelValue" @change="$emit(\'update:modelValue\', $event.target.checked)" />',
}
const MessageStub = { template: '<div class="msg"><slot /></div>' }
const stubs = { Dialog: DialogStub, Button: ButtonStub, Checkbox: CheckboxStub, Message: MessageStub }

const baseProps = {
  visible: true,
  modelId: 'abc',
  currentParams: [
    { qname: 'v/a', min: 1, max: 2, name_for_plotting: '\\alpha', param_type: 'global', initial_value: 1.5 },
  ],
  modelVariables: { params: ['v/a', 'v/b'], initial_values: { 'v/b': 2 } },
  loadedFilename: 'p.csv',
  modelName: 'M',
}

function mountDialog(props = {}) {
  return mount(EditParamsDialog, { props: { ...baseProps, ...props }, global: { stubs } })
}

// The modifier operation vocabulary as CA reports it through /api/config.
const MODIFIER_OPS = {
  default: 'scale',
  operations: [
    { value: 'scale', label: 'Scale', description: 'one calibrated multiplier',
      applies_to: 'value', dimensionless: true,
      default_min: 0.5, default_max: 2.0, identity: 1.0 },
  ],
}

beforeEach(() => {
  uploadParamsForId.mockReset()
  getConfig.mockReset()
  getConfig.mockResolvedValue({
    param_prior_types: PRIOR_TYPES,
    param_modifier_operations: MODIFIER_OPS,
  })
  // jsdom lacks createObjectURL; provide a stub so the download path runs.
  globalThis.URL.createObjectURL = vi.fn(() => 'blob:mock')
  globalThis.URL.revokeObjectURL = vi.fn()
})

describe('EditParamsDialog', () => {
  it('shows a hover hint about choosing physiologically realistic ranges', () => {
    const wrapper = mountDialog()
    const hint = wrapper.find('[data-testid="ep-ranges-hint"]')
    expect(hint.exists()).toBe(true)
    expect(hint.attributes('title')).toContain('physiologically realistic')
    expect(hint.attributes('title')).toContain('sensitivity analysis lacks meaning')
  })

  it('pre-includes loaded CSV params and lists model params unchecked', () => {
    const wrapper = mountDialog()
    const rows = wrapper.findAll('[data-testid="ep-row"]')
    expect(rows).toHaveLength(2)
    // The include column, not the multi-select one that now precedes it.
    const checks = wrapper.findAll('.ep-inc input[type="checkbox"]')
    expect(checks[0].element.checked).toBe(true) // v/a from CSV
    expect(checks[1].element.checked).toBe(false) // v/b from model
    expect(wrapper.text()).toContain('1 included')
  })

  it('filters the visible rows by the search box (qname / plot label)', async () => {
    const wrapper = mountDialog()
    expect(wrapper.findAll('[data-testid="ep-row"]')).toHaveLength(2)

    const search = wrapper.find('[data-testid="ep-search"]')
    await search.setValue('v/b')
    let rows = wrapper.findAll('[data-testid="ep-row"]')
    expect(rows).toHaveLength(1)
    expect(rows[0].text()).toContain('v/b')

    // case-insensitive, and also matches the plot label (\alpha on v/a)
    await search.setValue('ALPHA')
    rows = wrapper.findAll('[data-testid="ep-row"]')
    expect(rows).toHaveLength(1)
    expect(rows[0].text()).toContain('v/a')

    // clearing restores the full list
    await search.setValue('')
    expect(wrapper.findAll('[data-testid="ep-row"]')).toHaveLength(2)
  })

  it('keeps a filtered-out row included and saved', async () => {
    uploadParamsForId.mockResolvedValue({ params: [] })
    const wrapper = mountDialog()
    // v/a is included by default; hide it via search — inclusion is unaffected.
    await wrapper.find('[data-testid="ep-search"]').setValue('v/b')
    expect(wrapper.find('[data-testid="ep-row"]').text()).toContain('v/b')
    expect(wrapper.text()).toContain('1 included')
    // saving still proceeds (the hidden-but-included v/a is written to the CSV).
    await wrapper.find('[data-testid="ep-save"]').trigger('click')
    await flushPromises()
    expect(uploadParamsForId).toHaveBeenCalledOnce()
  })

  it('disables Save when nothing is selected', async () => {
    const wrapper = mountDialog()
    await wrapper.findAll('.ep-inc input[type="checkbox"]')[0].setValue(false)
    expect(wrapper.text()).toContain('0 included')
    expect(wrapper.find('[data-testid="ep-save"]').attributes('disabled')).toBeDefined()
  })

  it('disables Save when an included row has min >= max', async () => {
    const wrapper = mountDialog()
    const row = wrapper.findAll('[data-testid="ep-row"]')[0]
    const [minInput] = row.findAll('input.ep-num')
    await minInput.setValue('5') // min 5 >= max 2 -> invalid
    expect(wrapper.find('[data-testid="ep-save"]').attributes('disabled')).toBeDefined()
  })

  it('on Save: downloads a dated JSON, applies it, and emits saved', async () => {
    uploadParamsForId.mockResolvedValue({ params: [{ qname: 'v/a' }] })
    const clickSpy = vi
      .spyOn(HTMLAnchorElement.prototype, 'click')
      .mockImplementation(() => {})

    const wrapper = mountDialog()
    await wrapper.find('[data-testid="ep-save"]').trigger('click')
    await flushPromises()

    // download triggered
    expect(globalThis.URL.createObjectURL).toHaveBeenCalledOnce()
    expect(clickSpy).toHaveBeenCalledOnce()

    // applied via the existing upload endpoint with a File + modelId. Saved as
    // the JSON form: a CSV cannot express modifiers or cross-name groups, and a
    // study loaded from CSV keeps its stem so the lineage stays visible.
    expect(uploadParamsForId).toHaveBeenCalledOnce()
    const [fileArg, idArg] = uploadParamsForId.mock.calls[0]
    expect(idArg).toBe('abc')
    expect(fileArg).toBeInstanceOf(File)
    expect(fileArg.name).toMatch(/^p_\d{6}\.json$/) // <stem>_<yymmdd>.json

    // emits saved with parsed params + versioned filename, then closes
    const saved = wrapper.emitted('saved')[0][0]
    expect(saved.params).toEqual([{ qname: 'v/a' }])
    expect(saved.filename).toMatch(/^p_\d{6}\.json$/)
    expect(wrapper.emitted('update:visible').at(-1)).toEqual([false])

    clickSpy.mockRestore()
  })

  it('expands an annotation field, edits it, and writes it into the saved file', async () => {
    uploadParamsForId.mockResolvedValue({ params: [{ qname: 'v/a' }] })
    const wrapper = mountDialog()

    // Comment field is hidden until the note toggle is clicked.
    expect(wrapper.find('[data-testid="ep-note-input"]').exists()).toBe(false)
    const row = wrapper.findAll('[data-testid="ep-row"]')[0]
    await row.find('[data-testid="ep-note-toggle"]').trigger('click')

    const input = row.find('[data-testid="ep-note-input"]')
    expect(input.exists()).toBe(true)
    await input.setValue('range from Dash 2016')

    await wrapper.find('[data-testid="ep-save"]').trigger('click')
    await flushPromises()

    const [fileArg] = uploadParamsForId.mock.calls[0]
    const doc = JSON.parse(
      await new Promise((resolve) => {
        const reader = new FileReader()
        reader.onload = () => resolve(reader.result)
        reader.readAsText(fileArg)
      }),
    )
    expect(doc.params[0].comment).toBe('range from Dash 2016')
  })

  it('auto-expands rows that already carry an annotation', () => {
    const wrapper = mountDialog({
      currentParams: [
        { qname: 'v/a', min: 1, max: 2, name_for_plotting: '\\alpha', comment: 'preloaded note' },
      ],
    })
    const input = wrapper.find('[data-testid="ep-note-input"]')
    expect(input.exists()).toBe(true)
    expect(input.element.value).toBe('preloaded note')
  })
})

describe('EditParamsDialog — prior column', () => {
  it('offers the priors CA reported, not a hardcoded list', async () => {
    const wrapper = mountDialog()
    await flushPromises()
    const select = wrapper.find('[data-testid="ep-prior"]')
    expect(select.exists()).toBe(true)
    const labels = select.findAll('option').map((o) => o.text())
    // The "not stated" choice, then CA's vocabulary verbatim.
    expect(labels).toEqual(['— (uniform)', 'Uniform', 'Normal', 'Exponential'])
  })

  it('hides the column when the backend reports no vocabulary', async () => {
    getConfig.mockResolvedValue({})
    const wrapper = mountDialog()
    await flushPromises()
    expect(wrapper.find('[data-testid="ep-prior"]').exists()).toBe(false)
  })

  it('stays usable when the config request fails', async () => {
    getConfig.mockRejectedValue(new Error('offline'))
    const wrapper = mountDialog()
    await flushPromises()
    expect(wrapper.find('[data-testid="ep-prior"]').exists()).toBe(false)
    expect(wrapper.findAll('[data-testid="ep-row"]')).toHaveLength(2)
  })

  it('shows a loaded prior as the selected value', async () => {
    const wrapper = mountDialog({
      currentParams: [{ qname: 'v/a', min: 1, max: 2, prior: 'normal' }],
    })
    await flushPromises()
    expect(wrapper.find('[data-testid="ep-prior"]').element.value).toBe('normal')
  })

  it('writes the chosen prior into the saved file', async () => {
    const wrapper = mountDialog({
      currentParams: [{ qname: 'v/a', min: 1, max: 2, prior: 'normal' }],
    })
    await flushPromises()
    await wrapper.find('[data-testid="ep-save"]').trigger('click')
    await flushPromises()

    const [file] = uploadParamsForId.mock.calls[0]
    const doc = JSON.parse(await readFile(file))
    expect(doc.params[0].prior).toBe('normal')
  })

  it('round-trips a prior the user never touched', async () => {
    // The regression: opening the dialog and saving used to drop the column, so
    // every non-uniform prior silently became uniform.
    const wrapper = mountDialog({
      currentParams: [
        { qname: 'v/a', min: 1, max: 2, prior: 'exponential' },
      ],
    })
    await flushPromises()
    await wrapper.find('[data-testid="ep-save"]').trigger('click')
    await flushPromises()

    const [file] = uploadParamsForId.mock.calls[0]
    expect(await readFile(file)).toContain('exponential')
  })
})

describe('EditParamsDialog — prior hyper-parameters', () => {
  it('shows only the fields the chosen prior declares', async () => {
    const wrapper = mountDialog({
      currentParams: [{ qname: 'v/a', min: 1, max: 2, prior: 'normal' }],
    })
    await flushPromises()
    // Collapsed until asked for, so the row reads like the others.
    expect(wrapper.find('[data-testid="ep-prior-param-prior_mean"]').exists()).toBe(false)
    await wrapper.find('[data-testid="ep-prior-toggle"]').trigger('click')
    expect(wrapper.find('[data-testid="ep-prior-param-prior_mean"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="ep-prior-param-prior_std"]').exists()).toBe(true)
    // Belongs to the exponential, not the normal.
    expect(wrapper.find('[data-testid="ep-prior-param-prior_lambda"]').exists()).toBe(false)
  })

  it('shows no fields for a prior that takes none', async () => {
    const wrapper = mountDialog({
      currentParams: [{ qname: 'v/a', min: 1, max: 2, prior: 'uniform' }],
    })
    await flushPromises()
    expect(wrapper.find('.ep-prior-block').exists()).toBe(false)
  })

  it('renders a loaded value and writes it into the saved file', async () => {
    const wrapper = mountDialog({
      currentParams: [
        { qname: 'v/a', min: 1, max: 2, prior: 'normal', prior_params: { prior_mean: '7' } },
      ],
    })
    await flushPromises()
    // A row that already states a value opens showing it, like an annotation does.
    const mean = wrapper.find('[data-testid="ep-prior-param-prior_mean"]')
    expect(mean.element.value).toBe('7')

    await mean.setValue('9.5')
    await wrapper.find('[data-testid="ep-save"]').trigger('click')
    await flushPromises()

    const doc = JSON.parse(await readFile(uploadParamsForId.mock.calls[0][0]))
    expect(doc.params[0].prior_params.prior_mean).toBe('9.5')
  })

  it('drops values the newly chosen prior does not take', async () => {
    // CA rejects a hyper-parameter set on a prior that ignores it, so leaving it
    // behind would make the file unsavable for a reason the user cannot see.
    const wrapper = mountDialog({
      currentParams: [
        { qname: 'v/a', min: 1, max: 2, prior: 'normal', prior_params: { prior_std: '0.5' } },
      ],
    })
    await flushPromises()
    await wrapper.find('[data-testid="ep-prior"]').setValue('uniform')
    await flushPromises()

    await wrapper.find('[data-testid="ep-save"]').trigger('click')
    await flushPromises()
    const text = await readFile(uploadParamsForId.mock.calls[0][0])
    expect(text).not.toContain('prior_std')
    expect(text).not.toContain('0.5')
  })

  it('offers whatever CA declares, without knowing the names', async () => {
    // A value CA adds to a prior must appear with no change in this repo.
    getConfig.mockResolvedValue({
      param_prior_types: {
        default: 'uniform',
        types: [{
          value: 'lognormal', label: 'Log-normal', description: '',
          params: [{ name: 'prior_sigma', type: 'float', default: 1.0, positive: true,
                     description: 'Shape.' }],
        }],
      },
    })
    const wrapper = mountDialog({
      currentParams: [{ qname: 'v/a', min: 1, max: 2, prior: 'lognormal' }],
    })
    await flushPromises()
    await wrapper.find('[data-testid="ep-prior-toggle"]').trigger('click')
    expect(wrapper.find('[data-testid="ep-prior-param-prior_sigma"]').exists()).toBe(true)
  })
})

describe('EditParamsDialog — prior settings disclosure', () => {
  it('keeps the settings out of the main columns', async () => {
    // Their own block spanning the row, not extra grid columns: which values
    // exist differs per prior, so as columns they would be blank for most rows.
    const wrapper = mountDialog({
      currentParams: [{ qname: 'v/a', min: 1, max: 2, prior: 'normal' }],
    })
    await flushPromises()
    const head = wrapper.find('.ep-head').text()
    expect(head).not.toContain('prior_mean')
    expect(wrapper.find('.ep-prior-block').exists()).toBe(true)
  })

  it('names the prior in the panel heading', async () => {
    const wrapper = mountDialog({
      currentParams: [{ qname: 'v/a', min: 1, max: 2, prior: 'normal' }],
    })
    await flushPromises()
    expect(wrapper.find('[data-testid="ep-prior-toggle"]').text()).toContain('Normal prior settings')
  })

  it('summarises a set value while collapsed', async () => {
    // So a row that departs from the defaults is legible without opening it.
    const wrapper = mountDialog({
      currentParams: [{ qname: 'v/a', min: 1, max: 2, prior: 'normal' }],
    })
    await flushPromises()
    expect(wrapper.find('.ep-prior-summary').text()).toBe('defaults')

    await wrapper.find('[data-testid="ep-prior-toggle"]').trigger('click')
    await wrapper.find('[data-testid="ep-prior-param-prior_std"]').setValue('0.5')
    await wrapper.find('[data-testid="ep-prior-toggle"]').trigger('click')
    expect(wrapper.find('.ep-prior-summary').text()).toContain('prior_std 0.5')
  })

  it('opens when a prior with settings is chosen, and shuts on one without', async () => {
    const wrapper = mountDialog({
      currentParams: [{ qname: 'v/a', min: 1, max: 2, prior: 'uniform' }],
    })
    await flushPromises()
    expect(wrapper.find('.ep-prior-block').exists()).toBe(false)

    await wrapper.find('[data-testid="ep-prior"]').setValue('normal')
    expect(wrapper.find('[data-testid="ep-prior-param-prior_mean"]').exists()).toBe(true)

    await wrapper.find('[data-testid="ep-prior"]').setValue('uniform')
    expect(wrapper.find('.ep-prior-block').exists()).toBe(false)
  })
})

describe('EditParamsDialog — unbounded parameters', () => {
  const NORMAL = { qname: 'v/a', min: 1, max: 2, prior: 'normal' }

  it('is offered only where CA says the prior can derive a range', async () => {
    const wrapper = mountDialog({ currentParams: [{ ...NORMAL, prior: 'uniform' }] })
    await flushPromises()
    expect(wrapper.find('[data-testid="ep-unbounded"]').exists()).toBe(false)

    await wrapper.find('[data-testid="ep-prior"]').setValue('normal')
    expect(wrapper.find('[data-testid="ep-unbounded"]').exists()).toBe(true)
  })

  it('makes min and max unenterable', async () => {
    const wrapper = mountDialog({ currentParams: [NORMAL] })
    await flushPromises()
    await wrapper.find('[data-testid="ep-prior-toggle"]').trigger('click')

    const nums = () => wrapper.findAll('input.ep-num')
    expect(nums()[0].attributes('disabled')).toBeUndefined()

    await wrapper.find('[data-testid="ep-unbounded"]').setValue(true)
    expect(nums()[0].attributes('disabled')).toBeDefined()
    expect(nums()[1].attributes('disabled')).toBeDefined()
  })

  it('writes the flag and no bounds', async () => {
    // The bounds were derived from the prior; writing them back would freeze a
    // range that should follow the prior.
    const wrapper = mountDialog({
      currentParams: [{ ...NORMAL, prior_params: { prior_mean: '7', prior_std: '1.5' } }],
    })
    await flushPromises()
    await wrapper.find('[data-testid="ep-unbounded"]').setValue(true)
    await wrapper.find('[data-testid="ep-save"]').trigger('click')
    await flushPromises()

    const doc = JSON.parse(await readFile(uploadParamsForId.mock.calls[0][0]))
    expect(doc.params[0].unbounded).toBe(true)
    expect(doc.params[0]).not.toHaveProperty('min')
    expect(doc.params[0]).not.toHaveProperty('max')
  })

  it('refuses to save until the centre and width are given', async () => {
    // CA derives the range from them, and their usual defaults come from the
    // range that is no longer there.
    const wrapper = mountDialog({ currentParams: [NORMAL] })
    await flushPromises()
    await wrapper.find('[data-testid="ep-unbounded"]').setValue(true)
    expect(wrapper.find('[data-testid="ep-save"]').attributes('disabled')).toBeDefined()

    await wrapper.find('[data-testid="ep-prior-param-prior_mean"]').setValue('7')
    await wrapper.find('[data-testid="ep-prior-param-prior_std"]').setValue('1.5')
    expect(wrapper.find('[data-testid="ep-save"]').attributes('disabled')).toBeUndefined()
  })

  it('gives the bounds back when unticked', async () => {
    const wrapper = mountDialog({ currentParams: [NORMAL] })
    await flushPromises()
    await wrapper.find('[data-testid="ep-unbounded"]').setValue(true)
    await wrapper.find('[data-testid="ep-unbounded"]').setValue(false)
    expect(wrapper.findAll('input.ep-num')[0].attributes('disabled')).toBeUndefined()
  })

  it('says so in the collapsed summary', async () => {
    const wrapper = mountDialog({
      currentParams: [{ ...NORMAL, unbounded: true, prior_params: { prior_mean: '7' } }],
    })
    await flushPromises()
    await wrapper.find('[data-testid="ep-prior-toggle"]').trigger('click')
    expect(wrapper.find('.ep-prior-summary').text()).toBe('unbounded')
  })
})

describe('EditParamsDialog — placeholder tells the truth', () => {
  const NORMAL = { qname: 'v/a', min: 1, max: 2, prior: 'normal' }

  it('says a blank value is derived from the range', async () => {
    const wrapper = mountDialog({ currentParams: [NORMAL] })
    await flushPromises()
    await wrapper.find('[data-testid="ep-prior-toggle"]').trigger('click')
    // The number CA will actually use for [1, 2], not a description of it.
    expect(
      wrapper.find('[data-testid="ep-prior-param-prior_mean"]').attributes('placeholder'),
    ).toBe('1.5')
    expect(
      wrapper.find('[data-testid="ep-prior-param-prior_std"]').attributes('placeholder'),
    ).toBe('0.1667')
  })

  it('says required once the range is derived from it instead', async () => {
    // Unbounded reverses the relationship: min/max come from the centre and
    // width, so "from min/max" would be circular -- and these are the one thing
    // that cannot be left blank.
    const wrapper = mountDialog({ currentParams: [NORMAL] })
    await flushPromises()
    await wrapper.find('[data-testid="ep-unbounded"]').setValue(true)

    for (const f of ['prior_mean', 'prior_std']) {
      expect(
        wrapper.find(`[data-testid="ep-prior-param-${f}"]`).attributes('placeholder'),
      ).toBe('required')
    }
  })

  it('leaves a field with a real default showing that default', async () => {
    const wrapper = mountDialog({
      currentParams: [{ qname: 'v/a', min: 1, max: 2, prior: 'exponential' }],
    })
    await flushPromises()
    await wrapper.find('[data-testid="ep-prior-toggle"]').trigger('click')
    expect(
      wrapper.find('[data-testid="ep-prior-param-prior_lambda"]').attributes('placeholder'),
    ).toBe('1')
  })
})

// ---------------------------------------------------------------------------
// Grouped parameters (issue #193)
// ---------------------------------------------------------------------------
describe('EditParamsDialog — grouped parameters', () => {
  // Four aorta segments sharing one elastance, plus one unrelated parameter.
  const groupProps = {
    currentParams: [],
    modelVariables: {
      params: ['ao_A/E', 'ao_B/E', 'ao_C/E', 'ao_A/R'],
      initial_values: { 'ao_A/E': 1, 'ao_B/E': 1, 'ao_C/E': 1, 'ao_A/R': 2 },
    },
  }

  function rowFor(wrapper, qname) {
    return wrapper
      .findAll('[data-testid="ep-row"]')
      .find((r) => r.find('.ep-name').text().startsWith(qname))
  }

  it('shows a loaded group as one row, marked with its size', () => {
    const wrapper = mountDialog({
      currentParams: [
        { qname: 'ao_A/E', qnames: ['ao_A/E', 'ao_B/E'], min: 1, max: 2, name_for_plotting: 'E' },
      ],
      modelVariables: groupProps.modelVariables,
    })
    expect(wrapper.findAll('[data-testid="ep-row"]').map((r) => r.find('.ep-name').text())).toEqual(
      ['ao_A/E×2', 'ao_A/R', 'ao_C/E'],
    )
    expect(wrapper.find('[data-testid="ep-group-badge"]').text()).toBe('×2')
  })

  it('has no per-row grouping panel — the toolbar Group (override) replaced it', () => {
    const wrapper = mountDialog(groupProps)
    expect(wrapper.find('[data-testid="ep-group-toggle"]').exists()).toBe(false)
    expect(wrapper.find('[data-testid="ep-group-panel"]').exists()).toBe(false)
  })

  async function selectRows(wrapper, qnames) {
    for (const row of wrapper.findAll('[data-testid="ep-row"]')) {
      const name = row.find('.ep-name').text()
      if (qnames.some((q) => name.startsWith(q))) {
        await row.find('[data-testid="ep-select"]').setValue(true)
      }
    }
  }

  it('writes a toolbar-created group as one entry naming every target', async () => {
    uploadParamsForId.mockResolvedValue({ params: [] })
    const wrapper = mountDialog(groupProps)
    await flushPromises()
    await selectRows(wrapper, ['ao_A/E', 'ao_B/E', 'ao_C/E'])
    await wrapper.find('[data-testid="ep-group-selected"]').trigger('click')

    // The absorbed rows stop being parameters of their own, and the new group
    // sits at the top of the list where the user can see it happened.
    expect(wrapper.findAll('[data-testid="ep-row"]')).toHaveLength(2)
    expect(wrapper.findAll('[data-testid="ep-row"]')[0].text()).toContain('ao_A/E')
    expect(wrapper.text()).toContain('1 included')

    await wrapper.find('[data-testid="ep-save"]').trigger('click')
    await flushPromises()
    const doc = JSON.parse(await readFile(uploadParamsForId.mock.calls[0][0]))
    // targets order preserved: CA's baselines pair with targets by index.
    expect(doc.params[0].targets).toEqual(['ao_A/E', 'ao_B/E', 'ao_C/E'])
  })

  it('ungroups from the row button, giving every component back', async () => {
    const wrapper = mountDialog(groupProps)
    await flushPromises()
    await selectRows(wrapper, ['ao_A/E', 'ao_B/E'])
    await wrapper.find('[data-testid="ep-group-selected"]').trigger('click')
    expect(wrapper.findAll('[data-testid="ep-row"]')).toHaveLength(3)

    await wrapper.find('[data-testid="ep-ungroup"]').trigger('click')
    expect(wrapper.findAll('[data-testid="ep-row"]')).toHaveLength(4)
    expect(wrapper.find('[data-testid="ep-ungroup"]').exists()).toBe(false)
  })

  it('a created modifier also lands at the top of the list', async () => {
    const wrapper = mountDialog(groupProps)
    await flushPromises()
    await selectRows(wrapper, ['ao_A/E', 'ao_B/E'])
    await wrapper.find('[data-testid="ep-create-modifier"]').trigger('click')
    const first = wrapper.findAll('[data-testid="ep-row"]')[0]
    expect(first.find('[data-testid="ep-modifier-badge"]').exists()).toBe(true)
  })

  it('finds a group by any of its components, not just the one it is named after', async () => {
    const wrapper = mountDialog({
      currentParams: [
        { qname: 'ao_A/E', qnames: ['ao_A/E', 'ao_B/E'], min: 1, max: 2, name_for_plotting: 'E' },
      ],
      modelVariables: groupProps.modelVariables,
    })
    await wrapper.find('[data-testid="ep-search"]').setValue('ao_B')
    const rows = wrapper.findAll('[data-testid="ep-row"]')
    expect(rows).toHaveLength(1)
    expect(rows[0].find('.ep-name').text()).toBe('ao_A/E×2')
  })
})

describe('EditParamsDialog — placeholder follows the bounds', () => {
  it('recomputes when min or max is edited', async () => {
    const wrapper = mountDialog({
      currentParams: [{ qname: 'v/a', min: 0, max: 6, prior: 'normal' }],
    })
    await flushPromises()
    await wrapper.find('[data-testid="ep-prior-toggle"]').trigger('click')
    const std = () => wrapper.find('[data-testid="ep-prior-param-prior_std"]')
    expect(std().attributes('placeholder')).toBe('1')

    // (max - min) / 6 with max now 12.
    await wrapper.findAll('input.ep-num')[1].setValue('12')
    expect(std().attributes('placeholder')).toBe('2')
  })
})

// Issue #198. When the number can be computed the placeholder is the number
// (above). When it cannot, it used to be the fixed phrase "from min/max", which
// says neither what the default is nor what to fill in to see it -- and for a
// formula over something other than the bounds it was simply wrong. The fallback
// is now CA's own default_expr.
describe('EditParamsDialog — placeholder when the number cannot be computed (#198)', () => {
  // CA's real exponential: prior_scale defaults to `max / prior_lambda`, so min
  // has nothing to do with it.
  const EXPONENTIAL_WITH_SCALE = {
    default: 'uniform',
    types: [
      { value: 'uniform', label: 'Uniform', description: '', supports_unbounded: false, params: [] },
      {
        value: 'exponential', label: 'Exponential', description: 'decays',
        supports_unbounded: true,
        params: [
          { name: 'prior_lambda', type: 'float', default: 1.0, positive: true, role: 'rate',
            default_expr: null, description: 'Decay rate.' },
          { name: 'prior_scale', type: 'float', default: null, positive: true, role: 'scale',
            default_expr: 'max / prior_lambda', description: 'Decay scale.' },
        ],
      },
    ],
  }

  it('shows the formula, not a phrase, once a bound is cleared', async () => {
    const wrapper = mountDialog({
      currentParams: [{ qname: 'v/a', min: 1, max: 2, prior: 'normal' }],
    })
    await flushPromises()
    await wrapper.find('[data-testid="ep-prior-toggle"]').trigger('click')
    const mean = () => wrapper.find('[data-testid="ep-prior-param-prior_mean"]')
    expect(mean().attributes('placeholder')).toBe('1.5')

    await wrapper.findAll('input.ep-num')[0].setValue('')
    expect(mean().attributes('placeholder')).toBe('= (min + max) / 2')
  })

  it('names the values the formula actually uses', async () => {
    // prior_scale is `max / prior_lambda`; a prior_lambda of 0 makes it
    // uncomputable. "from min/max" pointed at a bound the formula never reads.
    getConfig.mockResolvedValue({ param_prior_types: EXPONENTIAL_WITH_SCALE })
    const wrapper = mountDialog({
      currentParams: [{ qname: 'v/a', min: 1, max: 8, prior: 'exponential' }],
    })
    await flushPromises()
    await wrapper.find('[data-testid="ep-prior-toggle"]').trigger('click')
    const scale = () => wrapper.find('[data-testid="ep-prior-param-prior_scale"]')
    expect(scale().attributes('placeholder')).toBe('8')

    await wrapper.find('[data-testid="ep-prior-param-prior_lambda"]').setValue('0')
    expect(scale().attributes('placeholder')).toBe('= max / prior_lambda')
    expect(scale().attributes('placeholder')).not.toContain('min')
  })

  it('says required when CA declares no default at all', async () => {
    getConfig.mockResolvedValue({
      param_prior_types: {
        default: 'uniform',
        types: [{
          value: 'weird', label: 'Weird', description: '', supports_unbounded: false,
          params: [{ name: 'prior_k', type: 'float', default: null, positive: false,
                     role: 'shape', default_expr: null, description: '' }],
        }],
      },
    })
    const wrapper = mountDialog({
      currentParams: [{ qname: 'v/a', min: 1, max: 2, prior: 'weird' }],
    })
    await flushPromises()
    await wrapper.find('[data-testid="ep-prior-toggle"]').trigger('click')
    expect(
      wrapper.find('[data-testid="ep-prior-param-prior_k"]').attributes('placeholder'),
    ).toBe('required')
  })
})

// ---------------------------------------------------------------------------
// Modifier parameters (#208): multi-select toolbar, θ rows, JSON save
// ---------------------------------------------------------------------------
describe('EditParamsDialog — modifier parameters', () => {
  const modProps = {
    currentParams: [],
    modelVariables: {
      params: ['a/C', 'b/C', 'c/R'],
      initial_values: { 'a/C': 2e-8, 'b/C': 4e-8, 'c/R': 5 },
    },
  }

  async function selectRows(wrapper, qnames) {
    for (const row of wrapper.findAll('[data-testid="ep-row"]')) {
      const name = row.find('.ep-name').text()
      if (qnames.some((q) => name.startsWith(q))) {
        await row.find('[data-testid="ep-select"]').setValue(true)
      }
    }
  }

  it('creates a scale modifier from the selection and saves modifies+operation', async () => {
    uploadParamsForId.mockResolvedValue({ params: [] })
    const wrapper = mountDialog(modProps)
    await flushPromises() // vocabulary load gates the button

    await selectRows(wrapper, ['a/C', 'b/C'])
    const create = wrapper.find('[data-testid="ep-create-modifier"]')
    expect(create.attributes('disabled')).toBeUndefined()
    await create.trigger('click')

    // One modifier row replaces the two claimed targets.
    const badge = wrapper.find('[data-testid="ep-modifier-badge"]')
    expect(badge.text()).toBe('scale ×2')
    expect(wrapper.find('[data-testid="ep-modifier-targets"]').text()).toContain('a/C')

    await wrapper.find('[data-testid="ep-save"]').trigger('click')
    await flushPromises()
    const doc = JSON.parse(await readFile(uploadParamsForId.mock.calls[0][0]))
    const mod = doc.params.find((p) => p.modifies)
    expect(mod.modifies).toEqual(['a/C', 'b/C'])
    expect(mod.operation).toBe('scale')
    expect(mod.min).toBe(0.5) // θ bounds from CA's vocabulary
    expect(mod.max).toBe(2)
    expect(mod).not.toHaveProperty('targets')
  })

  it('deleting a modifier restores its targets', async () => {
    const wrapper = mountDialog(modProps)
    await flushPromises()
    await selectRows(wrapper, ['a/C', 'b/C'])
    await wrapper.find('[data-testid="ep-create-modifier"]').trigger('click')
    expect(wrapper.findAll('[data-testid="ep-row"]')).toHaveLength(2) // mod + c/R

    await wrapper.find('[data-testid="ep-remove-modifier"]').trigger('click')
    expect(wrapper.findAll('[data-testid="ep-row"]')).toHaveLength(3)
    expect(wrapper.find('[data-testid="ep-modifier-badge"]').exists()).toBe(false)
  })

  it('groups differently-named parameters via the toolbar (override)', async () => {
    uploadParamsForId.mockResolvedValue({ params: [] })
    const wrapper = mountDialog(modProps)
    await flushPromises()
    await selectRows(wrapper, ['a/C', 'c/R'])
    await wrapper.find('[data-testid="ep-group-selected"]').trigger('click')

    expect(wrapper.find('[data-testid="ep-group-badge"]').text()).toBe('×2')
    await wrapper.find('[data-testid="ep-save"]').trigger('click')
    await flushPromises()
    const doc = JSON.parse(await readFile(uploadParamsForId.mock.calls[0][0]))
    // The CSV could never say this: one entry, two differently-named targets.
    expect(doc.params[0].targets).toEqual(['a/C', 'c/R'])
  })

  it('greys Group and Create-modifier on the CasADi backend', async () => {
    const wrapper = mountDialog({ ...modProps, generatedModelFormat: 'casadi_python' })
    await flushPromises()
    await selectRows(wrapper, ['a/C', 'b/C'])
    expect(
      wrapper.find('[data-testid="ep-group-selected"]').attributes('disabled'),
    ).toBeDefined()
    expect(
      wrapper.find('[data-testid="ep-create-modifier"]').attributes('disabled'),
    ).toBeDefined()
  })

  it('shows Calculate disabled, pending upstream CA support', async () => {
    const wrapper = mountDialog(modProps)
    await flushPromises()
    const calc = wrapper.find('[data-testid="ep-create-calculate"]')
    expect(calc.exists()).toBe(true)
    expect(calc.attributes('disabled')).toBeDefined()
    expect(calc.attributes('title')).toContain('pending upstream support')
  })
})
