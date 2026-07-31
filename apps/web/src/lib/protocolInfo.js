// Pure helpers for editing obs_data `protocol_info` in the GUI.
//
// A params_to_change entry per [experiment][subexperiment] is either a NUMBER
// (held constant over the subexp) or a STRING naming a time-varying input. The
// name resolves to `protocol_shapes` -- a declaration of the input, in Myokit's
// [[protocol]] vocabulary -- which circulatory_autogen expands into the
// `protocol_traces` point table the solver wants.
//
// The editor writes shapes, not traces. A pulse saved as forty interpolation
// points can be simulated but not read back: reopening the file would show a
// nameless waveform rather than the four numbers that were typed. Written as a
// shape it round-trips, and it is legible to anyone who opens the JSON.
//
// Hand-written `protocol_traces` are still understood and preserved untouched --
// they are the escape hatch for a waveform no shape describes.

export const SHAPES = ['constant', 'ramp', 'step', 'pulse', 'paced']

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
    case 'paced':
      // Myokit's five columns. A period of one tenth of the subexperiment gives
      // ten beats, which is a protocol rather than a single stimulus that
      // happens to be at the start.
      return {
        shape: 'paced',
        baseline: 0,
        level: 1,
        start: 0,
        length: num(dur, 1) / 100,
        period: num(dur, 1) / 10,
        multiplier: 0,
      }
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
    opaqueShapes: {},
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
  const shapes = { ...(pi.protocol_shapes || {}) }
  const ptc = pi.params_to_change || {}
  const params = {}
  // Shapes the editor has no form for (several events in one, an unknown type)
  // are kept verbatim and written back untouched, the same way hand-written
  // traces are. Nothing the user wrote is lost by opening the dialog.
  const opaque = {}
  for (const qname of Object.keys(ptc)) {
    const matrix = ptc[qname]
    params[qname] = experiments.map((exp, e) =>
      exp.subexps.map((sub, s) => {
        const leaf = matrix?.[e]?.[s]
        if (typeof leaf === 'string' && shapes[leaf]) {
          const cell = shapeToCell(shapes[leaf], sub.duration)
          if (cell) return cell
          opaque[leaf] = shapes[leaf]
        }
        return parseCell(leaf)
      }),
    )
  }
  return {
    experiments,
    params,
    traces,
    opaqueShapes: opaque,
    experimentColors: pi.experiment_colors ?? null,
    offlinePreTime: pi.offline_pre_time ?? null,
    comment: pi.comment ?? null,
  }
}

/** Deterministic name for a generated trace (stable across re-saves). */
export function traceName(qname, e, s) {
  return `${String(qname).replaceAll('/', '_')}_e${e}s${s}`
}

/**
 * Compile a cell to its params_to_change leaf plus, for a time-varying input,
 * the protocol_shapes entry it names.
 *
 * A step and a pulse are both single non-repeating pacing events -- a step is
 * simply one that runs to the end of the subexperiment -- so they share the
 * events form rather than each getting a type of their own. A ramp is a linear
 * sweep and cannot be written as a square event, so it has its own type.
 */
export function compileCell(cell, dur, qname, e, s) {
  const d = num(dur, 1)
  const name = traceName(qname, e, s)
  switch (cell?.shape) {
    case 'ramp':
      return {
        value: name,
        shape: { name, def: { type: 'ramp', from: num(cell.from, 0), to: num(cell.to, 0) } },
      }
    case 'step': {
      const ts = clamp(num(cell.ts, 0), 0, d)
      return {
        value: name,
        shape: {
          name,
          def: {
            baseline: num(cell.baseline, 0),
            events: [
              {
                level: num(cell.level, 0),
                start: ts,
                length: Math.max(d - ts, 0),
                period: 0,
                multiplier: 0,
              },
            ],
          },
        },
      }
    }
    case 'pulse': {
      const ts = clamp(num(cell.ts, 0), 0, d)
      const te = clamp(num(cell.te, d), ts, d)
      return {
        value: name,
        shape: {
          name,
          def: {
            baseline: num(cell.baseline, 0),
            events: [
              {
                level: num(cell.peak, 0),
                start: ts,
                length: Math.max(te - ts, 0),
                period: 0,
                multiplier: 0,
              },
            ],
          },
        },
      }
    }
    case 'paced':
      return {
        value: name,
        shape: {
          name,
          def: {
            baseline: num(cell.baseline, 0),
            events: [
              {
                level: num(cell.level, 0),
                start: num(cell.start, 0),
                length: num(cell.length, 0),
                period: num(cell.period, 0),
                multiplier: num(cell.multiplier, 0),
              },
            ],
          },
        },
      }
    case 'trace':
      return { value: cell.key }
    default:
      return { value: num(cell?.value, 0) }
  }
}

