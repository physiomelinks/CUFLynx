import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import SciNumberInput from './SciNumberInput.vue'

describe('SciNumberInput', () => {
  it('displays a large/small value in scientific notation when not focused', () => {
    const w = mount(SciNumberInput, { props: { modelValue: 1e-9 } })
    expect(w.find('input').element.value).toBe('1e-9')
  })

  it('shows the raw editable number on focus (no reformatting mid-type)', async () => {
    const w = mount(SciNumberInput, { props: { modelValue: 1500000 } })
    const input = w.find('input')
    expect(input.element.value).toBe('1.5e6') // blurred: formatted
    await input.trigger('focus')
    expect(input.element.value).toBe('1500000') // focused: raw
  })

  it('emits the parsed number, accepting scientific-notation input', async () => {
    const w = mount(SciNumberInput, { props: { modelValue: null } })
    const input = w.find('input')
    await input.setValue('2.5e-8')
    expect(w.emitted('update:modelValue').at(-1)).toEqual([2.5e-8])
  })

  it('emits null for a blank field', async () => {
    const w = mount(SciNumberInput, { props: { modelValue: 5 } })
    await w.find('input').setValue('')
    expect(w.emitted('update:modelValue').at(-1)).toEqual([null])
  })

  it('never emits NaN for garbage input', async () => {
    const w = mount(SciNumberInput, { props: { modelValue: 5 } })
    await w.find('input').setValue('abc')
    expect(w.emitted('update:modelValue').at(-1)).toEqual([null])
  })

  it('reformats on blur', async () => {
    const w = mount(SciNumberInput, { props: { modelValue: null } })
    const input = w.find('input')
    await input.trigger('focus')
    await input.setValue('1500000')
    await w.setProps({ modelValue: 1500000 }) // parent applies the emitted value
    await input.trigger('blur')
    expect(input.element.value).toBe('1.5e6')
  })
})
