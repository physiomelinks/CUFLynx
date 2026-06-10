const PALETTE = [
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
  return item.plot_type === 'horizontal' || item.plot_type === 'vertical'
}

/**
 * Variables worth plotting, derived from an obs_data response: every
 * prediction_item variable plus every model variable referenced by a plottable
 * (horizontal/vertical) data_item. Returns [{ qname, label }] de-duplicated.
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
    if (v && !map.has(v)) map.set(v, v)
  }
  return [...map.entries()].map(([qname, label]) => ({ qname, label }))
}

/** data_items overlaying a given (experiment, variable) plot cell. */
export function overlayItemsFor(obsData, expIdx, qname) {
  if (!obsData) return []
  return (obsData.data_items ?? []).filter(
    (d) =>
      isPlottableOverlay(d) &&
      d.experiment_idx === expIdx &&
      obsModelVar(d) === qname,
  )
}

/**
 * Build Chart.js datasets from a simulation result and obs_data items.
 *
 * Simulation outputs render as solid lines. obs_data `data_items` overlay as:
 *  - horizontal (constant) -> dashed horizontal reference line at `value`
 *  - vertical -> dashed vertical reference line at x = `value`
 *  - series -> scatter overlay sampled at obs_dt
 */
export function buildChartData(simResult, options = {}) {
  const dataItems = options.dataItems ?? []
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
      data: toXY(time, series),
      borderColor: color(colorIdx),
      backgroundColor: color(colorIdx),
      borderWidth: 1.5,
      pointRadius: 0,
      tension: 0.15,
      kind: 'simulation',
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
    const label = item.name_for_plotting ?? item.variable ?? 'obs'
    if (item.data_type === 'series') {
      const dt = item.obs_dt ?? 1
      const values = item.value ?? item.values ?? []
      datasets.push({
        label,
        data: values.map((y, i) => ({ x: i * dt, y })),
        type: 'scatter',
        showLine: false,
        pointRadius: 3,
        borderColor: color(colorIdx),
        backgroundColor: color(colorIdx),
        kind: 'obs-series',
      })
    } else if (item.plot_type === 'vertical') {
      datasets.push({
        label,
        data: [
          { x: item.value, y: yMin },
          { x: item.value, y: yMax },
        ],
        borderColor: color(colorIdx),
        borderDash: [4, 4],
        borderWidth: 1.5,
        pointRadius: 0,
        kind: 'obs-vertical',
      })
    } else {
      datasets.push({
        label,
        data: [
          { x: xMin, y: item.value },
          { x: xMax, y: item.value },
        ],
        borderColor: color(colorIdx),
        borderDash: [6, 6],
        borderWidth: 1.5,
        pointRadius: 0,
        kind: 'obs-constant',
      })
    }
    colorIdx += 1
  }

  return { datasets }
}
