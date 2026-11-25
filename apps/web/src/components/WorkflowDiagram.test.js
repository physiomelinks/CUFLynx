import { describe, expect, it, vi } from 'vitest'
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
      'sliders-1': { col: 0, row: 0, key: 0, raw: 0 },
      'calibration-1': { col: 1, row: 0, key: 1, raw: 1 },
      'sobol-1': { col: 0, row: 1, key: 2, raw: 0 },
      'mcmc-1': { col: 2, row: 0, key: 3, raw: 2 },
    })
  })

  it('puts a box where it was moved, never left of what it takes from', () => {
    const placed = layoutSteps(steps, {
      'sobol-1': { col: 3, row: 0 },
      // above sliders-1 in its column
      'calibration-1': { col: 0, row: -1 },
    })
    expect(placed['sobol-1']).toMatchObject({ col: 3, row: 0 })
    // calibration-1 takes from sliders-1, so it stays right of it
    expect(placed['calibration-1'].col).toBe(1)
    expect(placed['mcmc-1'].col).toBe(2)
  })

  it('leaves no empty column: one a box was moved out of closes up', () => {
    // sobol-1 pushed to column 5, past the empty 3 and 4; mcmc-1 alone in column 2
    const placed = layoutSteps(steps, { 'sobol-1': { col: 5, row: 0 } })
    expect(Object.fromEntries(Object.entries(placed).map(([id, p]) => [id, p.col]))).toEqual(
      { 'sliders-1': 0, 'calibration-1': 1, 'mcmc-1': 2, 'sobol-1': 3 })
    // a column emptied by a move: calibration-1 moved right of mcmc-1's column is still right
    // of sliders-1, and nothing is left in column 1
    const moved = layoutSteps(steps, { 'calibration-1': { col: 4, row: 0 } })
    const cols = Object.values(moved).map((p) => p.col)
    expect([...new Set(cols)].sort()).toEqual([0, 1, 2])
    expect(moved['calibration-1'].col).toBeGreaterThan(moved['sliders-1'].col)
    expect(moved['mcmc-1'].col).toBeGreaterThan(moved['calibration-1'].col)
  })

  it('places a new step after what its waiting arrows come from', () => {
    const drafts = [...steps, { id: 'new-1', draft: true }]
    const placed = layoutSteps(drafts, {}, [{ source: 'mcmc-1', target: 'new-1', key: 'fixed_from' }])
    expect(placed['new-1'].col).toBe(3)
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

  function pointers(c) {
    const pan = c.find('[data-testid="workflow-pan"]')
    pan.element.setPointerCapture = () => {}
    pan.element.releasePointerCapture = () => {}
    return pan
  }

  it('a box dragged elsewhere asks to go to that column and row; a click still selects', async () => {
    const c = chart()
    const pan = pointers(c)
    const node = c.find('[data-testid="workflow-node-sobol-1"]')
    await node.trigger('pointerdown', { button: 0, clientX: 10, clientY: 10, pointerId: 3 })
    // jsdom has no layout: the chart is at (0, 0), so a move is chart coordinates; two
    // columns right
    await pan.trigger('pointermove', { clientX: 10 + 2 * (250 + 96), clientY: 10, pointerId: 3 })
    expect(node.classes()).toContain('moving')
    await pan.trigger('pointerup', { clientX: 10 + 2 * (250 + 96), clientY: 10, pointerId: 3 })
    const [[placed]] = c.emitted('place')
    expect(placed.node).toBe('sobol-1')
    expect(placed.col).toBe(2)
    expect(typeof placed.row).toBe('number')
    // the click that ends a move does not select; the next one does
    await node.trigger('click')
    expect(c.emitted('select')).toBeUndefined()
    await node.trigger('click')
    expect(c.emitted('select')).toEqual([['sobol-1']])
  })

  it('draws a box where it was moved to in its column, not in file order', () => {
    const top = (c, id) => parseFloat(c.find(`[data-testid="workflow-node-${id}"]`).attributes('style').match(/top: ([\d.]+)px/)[1])
    const before = chart()
    expect(top(before, 'sliders-1')).toBeLessThan(top(before, 'sobol-1'))
    const moved = chart({ layout: { positions: { 'sobol-1': { col: 0, row: -1 } }, drafts: [], links: [] } })
    expect(top(moved, 'sobol-1')).toBeLessThan(top(moved, 'sliders-1'))
  })

  it('draws an arrow from a box\'s handle to the box it is dropped on', async () => {
    const c = chart()
    const pan = pointers(c)
    const target = c.find('[data-testid="workflow-node-mcmc-1"]').element
    document.elementFromPoint = () => target
    await c.find('[data-testid="workflow-port-calibration-1"]').trigger('pointerdown',
      { button: 0, clientX: 5, clientY: 5, pointerId: 4 })
    await pan.trigger('pointermove', { clientX: 900, clientY: 40, pointerId: 4 })
    expect(c.find('[data-testid="workflow-drawing"]').exists()).toBe(true)
    expect(c.find('[data-testid="workflow-node-mcmc-1"]').classes()).toContain('over')
    await pan.trigger('pointerup', { clientX: 900, clientY: 40, pointerId: 4 })
    expect(c.emitted('connect')).toEqual([[{ source: 'calibration-1', target: 'mcmc-1' }]])
    expect(c.find('[data-testid="workflow-drawing"]').exists()).toBe(false)
    delete document.elementFromPoint
  })

  it('shows new steps and the arrows waiting for them dashed; double-click edits an arrow', async () => {
    const c = chart({ layout: { positions: {}, drafts: [{ id: 'new-1' }],
      links: [{ source: 'calibration-1', target: 'new-1', key: 'fixed_from' }] }, editing: 'new-1' })
    const draft = c.find('[data-testid="workflow-node-new-1"]')
    expect(draft.classes()).toContain('draft')
    expect(draft.text()).toContain('run a panel to make it')
    expect(draft.text()).toContain('next run')
    expect(c.findAll('.wf-edge.pending')).toHaveLength(1)
    await draft.trigger('click')
    expect(c.emitted('select-draft')).toEqual([['new-1']])
    await c.find('[data-testid="workflow-delete-new-1"]').trigger('click')
    expect(c.emitted('delete-draft')).toEqual([['new-1']])
    await c.find('[data-testid="workflow-edge-calibration-1-new-1"]').trigger('dblclick')
    await c.find('[data-testid="workflow-edge-calibration-1-mcmc-1"]').trigger('dblclick')
    expect(c.emitted('edit-edge')).toEqual([
      [{ source: 'calibration-1', target: 'new-1', key: 'fixed_from', pending: true }],
      [{ source: 'calibration-1', target: 'mcmc-1', key: 'result_from', pending: false }],
    ])
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

  it('draws a figure step after the steps it reads, after the target when one runs on it', () => {
    const c = chart({
      steps: [
        { id: 'fit', settings: {} },
        { id: 'run', kind: 'simulate', fixed_from: ['fit'] },
        { id: 'scan', kind: 'sweep', on_target: true },
        { id: 'traces', kind: 'figure', function: 'figures/standard_figures.py:sweep_traces',
          inputs: { sweep: { sweep: 'run' } } },
        { id: 'both', kind: 'figure', function: 'figures/mine.py:draw',
          inputs: { curves: { sweep: 'scan' }, fit: { values: 'fit' } } },
      ],
      results: { traces: { figures: ['figure/a.png', 'figure/b.png'] } },
    })
    const left = (id) => parseFloat(c.find(`[data-testid="${id}"]`).attributes('style').match(/left: ([\d.]+)px/)[1])
    expect(left('workflow-node-traces')).toBeGreaterThan(left('workflow-node-run'))
    // reads a sweep on the merged target, so sits right of it, and of the target
    expect(left('workflow-node-both')).toBeGreaterThan(left('workflow-node-scan'))
    expect(left('workflow-node-scan')).toBeGreaterThan(left('workflow-node-target'))
    for (const id of ['run-traces', 'scan-both', 'fit-both']) {
      expect(c.find(`[data-testid="workflow-edge-${id}"]`).exists(), id).toBe(true)
    }
    // a figure step takes nothing from the model, and is never merged into the target
    expect(c.find('[data-testid="workflow-edge-model-traces"]').exists()).toBe(false)
    expect(c.find('[data-testid="workflow-edge-traces-target"]').exists()).toBe(false)
    const box = c.find('[data-testid="workflow-node-traces"]').text()
    expect(box).toContain('figures · 2 files · sweep_traces')
    expect(box).toContain('sweep ← run')
    expect(c.find('[data-testid="workflow-node-run"]').text()).toContain('simulation')
  })

  it('arrows into or out of one box run side by side, and the whole of one lights up', async () => {
    const c = chart({
      steps: [
        { id: 'kir', settings: {} },
        { id: 'sglt1', settings: {} },
        { id: 'whole_cell', fixed_from: ['kir', 'sglt1'], settings: {} },
        { id: 'sweep_a', kind: 'sweep', on_target: true },
        { id: 'sweep_b', kind: 'sweep', on_target: true },
      ],
    })
    const path = (id) => c.find(`[data-testid="workflow-edge-${id}"]`).attributes('d')
    // no two arrows share a line: each has its own route
    expect(path('kir-whole_cell')).not.toBe(path('sglt1-whole_cell'))
    const end = (d) => d.trim().split(/\s+/).slice(-1)[0]
    expect(end(path('kir-whole_cell'))).not.toBe(end(path('sglt1-whole_cell')))
    expect(path('target-sweep_a')).not.toBe(path('target-sweep_b'))
    // hovering an arrow lights all of it (the drawn path, not a piece) and labels it
    await c.find('[data-testid="workflow-edge-kir-whole_cell"]').trigger('mouseenter')
    const lit = c.findAll('.wf-edge.lit')
    expect(lit).toHaveLength(1)
    expect(lit[0].attributes('d')).toBe(path('kir-whole_cell'))
    expect(c.find('.wf-edge-label.lit').text()).toBe('fixed')
    await c.find('[data-testid="workflow-edge-kir-whole_cell"]').trigger('mouseleave')
    expect(c.findAll('.wf-edge.lit')).toHaveLength(0)
    // the arrow whose detail is open stays lit
    await c.setProps({ active: { source: 'target', target: 'sweep_b', key: 'on_target' } })
    expect(c.find('.wf-edge.lit').attributes('d')).toBe(path('target-sweep_b'))
  })

  it('clicking an arrow says which it is, and does not pan', async () => {
    const c = chart()
    const pan = c.find('[data-testid="workflow-pan"]')
    pan.element.setPointerCapture = () => { throw new Error('should not pan') }
    const hit = c.find('[data-testid="workflow-edge-calibration-1-mcmc-1"]')
    await hit.trigger('pointerdown', { button: 0, pointerId: 1 })
    // an arrow between steps: a click or a double-click opens the arrow dialog (what passes,
    // change it, pass more)
    await hit.trigger('click')
    await hit.trigger('dblclick')
    expect(c.emitted('edit-edge')).toEqual([
      [{ source: 'calibration-1', target: 'mcmc-1', key: 'result_from', pending: false }],
      [{ source: 'calibration-1', target: 'mcmc-1', key: 'result_from', pending: false }],
    ])
    // one from the model shows what passes, with nothing to change
    await c.find('[data-testid="workflow-edge-model-sliders-1"]').trigger('click')
    expect(c.emitted('edge')).toEqual([[{ source: 'model', target: 'sliders-1', key: 'model', files: null }]])
  })

  it('shows where a dragged new step can go, and drops it there', async () => {
    const c = chart()
    const pan = c.find('[data-testid="workflow-pan"]')
    const types = ['application/x-cuflynx-new-step']
    // jsdom has no layout: the chart is at (0, 0). The gap between the first and second
    // columns of steps (x of step column 1, less the space between columns)
    const gapX = 8 + 2 * (250 + 96) - 96 / 2
    await pan.trigger('dragover', { clientX: gapX, clientY: 60, dataTransfer: { types } })
    expect(c.find('[data-testid="workflow-drop-gap-1"]').classes()).toContain('on')
    expect(c.find('[data-testid="workflow-drop-gap-0"]').classes()).not.toContain('on')
    await pan.trigger('drop', { clientX: gapX, clientY: 60, dataTransfer: { types } })
    const [[gap]] = c.emitted('add-draft')
    // a new column of steps at 1: the boxes in it and right of it move one on
    expect(gap).toMatchObject({ col: 1, row: 0 })
    expect(gap.shift).toEqual({ 'calibration-1': { col: 2, row: 1 }, 'mcmc-1': { col: 3, row: 3 } })

    // in a column: a bar where it would sit
    const colX = 8 + 1 * (250 + 96) + 100
    await pan.trigger('dragover', { clientX: colX, clientY: 5, dataTransfer: { types } })
    expect(c.find('[data-testid="workflow-drop-cell"]').exists()).toBe(true)
    await pan.trigger('drop', { clientX: colX, clientY: 5, dataTransfer: { types } })
    expect(c.emitted('add-draft')[1][0]).toMatchObject({ col: 0 })

    // after the target: a simulation
    await pan.trigger('dragover', { clientX: 5000, clientY: 60, dataTransfer: { types } })
    expect(c.find('[data-testid="workflow-drop-after"]').classes()).toContain('on')
    await pan.trigger('drop', { clientX: 5000, clientY: 60, dataTransfer: { types } })
    expect(c.emitted('add-draft')[2]).toEqual([{ onTarget: true }])
    // something else dragged over the chart is not ours
    await pan.trigger('dragover', { clientX: gapX, clientY: 60, dataTransfer: { types: ['Files'] } })
    expect(c.find('[data-testid="workflow-drop-gap-1"]').exists()).toBe(false)
  })

  it('an arrow goes left to right only: a box in the same column or left of it is refused', async () => {
    const c = chart()
    const pan = pointers(c)
    // sobol-1 is in the same column as sliders-1; calibration-1 is right of sliders-1
    const left = c.find('[data-testid="workflow-node-sliders-1"]').element
    document.elementFromPoint = () => left
    await c.find('[data-testid="workflow-port-calibration-1"]').trigger('pointerdown',
      { button: 0, clientX: 5, clientY: 5, pointerId: 6 })
    await pan.trigger('pointermove', { clientX: 20, clientY: 40, pointerId: 6 })
    expect(c.find('[data-testid="workflow-node-sliders-1"]').classes()).toContain('blocked')
    await pan.trigger('pointerup', { clientX: 20, clientY: 40, pointerId: 6 })
    expect(c.emitted('connect')).toBeUndefined()
    expect(c.emitted('refused')[0][0]).toContain('left to right')
    const same = c.find('[data-testid="workflow-node-sobol-1"]').element
    document.elementFromPoint = () => same
    await c.find('[data-testid="workflow-port-sliders-1"]').trigger('pointerdown',
      { button: 0, clientX: 5, clientY: 5, pointerId: 7 })
    await pan.trigger('pointerup', { clientX: 20, clientY: 40, pointerId: 7 })
    expect(c.emitted('connect')).toBeUndefined()
    delete document.elementFromPoint
  })

  it('a model or action box dropped from the step prep box carries what it is', async () => {
    const c = chart()
    const pan = c.find('[data-testid="workflow-pan"]')
    const model = { module_type: 'i_K1', parameterisation: 'k1998' }
    const types = ['application/x-cuflynx-new-step']
    const colX = 8 + 1 * (250 + 96) + 100
    await pan.trigger('drop', { clientX: colX, clientY: 5, dataTransfer: { types,
      getData: () => JSON.stringify({ model }) } })
    expect(c.emitted('add-draft')[0][0]).toMatchObject({ col: 0, model })
    // after the target is only a simulation of the merged target
    await pan.trigger('drop', { clientX: 5000, clientY: 60, dataTransfer: { types,
      getData: () => JSON.stringify({ action: 'set' }) } })
    expect(c.emitted('add-draft')).toHaveLength(1)
    expect(c.emitted('refused')[0][0]).toContain('After the target')
  })

  it('a press on a box captures the pointer only once it moves, so a click reaches the box', async () => {
    // captured at once, the browser sends the click to the chart, and the box never hears it
    const c = chart()
    const pan = c.find('[data-testid="workflow-pan"]')
    const captured = []
    pan.element.setPointerCapture = (id) => captured.push(id)
    pan.element.releasePointerCapture = () => {}
    const node = c.find('[data-testid="workflow-node-sobol-1"]')
    await node.trigger('pointerdown', { button: 0, clientX: 10, clientY: 10, pointerId: 9 })
    await pan.trigger('pointermove', { clientX: 11, clientY: 10, pointerId: 9 })
    expect(captured).toEqual([])
    await pan.trigger('pointermove', { clientX: 40, clientY: 10, pointerId: 9 })
    expect(captured).toEqual([9])
    await pan.trigger('pointerup', { clientX: 40, clientY: 10, pointerId: 9 })
  })

  it('a move that pushes the steps taking from the box on says so', async () => {
    const c = chart()
    const pan = pointers(c)
    const node = c.find('[data-testid="workflow-node-sliders-1"]')
    // into calibration-1's column, which takes from sliders-1 (start_from)
    await node.trigger('pointerdown', { button: 0, clientX: 10, clientY: 10, pointerId: 11 })
    await pan.trigger('pointermove', { clientX: 10 + (250 + 96), clientY: 10, pointerId: 11 })
    await pan.trigger('pointerup', { clientX: 10 + (250 + 96), clientY: 10, pointerId: 11 })
    expect(c.emitted('place')[0][0]).toMatchObject({ node: 'sliders-1', col: 1 })
    expect(c.emitted('notice')[0][0]).toContain('calibration-1 takes from sliders-1')
    expect(c.emitted('notice')[0][0]).toContain('left to right')
  })

  it('a model box and an action box say what they are', () => {
    const c = chart({ layout: { positions: {}, links: [], drafts: [
      { id: 'new-1', model: { module_type: 'i_K1', parameterisation: 'k1998' } },
      { id: 'new-2', action: 'set' }] } })
    expect(c.find('[data-testid="workflow-node-new-1"]').text()).toContain('click: choose what it does')
    const set = c.find('[data-testid="workflow-node-new-2"]')
    expect(set.text()).toContain('Set values by hand')
    expect(set.classes()).toContain('set-action')
  })

  it("a drop writes the layout's own column, not the drawn one, where a column was closed up", async () => {
    // as in the enterocyte layout: the first column was saved as 1 (column 0 emptied), so the
    // chart draws it as 0; a drop in calibration-1's drawn column 1 is its saved column 2
    const c = chart({ layout: { positions: { 'sliders-1': { col: 1, row: 0 }, 'sobol-1': { col: 1, row: 1 } },
      drafts: [], links: [] } })
    const pan = c.find('[data-testid="workflow-pan"]')
    const types = ['application/x-cuflynx-new-step']
    const colX = 8 + 2 * (250 + 96) + 100
    await pan.trigger('drop', { clientX: colX, clientY: 5, dataTransfer: { types, getData: () => '{}' } })
    expect(c.emitted('add-draft')[0][0]).toMatchObject({ col: 2 })
    // a new stage in the gap before it: it takes column 2, and calibration-1 and mcmc-1 move on
    const gapX = 8 + 2 * (250 + 96) - 96 / 2
    await pan.trigger('drop', { clientX: gapX, clientY: 5, dataTransfer: { types, getData: () => '{}' } })
    const gap = c.emitted('add-draft')[1][0]
    expect(gap.col).toBe(2)
    expect(gap.shift).toMatchObject({ 'calibration-1': { col: 3 }, 'mcmc-1': { col: 4 } })
  })

  it('draws an arrow into the target only for values no later step carries on', () => {
    const kirSteps = [
      { id: 'kir' },
      { id: 'sglt1' },
      { id: 'whole_cell', fixed_from: ['kir', { step: 'sglt1', parameters: ['a_mod'] }] },
    ]
    const details = { kir: { calibrates: [{ model_name: 'k_mod' }] },
                      sglt1: { calibrates: [{ model_name: 'a_mod' }, { model_name: 'b_mod' }] } }
    const c = chart({ steps: kirSteps, details, states: {}, results: {} })
    const into = c.findAll('.wf-hit').map((h) => h.attributes('data-testid'))
      .filter((t) => t.endsWith('-target'))
    // kir's values all reach the target through whole_cell; sglt1's b_mod does not
    expect(into.sort()).toEqual(['workflow-edge-sglt1-target', 'workflow-edge-whole_cell-target'])
    // the target box still names every step whose values it merges
    expect(c.find('[data-testid="workflow-node-target"]').text()).toContain('kir')
  })

  it('a combine step takes its inputs\' place in the target, and says which it chose', () => {
    const fits = [
      { id: 'fit-a' }, { id: 'fit-b' },
      { id: 'best', kind: 'combine', combine_from: ['fit-a', 'fit-b'], combine: { method: 'best_fit' } },
    ]
    const c = chart({ steps: fits, details: {}, states: {}, results: { best: { chosen_from: 'fit-b' } } })
    const hits = c.findAll('.wf-hit').map((h) => h.attributes('data-testid'))
    expect(hits).toEqual(expect.arrayContaining(['workflow-edge-fit-a-best', 'workflow-edge-fit-b-best']))
    expect(hits.filter((t) => t.endsWith('-target'))).toEqual(['workflow-edge-best-target'])
    const box = c.find('[data-testid="workflow-node-best"]').text()
    expect(box).toContain('chosen by cost')
    expect(box).toContain('chose fit-b')
  })

  it('a reduction chosen by hand is an orange box that names what it keeps', () => {
    const steps = [{ id: 'keep', kind: 'reduction', params_for_id: 'data/keep_params_for_id.json',
      settings: { param_reduction_options: { method: 'manual', parameters: ['m/alpha', 'm/beta'], reason: 'x' } } }]
    const c = chart({ steps, details: {}, states: {}, results: {} })
    const box = c.find('[data-testid="workflow-node-keep"]')
    expect(box.classes()).toContain('chosen')
    expect(box.text()).toContain('chosen by hand · 2 parameters')
    expect(box.text()).toContain('alpha, beta')
  })

  it('a box after the target is a simulation, set up in its form', async () => {
    const c = chart({ layout: { positions: {}, drafts: [{ id: 'new-2', on_target: true }], links: [] } })
    const box = c.find('[data-testid="workflow-node-new-2"]')
    expect(box.text()).toContain('Simulation')
    await box.trigger('click')
    await box.trigger('dblclick')
    expect(c.emitted('setup-sweep')).toEqual([['new-2'], ['new-2']])
    expect(c.emitted('select-draft')).toBeUndefined()
  })

  it('draws values given to the model source into the model box, from a column before it', () => {
    // CA #692: the values are in the model source, so every step generated from it has them
    const c = chart({ origins: { boxes: [
      { id: '__set_by_hand__', title: 'Values set by hand', mark: 'set_by_hand', to: ['model'], values: [] },
    ] } })
    expect(c.find('[data-testid="workflow-edge-origin:__set_by_hand__-model"]').exists()).toBe(true)
    const left = (w) => parseFloat(w.attributes('style').match(/left: ([\d.-]+)px/)[1])
    const hand = c.find('[data-testid="workflow-origin-set_by_hand"]')
    const model = c.find('[data-testid="workflow-node-model"]')
    const firstStep = c.find('[data-testid="workflow-node-sliders-1"]')
    expect(left(hand)).toBeLessThan(left(model))
    expect(left(model)).toBeLessThan(left(firstStep))
  })

  it("shows where the model's values come from, as CA's provenance boxes them", async () => {
    const c = chart({ origins: {
      model_counts: { unclassified: 73 },
      boxes: [
        { id: '__set_by_hand__', title: 'Values set by hand', mark: 'set_by_hand', to: ['target'],
          values: [{ name: 'v_ref_Kir_Kir', value: 775.4, units: 'fmol', note: 'a normalisation' }] },
        { id: '__measured__', title: 'Measured values', mark: 'measured', to: ['calibration-1'],
          values: [{ name: 'c_K_i', value: 140, units: 'mM', note: 'Krapivinsky 1998' }] },
      ],
    } })
    const hand = c.find('[data-testid="workflow-origin-set_by_hand"]')
    const measured = c.find('[data-testid="workflow-origin-measured"]')
    expect(hand.classes()).toContain('hand')
    expect(measured.classes()).toContain('measured')
    const top = (w) => parseFloat(w.attributes('style').match(/top: ([\d.-]+)px/)[1])
    const model = c.find('[data-testid="workflow-node-model"]')
    // set by hand above the model, measured below it, in the model's column
    expect(top(hand)).toBeLessThan(top(model))
    expect(top(measured)).toBeGreaterThan(top(model))
    expect(model.text()).toContain('73 unclassified')
    expect(c.find('[data-testid="workflow-edge-origin:__set_by_hand__-target"]').exists()).toBe(true)
    expect(c.find('[data-testid="workflow-edge-origin:__measured__-calibration-1"]').exists()).toBe(true)
    await hand.trigger('click')
    await c.find('[data-testid="workflow-edge-origin:__measured__-calibration-1"]').trigger('click')
    expect(c.emitted('origin')).toEqual([['__set_by_hand__'], ['__measured__']])
  })

  it("boxes every value of the target by its origin, from CA's plan", async () => {
    const c = chart({ origins: { model_counts: {}, boxes: [
      { id: 'measured', title: 'Values from literature', mark: 'measured', to: ['model'], values: [
        { name: 'm/K', value: 1, units: 'mM', note: 'smith2001', calibrated_by: ['calibration-1'] },
        { name: 'm/V', value: 2, units: 'mM', note: 'smith2001', calibrated_by: [] }] },
      { id: 'to_calibrate', title: 'Values to be calibrated', mark: 'to_calibrate',
        to: ['calibration-1'], values: [{ name: 'm/k', value: 3, units: '', note: '', calibrated_by: ['calibration-1'] }] },
      { id: 'unrecorded', title: 'Origin not recorded', mark: 'unclassified', to: ['model'],
        values: [{ name: 'm/x', value: 4, units: '', note: '', calibrated_by: [] }] },
    ] } })
    const lit = c.find('[data-testid="workflow-origin-measured"]')
    expect(lit.text()).toContain('2 values · 1 calibrated')
    expect(lit.attributes('title')).toContain('m/K = 1: smith2001 (calibrated in calibration-1)')
    const toCal = c.find('[data-testid="workflow-origin-to_calibrate"]')
    expect(toCal.classes()).toContain('cal')
    expect(toCal.text()).toContain('calibrated by a step · 1 values')
    expect(toCal.text()).not.toContain('1 calibrated')
    expect(c.find('[data-testid="workflow-origin-unclassified"]').classes()).toContain('unrecorded')
    expect(c.find('[data-testid="workflow-origin-unclassified"]').text()).toContain('origin not recorded')
    // the values are in the model; those to be calibrated go to the step that fits them
    expect(c.find('[data-testid="workflow-edge-origin:measured-model"]').exists()).toBe(true)
    expect(c.find('[data-testid="workflow-edge-origin:to_calibrate-calibration-1"]').exists()).toBe(true)
  })

  it('marks the step the next run changes and the steps a delete would take', () => {
    const c = chart({ editing: 'calibration-1', doomed: ['calibration-1', 'mcmc-1'] })
    expect(c.find('[data-testid="workflow-node-calibration-1"]').classes()).toContain('editing')
    expect(c.find('[data-testid="workflow-node-mcmc-1"]').classes()).toContain('doomed')
    expect(c.find('[data-testid="workflow-node-sobol-1"]').classes()).not.toContain('doomed')
  })

  it('a surrogate step is drawn as one, with an arrow to the step on its emulator', async () => {
    const c = chart({ steps: [
      { id: 'surrogate-1', kind: 'emulation', settings: { emulator_settings: { models: ['RBF'] } } },
      { id: 'sobol-1', kind: 'sensitivity', emulator_dir: '../../workflows/study/surrogate-1/emulator',
        settings: { sa_options: { method: 'sobol' } } },
    ] })
    expect(c.find('[data-testid="workflow-node-surrogate-1"]').classes()).toContain('surrogate')
    expect(c.find('[data-testid="workflow-node-surrogate-1"] .wf-tag').text()).toContain('RBF')
    expect(c.findAll('.wf-edge.step')).toHaveLength(1)
    await c.find('[data-testid="workflow-edge-surrogate-1-sobol-1"]').trigger('mouseenter')
    expect(c.find('.wf-edge-label').text()).toBe('emulator')
  })
})
