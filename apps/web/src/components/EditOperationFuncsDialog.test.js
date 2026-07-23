import { describe, it, expect, vi, beforeEach } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'

vi.mock('../lib/api', () => ({
  getUserFuncs: vi.fn(),
  saveUserFunc: vi.fn(),
  deleteUserFunc: vi.fn(),
}))

import EditOperationFuncsDialog from './EditOperationFuncsDialog.vue'
import { getUserFuncs, saveUserFunc, deleteUserFunc } from '../lib/api'

const DialogStub = {
  props: ['visible'],
  template: '<div v-if="visible"><slot /><slot name="footer" /></div>',
}
const ButtonStub = {
  props: ['label', 'icon', 'disabled', 'size', 'text', 'outlined', 'rounded', 'title', 'loading', 'severity'],
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

const OP_TEMPLATES = {
  basic: 'def my_operation(x, series_output=False):\n    return x\n',
  multi_operand: 'def my_operation(x, y, series_output=False):\n    return x - y\n',
  kwargs: 'def my_operation(x, threshold=0.0, series_output=False):\n    return x\n',
}
const OP_LISTED = {
  kind: 'operation',
  functions: [{ name: 'spread', source: 'def spread(x, series_output=False):\n    return x\n' }],
  templates: OP_TEMPLATES,
  template: OP_TEMPLATES.basic,
  available: true,
}
const COST_LISTED = {
  kind: 'cost',
  functions: [{ name: 'my_cost', source: 'def my_cost(o, d, s, w):\n    return 0.0\n' }],
  templates: {
    basic: 'def my_cost(output, desired_mean, std, weight):\n    return 0.0\n',
    MLE: '@is_MLE\ndef my_mle(output, desired_mean, std, weight):\n    return 0.0\n',
  },
  template: 'def my_cost(output, desired_mean, std, weight):\n    return 0.0\n',
  available: true,
}

function mountDialog(props = {}) {
  return mount(EditOperationFuncsDialog, {
    props: { visible: true, ...props },
    global: { stubs },
  })
}

beforeEach(() => {
  getUserFuncs.mockReset().mockImplementation((kind) =>
    Promise.resolve(kind === 'cost' ? COST_LISTED : OP_LISTED),
  )
  saveUserFunc.mockReset()
  deleteUserFunc.mockReset()
})

describe('EditOperationFuncsDialog', () => {
  it('lists existing operations and prefills the first template on open', async () => {
    const wrapper = mountDialog()
    await flushPromises()
    expect(getUserFuncs).toHaveBeenCalledWith('operation', '')
    const items = wrapper.findAll('[data-testid="of-item"]')
    expect(items).toHaveLength(1)
    expect(items[0].text()).toBe('spread')
    expect(wrapper.get('[data-testid="of-source"]').element.value).toContain('def my_operation')
  })

  it('offers a tab per template and swaps the editor when one is picked', async () => {
    const wrapper = mountDialog()
    await flushPromises()
    // A tab per operation template.
    expect(wrapper.find('[data-testid="of-template-basic"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="of-template-multi_operand"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="of-template-kwargs"]').exists()).toBe(true)
    await wrapper.get('[data-testid="of-template-kwargs"]').trigger('click')
    expect(wrapper.get('[data-testid="of-source"]').element.value).toContain('threshold=0.0')
  })

  it('switches to the cost kind and loads cost funcs + templates', async () => {
    const wrapper = mountDialog()
    await flushPromises()
    await wrapper.get('[data-testid="of-kind-cost"]').trigger('click')
    await flushPromises()
    expect(getUserFuncs).toHaveBeenCalledWith('cost', '')
    expect(wrapper.get('[data-testid="of-item"]').text()).toBe('my_cost')
    expect(wrapper.find('[data-testid="of-template-MLE"]').exists()).toBe(true)
    // Editor prefilled with the cost basic template.
    expect(wrapper.get('[data-testid="of-source"]').element.value).toContain('desired_mean')
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

  it('sends kind + name + code on save and emits saved', async () => {
    const updated = { ...OP_LISTED, functions: [...OP_LISTED.functions, { name: 'my_op', source: 'x' }] }
    saveUserFunc.mockResolvedValue(updated)
    const wrapper = mountDialog()
    await flushPromises()
    await wrapper.get('[data-testid="of-name-input"]').setValue('my_op')
    await wrapper.get('[data-testid="of-source"]').setValue('def my_op(x):\n    return x\n')
    await wrapper.get('[data-testid="of-save"]').trigger('click')
    await flushPromises()
    expect(saveUserFunc).toHaveBeenCalledWith('operation', 'my_op', 'def my_op(x):\n    return x\n', '')
    expect(wrapper.emitted('saved')).toBeTruthy()
    expect(wrapper.findAll('[data-testid="of-item"]')).toHaveLength(2)
  })

  it('saves a cost func with the cost kind', async () => {
    saveUserFunc.mockResolvedValue({ ...COST_LISTED })
    const wrapper = mountDialog()
    await flushPromises()
    await wrapper.get('[data-testid="of-kind-cost"]').trigger('click')
    await flushPromises()
    await wrapper.get('[data-testid="of-name-input"]').setValue('my_cost2')
    await wrapper.get('[data-testid="of-source"]').setValue('def my_cost2(o, d, s, w):\n    return 0.0\n')
    await wrapper.get('[data-testid="of-save"]').trigger('click')
    await flushPromises()
    expect(saveUserFunc).toHaveBeenCalledWith('cost', 'my_cost2', 'def my_cost2(o, d, s, w):\n    return 0.0\n', '')
  })

  it('loads an existing func into the editor when selected', async () => {
    const wrapper = mountDialog()
    await flushPromises()
    await wrapper.get('[data-testid="of-item"]').trigger('click')
    expect(wrapper.get('[data-testid="of-name-input"]').element.value).toBe('spread')
    expect(wrapper.get('[data-testid="of-source"]').element.value).toContain('def spread')
  })

  it('surfaces the backend error detail on a failed save', async () => {
    saveUserFunc.mockRejectedValue({ response: { data: { detail: "'def' is reserved" } } })
    const wrapper = mountDialog()
    await flushPromises()
    await wrapper.get('[data-testid="of-name-input"]').setValue('my_op')
    await wrapper.get('[data-testid="of-save"]').trigger('click')
    await flushPromises()
    expect(wrapper.get('[data-testid="of-error"]').text()).toContain("'def' is reserved")
  })

  it('deletes a func with its kind', async () => {
    deleteUserFunc.mockResolvedValue({ ...OP_LISTED, functions: [] })
    const wrapper = mountDialog()
    await flushPromises()
    await wrapper.get('[data-testid="of-delete"]').trigger('click')
    await flushPromises()
    expect(deleteUserFunc).toHaveBeenCalledWith('operation', 'spread', '')
    expect(wrapper.findAll('[data-testid="of-item"]')).toHaveLength(0)
  })
})
