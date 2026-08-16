import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'

import StartDialog from './StartDialog.vue'
import {
  PHLYNX_URL,
  PMR_URL,
  EXAMPLE_MODELS,
  EXTERNAL_PYTHON_TUTORIAL_URL,
} from '../lib/examples'

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
      filename: '3compartment.omex',
    })
    expect(wrapper.emitted('update:visible')[0][0]).toBe(false)
  })

  it('renders nothing until visible', () => {
    const wrapper = mount(StartDialog, { props: { visible: false }, global: { stubs } })
    expect(wrapper.find('[data-testid="dialog"]').exists()).toBe(false)
  })
})

// The fourth way in: the user brings the solver rather than a model description.
// The dialog is where the other three starting points live, so this one belongs
// beside them -- and the contract is a page of code, hence a link to the tutorial
// rather than an explanation in the dialog.
describe('StartDialog External Python section', () => {
  it('offers the section and links to the tutorial', () => {
    const wrapper = mount(StartDialog, { props: { visible: true }, global: { stubs } })
    expect(wrapper.text()).toContain('External Python')
    const link = wrapper.find('[data-testid="start-external-python-link"]')
    expect(link.exists()).toBe(true)
    expect(link.attributes('href')).toBe(EXTERNAL_PYTHON_TUTORIAL_URL)
    // Same treatment as the PhLynx / PMR links: opens away from the app.
    expect(link.attributes('target')).toBe('_blank')
    expect(link.classes()).toContain('phlynx-link')
  })

  it('summarises the three steps to getting one running', () => {
    const wrapper = mount(StartDialog, { props: { visible: true }, global: { stubs } })
    const steps = wrapper.find('[data-testid="start-external-python-steps"]')
    expect(steps.findAll('li')).toHaveLength(3)
    // The three things that are not guessable: the export name, where the file
    // goes, and that the interpreter has to be the one with the dependencies.
    expect(steps.text()).toContain('SIM_HELPER')
    expect(steps.text()).toContain('.py')
    expect(steps.text()).toContain('Settings')
  })
})

describe('tour anchors', () => {
  it('marks each of the four ways in', () => {
    const wrapper = mount(StartDialog, { props: { visible: true }, global: { stubs } })
    for (const id of [
      'start-build-your-own',
      'start-pmr',
      'start-example',
      'start-external-python',
    ]) {
      expect(wrapper.find(`[data-testid="${id}"]`).exists()).toBe(true)
    }
  })
})
