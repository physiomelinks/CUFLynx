import { describe, it, expect } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import SaveParamsDialog from './SaveParamsDialog.vue'

const DialogStub = {
  props: ['visible'],
  template: '<div v-if="visible"><slot /><slot name="footer" /></div>',
}
const ButtonStub = {
  props: ['label', 'icon', 'disabled', 'text'],
  emits: ['click'],
  template: '<button v-bind="$attrs" :disabled="disabled" @click="$emit(\'click\')">{{ label }}</button>',
}
const InputTextStub = {
  props: ['modelValue'],
  emits: ['update:modelValue'],
  template: '<input :value="modelValue" @input="$emit(\'update:modelValue\', $event.target.value)" />',
}
const SelectStub = {
  props: ['modelValue', 'options', 'optionLabel', 'optionValue'],
  emits: ['update:modelValue'],
  template:
    '<select :value="modelValue" @change="$emit(\'update:modelValue\', $event.target.value)">' +
    '<option v-for="o in options" :key="o.value" :value="o.value">{{ o.label }}</option></select>',
}
const stubs = { Dialog: DialogStub, Button: ButtonStub, InputText: InputTextStub, Select: SelectStub }

function mountDialog(props = {}) {
  return mount(SaveParamsDialog, { props: { visible: true, ...props }, global: { stubs } })
}

describe('SaveParamsDialog (#106)', () => {
  it('defaults to manual_params.npy and shows the output dir', () => {
    const wrapper = mountDialog({ outputDir: '/out' })
    const preview = wrapper.get('[data-testid="save-params-preview"]').text()
    expect(preview).toContain('manual_params.npy')
    expect(preview).toContain('/out')
  })

  it('saves with the .npy filename by default and closes', async () => {
    const wrapper = mountDialog()
    await wrapper.get('[data-testid="save-params-confirm"]').trigger('click')
    expect(wrapper.emitted('save').at(-1)[0]).toEqual({ filename: 'manual_params.npy' })
    expect(wrapper.emitted('update:visible').at(-1)[0]).toBe(false)
  })

  it('switching the format to csv changes the extension', async () => {
    const wrapper = mountDialog()
    await wrapper.get('[data-testid="save-params-format"]').setValue('csv')
    expect(wrapper.get('[data-testid="save-params-preview"]').text()).toContain('manual_params.csv')
    await wrapper.get('[data-testid="save-params-confirm"]').trigger('click')
    expect(wrapper.emitted('save').at(-1)[0]).toEqual({ filename: 'manual_params.csv' })
  })

  it('honours a custom base name, keeping the chosen format extension', async () => {
    const wrapper = mountDialog()
    await wrapper.get('[data-testid="save-params-name"]').setValue('my_run')
    await wrapper.get('[data-testid="save-params-confirm"]').trigger('click')
    expect(wrapper.emitted('save').at(-1)[0]).toEqual({ filename: 'my_run.npy' })
  })

  it('disables save when the name is blank', async () => {
    const wrapper = mountDialog()
    await wrapper.get('[data-testid="save-params-name"]').setValue('   ')
    expect(wrapper.get('[data-testid="save-params-confirm"]').attributes('disabled')).toBeDefined()
  })
})
