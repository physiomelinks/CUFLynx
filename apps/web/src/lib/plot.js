export const PALETTE = [
  '#5b9bd5',
  '#ed7d31',
  '#70ad47',
  '#ffc000',
  '#a142f4',
  '#e84a5f',
]

/**
 * Colours for saved-run overlays (#126), deliberately disjoint from PALETTE.
 *
 * Saved runs used to take a PALETTE colour at an offset, which meant they
 * collided with whatever obs/calc reference lines the same cell had drawn —
 * a saved trace came out the same green as a `max` measurement, so the colour
 * stopped identifying anything. These hues appear nowhere in PALETTE, so a
 * colour on a plot answers "live trace or obs?" vs "saved run?" on its own.
 *
 * Grey leads: the first saved run is the common case, and a neutral reads as
 * "an earlier version of this" rather than as another measurement.
 */
export const SAVED_PALETTE = [
  '#7f7f7f', // grey
  '#e377c2', // pink
  '#8c564b', // brown
  '#17becf', // cyan
  '#414487', // dark indigo
]

const TIME_NAMES = new Set(['time', 't'])

function color(i) {
  return PALETTE[i % PALETTE.length]
}

/** Blend a #rrggbb colour toward white by t in [0, 1] (0 = base, 1 = white). */
export function lighten(hex, t) {
  const c = String(hex).replace('#', '')
  const n = parseInt(c, 16)
  const r = (n >> 16) & 0xff
  const g = (n >> 8) & 0xff
  const b = n & 0xff
  const mix = (x) => Math.round(x + (255 - x) * t)
  const h = (x) => mix(x).toString(16).padStart(2, '0')
  return `#${h(r)}${h(g)}${h(b)}`
}

/**
 * A distinct shade of `hex` for start `s` of `n` starts: start 0 is the base
 * colour, later starts progressively lighter (up to 60% toward white), so a
 * family of multi-start lines reads as one hue in graduated shades.
 */
export function shadeForStart(hex, s, n) {
  if (n <= 1) return hex
  return lighten(hex, (s / (n - 1)) * 0.6)
}

function toXY(time, values) {
  if (!time || !values) return []
  const n = Math.min(time.length, values.length)
  const out = new Array(n)
  for (let i = 0; i < n; i++) out[i] = { x: time[i], y: values[i] }
  return out
}

function minOf(a) {
  let m = Infinity
  for (const v of a) if (v < m) m = v
  return m
}
function maxOf(a) {
  let m = -Infinity
  for (const v of a) if (v > m) m = v
  return m
}

/** The model variable a data_item attaches to (operands minus the time axis). */
export function obsModelVar(item) {
  if (Array.isArray(item.operands) && item.operands.length) {
    const v = item.operands.find(
      (o) => !TIME_NAMES.has(String(o).split('/').pop()),
    )
    if (v) return v
  }
  return item.variable
}

/** A data_item that renders as a reference line (horizontal or vertical). */
export function isPlottableOverlay(item) {
  if (item.data_type === 'frequency') return false // frequency overlays: future work
  const pt = item.plot_type
  // 'horizontal', 'horizontal_from_min', ... and 'vertical'.
  return (typeof pt === 'string' && pt.startsWith('horizontal')) || pt === 'vertical'
}

/**
 * Compute a data_item's feature (its `operation`) from a simulated trace, so the
 * calculated value can be compared against the experimental `value`.
 * Returns { value, at } where `at` is the time of a max/min (or null), or null
 * if the operation is unsupported.
 */
export function computeFeature(operation, time, values) {
  if (!values || !values.length) return null
  const n = values.length
  switch (operation) {
    case 'max': {
      let m = values[0]
      let idx = 0
      for (let i = 1; i < n; i++) if (values[i] > m) ((m = values[i]), (idx = i))
      return { value: m, at: time?.[idx] ?? null }
    }
    case 'min': {
      let m = values[0]
      let idx = 0
      for (let i = 1; i < n; i++) if (values[i] < m) ((m = values[i]), (idx = i))
      return { value: m, at: time?.[idx] ?? null }
    }
    case 'mean': {
      let s = 0
      for (const v of values) s += v
      return { value: s / n, at: null }
    }
    case 'max_minus_min':
      return { value: maxOf(values) - minOf(values), at: null }
    case 'first_peak_time': {
      for (let i = 1; i < n - 1; i++) {
        if (values[i] > values[i - 1] && values[i] >= values[i + 1]) {
          return { value: time?.[i] ?? null, at: time?.[i] ?? null }
        }
      }
      return null
    }
    default:
      return null
  }
}

