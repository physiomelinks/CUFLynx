// Pure helpers for editing obs_data `protocol_info` in the GUI.
//
// circulatory_autogen has NO input-shape concept: a params_to_change entry per
// [experiment][subexperiment] is either a NUMBER (held constant over the subexp)
// or a STRING key into `protocol_traces` (an arbitrary {t, values} trace). This
// module defines GUI "shapes" (constant / ramp / pulse) that compile down to
// those native traces, and round-trips the working model <-> protocol_info.

export const SHAPES = ['constant', 'ramp', 'step', 'pulse']
// Edge sharpness for pulses, as a fraction of the subexperiment duration. Keeps
// myokit's TimeSeriesProtocol happy (strictly-increasing t) while looking ~step.
const EPS_FRACTION = 1e-3

function num(v, fallback = 0) {
  return v === '' || v == null || !Number.isFinite(Number(v)) ? fallback : Number(v)
}
function clamp(v, lo, hi) {
  return Math.min(hi, Math.max(lo, v))
}

/** A fresh cell with that shape's default fields (used when adding/switching). */
export function makeCell(shape, dur = 1) {
  switch (shape) {
    case 'ramp':
      return { shape: 'ramp', from: 0, to: 0 }
    case 'step':
      return { shape: 'step', baseline: 0, level: 1, ts: num(dur, 1) / 2 }
    case 'pulse':
      return { shape: 'pulse', baseline: 0, peak: 1, ts: 0, te: num(dur, 1) }
    case 'trace':
      return { shape: 'trace', key: '' }
    default:
      return { shape: 'constant', value: 0 }
  }
}

/** Parse a params_to_change leaf into a cell. Strings are preserved as trace refs. */
export function parseCell(value) {
  if (typeof value === 'number') return { shape: 'constant', value }
  if (typeof value === 'string') return { shape: 'trace', key: value }
  return { shape: 'constant', value: 0 }
}

export function emptyModel() {
  return {
    experiments: [{ label: 'experiment_0', preTime: 0, subexps: [{ duration: 1 }] }],
    params: {},
    traces: {},
    experimentColors: null,
    offlinePreTime: null,
    comment: null,
  }
}

export function protocolToModel(protocolInfo) {
  const pi = protocolInfo || {}
  const simTimes = pi.sim_times || []
  const preTimes = pi.pre_times || []
  const labels = pi.experiment_labels || []
  const experiments = simTimes.map((subs, e) => ({
    label: labels[e] ?? `experiment_${e}`,
    preTime: preTimes[e] ?? 0,
    subexps: (subs || []).map((d) => ({ duration: d })),
  }))
  const traces = { ...(pi.protocol_traces || {}) }
  const ptc = pi.params_to_change || {}
  const params = {}
  for (const qname of Object.keys(ptc)) {
    const matrix = ptc[qname]
    params[qname] = experiments.map((exp, e) =>
      exp.subexps.map((_s, s) => parseCell(matrix?.[e]?.[s])),
    )
  }
  return {
    experiments,
    params,
    traces,
    experimentColors: pi.experiment_colors ?? null,
    offlinePreTime: pi.offline_pre_time ?? null,
    comment: pi.comment ?? null,
  }
}

/** Deterministic name for a generated trace (stable across re-saves). */
export function traceName(qname, e, s) {
  return `${String(qname).replaceAll('/', '_')}_e${e}s${s}`
}

export function rampTrace(from, to, dur) {
  return { t: [0, num(dur, 0)], values: [num(from, 0), num(to, 0)] }
}

/**
 * A pulse: baseline `b`, raised to `p` over [ts, te] within [0, dur], with
 * near-instant edges (eps) so it reads as a step. Guarantees strictly-increasing
 * t and a final point at `dur`.
 */
