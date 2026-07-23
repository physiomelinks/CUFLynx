import { describe, it, expect, vi, beforeEach } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'

vi.mock('../lib/api', () => ({ uploadParamsForId: vi.fn() }))

import EditParamsDialog from './EditParamsDialog.vue'
import { uploadParamsForId } from '../lib/api'

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