/**
 * Variables worth plotting, derived from an obs_data response: every
 * prediction_item variable plus every model variable referenced by a plottable
 * (horizontal/vertical) data_item. Returns [{ qname, label }] de-duplicated,
 * preferring a name_for_plotting label.
 */
export function derivePlotVariables(obsData) {
  if (!obsData) return []
  const map = new Map()
  for (const p of obsData.prediction_items ?? []) {
    if (p.variable && !map.has(p.variable)) {
      map.set(p.variable, p.name_for_plotting ?? p.variable)
    }
  }
  for (const d of obsData.data_items ?? []) {
    if (!isPlottableOverlay(d)) continue
    const v = obsModelVar(d)
    if (v && !map.has(v)) map.set(v, d.name_for_plotting ?? v)
  }
  return [...map.entries()].map(([qname, label]) => ({ qname, label }))
}

/**
 * Build a time series for each controlled parameter (protocol_info
 * params_to_change) in an experiment. Numeric sub-values render as a step held
 * over each sub-experiment; a string sub-value references a protocol_traces key
 * and is plotted as that trace, offset to the sub-experiment start.
 * Returns [{ qname, label, time, values }].
 */
export function controlledSeries(protocolInfo, expIdx) {
  if (!protocolInfo) return []
  const ptc = protocolInfo.params_to_change ?? {}
  const traces = protocolInfo.protocol_traces ?? {}
  const durations = (protocolInfo.sim_times ?? [])[expIdx] ?? []

  const starts = []
  let acc = 0
  for (const d of durations) {
    starts.push(acc)
    acc += d
  }

  const out = []
  for (const qname of Object.keys(ptc)) {
    const matrix = ptc[qname]
    const subVals = Array.isArray(matrix) ? matrix[expIdx] : undefined
    if (!Array.isArray(subVals)) continue

    const time = []
    const values = []
    for (let k = 0; k < subVals.length; k++) {
      const start = starts[k] ?? 0
      const dur = durations[k] ?? 0
      const val = subVals[k]
      if (typeof val === 'string') {
        const tr = traces[val]
        if (tr && Array.isArray(tr.t) && Array.isArray(tr.values)) {
          const m = Math.min(tr.t.length, tr.values.length)
          for (let i = 0; i < m; i++) {
            time.push(start + tr.t[i])
            values.push(tr.values[i])
          }
        }
      } else {
        // held constant over the sub-experiment -> a step
        time.push(start, start + dur)
        values.push(val, val)
      }
    }
    if (time.length) out.push({ qname, label: qname, time, values })
  }
  return out
}

/**
 * Plot cells for user-added ("Add plot") outputs scoped to one experiment
 * group. Each entry of `extraPlots` is { id, groupKey, qname, xqname, label };
 * only those whose `groupKey` matches build a single-variable cell from this
 * group's own `outputs`/`time`.
 *
 * `xqname` (issue #124) makes the cell a phase-plane plot: the named variable's
 * series becomes the x axis instead of time (e.g. a PV loop). Unset (the
 * default) keeps the plain time-series cell.
 */
export function buildExtraPlotCells(extraPlots, groupKey, time, outputs, units) {
  return (extraPlots ?? [])
    .filter((p) => p.groupKey === groupKey)
    .map((p) => ({
      key: `extra:${p.id}`,
      title: p.label,
      varLabel: p.label,
      // The model variables this cell draws, so callers can look up per-variable
      // extras (saved-run overlays, #126) without parsing the label back.
      qname: p.qname,
      // The x variable of a phase-plane cell, so a saved run can be overlaid
      // against its own x rather than dropped (#150).
      xqname: p.xqname ?? null,
      yUnit: unitForVars(units, [p.qname]),
      controlled: false,
      removeId: p.id,
      simResult: {
        time,
        outputs: { [p.qname]: outputs?.[p.qname] ?? [] },
        ...(p.xqname ? { xValues: outputs?.[p.xqname] ?? [] } : {}),
      },
      // A phase-plane cell's x axis is a model variable, so it takes that
      // variable's unit (#125) rather than the caller's time unit.
      ...(p.xqname ? { xLabel: p.xqname, xUnit: unitForVars(units, [p.xqname]) } : {}),
      dataItems: [],
    }))
}

/**
 * The units to annotate a plot's y-axis with, given the model's qname -> units
 * map and the model variables drawn in that cell (#125).
 *
 * The first variable is the primary one. A cell showing several variables is
 * only annotated when they all share the same units — mixing (say) mM and kPa
 * under one axis label would be worse than no label at all. Returns '' when the
 * units are unknown, so the caller can fall back to an unlabelled axis.
 */