export function pulseTrace(b, p, ts, te, dur) {
  const d = num(dur, 1)
  const lo = clamp(num(ts, 0), 0, d)
  const hi = clamp(num(te, d), lo, d)
  const eps = d * EPS_FRACTION
  const t = [0]
  const values = [num(b, 0)]
  const push = (tt, vv) => {
    const x = clamp(tt, 0, d)
    if (x > t[t.length - 1]) {
      t.push(x)
      values.push(vv)
    } else {
      values[values.length - 1] = vv // collision: take the transition target
    }
  }
  if (lo > 0) push(lo, num(b, 0)) // hold baseline up to ts
  push(Math.min(lo + eps, d), num(p, 0)) // rise to peak
  if (hi > lo + eps) push(hi, num(p, 0)) // hold peak to te
  if (hi < d) {
    push(Math.min(hi + eps, d), num(b, 0)) // fall back to baseline
    push(d, num(b, 0))
  } else {
    push(d, num(p, 0)) // pulse runs to the end of the subexp
  }
  return { t, values }
}

/** A step: baseline until ts, then jumps to `level` and holds to the end. */
export function stepTrace(baseline, level, ts, dur) {
  const d = num(dur, 1)
  const t0 = clamp(num(ts, 0), 0, d)
  const eps = d * EPS_FRACTION
  const t = [0]
  const values = [num(baseline, 0)]
  const push = (tt, vv) => {
    const x = clamp(tt, 0, d)
    if (x > t[t.length - 1]) {
      t.push(x)
      values.push(vv)
    } else {
      values[values.length - 1] = vv
    }
  }
  if (t0 > 0) push(t0, num(baseline, 0)) // hold baseline up to the step
  push(Math.min(t0 + eps, d), num(level, 0)) // jump to level
  push(d, num(level, 0)) // hold to the end
  return { t, values }
}

/** Compile a cell to its params_to_change leaf + an optional generated trace. */
export function compileCell(cell, dur, qname, e, s) {
  switch (cell?.shape) {
    case 'ramp': {
      const name = traceName(qname, e, s)
      return { value: name, trace: { name, def: rampTrace(cell.from, cell.to, dur) } }
    }
    case 'step': {
      const name = traceName(qname, e, s)
      return { value: name, trace: { name, def: stepTrace(cell.baseline, cell.level, cell.ts, dur) } }
    }
    case 'pulse': {
      const name = traceName(qname, e, s)
      return {
        value: name,
        trace: { name, def: pulseTrace(cell.baseline, cell.peak, cell.ts, cell.te, dur) },
      }
    }
    case 'trace':
      return { value: cell.key }
    default:
      return { value: num(cell?.value, 0) }
  }
}

export function buildProtocolInfo(model, original = null) {
  const experiments = model.experiments || []
  const pre_times = experiments.map((e) => num(e.preTime, 0))
  const sim_times = experiments.map((e) => (e.subexps || []).map((s) => num(s.duration, 0)))
  const experiment_labels = experiments.map((e, i) => e.label ?? `experiment_${i}`)

  const params_to_change = {}
  const generated = {}
  const referencedPreserved = {}
  for (const qname of Object.keys(model.params || {})) {
    const matrix = model.params[qname]
    params_to_change[qname] = experiments.map((exp, e) =>
      (exp.subexps || []).map((sub, s) => {
        const cell = matrix?.[e]?.[s] ?? { shape: 'constant', value: 0 }
        const dur = num(sub.duration, 0)
        const { value, trace } = compileCell(cell, dur, qname, e, s)
        if (trace) generated[trace.name] = trace.def
        else if (cell.shape === 'trace' && model.traces?.[cell.key]) {
          referencedPreserved[cell.key] = model.traces[cell.key]
        }
        return value
      }),
    )
  }

  const result = { pre_times, sim_times, params_to_change, experiment_labels }
  const protocol_traces = { ...referencedPreserved, ...generated }
  if (Object.keys(protocol_traces).length) result.protocol_traces = protocol_traces

  const colors = model.experimentColors ?? original?.experiment_colors
  if (Array.isArray(colors)) result.experiment_colors = colors.slice(0, experiments.length)
  if (model.offlinePreTime != null) result.offline_pre_time = model.offlinePreTime
  if (model.comment != null) result.comment = model.comment
  return result
}

