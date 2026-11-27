import { describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'
import { defineComponent, h, ref, withDirectives } from 'vue'
import { vFold } from './vFold'

function folding(open) {
  const value = ref(open)
  const C = defineComponent({
    setup: () => () => withDirectives(h('section', { class: 'part' }, [h('h3', 'Charts'), h('p', 'body')]),
      [[vFold, value.value]]),
  })
  return { wrapper: mount(C), value }
}
const folded = (w) => 'folded' in w.find('section').element.dataset

describe('v-fold', () => {
  it('folds and unfolds its section from its heading, by click or key', async () => {
    const { wrapper } = folding(undefined)
    const head = wrapper.find('h3')
    expect(head.classes()).toContain('fold-head')
    expect(folded(wrapper)).toBe(false)
    await head.trigger('click')
    expect(folded(wrapper)).toBe(true)
    expect(head.attributes('aria-expanded')).toBe('false')
    await head.trigger('keydown', { key: 'Enter' })
    expect(folded(wrapper)).toBe(false)
  })

  it('starts as its value says, and goes back to it only when the value changes', async () => {
    const { wrapper, value } = folding(false)
    expect(folded(wrapper)).toBe(true)
    await wrapper.find('h3').trigger('click')            // the user opens it
    expect(folded(wrapper)).toBe(false)
    value.value = true                                    // another step selected: its default
    await wrapper.vm.$nextTick()
    expect(folded(wrapper)).toBe(false)
    value.value = false
    await wrapper.vm.$nextTick()
    expect(folded(wrapper)).toBe(true)
  })
})