export function unitForVars(units, qnames) {
  const names = (qnames ?? []).filter(Boolean)
  if (!units || !names.length) return ''
  const first = units[names[0]] ?? ''
  if (!first) return ''
  for (const n of names.slice(1)) if ((units[n] ?? '') !== first) return ''
  return first
}

/** The model's time units, looked up from whichever variable is named time/t. */
export function timeUnit(units) {
  if (!units) return ''
  for (const [qname, u] of Object.entries(units)) {
    // `dimensionless` is what a model that never declares a time unit reports
    // (a Myokit .mmt with a bare `time = 0 bind time`, for instance). It is not
    // a unit, so treat it as "unknown" and let the caller supply one rather than
    // labelling the axis with it.
    if (u && u !== 'dimensionless' && TIME_NAMES.has(String(qname).split('/').pop())) {
      return u
    }
  }
  return ''
}

/** data_items overlaying a given (experiment, variable) plot cell. */
export function overlayItemsFor(obsData, expIdx, qname) {
  if (!obsData) return []
  return (obsData.data_items ?? []).filter(
    (d) =>
      isPlottableOverlay(d) &&
      (d.experiment_idx ?? 0) === expIdx &&
      obsModelVar(d) === qname,
  )
}

/**
 * Attach each overlay item's backend-computed series_output (transformed) series,
 * so buildChartData plots the operation's result instead of the raw operand
 * (issue #111). `seriesByIndex` maps a global data_item index (as returned by the
 * simulate / protocol response `output_series`) to the transformed series; items
 * are matched by their position in `allItems`. Items with no transformed series
 * are returned unchanged.
 */
export function attachOutputSeries(items, seriesByIndex, allItems) {
  if (!seriesByIndex || !allItems) return items
  return items.map((it) => {
    const idx = allItems.indexOf(it)
    const s = idx >= 0 ? seriesByIndex[idx] : undefined
    return Array.isArray(s) && s.length ? { ...it, output_series: s } : it
  })
}

function refLine({ name, op, role, dashed, kind, color: c, data }) {
  return {
    label: `${name} (${role}${op ? ' ' + op : ''})`,
    mathLabel: name,
    suffix: `${role}${op ? ' ' + op : ''}`,
    legendStyle: dashed ? 'dash' : 'line',
    kind,
    data,
    borderColor: c,
    borderDash: dashed ? [6, 4] : undefined,
    borderWidth: 1.5,
    pointRadius: 0,
  }
}

/**
 * Build Chart.js datasets from a simulation result and obs_data items.
 *
 * Simulation outputs render as solid lines. Each obs_data `data_item` overlays:
 *  - the experimental `value` as a dashed reference line, and
 *  - the calculated feature (its `operation` applied to the sim trace) as a
 *    solid reference line in the same colour, so the two can be compared.
 * `series` items render as a scatter overlay.
 *
 * Datasets carry `mathLabel` (LaTeX), `suffix` and `legendStyle` for the HTML
 * legend in PlotPanel.
 */
