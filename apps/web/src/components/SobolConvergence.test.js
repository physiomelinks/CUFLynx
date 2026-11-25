import { describe, it, expect, vi } from 'vitest'
import { mount } from '@vue/test-utils'

vi.mock('vue-chartjs', () => ({ Line: { name: 'Line', props: ['data', 'options'], render: () => null } }))

import SobolConvergence from './SobolConvergence.vue'
import { PALETTE } from '../lib/plot'

function convergence(params, outputs = ['y', 'z']) {
  const out = {}
  outputs.forEach((o, oi) => {
    const S1 = {}
    const ST = {}
    params.forEach((p, i) => {
      // parameter i's final ST is i / 10 for y, and the reverse order for z
      const final = oi === 0 ? i / 10 : (params.length - i) / 10
      S1[p] = [[0.1, 0.05], [null, null]]
      ST[p] = [[final + 0.2, 0.1], [final, 0.02]]
    })
    out[o] = { S1, ST }
  })
  return { n: [8, 16], outputs: out }
}

describe('SobolConvergence', () => {
  it('draws one line per parameter of the picked output and index', async () => {
    const w = mount(SobolConvergence, { props: { convergence: convergence(['a', 'b']) } })
    const data = w.getComponent({ name: 'Line' }).props('data')
    expect(data.datasets.map((d) => d.label)).toEqual(['a', 'b'])
    expect(data.datasets[1].data).toEqual([
      { x: 8, y: 0.30000000000000004, conf: 0.1 },
      { x: 16, y: 0.1, conf: 0.02 },
    ])
    await w.get('[data-testid="sobol-convergence-index"]').setValue('S1')
    expect(w.getComponent({ name: 'Line' }).props('data').datasets[0].data[1].y).toBe(null)
  })

  it("ticks the x axis at CA's sample counts, not the log scale's own", () => {
    const w = mount(SobolConvergence, { props: { convergence: convergence(['a']) } })
    const axis = { ticks: [{ value: 10 }, { value: 20 }] }
    w.getComponent({ name: 'Line' }).props('options').scales.x.afterBuildTicks(axis)
    expect(axis.ticks).toEqual([{ value: 8 }, { value: 16 }])
  })

  it('keeps the parameters with the largest ST, each in one colour for every output', async () => {
    const params = ['p0', 'p1', 'p2', 'p3', 'p4', 'p5', 'p6', 'p7']
    const w = mount(SobolConvergence, { props: { convergence: convergence(params) } })
    const colours = () =>
      Object.fromEntries(
        w.getComponent({ name: 'Line' }).props('data').datasets.map((d) => [d.label, d.borderColor]),
      )
    const first = colours()
    expect(Object.keys(first)).toHaveLength(PALETTE.length)
    // over both outputs, p0 (largest for z) and p7 (largest for y) are both kept
    expect(Object.keys(first)).toContain('p0')
    expect(Object.keys(first)).toContain('p7')
    expect(w.get('[data-testid="sobol-convergence-note"]').text()).toContain('6 of 8')
    await w.get('[data-testid="sobol-convergence-output"]').setValue('z')
    expect(colours()).toEqual(first)
  })
})
