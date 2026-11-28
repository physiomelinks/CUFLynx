import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import SobolPairs from './SobolPairs.vue'

const DESIGN = {
  names: ['a/k', 'b/k', 'c/k'],
  bounds: [[0, 1], [0, 10], [5, 6]],
  rows: [[0.1, 1, 5.1], [0.9, 9, 5.9], [0.5, 5, 5.5]],
  status: [2, 1, 1],
  counts: { total: 10, ran: 2, failed: 1, not_run: 7 },
}

describe('SobolPairs', () => {
  it('draws the failed samples in red, over the others', () => {
    const w = mount(SobolPairs, { props: { design: DESIGN } })
    const circles = w.findAll('circle')
    expect(circles).toHaveLength(3)
    expect(circles.at(-1).attributes('data-failed')).toBe('1')
    expect(circles.at(-1).attributes('fill')).toBe('#e84a5f')
    expect(circles.at(-1).find('title').text()).toContain('failed: a/k = 0.1, b/k = 1')
    expect(w.get('[data-testid="sobol-pairs-counts"]').text()).toContain('3 of 10 samples run')
  })

  it('places points by the bounds of the parameters picked', async () => {
    const w = mount(SobolPairs, { props: { design: DESIGN, labels: { 'c/k': 'C' } } })
    // a/k = 0.9 at the right edge of the x range [0, 1]
    const right = w.findAll('circle').find((c) => c.attributes('data-failed') === '0'
      && Number(c.attributes('cx')) > 300)
    expect(right).toBeTruthy()
    await w.get('[data-testid="sobol-pairs-y"]').setValue(2)
    expect(w.get('svg').attributes('aria-label')).toBe('Sobol design: C against a/k')
  })

  it('says when only part of the samples that ran is shown', () => {
    const w = mount(SobolPairs, {
      props: { design: { ...DESIGN, counts: { total: 100, ran: 50, failed: 1, not_run: 49 } } },
    })
    expect(w.text()).toContain('every failure and 2 of the others shown')
  })

  it('has no pair to plot for one parameter', () => {
    const w = mount(SobolPairs, {
      props: { design: { ...DESIGN, names: ['a/k'], rows: [[0.1]], bounds: [[0, 1]] } },
    })
    expect(w.find('svg').exists()).toBe(false)
    expect(w.text()).toContain('no pair to plot')
  })
})