export function buildChartData(simResult, options = {}) {
  const time = simResult?.time ?? []
  const outputs = simResult?.outputs ?? {}
  // Phase-plane plots (issue #124): an explicit x series — another variable's
  // trace — replaces the time axis (e.g. a PV loop). Samples keep their time
  // ordering, which is what makes the loop close.
  const xSource = options.xSource ?? simResult?.xValues ?? null
  const xAxis = xSource ?? time
  const phasePlane = !!xSource
  // Obs overlays are reference lines spanning/crossing the *time* axis, so they
  // mean nothing against another variable's axis: drop them rather than draw
  // them in the wrong place. (Extra plots pass dataItems: [] anyway.)
  const dataItems = phasePlane ? [] : (options.dataItems ?? [])
  const varLabel = options.varLabel ?? ''
  // Step series (e.g. controlled params_to_change inputs) must not be smoothed,
  // otherwise the bezier overshoots the risers. A phase-plane trace loops back
  // on itself, where the same smoothing overshoots the turns — so also 0.
  const tension = options.stepped || phasePlane ? 0 : 0.15
  const datasets = []

  // A data_item whose operation defines a series_output branch supplies a
  // transformed model series (e.g. 60/x) that replaces the raw operand as the
  // plotted waveform — matching CA's saved figures (issue #111).
  const overrideByVar = new Map()
  for (const item of dataItems) {
    if (Array.isArray(item.output_series) && item.output_series.length) {
      const v = obsModelVar(item)
      if (!overrideByVar.has(v)) overrideByVar.set(v, [])
      overrideByVar.get(v).push(item)
    }
  }

  let colorIdx = 0
  let yMin = Infinity
  let yMax = -Infinity
  const accumulate = (values) => {
    for (const v of values) {
      if (v < yMin) yMin = v
      if (v > yMax) yMax = v
    }
  }
  const pushLine = (labelName, mathName, values) => {
    accumulate(values)
    datasets.push({
      label: labelName,
      mathLabel: mathName,
      suffix: '',
      legendStyle: 'line',
      kind: 'simulation',
      data: toXY(xAxis, values),
      borderColor: color(colorIdx),
      backgroundColor: color(colorIdx),
      borderWidth: 1.5,
      pointRadius: 0,
      tension,
    })
    colorIdx += 1
  }
  for (const qname of Object.keys(outputs)) {
    const overrides = overrideByVar.get(qname)
    if (overrides && overrides.length) {
      // Plot the operation's transformed series instead of the raw operand.
      for (const item of overrides) {
        const name = item.name_for_plotting ?? item.variable ?? qname
        pushLine(name, varLabel || name, item.output_series)
      }
      continue
    }
    pushLine(qname, varLabel || qname, outputs[qname] ?? [])
  }

  // Saved runs shown for comparison (issue #126). Each carries its own colour —
  // the same one its tick box and slider markers use — and its own time base,
  // since it was recorded from a different run whose sampling need not match.
  // Dashed and thinner so the live trace stays the one being read; a saved run
  // is a backdrop, not a peer.
  // On a phase-plane cell a saved run is plotted against *its own* x series, not
  // this run's: pairing one run's y with another's x would draw a curve neither
  // of them followed. A saved run that has no x series for this cell contributes
  // nothing rather than being pinned to the wrong axis.
  for (const saved of options.savedSeries ?? []) {
    const values = saved.values ?? []
    if (!values.length) continue
    const savedX = phasePlane ? (saved.xValues ?? []) : (saved.time ?? [])
    if (phasePlane && !savedX.length) continue
    accumulate(values)
    datasets.push({
      label: saved.prefix,
      mathLabel: saved.prefix,
      suffix: 'saved',
      legendStyle: 'dash',
      kind: 'saved',
      // Against its own x: a saved run's samples are its own, recorded from a
      // run whose sampling need not match this one's.
      data: toXY(savedX.length ? savedX : xAxis, values),
      borderColor: saved.color,
      backgroundColor: saved.color,
      borderWidth: 1,
      borderDash: [5, 3],
      pointRadius: 0,
      tension,
    })
  }

  const xMin = xAxis.length ? xAxis[0] : 0
  const xMax = xAxis.length ? xAxis[xAxis.length - 1] : 1
  if (!Number.isFinite(yMin)) {
    yMin = 0
    yMax = 1
  }

  for (const item of dataItems) {
    const c = color(colorIdx)
    colorIdx += 1
    const name = item.name_for_plotting ?? item.variable ?? 'obs'
    const op = item.operation

    if (item.data_type === 'series') {
      const dt = item.obs_dt ?? 1
      const values = item.value ?? item.values ?? []
      datasets.push({
        label: name,
        mathLabel: name,
        suffix: 'obs',
        legendStyle: 'point',
        kind: 'obs-series',
        data: values.map((y, i) => ({ x: i * dt, y })),
        type: 'scatter',
        showLine: false,
        pointRadius: 3,
        borderColor: c,
        backgroundColor: c,
      })
      continue
    }

    const series = outputs[obsModelVar(item)] ?? Object.values(outputs)[0] ?? []
    const feature = computeFeature(op, time, series)

    if (item.plot_type === 'vertical') {
      const vline = (x) => [
        { x, y: yMin },
        { x, y: yMax },
      ]
      datasets.push(
        refLine({ name, op, role: 'obs', dashed: true, kind: 'obs-vertical', color: c, data: vline(item.value) }),
      )
      if (feature) {
        datasets.push(
          refLine({ name, op, role: 'calc', dashed: false, kind: 'calc-vertical', color: c, data: vline(feature.value) }),
        )
      }
    } else {
      // horizontal family (incl. horizontal_from_min)
      const hline = (y) => [
        { x: xMin, y },
        { x: xMax, y },
      ]
      let expY = item.value
      let calcY = feature ? feature.value : null
      if (op === 'max_minus_min') {
        const base = series.length ? minOf(series) : 0
        expY = base + item.value
        calcY = feature ? base + feature.value : null
      }
      datasets.push(
        refLine({ name, op, role: 'obs', dashed: true, kind: 'obs-constant', color: c, data: hline(expY) }),
      )
      if (calcY != null) {
        datasets.push(
          refLine({ name, op, role: 'calc', dashed: false, kind: 'calc-constant', color: c, data: hline(calcY) }),
        )
      }
    }
  }

  return { datasets }
}