/**
 * Expand a protocol_shapes entry into the `{t, values}` waveform it describes.
 *
 * circulatory_autogen does this too, when it reads the file -- this copy exists
 * so the editor and the plots can draw a shape without a round trip to the
 * server. The two must agree, so the rules are Myokit's in both: a period of 0
 * fires once, a multiplier of 0 repeats for as long as the sub-experiment runs,
 * and the value outside every event is the baseline.
 *
 * The edges are near-vertical rather than vertical because the waveform is
 * interpolated linearly downstream; a genuine discontinuity cannot be expressed
 * as two points at the same instant.
 */
export function expandShape(def, dur) {
  const d = num(dur, 1)
  if (!def || typeof def !== 'object' || d <= 0) return null
  if (def.type === 'ramp') return { t: [0, d], values: [num(def.from, 0), num(def.to, 0)] }
  if (def.type && def.type !== 'pacing') return null

  const events = Array.isArray(def.events) ? def.events : []
  const baseline = num(def.baseline, 0)

  const spans = []
  for (const ev of events) {
    const level = num(ev.level, 0)
    const length = num(ev.length ?? ev.duration, 0)
    const period = num(ev.period, 0)
    const multiplier = num(ev.multiplier, 0)
    if (!(length > 0)) continue
    let when = num(ev.start, 0)
    let fired = 0
    while (when < d) {
      spans.push([when, Math.min(when + length, d), level])
      fired += 1
      if (period <= 0) break
      if (multiplier && fired >= multiplier) break
      if (fired > 1e5) break // a pathological period must not hang the browser
      when += period
    }
  }
  if (!spans.length) return { t: [0, d], values: [baseline, baseline] }
  spans.sort((a, b) => a[0] - b[0])

  // An edge has to be short next to the shortest feature, or a brief stimulus
  // inside a long beat is ramped away instead of drawn.
  const features = spans.map(([a, b]) => b - a)
  for (let i = 1; i < spans.length; i++) {
    if (spans[i][0] > spans[i - 1][1]) features.push(spans[i][0] - spans[i - 1][1])
  }
  if (spans[0][0] > 0) features.push(spans[0][0])
  if (spans[spans.length - 1][1] < d) features.push(d - spans[spans.length - 1][1])
  const smallest = Math.min(...features.filter((f) => f > 0))
  const eps = Math.max(smallest * 1e-3, d * 1e-12)

  const t = [0]
  const values = [baseline]
  const push = (when, value) => {
    const x = clamp(when, 0, d)
    if (x > t[t.length - 1]) {
      t.push(x)
      values.push(value)
    } else {
      values[values.length - 1] = value
    }
  }
  for (const [start, end, level] of spans) {
    if (start > 0) push(start, baseline)
    push(Math.min(start + eps, d), level)
    if (end > start + eps) push(end, level)
    if (end < d) push(Math.min(end + eps, d), baseline)
  }
  const last = spans[spans.length - 1]
  push(d, last[1] < d ? baseline : last[2])
  return { t, values }
}

/**
 * Read a protocol_shapes entry back as an editor cell, so a saved protocol
 * reopens as the fields that were typed rather than as an opaque waveform.
 *
 * Returns null for a shape the editor has no form for -- several events in one
 * shape, say, which a .mmt protocol table can have. Those are preserved
 * verbatim instead of being flattened into something the editor can draw.
 */
