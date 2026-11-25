import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import RunPhases from './RunPhases.vue'

const iso = (secondsAgo) => new Date(Date.now() - secondsAgo * 1000).toISOString()

function doc(extra = {}) {
  return {
    schema_version: 1,
    kind: 'sobol',
    state: 'running',
    started: iso(70),
    updated: iso(10),
    phases: [{ name: 'sampling', state: 'running', done: 40, total: 100, failed: 4 }],
    ...extra,
  }
}

describe('RunPhases', () => {
  it('draws a phase as ok and failed parts of its total', () => {
    const w = mount(RunPhases, { props: { progress: doc() } })
    expect(w.get('[data-testid="run-state"]').text()).toBe('running')
    expect(w.get('[data-testid="run-phase"]').text()).toContain('40 / 100')
    expect(w.get('[data-testid="phase-failed"]').text()).toContain('4 failed')
    expect(w.get('.bar .ok').attributes('style')).toContain('width: 36%')
    expect(w.get('.bar .bad').attributes('style')).toContain('width: 4%')
  })

  it("names an emulator's sampling stages and lists the models it compares", () => {
    const progress = doc({
      kind: 'emulation',
      phases: [
        { name: 'sampling', state: 'done', done: 8, total: 8, failed: 0, stage: 1, method: 'lhs' },
        {
          name: 'training', state: 'running', done: 1, total: 2, failed: 0, metric: 'r2',
          models: [
            { name: 'rbf', state: 'done', score: 0.91234, duration_s: 3.2 },
            { name: 'gp', state: 'failed', score: null, duration_s: null },
          ],
        },
      ],
    })
    const w = mount(RunPhases, { props: { progress } })
    expect(w.findAll('[data-testid="run-phase"]')[0].text()).toContain('sampling · stage 1 (lhs)')
    const table = w.get('[data-testid="training-models"]')
    expect(table.text()).toContain('r2')
    expect(table.text()).toContain('0.9123')
    expect(table.findAll('tbody tr')[1].text()).toContain('failed')
  })

  it("shows a profile likelihood's entries by their plotting names", () => {
    const progress = doc({
      kind: 'profile_likelihood',
      phases: [{ name: 'heart/E', state: 'running', done: 3, total: 10, failed: 0, param_index: 0 }],
    })
    const w = mount(RunPhases, { props: { progress, labels: { 'heart/E': 'E_{lv}' } } })
    expect(w.get('.phase-name').text()).toBe('E_{lv}')
  })

  it('says a stopped run is stopped, and a running file that went quiet is quiet', () => {
    const stopped = mount(RunPhases, { props: { progress: doc({ state: 'stopped' }) } })
    expect(stopped.get('[data-testid="run-state"]').text()).toBe('stopped')
    expect(stopped.find('[data-testid="run-quiet"]').exists()).toBe(false)
    const quiet = mount(RunPhases, { props: { progress: doc({ updated: iso(600) }) } })
    expect(quiet.get('[data-testid="run-quiet"]').text()).toContain('no update for 10 min')
  })

  it('ignores phase keys it does not know, and a run with no phase yet', () => {
    const w = mount(RunPhases, {
      props: { progress: doc({ phases: [{ name: 'x', state: 'queued', done: 0, total: 0, failed: 0, extra: 1 }] }) },
    })
    expect(w.get('.bar .ok').attributes('style')).toContain('width: 0%')
    const empty = mount(RunPhases, { props: { progress: doc({ phases: [] }) } })
    expect(empty.text()).toContain('Starting')
  })
})
