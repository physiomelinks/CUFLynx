import { describe, it, expect } from 'vitest'
import {
  solversForFormat,
  defaultSolverFor,
  solverFields,
  solverFieldsForMethod,
  defaultSolverInfo,
  obsDataOperations,
  nonDifferentiableInUse,
} from './solverConfig'

const opts = {
  model_formats: ['cellml_only', 'python', 'casadi_python'],
  solvers_by_format: {
    cellml_only: ['CVODE_myokit', 'CVODE_opencor'],
    python: ['solve_ivp'],
    casadi_python: ['casadi_integrator'],
  },
  default_solver_by_format: {
    cellml_only: 'CVODE_myokit',
    python: 'solve_ivp',
    casadi_python: 'casadi_integrator',
  },
  solver_info_schema: {
    solve_ivp: [
      { key: 'method', label: 'Method', type: 'select', default: 'RK45', options: ['RK45', 'BDF'] },
      { key: 'rtol', label: 'Rel. tol', type: 'number', default: 1e-6 },
      { key: 'max_step', label: 'Max step', type: 'number', default: null },
    ],
  },
}

describe('solverConfig helpers', () => {
  it('lists solvers for a format and the default solver', () => {
    expect(solversForFormat(opts, 'python')).toEqual(['solve_ivp'])
    expect(defaultSolverFor(opts, 'casadi_python')).toBe('casadi_integrator')
  })

  it('falls back to the first valid solver when no explicit default', () => {
    const o = { solvers_by_format: { python: ['solve_ivp'] } }
    expect(defaultSolverFor(o, 'python')).toBe('solve_ivp')
    expect(defaultSolverFor(opts, 'unknown')).toBe('')
  })

  it('returns the schema fields for a solver', () => {
    expect(solverFields(opts, 'solve_ivp').map((f) => f.key)).toEqual([
      'method',
      'rtol',
      'max_step',
    ])
    expect(solverFields(opts, 'nope')).toEqual([])
  })

  it('seeds solver_info from non-null schema defaults', () => {
    // max_step has a null default, so it's omitted from the seed.
    expect(defaultSolverInfo(opts, 'solve_ivp')).toEqual({ method: 'RK45', rtol: 1e-6 })
    expect(defaultSolverInfo(opts, 'nope')).toEqual({})
  })

  it('seeds boolean solver_info fields (false is kept, not treated as null)', () => {
    // Introspected CA bool fields (e.g. vectorized/dense_output) must seed their
    // false default rather than being dropped by the non-null check.
    const o = {
      solver_info_schema: {
        s: [
          { key: 'vectorized', type: 'bool', default: false },
          { key: 'dense_output', type: 'bool', default: true },
        ],
      },
    }
    expect(defaultSolverInfo(o, 's')).toEqual({ vectorized: false, dense_output: true })
  })
})

describe('solverFieldsForMethod', () => {
  const opts2 = {
    solver_info_schema: {
      casadi_integrator: [
        { key: 'method', type: 'select', options: ['cvodes', 'semi_implicit_euler', 'bdf'] },
        { key: 'dt', type: 'number', default: 0.01 },
        { key: 'reltol', type: 'number', methods: ['cvodes'] },
        { key: 'abstol', type: 'number', methods: ['cvodes'] },
        { key: 'max_step', type: 'number', default: 1e-3, methods: ['bdf'] },
      ],
    },
  }

  it('shows method + unrestricted fields + only the matching restricted fields', () => {
    const cvodes = solverFieldsForMethod(opts2, 'casadi_integrator', 'cvodes').map((f) => f.key)
    expect(cvodes).toEqual(['method', 'dt', 'reltol', 'abstol'])

    // semi_implicit_euler: tolerance fields drop out; method + dt remain.
    const sie = solverFieldsForMethod(opts2, 'casadi_integrator', 'semi_implicit_euler').map((f) => f.key)
    expect(sie).toEqual(['method', 'dt'])

    // bdf: exposes its internal sub-step cap (max_step) but not the cvodes tolerances.
    const bdf = solverFieldsForMethod(opts2, 'casadi_integrator', 'bdf').map((f) => f.key)
    expect(bdf).toEqual(['method', 'dt', 'max_step'])
  })
})

describe('obs_data operation differentiability (in use)', () => {
  const obs = {
    data_items: [
      { operation: 'max' },
      { operation: 'calc_spike_period' },
      { operation: 'max' }, // duplicate
      { operation: 'None' }, // ignored
      { operation: '' }, // ignored
    ],
    prediction_items: [{ operation: 'mean' }],
  }
  const diffMap = { max: true, mean: true, calc_spike_period: false }

  it('collects the distinct meaningful operations actually used', () => {
    expect(obsDataOperations(obs)).toEqual(['max', 'calc_spike_period', 'mean'])
    expect(obsDataOperations(null)).toEqual([])
  })

  it('returns only the in-use operations that are not differentiable', () => {
    expect(nonDifferentiableInUse(obs, diffMap)).toEqual(['calc_spike_period'])
  })

  it('treats an operation missing from the map as not differentiable', () => {
    const o = { data_items: [{ operation: 'mystery' }] }
    expect(nonDifferentiableInUse(o, diffMap)).toEqual(['mystery'])
  })

  it('is empty when every used operation is differentiable', () => {
    const o = { data_items: [{ operation: 'max' }], prediction_items: [{ operation: 'mean' }] }
    expect(nonDifferentiableInUse(o, diffMap)).toEqual([])
  })
})
