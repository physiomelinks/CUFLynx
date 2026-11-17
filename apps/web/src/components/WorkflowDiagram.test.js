import { describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'
import WorkflowDiagram from './WorkflowDiagram.vue'
import { edgesOf } from '../lib/workflowTarget'

const steps = [
  { id: 'sliders-1', kind: 'set', set: [] },
  { id: 'calibration-1', start_from: 'sliders-1' },
  { id: 'sobol-1', kind: 'sensitivity', settings: { sa_options: { method: 'sobol' } } },
  { id: 'mcmc-1', kind: 'uq', result_from: 'calibration-1', fixed_from: ['sliders-1'] },
]

describe('edgesOf', () => {
  it('draws an arrow from each step to every step that takes from it', () => {
    expect(edgesOf(steps)).toEqual([
      { from: 0, to: 1, key: 'start_from' },
      { from: 1, to: 3, key: 'result_from' },
      { from: 0, to: 3, key: 'fixed_from' },
    ])
  })
})

describe('WorkflowDiagram', () => {
  function diagram(props = {}) {
    return mount(WorkflowDiagram, {
      props: { steps, states: { 'calibration-1': 'done', 'mcmc-1': 'stale' }, ...props },
    })
  }

  it('has a node per step in file order, with its kind and state', () => {
    const d = diagram()
    const nodes = d.findAll('.wf-node')
    expect(nodes.map((n) => n.attributes('data-testid'))).toEqual(
      steps.map((s) => `workflow-node-${s.id}`),
    )
    expect(d.find('[data-testid="workflow-node-sobol-1"]').text()).toContain('sobol')
    expect(d.find('[data-testid="workflow-node-mcmc-1"] .wf-dot').attributes('data-state'))
      .toBe('stale')
    expect(d.findAll('.wf-edge')).toHaveLength(3)
  })

  it('selects a step on click, and deletes it from the bin without selecting it', async () => {
    const d = diagram()
    await d.find('[data-testid="workflow-node-calibration-1"]').trigger('click')
    expect(d.emitted('select')).toEqual([['calibration-1']])
    await d.find('[data-testid="workflow-delete-mcmc-1"]').trigger('click')
    expect(d.emitted('delete')).toEqual([['mcmc-1']])
    expect(d.emitted('select')).toHaveLength(1)
  })

  it('a step dragged onto another asks to move it there', async () => {
    const d = diagram()
    const data = { setData: () => {}, effectAllowed: '' }
    await d.find('[data-testid="workflow-drag-sobol-1"]').trigger('dragstart', { dataTransfer: data })
    await d.find('[data-testid="workflow-node-sliders-1"]').trigger('dragover')
    await d.find('[data-testid="workflow-node-sliders-1"]').trigger('drop')
    expect(d.emitted('move')).toEqual([[{ step: 'sobol-1', index: 0 }]])
  })

  it('marks the step the next run changes and the steps a delete would take', () => {
    const d = diagram({ editing: 'calibration-1', doomed: ['calibration-1', 'mcmc-1'] })
    expect(d.find('[data-testid="workflow-node-calibration-1"]').classes()).toContain('editing')
    expect(d.find('[data-testid="workflow-node-mcmc-1"]').classes()).toContain('doomed')
    expect(d.find('[data-testid="workflow-node-sobol-1"]').classes()).not.toContain('doomed')
  })
})
