import { describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'
import EmulatorBundleDialog from './EmulatorBundleDialog.vue'

// PrimeVue's Dialog teleports and only renders when visible; a passthrough stub
// keeps the rows in the wrapper so the content can be asserted on directly.
const DialogStub = {
  props: ['visible', 'header'],
  template: '<div v-if="visible"><slot /><slot name="footer" /></div>',
}
const ButtonStub = {
  props: ['disabled', 'label'],
  template: '<button :disabled="disabled" v-bind="$attrs">{{ label }}</button>',
}
const stubs = { Dialog: DialogStub, Button: ButtonStub }

const CHOICES = [
  {
    dir: '/study/emulators/s_two_phase_RBF',
    name: 's_two_phase_RBF',
    model_name: 'two_phase_RadialBasisFunctions',
    worst_r2: 0.3066,
    median_r2: 0.6335,
    num_features: 36,
    num_samples: 12000,
    num_stages: 3,
    reusable: true,
  },
  {
    dir: '/study/emulators/s_two_phase_MLP',
    name: 's_two_phase_MLP',
    model_name: 'two_phase_MLP',
    worst_r2: 0.2836,
    median_r2: 0.7017,
    num_features: 36,
    num_samples: 12000,
    num_stages: 3,
    reusable: true,
  },
]

function mountDialog(props = {}) {
  return mount(EmulatorBundleDialog, {
    props: { visible: true, choices: CHOICES, currentDir: CHOICES[0].dir, chosen: true, ...props },
    global: { stubs },
  })
}

describe('EmulatorBundleDialog', () => {
  it('lists every emulator the study could be read with', () => {
    const text = mountDialog().text()
    expect(text).toContain('RadialBasisFunctions')
    expect(text).toContain('MLP')
  })

  it('shows the two numbers the choice turns on', () => {
    // The worst feature bounds what a bundle can be trusted for; the median says
    // whether one bad feature or a uniformly poor fit produced it.
    const text = mountDialog().text()
    expect(text).toContain('0.3066')
    expect(text).toContain('0.6335')
  })

  it('says which bundle is currently in use', () => {
    expect(mountDialog().text()).toContain('in use')
  })

  it('warns when the bundle on screen was fallen back to rather than chosen', () => {
    // "Most recent" and "chosen" look identical on screen, and only one is a
    // decision. Recency is not a reason to trust an emulator.
    const text = mountDialog({ chosen: false }).text()
    expect(text).toContain('No choice has been made')
  })

  it('does not warn once a choice has been made', () => {
    expect(mountDialog({ chosen: true }).text()).not.toContain('No choice has been made')
  })

  it('emits the picked directory and closes', async () => {
    const wrapper = mountDialog()
    await wrapper.findAll('input[type="radio"]')[1].setValue()
    await wrapper.findAll('button')[1].trigger('click')
    expect(wrapper.emitted('select')[0]).toEqual([CHOICES[1].dir])
    expect(wrapper.emitted('update:visible').at(-1)).toEqual([false])
  })

  it('flags a bundle whose directory name and reported family disagree', () => {
    // What a refit that died after its metadata was copied in looks like: the
    // name says one family, the metadata reports another, and each alone reads
    // as a healthy bundle.
    const wrapper = mountDialog({
      choices: [{ ...CHOICES[1], model_name: 'two_phase_RadialBasisFunctions' }],
    })
    expect(wrapper.text()).toContain('name/type mismatch')
  })

  it('does not flag a bundle whose name and family agree', () => {
    expect(mountDialog().text()).not.toContain('name/type mismatch')
  })

  it('accepts RBF as shorthand for RadialBasisFunctions', () => {
    // The directory the sweep writes is `..._two_phase_RBF` while CA reports the
    // full family name; treating that as a mismatch would flag every healthy run.
    const wrapper = mountDialog({ choices: [CHOICES[0]] })
    expect(wrapper.text()).not.toContain('name/type mismatch')
  })

  it('renders a missing score as a dash rather than as zero', () => {
    const wrapper = mountDialog({
      choices: [{ ...CHOICES[0], worst_r2: null, median_r2: null }],
    })
    expect(wrapper.text()).toContain('—')
  })

  it('marks a bundle with no saved samples as not refittable', () => {
    const wrapper = mountDialog({ choices: [{ ...CHOICES[0], reusable: false }] })
    expect(wrapper.text()).toContain('not refittable')
  })
})
