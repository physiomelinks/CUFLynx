const PALETTE = [
  '#5b9bd5',
  '#ed7d31',
  '#70ad47',
  '#ffc000',
  '#a142f4',
  '#e84a5f',
]

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

/**
 * Build Chart.js datasets from a simulation result and obs_data items.
 *
 * Simulation outputs render as solid lines. obs_data `data_items` overlay as:
 *  - constant -> dashed horizontal reference line (with borderDash)
 *  - series   -> scatter overlay sampled at obs_dt
 *  - frequency -> dashed horizontal band (single reference line here)
 */
export function buildChartData(simResult, options = {}) {
  const dataItems = options.dataItems ?? []
  const datasets = []

  let colorIdx = 0
  const time = simResult?.time ?? []
  const outputs = simResult?.outputs ?? {}
  for (const qname of Object.keys(outputs)) {
    datasets.push({
      label: qname,
      data: toXY(time, outputs[qname]),
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

  for (const item of dataItems) {
    const label = item.name_for_plotting ?? item.variable ?? 'obs'
    if (item.data_type === 'series') {
      const dt = item.obs_dt ?? 1
      const values = item.value ?? item.values ?? []
      const pts = values.map((y, i) => ({ x: i * dt, y }))
      datasets.push({
        label,
        data: pts,
        type: 'scatter',
        showLine: false,
        pointRadius: 3,
        borderColor: color(colorIdx),
        backgroundColor: color(colorIdx),
        kind: 'obs-series',
      })
    } else {
      // constant / frequency -> dashed horizontal reference line
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
