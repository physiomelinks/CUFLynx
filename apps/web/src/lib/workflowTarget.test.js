import { describe, expect, it } from 'vitest'
import {
  linkChoices,
  referencesOf,
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
      { id: 'calibration-3', description: 'calibrated first for step local-sa-1', settings: {} },
      {
        id: 'local-sa-1',
        kind: 'sensitivity',
        result_from: 'calibration-3',
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

  it('a local analysis at the calibration made first for it says so', () => {
    // calibration-3's description says the run made it for local-sa-1
    expect(panelSettings(steps['local-sa-1'], described)).toMatchObject({
      method: 'local',
      rel_step: 0.02,
      run_calibration_first: true,
    })
  })

  it('a reduction gives its options and the Sobol run of the step it reduces', () => {
    const step = { id: 'reduction-1', kind: 'reduction', sensitivity_from: 'sobol-1',
      settings: { param_reduction_options: { method: 'sobol', n_params: 2 } } }
    const run = { ...described, output_dir: '/w/workflows/study/' }
    expect(panelSettings(step, run)).toEqual({ param_reduction_options: {
      method: 'sobol', n_params: 2, sensitivity_dir: '/w/workflows/study/sobol-1/sensitivity' } })
    const windows = { ...described, output_dir: 'C:\\w\\workflows\\study' }
    expect(panelSettings(step, windows).param_reduction_options.sensitivity_dir).toBe(
      'C:\\w\\workflows\\study\\sobol-1\\sensitivity')
    // a reduction chosen by hand reads no Sobol run
    const manual = { id: 'r', kind: 'reduction',
      settings: { param_reduction_options: { method: 'manual' } } }
    expect(panelSettings(manual, run)).toEqual({ param_reduction_options: { method: 'manual' } })
  })

  it("a step at a calibration of the user's own is at its best fit, not after a fresh one", () => {
    const local = { ...steps['local-sa-1'], result_from: 'calibration-1' }
    expect(panelSettings(local, described)).toMatchObject({
      run_calibration_first: false,
      nominal: 'best_fit',
    })
  })

  it('a uq step gives its options; the only step at a calibration is not its maker', () => {
    expect(panelSettings(steps['mcmc-1'], described)).toEqual({
      method: 'mcmc',
      num_steps: 20,
      run_calibration_first: false,
      DEBUG: false,
    })
  })

  it('maps kinds to panels: a surrogate to its tab, identifiability and reduction to theirs', () => {
    expect(stepPanel(undefined)).toBe('calibration')
    expect(stepPanel('uq')).toBe('uq')
    expect(stepPanel('emulation')).toBe('emulator')
    expect(stepPanel('identifiability')).toBe('ia')
    expect(stepPanel('reduction')).toBe('ia')
    expect(stepPanel('set')).toBeNull()
  })

  it('a surrogate step gives its training options, not the seed or the bundle dir', () => {
    const step = { id: 'surrogate-1', kind: 'emulation', settings: { emulator_settings: {
      models: ['RBF'], num_train_samples: 64, random_seed: 1, emulator_dir: 'x' } } }
    expect(panelSettings(step, described)).toEqual({ models: ['RBF'], num_train_samples: 64, DEBUG: false })
  })
})

describe('New step: whatever runs next', () => {
  it('applies to a run of any kind, once', () => {
    const target = { mode: 'new_step' }
    for (const kind of ['calibration', 'sensitivity', 'uq', 'emulation']) {
      expect(runTargetFor(target, kind, described).mode).toBe('new_step')
    }
    expect(targetAfterRun(target, 'emulation')).toEqual({ mode: 'default' })
    expect(modeSummary(target, 'uq', described).text).toContain('whatever you run next')
  })
})

describe('combine steps (CA #685)', () => {
  const linkKinds = {
    fixed_from: { sources: ['calibration', 'set', 'combine'], users: ['calibration', 'uq'] },
    combine_from: { sources: ['calibration', 'set', 'combine'], users: ['combine'] },
  }
  it('a combine step takes from each step it combines', () => {
    expect(referencesOf({ id: 'best', kind: 'combine', combine_from: ['a', 'b'] }))
      .toEqual([['combine_from', 'a'], ['combine_from', 'b']])
  })
  it('an arrow into a combine box can only be combined; a plain new step is never offered it', () => {
    const fit = { id: 'a' }
    expect(linkChoices(linkKinds, fit, { id: 'new-1', draft: true, action: 'combine' })).toEqual(['combine_from'])
    expect(linkChoices(linkKinds, fit, { id: 'new-2', draft: true })).toEqual(['fixed_from'])
    expect(linkChoices(linkKinds, { id: 'new-1', draft: true, action: 'combine' }, { id: 'c' }))
      .toEqual(['fixed_from'])
  })
})