export function shapeToCell(def, dur) {
  const d = num(dur, 1)
  if (!def || typeof def !== 'object') return null
  if (def.type === 'ramp') return { shape: 'ramp', from: num(def.from, 0), to: num(def.to, 0) }
  if (def.type && def.type !== 'pacing') return null

  const events = def.events
  if (!Array.isArray(events) || events.length !== 1) return null
  const ev = events[0]
  const baseline = num(def.baseline, 0)
  const level = num(ev.level, 0)
  const start = num(ev.start, 0)
  const length = num(ev.length ?? ev.duration, 0)
  const period = num(ev.period, 0)
  const multiplier = num(ev.multiplier, 0)

  if (period > 0) {
    return { shape: 'paced', baseline, level, start, length, period, multiplier }
  }
  // A single event that runs to the end of the subexperiment is a step; one that
  // stops earlier is a pulse. They are the same declaration either way -- this
  // only picks which set of fields the editor shows.
  if (start + length >= d) return { shape: 'step', baseline, level, ts: start }
  return { shape: 'pulse', baseline, peak: level, ts: start, te: start + length }
}

export function buildProtocolInfo(model, original = null) {
  const experiments = model.experiments || []
  const pre_times = experiments.map((e) => num(e.preTime, 0))
  const sim_times = experiments.map((e) => (e.subexps || []).map((s) => num(s.duration, 0)))
  const experiment_labels = experiments.map((e, i) => e.label ?? `experiment_${i}`)

  const params_to_change = {}
  const generated = {}
  const referencedPreserved = {}
  const preservedShapes = {}
  for (const qname of Object.keys(model.params || {})) {
    const matrix = model.params[qname]
    params_to_change[qname] = experiments.map((exp, e) =>
      (exp.subexps || []).map((sub, s) => {
        const cell = matrix?.[e]?.[s] ?? { shape: 'constant', value: 0 }
        const dur = num(sub.duration, 0)
        const { value, shape } = compileCell(cell, dur, qname, e, s)
        if (shape) generated[shape.name] = shape.def
        else if (cell.shape === 'trace') {
          // A reference the editor did not author: either a hand-written trace
          // or a shape too rich for the editor's forms. Whichever it is, it is
          // written back exactly as it came in.
          if (model.opaqueShapes?.[cell.key]) preservedShapes[cell.key] = model.opaqueShapes[cell.key]
          else if (model.traces?.[cell.key]) referencedPreserved[cell.key] = model.traces[cell.key]
        }
        return value
      }),
    )
  }

  const result = { pre_times, sim_times, params_to_change, experiment_labels }
  if (Object.keys(referencedPreserved).length) result.protocol_traces = referencedPreserved
  const protocol_shapes = { ...preservedShapes, ...generated }
  if (Object.keys(protocol_shapes).length) result.protocol_shapes = protocol_shapes

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
        } else if (cell.shape === 'paced') {
          // The same rules circulatory_autogen applies, checked here so the
          // dialog says so before the file is written rather than after.
          const start = num(cell.start, 0)
          const length = num(cell.length, 0)
          const period = num(cell.period, 0)
          const mult = num(cell.multiplier, 0)
          if (!(length > 0))
            errors.push(`Param ${qname} e${e}s${s}: pacing length must be > 0`)
          if (start < 0 || start >= dur)
            errors.push(`Param ${qname} e${e}s${s}: pacing start must be within [0, ${dur})`)
          if (period < 0) errors.push(`Param ${qname} e${e}s${s}: pacing period must be ≥ 0`)
          if (period > 0 && length > period)
            errors.push(
              `Param ${qname} e${e}s${s}: pacing length (${length}) must fit inside the period (${period})`,
            )
          if (period === 0 && mult > 1)
            errors.push(
              `Param ${qname} e${e}s${s}: pacing repeats need a period, or every repeat lands on the first`,
            )
          if (mult < 0 || mult !== Math.round(mult))
            errors.push(`Param ${qname} e${e}s${s}: pacing multiplier must be a whole number ≥ 0`)
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
