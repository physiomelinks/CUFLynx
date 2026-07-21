import { describe, it, expect } from 'vitest'
import {
  protocolToModel,
  buildProtocolInfo,
  emptyModel,
  traceName,
  rampTrace,
  pulseTrace,
  subexpBoundaries,
  validateModel,
  makeCell,
  addParam,
  addExperiment,
  removeExperiment,
  addSubexp,
} from './protocolInfo'

const PI = {
  pre_times: [1, 1],
  sim_times: [[1, 2], [1, 2]],
  experiment_labels: ['A', 'B'],
  params_to_change: {
    'm/I': [[0, -0.15], [0, 'ramp_port']],
    'm/g': [[0.08, 0.08], [0.12, 0.12]],
  },
  protocol_traces: { ramp_port: { t: [0, 1], values: [0, -0.3] } },
}

describe('protocolToModel', () => {
  it('parses experiments, subexps, labels, pre_times', () => {
    const m = protocolToModel(PI)
    expect(m.experiments).toHaveLength(2)
    expect(m.experiments[0]).toMatchObject({ label: 'A', preTime: 1 })
    expect(m.experiments[0].subexps.map((s) => s.duration)).toEqual([1, 2])
  })
  it('parses cells: number→constant, string→preserved trace ref', () => {
    const m = protocolToModel(PI)
    expect(m.params['m/I'][0][1]).toEqual({ shape: 'constant', value: -0.15 })
    expect(m.params['m/I'][1][1]).toEqual({ shape: 'trace', key: 'ramp_port' })
    expect(m.traces.ramp_port).toEqual({ t: [0, 1], values: [0, -0.3] })
  })
})

describe('addParam', () => {
  it('seeds every subexp constant cell with the uploaded baseline value', () => {
    const m = emptyModel()
    addParam(m, 'a/x', 1.5e-8)
    for (const expCells of m.params['a/x'])
      for (const cell of expCells) expect(cell).toEqual({ shape: 'constant', value: 1.5e-8 })
  })

  it('defaults to 0 when no baseline is known', () => {
    const m = emptyModel()
    addParam(m, 'a/x')
    expect(m.params['a/x'][0][0]).toEqual({ shape: 'constant', value: 0 })
    addParam(m, 'a/y', undefined)
    expect(m.params['a/y'][0][0].value).toBe(0)
  })
})

describe('buildProtocolInfo', () => {
  it('round-trips structure + preserved trace', () => {
    const back = buildProtocolInfo(protocolToModel(PI), PI)
    expect(back.pre_times).toEqual([1, 1])
    expect(back.sim_times).toEqual([[1, 2], [1, 2]])
    expect(back.experiment_labels).toEqual(['A', 'B'])
    expect(back.params_to_change['m/I'][0]).toEqual([0, -0.15])
    expect(back.params_to_change['m/I'][1][1]).toBe('ramp_port')
    expect(back.protocol_traces.ramp_port).toEqual({ t: [0, 1], values: [0, -0.3] })
  })

  it('generates a ramp trace from a ramp cell', () => {
    const m = emptyModel()
    addParam(m, 'a/x')
    m.experiments[0].subexps[0].duration = 4
    m.params['a/x'][0][0] = { shape: 'ramp', from: 1, to: 5 }
    const pi = buildProtocolInfo(m, null)
    const key = traceName('a/x', 0, 0)
    expect(pi.params_to_change['a/x'][0][0]).toBe(key)
    expect(pi.protocol_traces[key]).toEqual({ t: [0, 4], values: [1, 5] })
  })

  it('generates a step trace that jumps to the level and holds', () => {
    const m = emptyModel()
    addParam(m, 'a/x')
    m.experiments[0].subexps[0].duration = 10
    m.params['a/x'][0][0] = { shape: 'step', baseline: 0, level: 3, ts: 4 }
    const pi = buildProtocolInfo(m, null)
    const tr = pi.protocol_traces[traceName('a/x', 0, 0)]
    for (let i = 1; i < tr.t.length; i++) expect(tr.t[i]).toBeGreaterThan(tr.t[i - 1])
    expect(tr.values[0]).toBe(0) // baseline first
    expect(tr.values[tr.values.length - 1]).toBe(3) // holds the level to the end
    expect(tr.t[tr.t.length - 1]).toBe(10)
  })

  it('generates a strictly-increasing pulse trace reaching the peak', () => {
    const m = emptyModel()
    addParam(m, 'a/y')
    m.experiments[0].subexps[0].duration = 10
    m.params['a/y'][0][0] = { shape: 'pulse', baseline: 0, peak: 2, ts: 3, te: 7 }
    const pi = buildProtocolInfo(m, null)
    const tr = pi.protocol_traces[traceName('a/y', 0, 0)]
    for (let i = 1; i < tr.t.length; i++) expect(tr.t[i]).toBeGreaterThan(tr.t[i - 1])
    expect(tr.t[0]).toBe(0)
    expect(tr.t[tr.t.length - 1]).toBe(10)
    expect(Math.max(...tr.values)).toBe(2)
    expect(tr.values[0]).toBe(0)
  })

  it('emits no protocol_traces when there are no generated/referenced traces', () => {
    const pi = buildProtocolInfo(emptyModel(), null)
    expect(pi.protocol_traces).toBeUndefined()
    expect(pi.params_to_change).toEqual({})
    expect(pi.pre_times).toEqual([0])
    expect(pi.sim_times).toEqual([[1]])
  })
})

