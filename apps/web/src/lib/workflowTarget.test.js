import { describe, expect, it } from 'vitest'
import {
  modeSummary,
  panelSettings,
  runTargetFor,
  stepPanel,
  targetAfterRun,
} from './workflowTarget'

const described = {
  loaded: true,
  path: '/w/workflow/study/calibration_workflow.json',
  workflow: {
    workflow_name: 'study',
    steps: [
      { id: 'sliders-1', kind: 'set', set: [{ parameter: 'a', value: 1, reason: 'slider values in CUFLynx' }] },
      {
        id: 'calibration-1',
        start_from: 'sliders-1',
        settings: {
          param_id_method: 'CMA-ES',
          optimiser_options: { num_calls_to_function: 50, seed: 3 },
        },
      },
      {
        id: 'local-sa-1',
        kind: 'sensitivity',
        result_from: 'calibration-1',
        settings: { sa_options: { method: 'local', fd_rel_step: 0.02, output_dir: 'x' } },
      },
      { id: 'calibration-2', settings: {} },
      {
        id: 'mcmc-1',
        kind: 'uq',
        result_from: 'calibration-2',
        settings: { UQ_options: { method: 'mcmc', num_steps: 20, seed: 1 } },
      },
    ],
  },
}

describe('runTargetFor', () => {
  it('sends the default, naming the open workflow', () => {
    expect(runTargetFor({ mode: 'default' }, 'calibration', described)).toEqual({
      mode: 'default',
      workflow: described.path,
    })
  })

  it('sends a new step only for the kind it was chosen for', () => {
    const target = { mode: 'new_step', kind: 'sensitivity' }
    expect(runTargetFor(target, 'sensitivity', described).mode).toBe('new_step')
    expect(runTargetFor(target, 'calibration', described).mode).toBe('default')
  })

  it('sends an edit only to the panel of that step', () => {
    const target = { mode: 'edit', step: 'mcmc-1' }
    expect(runTargetFor(target, 'uq', described)).toEqual({
      mode: 'edit',
      step: 'mcmc-1',
      workflow: described.path,
    })
    expect(runTargetFor(target, 'calibration', described).mode).toBe('default')
  })

  it('a new workflow names none', () => {
    expect(runTargetFor({ mode: 'new_workflow' }, 'uq', described)).toEqual({
      mode: 'new_workflow',
    })
  })
})

describe('targetAfterRun', () => {
  it('makes a new step or workflow once, and keeps an edit', () => {
    expect(targetAfterRun({ mode: 'new_workflow' }, 'uq')).toEqual({ mode: 'default' })
    expect(targetAfterRun({ mode: 'new_step', kind: 'uq' }, 'uq')).toEqual({ mode: 'default' })
    const other = { mode: 'new_step', kind: 'uq' }
    expect(targetAfterRun(other, 'calibration')).toBe(other)
    const edit = { mode: 'edit', step: 'mcmc-1' }
    expect(targetAfterRun(edit, 'uq')).toBe(edit)
  })
})

describe('modeSummary', () => {
  it('by default the latest step of the kind is changed', () => {
    expect(modeSummary({ mode: 'default' }, 'calibration', described)).toMatchObject({
      mode: 'edit',
      step: 'calibration-2',
    })
  })

  it('a kind with no step yet gets a new one', () => {
    const only = { ...described, workflow: { ...described.workflow, steps: [described.workflow.steps[1]] } }
    expect(modeSummary({ mode: 'default' }, 'uq', only).mode).toBe('new_step')
  })

  it('no workflow open means a new one', () => {
    expect(modeSummary({ mode: 'default' }, 'calibration', { loaded: false }).mode).toBe(
      'new_workflow',
    )
  })
})

describe('panelSettings', () => {
  const steps = Object.fromEntries(described.workflow.steps.map((s) => [s.id, s]))

  it('a calibration gives its method, options and start point, not the seed', () => {
    expect(panelSettings(steps['calibration-1'], described)).toEqual({
      num_calls_to_function: 50,
      param_id_method: 'CMA-ES',
      start_from: 'current',
      DEBUG: false,
    })
    expect(panelSettings(steps['calibration-2'], described).start_from).toBe('model')
  })

  it('a local analysis at the fit of a calibration other steps use is at its best fit', () => {
    // calibration-1 is taken by local-sa-1 only: it was made first for it
    expect(panelSettings(steps['local-sa-1'], described)).toMatchObject({
      method: 'local',
      rel_step: 0.02,
      run_calibration_first: true,
    })
  })

  it('a uq step gives its options', () => {
    expect(panelSettings(steps['mcmc-1'], described)).toEqual({
      method: 'mcmc',
      num_steps: 20,
      run_calibration_first: true,
      DEBUG: false,
    })
  })

  it('maps kinds to panels', () => {
    expect(stepPanel(undefined)).toBe('calibration')
    expect(stepPanel('uq')).toBe('uq')
    expect(stepPanel('set')).toBeNull()
  })
})
