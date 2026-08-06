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
          description: 'Centre of the Gaussian.' },
        { name: 'prior_std', type: 'float', default: null, positive: true, role: 'scale',
          description: 'Standard deviation.' },
      ],
    },
    {
      value: 'exponential', label: 'Exponential', description: 'decays',
      supports_unbounded: false,
      params: [{ name: 'prior_lambda', type: 'float', default: 1.0, positive: true,
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
    '<button :disabled="disabled" v-bind="$attrs" @click="$emit(\'click\')">{{ label }}</button>',
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

beforeEach(() => {
  uploadParamsForId.mockReset()
  getConfig.mockReset()
  getConfig.mockResolvedValue({ param_prior_types: PRIOR_TYPES })
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
    const checks = wrapper.findAll('input[type="checkbox"]')
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
    await wrapper.findAll('input[type="checkbox"]')[0].setValue(false)
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

  it('on Save: downloads a dated CSV, applies it, and emits saved', async () => {
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

    // applied via the existing upload endpoint with a File + modelId
    expect(uploadParamsForId).toHaveBeenCalledOnce()
    const [fileArg, idArg] = uploadParamsForId.mock.calls[0]
    expect(idArg).toBe('abc')
    expect(fileArg).toBeInstanceOf(File)
    expect(fileArg.name).toMatch(/^p_\d{6}\.csv$/) // <stem>_<yymmdd>.csv

    // emits saved with parsed params + versioned filename, then closes
    const saved = wrapper.emitted('saved')[0][0]
    expect(saved.params).toEqual([{ qname: 'v/a' }])
    expect(saved.filename).toMatch(/^p_\d{6}\.csv$/)
    expect(wrapper.emitted('update:visible').at(-1)).toEqual([false])

    clickSpy.mockRestore()
  })

  it('expands an annotation field, edits it, and writes it into the saved CSV', async () => {
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
    const text = await new Promise((resolve) => {
      const reader = new FileReader()
      reader.onload = () => resolve(reader.result)
      reader.readAsText(fileArg)
    })
    expect(text.split('\n')[0]).toContain('comment')
    expect(text).toContain('range from Dash 2016')
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

  it('writes the chosen prior into the CSV', async () => {
    const wrapper = mountDialog({
      currentParams: [{ qname: 'v/a', min: 1, max: 2, prior: 'normal' }],
    })
    await flushPromises()
    await wrapper.find('[data-testid="ep-save"]').trigger('click')
    await flushPromises()

    const [file] = uploadParamsForId.mock.calls[0]
    const text = await readFile(file)
    expect(text.split('\n')[0]).toContain('prior')
    expect(text).toContain('normal')
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

  it('renders a loaded value and writes it into the CSV', async () => {
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

    const text = await readFile(uploadParamsForId.mock.calls[0][0])
    expect(text.split('\n')[0]).toContain('prior_mean')
    expect(text).toContain('9.5')
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

    const text = await readFile(uploadParamsForId.mock.calls[0][0])
    const header = text.split('\n')[0].split(',')
    const row = text.split('\n')[1].split(',')
    expect(header).toContain('unbounded')
    expect(row[header.indexOf('unbounded')]).toBe('true')
    expect(row[header.indexOf('min')]).toBe('')
    expect(row[header.indexOf('max')]).toBe('')
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
