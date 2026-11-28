import { describe, expect, it } from 'vitest'
import { figureFunction, figureUrl, isImage, workflowFigures } from './workflowFigures'

describe('workflow figures', () => {
  it('names a figure step by its function, from the file or from CA\'s plan', () => {
    expect(figureFunction({ function: 'figures/standard_figures.py:sweep_traces' })).toBe('sweep_traces')
    expect(figureFunction({ figure: { function: 'prediction_plots' } })).toBe('prediction_plots')
    expect(figureFunction({})).toBe('')
  })

  it('serves a file by step and name, a redrawn one under a new version', () => {
    expect(figureUrl('traces', 'figure/a b.png'))
      .toBe('/api/workflow/figure?step=traces&name=figure%2Fa+b.png')
    expect(figureUrl('traces', 'figure/a.png', '2026-11-26T08:26:53')).toContain('&v=2026-11-26T08%3A26%3A53')
  })

  it('shows images and links every other file', () => {
    expect(['a.png', 'b.SVG', 'c.jpeg'].every(isImage)).toBe(true)
    expect(isImage('validation_scores.csv')).toBe(false)
  })

  it('lists the figure steps that drew, in the workflow\'s order', () => {
    const steps = [
      { id: 'run', kind: 'simulate' },
      { id: 'b', kind: 'figure', function: 'f.py:second' },
      { id: 'a', kind: 'figure', function: 'f.py:first' },
      { id: 'never', kind: 'figure', function: 'f.py:none' },
    ]
    const results = {
      run: { status: 'done' },
      a: { status: 'done', finished: 't1', figures: ['figure/x.png', 'figure/scores.csv'] },
      b: { status: 'done', finished: 't2', figures: ['figure/y.png'] },
      never: { status: 'not_run' },
    }
    const out = workflowFigures(steps, results)
    expect(out.map((f) => [f.step, f.function])).toEqual([['b', 'second'], ['a', 'first']])
    expect(out[1].files).toEqual([
      { name: 'x.png', url: figureUrl('a', 'figure/x.png', 't1'), image: true },
      { name: 'scores.csv', url: figureUrl('a', 'figure/scores.csv', 't1'), image: false },
    ])
    expect(workflowFigures(null, null)).toEqual([])
  })
})