/** Total simulated time of an experiment (sum of its subexperiment durations). */
export function experimentTotalSim(experiment) {
  return (experiment?.subexps ?? []).reduce((acc, s) => acc + num(s.duration, 0), 0)
}

/** Interior subexperiment boundary times (for vertical dashed plot lines). */
export function subexpBoundaries(experiment) {
  const out = []
  const subs = experiment?.subexps ?? []
  let acc = 0
  for (let i = 0; i < subs.length - 1; i++) {
    acc += num(subs[i].duration, 0)
    out.push(acc)
  }
  return out
}

export function validateModel(model) {
  const errors = []
  if (!model?.experiments?.length) {
    errors.push('At least one experiment is required')
    return errors
  }
  model.experiments.forEach((exp, e) => {
    if (!exp.subexps?.length) errors.push(`Experiment ${e} needs at least one subexperiment`)
    if (num(exp.preTime, 0) < 0) errors.push(`Experiment ${e}: pre_time must be ≥ 0`)
    ;(exp.subexps || []).forEach((s, si) => {
      if (!(num(s.duration, 0) > 0))
        errors.push(`Experiment ${e} subexp ${si}: duration must be > 0`)
    })
  })
  for (const qname of Object.keys(model.params || {})) {
    const matrix = model.params[qname]
    model.experiments.forEach((exp, e) => {
      const row = matrix?.[e]
      if (!Array.isArray(row) || row.length !== exp.subexps.length) {
        errors.push(`Param ${qname}: wrong shape for experiment ${e}`)
        return
      }
      row.forEach((cell, s) => {
        const dur = num(exp.subexps[s].duration, 0)
        if (cell.shape === 'pulse') {
          const ts = num(cell.ts, 0)
          const te = num(cell.te, dur)
          if (!(ts < te)) errors.push(`Param ${qname} e${e}s${s}: pulse start must be < end`)
          if (ts < 0 || te > dur)
            errors.push(`Param ${qname} e${e}s${s}: pulse times must be within [0, ${dur}]`)
        } else if (cell.shape === 'step') {
          const ts = num(cell.ts, 0)
          if (ts < 0 || ts > dur)
            errors.push(`Param ${qname} e${e}s${s}: step time must be within [0, ${dur}]`)
        }
      })
    })
  }
  return errors
}

// --- In-place mutation helpers (the editor owns the reactive model) -----------

export function addExperiment(model) {
  const e = model.experiments.length
  model.experiments.push({ label: `experiment_${e}`, preTime: 0, subexps: [{ duration: 1 }] })
  for (const qname of Object.keys(model.params)) {
    model.params[qname].push([makeCell('constant')])
  }
  if (Array.isArray(model.experimentColors)) model.experimentColors.push('r')
}

export function removeExperiment(model, e) {
  model.experiments.splice(e, 1)
  for (const qname of Object.keys(model.params)) model.params[qname].splice(e, 1)
  if (Array.isArray(model.experimentColors)) model.experimentColors.splice(e, 1)
}

export function addSubexp(model, e) {
  model.experiments[e].subexps.push({ duration: 1 })
  for (const qname of Object.keys(model.params)) model.params[qname][e].push(makeCell('constant'))
}

export function removeSubexp(model, e, s) {
  model.experiments[e].subexps.splice(s, 1)
  for (const qname of Object.keys(model.params)) model.params[qname][e].splice(s, 1)
}

export function addParam(model, qname, baseline) {
  if (!qname || model.params[qname]) return
  // Start every subexp at the param's uploaded value (the baseline the user will
  // change from); fall back to 0 when it's unknown.
  const value = Number.isFinite(Number(baseline)) ? Number(baseline) : 0
  model.params[qname] = model.experiments.map((exp) =>
    exp.subexps.map(() => ({ shape: 'constant', value })),
  )
}

export function removeParam(model, qname) {
  delete model.params[qname]
}
