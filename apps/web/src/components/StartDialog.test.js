import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'

import StartDialog from './StartDialog.vue'
import { PHLYNX_URL, PMR_URL, EXAMPLE_MODELS } from '../lib/examples'

// Render the Dialog's default slot inline when visible so the body is testable
// without PrimeVue's overlay/teleport machinery.
const DialogStub = {
  props: ['visible'],
  template: '<div v-if="visible" data-testid="dialog"><slot /></div>',
}
const ButtonStub = {
  props: ['label', 'icon', 'size', 'text'],
  emits: ['click'],
  template:
    '<button v-bind="$attrs" @click="$emit(\'click\')">{{ label }}</button>',
}
const stubs = { Dialog: DialogStub, Button: ButtonStub }

describe('StartDialog', () => {
  it('lists the 3compartment example and links to PhLynx and the PMR', () => {
    const wrapper = mount(StartDialog, { props: { visible: true }, global: { stubs } })
    const link = wrapper.find('[data-testid="start-phlynx-link"]')
    expect(link.exists()).toBe(true)
    expect(link.attributes('href')).toBe(PHLYNX_URL)
    // Download-from-PMR option links to the Physiome Model Repository.
    const pmr = wrapper.find('[data-testid="start-pmr-link"]')
    expect(pmr.exists()).toBe(true)
    expect(pmr.attributes('href')).toBe(PMR_URL)
    // Every data-driven example gets a button; the 3compartment one is present.
    expect(wrapper.find('[data-testid="start-example-3compartment"]').exists()).toBe(true)
    expect(wrapper.findAll('.example-list li')).toHaveLength(EXAMPLE_MODELS.length)
  })

  it('emits select-example and closes when an example is chosen', async () => {
    const wrapper = mount(StartDialog, { props: { visible: true }, global: { stubs } })
    await wrapper.find('[data-testid="start-example-3compartment"]').trigger('click')
    expect(wrapper.emitted('select-example')[0][0]).toMatchObject({
      name: '3compartment',
      filename: '3compartment_flat.cellml',
    })
    expect(wrapper.emitted('update:visible')[0][0]).toBe(false)
  })

  it('renders nothing until visible', () => {
    const wrapper = mount(StartDialog, { props: { visible: false }, global: { stubs } })
    expect(wrapper.find('[data-testid="dialog"]').exists()).toBe(false)
  })
})
