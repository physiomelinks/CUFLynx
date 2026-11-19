import { describe, expect, it } from 'vitest'
import { ADVANCED, appliesToMethod, currentDefault, differs, isAdvanced, splitSettings } from './advancedSettings'

describe('advancedSettings', () => {
  it('lists, per tab, CA option names and its own fields apart, each once', () => {
    for (const [tab, entry] of Object.entries(ADVANCED)) {
      if (tab === 'about') continue
      for (const key of ['ca', 'cuflynx']) {
        expect(Array.isArray(entry[key]), `${tab}.${key}`).toBe(true)
        expect(new Set(entry[key]).size, `${tab}.${key} has a duplicate`).toBe(entry[key].length)
      }
    }
  })

  it('keeps Cores a standard setting on every tab (Finbar, 2026-11-20)', () => {
    for (const [tab, entry] of Object.entries(ADVANCED)) {
      if (tab === 'about') continue
      expect(entry.cuflynx, tab).not.toContain('num_cores')
      expect(entry.ca, tab).not.toContain('num_cores')
    }
  })

  it('a setting not in the table is standard, so a new CA option is too', () => {
    const items = [{ name: 'num_calls_to_function' }, { name: 'num_elite' },
      { name: 'an_option_ca_adds_later' }, { name: 'num_cores', own: true }]
    const { standard, advanced } = splitSettings('calibration', items)
    expect(standard.map((i) => i.name)).toEqual(['num_calls_to_function', 'an_option_ca_adds_later', 'num_cores'])
    expect(advanced.map((i) => i.name)).toEqual(['num_elite'])
  })

  it("keeps a tab's own field apart from a CA option of the same name", () => {
    // Sensitivity: the tab's own include_prediction_items box is advanced; a CA option of
    // that name would be decided by the `ca` list, which does not name it.
    expect(isAdvanced('sensitivity', { name: 'include_prediction_items', own: true })).toBe(true)
    expect(isAdvanced('sensitivity', { name: 'include_prediction_items' })).toBe(false)
    expect(isAdvanced('no_such_tab', { name: 'x' })).toBe(false)
  })

  it("follows CA's `methods` marker; an unmarked option keeps to the form's method", () => {
    expect(appliesToMethod({ methods: ['laplace'] }, 'mcmc', 'mcmc')).toBe(false)
    expect(appliesToMethod({ methods: ['laplace'] }, 'laplace', 'mcmc')).toBe(true)
    expect(appliesToMethod({}, 'mcmc', 'mcmc')).toBe(true)
    expect(appliesToMethod({}, 'laplace', 'mcmc')).toBe(false)
    expect(appliesToMethod({}, 'laplace')).toBe(true)
  })

  it('counts a value as changed only when it differs from the default', () => {
    expect(differs(undefined, null)).toBe(false)
    expect(differs('', null)).toBe(false)
    expect(differs(12, 12)).toBe(false)
    expect(differs(13, 12)).toBe(true)
    expect(differs(0.5, null)).toBe(true)
    expect(differs({ a: 1 }, { a: 1 })).toBe(false)
    expect(currentDefault({ default: 12, debug_default: 4 }, true)).toBe(4)
    expect(currentDefault({ default: 12, debug_default: 4 }, false)).toBe(12)
    expect(currentDefault({ default: 12 }, true)).toBe(12)
  })
})
