import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import SearchableSelect from './SearchableSelect.vue'

// Issue #160: the operand and operation lists are too long to scroll. A model's
// operand list is every variable it has; scrolling to find `aortic_root/v`
// among hundreds is the problem, typing three characters is the fix.

const OPTIONS = ['aortic_root/v', 'aortic_root/u', 'heart/q_lv', 'pvn_module/u']

const mountIt = (props = {}) =>
  mount(SearchableSelect, { props: { options: OPTIONS, ...props } })

const open = async (w) => {
  await w.find('[data-testid="searchable-select"]').trigger('click')
  return w
}
const optionTexts = (w) =>
  w.findAll('[data-testid="searchable-select-option"]').map((o) => o.text())

describe('SearchableSelect', () => {
  it('shows the current value when closed, like the select it replaces', () => {
    const w = mountIt({ modelValue: 'heart/q_lv' })
    expect(w.find('[data-testid="searchable-select"]').text()).toBe('heart/q_lv')
  })

  it('shows the placeholder when nothing is chosen', () => {
    expect(mountIt({ modelValue: '' }).find('[data-testid="searchable-select"]').text()).toBe('—')
  })

  it('lists every option when opened', async () => {
    const w = await open(mountIt())
    // The empty choice plus the four real ones.
    expect(optionTexts(w)).toHaveLength(OPTIONS.length + 1)
  })

  it('narrows the list as you type', async () => {
    const w = await open(mountIt())
    await w.find('[data-testid="searchable-select-search"]').setValue('aortic')
    expect(optionTexts(w)).toEqual(['aortic_root/v', 'aortic_root/u'])
  })

  it('matches anywhere in the name, not just the start', async () => {
    // A user thinks in variable names, not in component prefixes.
    const w = await open(mountIt())
    await w.find('[data-testid="searchable-select-search"]').setValue('q_lv')
    expect(optionTexts(w)).toEqual(['heart/q_lv'])
  })

  it('ignores case', async () => {
    const w = await open(mountIt())
    await w.find('[data-testid="searchable-select-search"]').setValue('HEART')
    expect(optionTexts(w)).toEqual(['heart/q_lv'])
  })

  it('emits the chosen value and closes', async () => {
    const w = await open(mountIt())
    const target = w
      .findAll('[data-testid="searchable-select-option"]')
      .find((o) => o.text() === 'heart/q_lv')
    await target.trigger('mousedown')
    expect(w.emitted('update:modelValue')[0]).toEqual(['heart/q_lv'])
    expect(w.find('[data-testid="searchable-select"]').exists()).toBe(true)
  })

  it('always offers the empty choice, so a field can be cleared', async () => {
    const w = await open(mountIt({ modelValue: 'heart/q_lv' }))
    expect(optionTexts(w)).toContain('—')
  })

  it('says so when nothing matches, rather than showing an empty box', async () => {
    const w = await open(mountIt())
    await w.find('[data-testid="searchable-select-search"]').setValue('zzz')
    expect(w.find('[data-testid="searchable-select-empty"]').text()).toContain('zzz')
  })

  it('starts each search fresh rather than keeping the last query', async () => {
    const w = await open(mountIt())
    await w.find('[data-testid="searchable-select-search"]').setValue('heart')
    await w.find('[data-testid="searchable-select-search"]').trigger('keydown', { key: 'Enter' })
    await open(w)
    expect(optionTexts(w)).toHaveLength(OPTIONS.length + 1)
  })

  // Keyboard: the list is navigable without reaching for the mouse.
  it('takes the highlighted option on Enter', async () => {
    const w = await open(mountIt())
    const search = w.find('[data-testid="searchable-select-search"]')
    await search.setValue('aortic')
    await search.trigger('keydown', { key: 'ArrowDown' })
    await search.trigger('keydown', { key: 'Enter' })
    expect(w.emitted('update:modelValue')[0]).toEqual(['aortic_root/v'])
  })

  it('takes a single match on Enter without arrowing to it', async () => {
    const w = await open(mountIt())
    const search = w.find('[data-testid="searchable-select-search"]')
    await search.setValue('q_lv')
    await search.trigger('keydown', { key: 'Enter' })
    expect(w.emitted('update:modelValue')[0]).toEqual(['heart/q_lv'])
  })

  it('does not guess when several match and none is highlighted', async () => {
    const w = await open(mountIt())
    const search = w.find('[data-testid="searchable-select-search"]')
    await search.setValue('aortic')
    await search.trigger('keydown', { key: 'Enter' })
    expect(w.emitted('update:modelValue')).toBeFalsy()
  })

  it('closes on Escape without choosing anything', async () => {
    const w = await open(mountIt({ modelValue: 'heart/q_lv' }))
    await w.find('[data-testid="searchable-select-search"]').trigger('keydown', { key: 'Escape' })
    expect(w.emitted('update:modelValue')).toBeFalsy()
    expect(w.find('[data-testid="searchable-select"]').text()).toBe('heart/q_lv')
  })

  it('renders labels through labelFor, so a value can read differently', async () => {
    const w = await open(
      mountIt({ options: ['', 'max'], labelFor: (v) => v || '(none)' }),
    )
    expect(optionTexts(w)).toContain('(none)')
  })

  it('lets the caller mark options, e.g. as non-differentiable', async () => {
    const w = await open(
      mountIt({ options: ['max', 'spike'], classFor: (v) => (v === 'spike' ? 'flagged' : '') }),
    )
    const flagged = w
      .findAll('[data-testid="searchable-select-option"]')
      .find((o) => o.text() === 'spike')
    expect(flagged.classes()).toContain('flagged')
  })

  it('uses the caller test id, so two on one row are distinguishable', () => {
    const w = mountIt({ testid: 'eo-operand' })
    expect(w.find('[data-testid="eo-operand"]').exists()).toBe(true)
  })

  it('does not open when disabled', async () => {
    const w = mountIt({ disabled: true })
    await w.find('[data-testid="searchable-select"]').trigger('click')
    expect(w.find('[data-testid="searchable-select-search"]').exists()).toBe(false)
  })
})
