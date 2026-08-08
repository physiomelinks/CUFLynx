import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import CostSensitivityBar from './CostSensitivityBar.vue'

/**
 * Issue #188: the cost line said what the parameters cost; this says which
 * parameter the number is about.
 */
const payload = (over = {}) => ({
  cost: 36.8,
  rel_step: 0.001,
  method: 'central finite difference',
  n_simulations: 7,
  unavailable: null,
  params: [
    { name: 'a/alpha', value: 1, derivative: 10, elasticity: 0.5, reason: null },
    { name: 'a/beta', value: 2, derivative: -1, elasticity: -4, reason: null },
    { name: 'a/gamma', value: 3, derivative: 0, elasticity: 0.01, reason: null },
  ],
  ...over,
})

const mountBar = (props = {}) =>
  mount(CostSensitivityBar, { props: { result: payload(), ...props } })

describe('CostSensitivityBar (#188)', () => {
  it('keeps the parameter column\'s order rather than ranking by magnitude', () => {
    // The panel sits beside the parameter list; reordering it means finding a
    // parameter is a re-scan instead of a glance across. The bar lengths carry
    // the ranking without moving any row.
    const rows = mountBar().findAll('[data-testid="cost-sens-row"]')
    expect(rows.map((r) => r.find('.cost-sens-name').text())).toEqual([
      'a/alpha',
      'a/beta',
      'a/gamma',
    ])
  })

  it('still scales the bars by magnitude, so the ranking is not lost', () => {
    // beta is the largest at |-4|, so it is the full-width bar even though it is
    // no longer the first row.
    const rows = mountBar().findAll('[data-testid="cost-sens-row"]')
    const widths = rows.map((r) => r.find('.cost-sens-fill').attributes('style'))
    expect(widths[1]).toContain('width: 100%')
  })

  it('says which way to drag, not only how much', () => {
    const rows = mountBar().findAll('[data-testid="cost-sens-row"]')
    // Rows are in the parameter column's order: alpha (+0.5) then beta (-4).
    // A negative elasticity means the cost falls as the parameter rises.
    expect(rows[0].text()).toContain('decrease to improve')
    expect(rows[1].text()).toContain('increase to improve')
  })

  it('names the quantity and the step it used', () => {
    // "sensitivity" without units is not a number anyone can act on; a relative
    // one is comparable across parameters, and the FD step changes the answer.
    const text = mountBar().find('[data-testid="cost-sens-method"]').text()
    expect(text).toContain('d ln(cost)/d ln(p)')
    expect(text).toContain('0.1%')
  })

  it('typesets a LaTeX label the way the parameter column does', () => {
    // name_for_plotting is LaTeX ("C_{ao}"); showing it raw puts braces on
    // screen beside a slider that renders the same label properly.
    const wrapper = mountBar({ labels: { 'a/alpha': 'C_{ao}' } })
    const name = wrapper.get('.cost-sens-name')
    expect(name.html()).toContain('katex')
    expect(name.text()).not.toContain('{')
  })

  it('leaves a plain qname as readable text', () => {
    // No braces or backslashes: "a/alpha" is not maths and must not be mangled.
    const wrapper = mountBar()
    expect(wrapper.get('.cost-sens-name').text()).toBe('a/alpha')
  })

  it('prefers a label to a qualified name where one exists', () => {
    const wrapper = mountBar({ labels: { 'a/alpha': 'α' } })
    expect(wrapper.text()).toContain('α')
  })

  it('shows a reason instead of a number when it could not tell', () => {
    const wrapper = mountBar({
      result: payload({
        params: [
          { name: 'a/alpha', value: 1, derivative: null, elasticity: null,
            reason: 'the run at a/alpha = 1.001 did not run: CVODE failed' },
        ],
      }),
    })
    const row = wrapper.find('[data-testid="cost-sens-row"]')
    expect(row.find('[data-testid="cost-sens-value"]').text()).toBe('—')
    expect(row.text()).toContain('CVODE failed')
  })

  it('reports a cost that could not be scored rather than showing zeros', () => {
    const wrapper = mountBar({
      result: payload({ cost: null, unavailable: 'no cost to take a gradient of' }),
    })
    expect(wrapper.find('[data-testid="cost-sens-unavailable"]').text()).toContain(
      'no cost to take a gradient of',
    )
    expect(wrapper.findAll('[data-testid="cost-sens-row"]')).toHaveLength(0)
  })

  it('surfaces a failed request', () => {
    const wrapper = mountBar({ status: 'error', error: 'Simulation failed: boom' })
    expect(wrapper.find('[data-testid="cost-sens-error"]').text()).toContain('boom')
  })

  it('keeps the last ranking when the sliders have moved, but marks it stale', () => {
    // Dropping it would leave the panel emptier the more the user explores; a
    // stale ranking is usually still the useful one, provided it says so.
    const wrapper = mountBar({ status: 'stale' })
    expect(wrapper.findAll('[data-testid="cost-sens-row"]')).toHaveLength(3)
    expect(wrapper.text()).toContain('measured at the previous parameters')
    expect(wrapper.find('[data-testid="cost-sens-recompute"]').exists()).toBe(true)
  })

  it('offers a recompute rather than doing it on every pixel of a drag', async () => {
    const wrapper = mountBar({ status: 'stale' })
    await wrapper.find('[data-testid="cost-sens-recompute"]').trigger('click')
    expect(wrapper.emitted('recompute')).toHaveLength(1)
  })

  it('says it is working before the first numbers arrive', () => {
    const wrapper = mountBar({ result: null, status: 'running' })
    expect(wrapper.find('[data-testid="cost-sens-empty"]').text()).toContain('measuring')
  })

  it('flags a backend with no analytic gradients as the slow case (#188)', () => {
    // Differencing costs the same 2M+1 solves everywhere; only a backend without
    // AD has no cheaper route in principle, so that is what is worth saying.
    const wrapper = mountBar({ adAvailable: false })
    const badge = wrapper.get('[data-testid="cost-sens-no-ad"]')
    expect(badge.text()).toContain('no AD')
    // The badge says it is slow; the tooltip says how slow, and what would fix it.
    expect(badge.attributes('title')).toContain(String(payload().n_simulations))
    expect(badge.attributes('title')).toContain('casadi_python')
  })

  it('says nothing about AD when the backend has it', () => {
    const wrapper = mountBar({ adAvailable: true })
    expect(wrapper.find('[data-testid="cost-sens-no-ad"]').exists()).toBe(false)
  })

  it('says nothing when the caller does not wire the flag', () => {
    // Absence is not a claim of slowness -- the prop defaults to available.
    const wrapper = mountBar()
    expect(wrapper.find('[data-testid="cost-sens-no-ad"]').exists()).toBe(false)
  })
})