describe('trace generators', () => {
  it('rampTrace is two points', () => {
    expect(rampTrace(2, 8, 5)).toEqual({ t: [0, 5], values: [2, 8] })
  })
  it('pulseTrace clamps ts>te and stays strictly increasing', () => {
    const tr = pulseTrace(0, 1, 8, 3, 10) // ts>te
    for (let i = 1; i < tr.t.length; i++) expect(tr.t[i]).toBeGreaterThan(tr.t[i - 1])
    expect(tr.t[tr.t.length - 1]).toBe(10)
  })
})

describe('subexpBoundaries', () => {
  it('returns interior cumulative cut times', () => {
    expect(subexpBoundaries({ subexps: [{ duration: 1 }, { duration: 2 }, { duration: 1 }] })).toEqual([1, 3])
    expect(subexpBoundaries({ subexps: [{ duration: 5 }] })).toEqual([])
  })
})

describe('mutation helpers', () => {
  it('addExperiment/addSubexp keep param matrices rectangular; removeExperiment clamps', () => {
    const m = emptyModel()
    addParam(m, 'a/z')
    addExperiment(m)
    expect(m.experiments).toHaveLength(2)
    expect(m.params['a/z']).toHaveLength(2)
    addSubexp(m, 1)
    expect(m.params['a/z'][1]).toHaveLength(2)
    removeExperiment(m, 0)
    expect(m.experiments).toHaveLength(1)
    expect(m.params['a/z']).toHaveLength(1)
  })
})

describe('validateModel', () => {
  it('flags a pulse with start >= end', () => {
    const m = emptyModel()
    addParam(m, 'a/p')
    m.experiments[0].subexps[0].duration = 10
    m.params['a/p'][0][0] = { shape: 'pulse', baseline: 0, peak: 1, ts: 5, te: 2 }
    expect(validateModel(m).length).toBeGreaterThan(0)
  })
  it('accepts a clean model', () => {
    expect(validateModel(emptyModel())).toEqual([])
  })
})

describe('makeCell', () => {
  it('returns shape defaults', () => {
    expect(makeCell('constant')).toEqual({ shape: 'constant', value: 0 })
    expect(makeCell('ramp')).toEqual({ shape: 'ramp', from: 0, to: 0 })
    expect(makeCell('step', 4)).toEqual({ shape: 'step', baseline: 0, level: 1, ts: 2 })
    expect(makeCell('pulse', 4)).toEqual({ shape: 'pulse', baseline: 0, peak: 1, ts: 0, te: 4 })
  })
})
