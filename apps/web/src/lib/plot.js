export const PALETTE = [
  '#5b9bd5',
  '#ed7d31',
  '#70ad47',
  '#ffc000',
  '#a142f4',
  '#e84a5f',
]

const TIME_NAMES = new Set(['time', 't'])

function color(i) {
  return PALETTE[i % PALETTE.length]
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
 * group. Each entry of `extraPlots` is { id, groupKey, qname, label }; only
 * those whose `groupKey` matches build a single-variable cell from this group's
 * own `outputs`/`time`.
 */
export function buildExtraPlotCells(extraPlots, groupKey, time, outputs) {
  return (extraPlots ?? [])
    .filter((p) => p.groupKey === groupKey)
    .map((p) => ({
      key: `extra:${p.id}`,
      title: p.label,
      varLabel: p.label,
      controlled: false,
      removeId: p.id,
      simResult: { time, outputs: { [p.qname]: outputs?.[p.qname] ?? [] } },
      dataItems: [],
    }))
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
  const dataItems = options.dataItems ?? []
  const varLabel = options.varLabel ?? ''
  // Step series (e.g. controlled params_to_change inputs) must not be smoothed,
  // otherwise the bezier overshoots the risers.
  const tension = options.stepped ? 0 : 0.15
  const datasets = []

  const time = simResult?.time ?? []
  const outputs = simResult?.outputs ?? {}

  let colorIdx = 0
  let yMin = Infinity
  let yMax = -Infinity
  for (const qname of Object.keys(outputs)) {
    const series = outputs[qname] ?? []
    for (const v of series) {
      if (v < yMin) yMin = v
      if (v > yMax) yMax = v
    }
    datasets.push({
      label: qname,
      mathLabel: varLabel || qname,
      suffix: '',
      legendStyle: 'line',
      kind: 'simulation',
      data: toXY(time, series),
      borderColor: color(colorIdx),
      backgroundColor: color(colorIdx),
      borderWidth: 1.5,
      pointRadius: 0,
      tension,
    })
    colorIdx += 1
  }

  const xMin = time.length ? time[0] : 0
  const xMax = time.length ? time[time.length - 1] : 1
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
