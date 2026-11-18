import { describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'
import WorkflowDiagram from './WorkflowDiagram.vue'
import { edgesOf, layoutSteps } from '../lib/workflowTarget'

const steps = [
  { id: 'sliders-1', kind: 'set', point: true, set: [{ parameter: 'm/alpha', value: 2 }] },
  { id: 'calibration-1', start_from: 'sliders-1', settings: { param_id_method: 'CMA-ES' } },
  { id: 'sobol-1', kind: 'sensitivity', settings: { sa_options: { method: 'sobol' } } },
  { id: 'mcmc-1', kind: 'uq', result_from: 'calibration-1', fixed_from: ['sliders-1'] },
]

describe('edgesOf / layoutSteps', () => {
  it('draws an arrow from each step to every step that takes from it', () => {
    expect(edgesOf(steps)).toEqual([
      { from: 0, to: 1, key: 'start_from' },
      { from: 1, to: 3, key: 'result_from' },
      { from: 0, to: 3, key: 'fixed_from' },
    ])
  })

  it('puts a step one column right of the furthest step it takes from', () => {
    expect(layoutSteps(steps)).toEqual({
      'sliders-1': { col: 0, row: 0 },
      'calibration-1': { col: 1, row: 0 },
      'sobol-1': { col: 0, row: 1 },
      'mcmc-1': { col: 2, row: 0 },
    })
  })
})

describe('WorkflowDiagram', () => {
  function chart(props = {}) {
    return mount(WorkflowDiagram, {
      props: {
        steps,
        states: { 'calibration-1': 'done', 'mcmc-1': 'stale' },
        results: { 'calibration-1': { best_cost: 0.0123456, duration_s: 9.4 } },
        details: { 'calibration-1': { detail: 'LV.cellml', calibrates: [{ model_name: 'a' }, { model_name: 'b' }] } },
        target: { cellml_model_path: '../inputs/LV.cellml' },
        ...props,
      },
    })
  }

  it('has the model, a box per step in columns by dependency, and the target', () => {
    const c = chart()
    expect(c.findAll('.wf-node').map((n) => n.attributes('data-testid'))).toEqual(
      steps.map((s) => `workflow-node-${s.id}`),
    )
    expect(c.find('[data-testid="workflow-node-model"]').text()).toContain('LV.cellml')
    // only the calibration is merged into the target; the slider point is not
    expect(c.find('[data-testid="workflow-node-target"]').text()).toContain('from calibration-1')
    const left = (id) => parseFloat(c.find(`[data-testid="workflow-node-${id}"]`).attributes('style').match(/left: ([\d.]+)px/)[1])
    expect(left('sliders-1')).toBeLessThan(left('calibration-1'))
    expect(left('calibration-1')).toBeLessThan(left('mcmc-1'))
    expect(left('sobol-1')).toBe(left('sliders-1'))
    // 3 between steps, 2 from the model (to the steps that take from no step), 1 to the target
    expect(c.findAll('.wf-edge.step')).toHaveLength(3)
    expect(c.findAll('.wf-edge.model')).toHaveLength(2)
    expect(c.findAll('.wf-edge.target')).toHaveLength(1)
  })

  it('says in each box what set its values, its cost, time and state', () => {
    const c = chart()
    const cal = c.find('[data-testid="workflow-node-calibration-1"]')
    expect(cal.classes()).toContain('cal')
    expect(cal.text()).toContain('cost 0.0123')
    expect(cal.text()).toContain('9.4 s')
    expect(cal.find('.wf-detail').text()).toBe('CMA-ES')
    expect(cal.find('.wf-tag').text()).toContain('2 parameters')
    expect(c.find('[data-testid="workflow-node-sliders-1"]').classes()).toContain('hand')
    expect(c.find('[data-testid="workflow-node-sliders-1"] .wf-tag').text()).toContain('point only')
    expect(c.find('[data-testid="workflow-node-sobol-1"]').classes()).toContain('analysis')
    expect(c.find('[data-testid="workflow-node-mcmc-1"] .wf-dot').attributes('data-state')).toBe('stale')
  })

  it('click selects, double-click inspects, the bin deletes without selecting', async () => {
    const c = chart()
    await c.find('[data-testid="workflow-node-calibration-1"]').trigger('click')
    await c.find('[data-testid="workflow-node-calibration-1"]').trigger('dblclick')
    expect(c.emitted('select')).toEqual([['calibration-1']])
    expect(c.emitted('inspect')).toEqual([['calibration-1']])
    await c.find('[data-testid="workflow-delete-mcmc-1"]').trigger('click')
    expect(c.emitted('delete')).toEqual([['mcmc-1']])
    expect(c.emitted('select')).toHaveLength(1)
  })

  it('a box dragged onto another asks to take its place in the file', async () => {
    const c = chart()
    const data = { setData: () => {}, effectAllowed: '' }
    await c.find('[data-testid="workflow-node-sobol-1"]').trigger('dragstart', { dataTransfer: data })
    await c.find('[data-testid="workflow-node-sliders-1"]').trigger('dragover')
    await c.find('[data-testid="workflow-node-sliders-1"]').trigger('drop')
    expect(c.emitted('move')).toEqual([[{ step: 'sobol-1', index: 0 }]])
  })

  it('pans when the chart is dragged by its background, not by a step box', async () => {
    const c = chart()
    const pan = c.find('[data-testid="workflow-pan"]')
    pan.element.setPointerCapture = () => {}
    pan.element.releasePointerCapture = () => {}
    pan.element.scrollLeft = 100
    await pan.trigger('pointerdown', { button: 0, clientX: 300, clientY: 50, pointerId: 1 })
    await pan.trigger('pointermove', { clientX: 240, clientY: 50, pointerId: 1 })
    // jsdom has no layout, so scrollLeft takes whatever it is set to
    expect(pan.element.scrollLeft).toBe(160)
    await pan.trigger('pointerup', { pointerId: 1 })
    // a press on a step box is that box's (click, or drag to reorder): no pan
    pan.element.scrollLeft = 0
    await c.find('[data-testid="workflow-node-sobol-1"]').trigger('pointerdown', {
      button: 0, clientX: 300, clientY: 50, pointerId: 2 })
    await pan.trigger('pointermove', { clientX: 200, clientY: 50, pointerId: 2 })
    expect(pan.element.scrollLeft).toBe(0)
  })

  it('keeps the colour key outside the part that moves', () => {
    const c = chart()
    expect(c.find('[data-testid="workflow-pan"] .wf-key').exists()).toBe(false)
    expect(c.find('.wf-key').exists()).toBe(true)
  })

  it('clicking the target opens it', async () => {
    const c = chart()
    await c.find('[data-testid="workflow-node-target"]').trigger('click')
    expect(c.emitted('open-target')).toHaveLength(1)
  })

  it('runs a step on_target from the target, not the model, and figures after what they read', () => {
    // the enterocyte workflow's shape: calibrations merged, sweeps on the merged target,
    // and external figures reading a sweep's results and another figure's output
    const c = chart({
      steps: [
        { id: 'kir', settings: {} },
        { id: 'whole_cell', fixed_from: ['kir'], settings: {} },
        { id: 'energy_cost', kind: 'sweep', on_target: true },
      ],
      figures: [
        { id: 'paper_plots', from: 'target', reads: ['workflow_result.json'], writes: ['pp/figures.json'] },
        { id: 'paper_figures', from: 'target',
          reads: ['pp/figures.json', 'energy_cost/sweep/sweep_results.npz', 'workflow_result.json'], writes: [] },
      ],
    })
    const left = (id) => parseFloat(c.find(`[data-testid="${id}"]`).attributes('style').match(/left: ([\d.]+)px/)[1])
    expect(left('workflow-node-target')).toBeGreaterThan(left('workflow-node-whole_cell'))
    expect(left('workflow-node-energy_cost')).toBeGreaterThan(left('workflow-node-target'))
    expect(left('workflow-figure-paper_figures')).toBeGreaterThan(left('workflow-node-energy_cost'))
    // the sweep takes the merged target, not the model
    expect(c.find('[data-testid="workflow-edge-target-energy_cost"]').exists()).toBe(true)
    expect(c.find('[data-testid="workflow-edge-model-energy_cost"]').exists()).toBe(false)
    expect(c.find('[data-testid="workflow-edge-energy_cost-figure:paper_figures"]').exists()).toBe(true)
    expect(c.find('[data-testid="workflow-edge-figure:paper_plots-figure:paper_figures"]').exists()).toBe(true)
  })

  it('each arrow is clicked on the segment that is its own', () => {
    const c = chart({
      steps: [
        { id: 'kir', settings: {} },
        { id: 'sglt1', settings: {} },
        { id: 'whole_cell', fixed_from: ['kir', 'sglt1'], settings: {} },
        { id: 'sweep_a', kind: 'sweep', on_target: true },
        { id: 'sweep_b', kind: 'sweep', on_target: true },
      ],
    })
    const hit = (id) => c.find(`[data-testid="workflow-edge-${id}"]`).attributes('d')
    // two arrows into whole_cell share their end: each is picked by its start, one level line
    expect(hit('kir-whole_cell')).toMatch(/^M [\d.]+ [\d.]+ H [\d.]+$/)
    expect(hit('kir-whole_cell')).not.toBe(hit('sglt1-whole_cell'))
    // two arrows out of the target share their start: each is picked by its end
    expect(hit('target-sweep_a')).toMatch(/^M [\d.]+ [\d.]+ H [\d.]+$/)
    expect(hit('target-sweep_a')).not.toBe(hit('target-sweep_b'))
  })

  it('clicking an arrow says which it is, and does not pan', async () => {
    const c = chart()
    const pan = c.find('[data-testid="workflow-pan"]')
    pan.element.setPointerCapture = () => { throw new Error('should not pan') }
    const hit = c.find('[data-testid="workflow-edge-calibration-1-mcmc-1"]')
    await hit.trigger('pointerdown', { button: 0, pointerId: 1 })
    await hit.trigger('click')
    expect(c.emitted('edge')).toEqual([[{ source: 'calibration-1', target: 'mcmc-1',
                                          key: 'result_from', files: null }]])
  })

  it('marks the step the next run changes and the steps a delete would take', () => {
    const c = chart({ editing: 'calibration-1', doomed: ['calibration-1', 'mcmc-1'] })
    expect(c.find('[data-testid="workflow-node-calibration-1"]').classes()).toContain('editing')
    expect(c.find('[data-testid="workflow-node-mcmc-1"]').classes()).toContain('doomed')
    expect(c.find('[data-testid="workflow-node-sobol-1"]').classes()).not.toContain('doomed')
  })

  it('a surrogate step is drawn as one, with an arrow to the step on its emulator', () => {
    const c = chart({ steps: [
      { id: 'surrogate-1', kind: 'emulation', settings: { emulator_settings: { models: ['RBF'] } } },
      { id: 'sobol-1', kind: 'sensitivity', emulator_dir: '../../workflows/study/surrogate-1/emulator',
        settings: { sa_options: { method: 'sobol' } } },
    ] })
    expect(c.find('[data-testid="workflow-node-surrogate-1"]').classes()).toContain('surrogate')
    expect(c.find('[data-testid="workflow-node-surrogate-1"] .wf-tag').text()).toContain('RBF')
    expect(c.findAll('.wf-edge.step')).toHaveLength(1)
    expect(c.find('.wf-edge-label').text()).toBe('emulator')
  })
})
