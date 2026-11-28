import { describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'
import SettingsSections from './SettingsSections.vue'

function sections(props) {
  return mount(SettingsSections, {
    props,
    slots: { default: `<template #default="{ item }"><i class="f" :data-name="item.name">{{ item.name }}</i></template>` },
  })
}

describe('SettingsSections', () => {
  it('shows the standard settings, then the advanced ones in a closed section', () => {
    const w = sections({ tab: 'calibration', items: [
      { name: 'num_calls_to_function' }, { name: 'num_elite' }, { name: 'num_survivors' }, { name: 'num_cores', own: true }] })
    const adv = w.find('[data-testid="calibration-advanced"]')
    expect(adv.element.tagName).toBe('DETAILS')
    expect(adv.attributes('open')).toBeUndefined()
    expect(adv.findAll('.f').map((f) => f.text())).toEqual(['num_elite', 'num_survivors'])
    const outside = w.findAll('.f').filter((f) => !adv.element.contains(f.element))
    expect(outside.map((f) => f.text())).toEqual(['num_calls_to_function', 'num_cores'])
    expect(adv.find('summary').text()).toContain('(2)')
  })

  it('says how many advanced settings are changed, so a closed section hides none', () => {
    const w = sections({ tab: 'calibration', items: [{ name: 'num_elite' }, { name: 'num_survivors' }],
      changed: (item) => item.name === 'num_survivors' })
    expect(w.find('[data-testid="calibration-advanced-changed"]').text()).toContain('1 changed')
  })

  it('has no Advanced section when nothing in the form is advanced', () => {
    const w = sections({ tab: 'calibration', items: [{ name: 'num_calls_to_function' }] })
    expect(w.find('[data-testid="calibration-advanced"]').exists()).toBe(false)
  })
})
