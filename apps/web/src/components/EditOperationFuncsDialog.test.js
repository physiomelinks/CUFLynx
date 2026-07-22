import { describe, it, expect, vi, beforeEach } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'

vi.mock('../lib/api', () => ({
  getUserOperations: vi.fn(),
  saveUserOperation: vi.fn(),
  deleteUserOperation: vi.fn(),
}))

import EditOperationFuncsDialog from './EditOperationFuncsDialog.vue'
import { getUserOperations, saveUserOperation, deleteUserOperation } from '../lib/api'

const DialogStub = {
  props: ['visible'],
  template: '<div v-if="visible"><slot /><slot name="footer" /></div>',
}
const ButtonStub = {
  props: ['label', 'icon', 'disabled', 'size', 'text', 'rounded', 'title', 'loading', 'severity'],
  emits: ['click'],
  template:
    '<button v-bind="$attrs" :disabled="disabled" @click="$emit(\'click\', $event)">{{ label }}</button>',
}
const InputTextStub = {
  props: ['modelValue', 'invalid'],
  emits: ['update:modelValue'],
  template:
    '<input :value="modelValue" @input="$emit(\'update:modelValue\', $event.target.value)" />',
}
const MessageStub = { template: '<div class="msg"><slot /></div>' }
const stubs = { Dialog: DialogStub, Button: ButtonStub, InputText: InputTextStub, Message: MessageStub }

const LISTED = {
  functions: [
    { name: 'spread', source: 'def spread(x, series_output=False):\n    return x\n' },
  ],
  template: 'def my_operation(x, series_output=False):\n    return x\n',
  available: true,
}

function mountDialog(props = {}) {
  return mount(EditOperationFuncsDialog, {
    props: { visible: true, ...props },
    global: { stubs },
  })
}

beforeEach(() => {
  getUserOperations.mockReset().mockResolvedValue(LISTED)
  saveUserOperation.mockReset()
  deleteUserOperation.mockReset()
})

describe('EditOperationFuncsDialog', () => {
  it('lists existing funcs and prefills the template on open', async () => {
    const wrapper = mountDialog()
    await flushPromises()
    expect(getUserOperations).toHaveBeenCalled()
    const items = wrapper.findAll('[data-testid="of-item"]')
    expect(items).toHaveLength(1)
    expect(items[0].text()).toBe('spread')
    // New (default) editor prefilled with the template.
    expect(wrapper.get('[data-testid="of-source"]').element.value).toContain('def my_operation')
  })

  it('disables save for an invalid name and enables it for a valid one', async () => {
    const wrapper = mountDialog()
    await flushPromises()
    const saveBtn = () => wrapper.get('[data-testid="of-save"]')

    await wrapper.get('[data-testid="of-name-input"]').setValue('1bad')
    expect(saveBtn().attributes('disabled')).toBeDefined()

    await wrapper.get('[data-testid="of-name-input"]').setValue('my_op')
    expect(saveBtn().attributes('disabled')).toBeUndefined()
  })

  it('sends name + code on save and emits saved', async () => {
    const updated = { ...LISTED, functions: [...LISTED.functions, { name: 'my_op', source: 'x' }] }
    saveUserOperation.mockResolvedValue(updated)
    const wrapper = mountDialog()
    await flushPromises()

    await wrapper.get('[data-testid="of-name-input"]').setValue('my_op')
    await wrapper.get('[data-testid="of-source"]').setValue('def my_op(x):\n    return x\n')
    await wrapper.get('[data-testid="of-save"]').trigger('click')
    await flushPromises()

    expect(saveUserOperation).toHaveBeenCalledWith('my_op', 'def my_op(x):\n    return x\n')
    expect(wrapper.emitted('saved')).toBeTruthy()
    expect(wrapper.findAll('[data-testid="of-item"]')).toHaveLength(2)
  })

  it('loads an existing func into the editor when selected', async () => {
    const wrapper = mountDialog()
    await flushPromises()
    await wrapper.get('[data-testid="of-item"]').trigger('click')
    expect(wrapper.get('[data-testid="of-name-input"]').element.value).toBe('spread')
    expect(wrapper.get('[data-testid="of-source"]').element.value).toContain('def spread')
  })

  it('surfaces the backend error detail on a failed save', async () => {
    saveUserOperation.mockRejectedValue({ response: { data: { detail: "'def' is reserved" } } })
    const wrapper = mountDialog()
    await flushPromises()
    await wrapper.get('[data-testid="of-name-input"]').setValue('my_op')
    await wrapper.get('[data-testid="of-save"]').trigger('click')
    await flushPromises()
    expect(wrapper.get('[data-testid="of-error"]').text()).toContain("'def' is reserved")
  })

  it('deletes a func', async () => {
    deleteUserOperation.mockResolvedValue({ ...LISTED, functions: [] })
    const wrapper = mountDialog()
    await flushPromises()
    await wrapper.get('[data-testid="of-delete"]').trigger('click')
    await flushPromises()
    expect(deleteUserOperation).toHaveBeenCalledWith('spread')
    expect(wrapper.findAll('[data-testid="of-item"]')).toHaveLength(0)
  })
})
