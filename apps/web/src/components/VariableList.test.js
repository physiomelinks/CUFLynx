import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import VariableList from './VariableList.vue'

const stubs = {
  Button: {
    props: ['label', 'disabled'],
    emits: ['click'],
    template: '<button v-bind="$attrs" :disabled="disabled" @click="$emit(\'click\')">{{ label }}</button>',
  },
}

describe('VariableList', () => {
  it('opens the LaTeX symbols editor', async () => {
    const w = mount(VariableList, {
      props: { variables: { params: ['parameters/p'], odes: [], algebraic: [] } },
      global: { stubs },
    })
    await w.find('[data-testid="edit-variable-mapping"]').trigger('click')
    expect(w.emitted('edit-symbols')).toHaveLength(1)
  })

  it('has nothing to edit without a model', () => {
    const w = mount(VariableList, { global: { stubs } })
    expect(w.find('[data-testid="edit-variable-mapping"]').attributes('disabled')).toBeDefined()
  })
})
