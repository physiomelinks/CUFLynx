import { describe, it, expect } from 'vitest'
import {
  protocolToModel,
  buildProtocolInfo,
  emptyModel,
  traceName,
  shapeToCell,
  expandShape,
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

  it('declares a ramp rather than expanding it into points', () => {
    const m = emptyModel()
    addParam(m, 'a/x')
    m.experiments[0].subexps[0].duration = 4
    m.params['a/x'][0][0] = { shape: 'ramp', from: 1, to: 5 }
    const pi = buildProtocolInfo(m, null)
    const key = traceName('a/x', 0, 0)
    expect(pi.params_to_change['a/x'][0][0]).toBe(key)
    expect(pi.protocol_shapes[key]).toEqual({ type: 'ramp', from: 1, to: 5 })
    expect(pi.protocol_traces).toBeUndefined()
  })

  // A step is a single pacing event that runs to the end of the subexperiment,
  // so it needs no type of its own.
  it('declares a step as one event lasting to the end', () => {
    const m = emptyModel()
    addParam(m, 'a/x')
    m.experiments[0].subexps[0].duration = 10
    m.params['a/x'][0][0] = { shape: 'step', baseline: 0, level: 3, ts: 4 }
    const pi = buildProtocolInfo(m, null)
    expect(pi.protocol_shapes[traceName('a/x', 0, 0)]).toEqual({
      baseline: 0,
      events: [{ level: 3, start: 4, length: 6, period: 0, multiplier: 0 }],
    })
  })

  it('declares a pulse as one event that stops before the end', () => {
    const m = emptyModel()
    addParam(m, 'a/y')
    m.experiments[0].subexps[0].duration = 10
    m.params['a/y'][0][0] = { shape: 'pulse', baseline: 0, peak: 2, ts: 3, te: 7 }
    const pi = buildProtocolInfo(m, null)
    expect(pi.protocol_shapes[traceName('a/y', 0, 0)]).toEqual({
      baseline: 0,
      events: [{ level: 2, start: 3, length: 4, period: 0, multiplier: 0 }],
    })
  })

  it('declares pacing in the five fields a .mmt protocol line uses', () => {
    const m = emptyModel()
    addParam(m, 'engine/pace')
    m.experiments[0].subexps[0].duration = 2000
    m.params['engine/pace'][0][0] = {
      shape: 'paced', baseline: 0, level: 1, start: 100, length: 2, period: 1000, multiplier: 0,
    }
    const pi = buildProtocolInfo(m, null)
    expect(pi.protocol_shapes[traceName('engine/pace', 0, 0)]).toEqual({
      baseline: 0,
      events: [{ level: 1, start: 100, length: 2, period: 1000, multiplier: 0 }],
    })
  })

  // The reason for declaring rather than expanding: what you typed comes back.
  it.each([
    ['ramp', { shape: 'ramp', from: 1, to: 5 }],
    ['step', { shape: 'step', baseline: 0, level: 3, ts: 4 }],
    ['pulse', { shape: 'pulse', baseline: 0, peak: 2, ts: 3, te: 7 }],
    ['paced', { shape: 'paced', baseline: 0, level: 1, start: 1, length: 0.5, period: 2, multiplier: 0 }],
  ])('round-trips a %s cell through protocol_info', (_name, cell) => {
    const m = emptyModel()
    addParam(m, 'a/x')
    m.experiments[0].subexps[0].duration = 10
    m.params['a/x'][0][0] = { ...cell }
    const reopened = protocolToModel(buildProtocolInfo(m, null))
    expect(reopened.params['a/x'][0][0]).toEqual(cell)
  })

  // A .mmt protocol table can have several lines; the editor has no form for
  // that, so it must be preserved rather than mangled.
  it('preserves a shape it has no form for, and writes it back unchanged', () => {
    const rich = {
      baseline: 0,
      events: [
        { level: 1, start: 1, length: 1, period: 0, multiplier: 0 },
        { level: 2, start: 5, length: 1, period: 0, multiplier: 0 },
      ],
    }
    const pi = {
      pre_times: [0],
      sim_times: [[10]],
      params_to_change: { 'a/x': [['rich']] },
      protocol_shapes: { rich: rich },
    }
    const model = protocolToModel(pi)
    expect(model.params['a/x'][0][0]).toEqual({ shape: 'trace', key: 'rich' })
    expect(buildProtocolInfo(model, pi).protocol_shapes.rich).toEqual(rich)
  })

  it('preserves a hand-written protocol_trace', () => {
    const def = { t: [0, 5, 10], values: [0, 1, 0] }
    const pi = {
      pre_times: [0],
      sim_times: [[10]],
      params_to_change: { 'a/x': [['mine']] },
      protocol_traces: { mine: def },
    }
    const out = buildProtocolInfo(protocolToModel(pi), pi)
    expect(out.protocol_traces.mine).toEqual(def)
    expect(out.protocol_shapes).toBeUndefined()
  })

  it('emits neither traces nor shapes when there are none', () => {
    const pi = buildProtocolInfo(emptyModel(), null)
    expect(pi.protocol_traces).toBeUndefined()
    expect(pi.protocol_shapes).toBeUndefined()
    expect(pi.params_to_change).toEqual({})
    expect(pi.pre_times).toEqual([0])
    expect(pi.sim_times).toEqual([[1]])
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

// The editor writes declarations, but the plots draw waveforms — so the same
// expansion circulatory_autogen does on read has to exist here too, and mean the
// same thing. Myokit's rules in both.
describe('expandShape', () => {
  const paced = (over = {}) => ({
    baseline: 0,
    events: [{ level: 1, start: 100, length: 2, period: 1000, multiplier: 0, ...over }],
  })
  const at = (tr, times) =>
    times.map((x) => {
      for (let i = 1; i < tr.t.length; i++) {
        if (x <= tr.t[i]) {
          const f = (x - tr.t[i - 1]) / (tr.t[i] - tr.t[i - 1] || 1)
          return tr.values[i - 1] + f * (tr.values[i] - tr.values[i - 1])
        }
      }
      return tr.values[tr.values.length - 1]
    })

  it('repeats for as long as the sub-experiment runs when the multiplier is 0', () => {
    expect(at(expandShape(paced(), 2000), [50, 101, 500, 1101, 1500])).toEqual([0, 1, 0, 1, 0])
  })

  it('stops after the multiplier says to', () => {
    expect(at(expandShape(paced({ multiplier: 1 }), 2000), [101, 1101])).toEqual([1, 0])
  })

  it('fires once when there is no period', () => {
    expect(at(expandShape(paced({ period: 0 }), 2000), [101, 1101])).toEqual([1, 0])
  })

  it('holds the baseline between stimuli', () => {
    expect(at(expandShape({ ...paced(), baseline: -80 }, 2000), [50, 101])).toEqual([-80, 1])
  })

  it('expands a ramp across the sub-experiment', () => {
    expect(expandShape({ type: 'ramp', from: 1, to: 5 }, 4)).toEqual({ t: [0, 4], values: [1, 5] })
  })

  it('keeps a brief stimulus sharp inside a long beat', () => {
    // 2 units of stimulus in 2000: a proportional edge would erase it.
    expect(at(expandShape(paced(), 2000), [100.1, 101.9])).toEqual([1, 1])
  })

  it('never emits a duplicate instant', () => {
    const tr = expandShape(paced(), 2000)
    for (let i = 1; i < tr.t.length; i++) expect(tr.t[i]).toBeGreaterThan(tr.t[i - 1])
  })

  it('covers the whole sub-experiment', () => {
    const tr = expandShape(paced(), 2000)
    expect(tr.t[0]).toBe(0)
    expect(tr.t[tr.t.length - 1]).toBe(2000)
  })

  it('returns null for a shape it does not understand, rather than a wrong line', () => {
    expect(expandShape({ type: 'sine', amplitude: 1 }, 10)).toBeNull()
    expect(expandShape(null, 10)).toBeNull()
  })

  // A period small enough to fill the run with millions of beats would hang the
  // browser; the plot is not worth that.
  it('does not hang on a pathological period', () => {
    const tr = expandShape(paced({ start: 0, length: 1e-9, period: 1e-9 }), 1e6)
    expect(tr.t.length).toBeLessThan(1e6)
  })
})
