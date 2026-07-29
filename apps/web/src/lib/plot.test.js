import { describe, it, expect } from 'vitest'
import {
  obsModelVar,
  isPlottableOverlay,
  derivePlotVariables,
  overlayItemsFor,
  attachOutputSeries,
  buildChartData,
  computeFeature,
  controlledSeries,
  buildExtraPlotCells,
  lighten,
  shadeForStart,
  unitForVars,
  timeUnit,
} from './plot'

// Mirrors the SN_simple obs_data shape (3 experiments, predictions + overlays).
const obs = {
  n_experiments: 3,
  experiment_labels: ['SHR', 'SHR M', 'I_ramp'],
  prediction_items: [
    { variable: 'var_SN/Cai', name_for_plotting: 'Ca_{ter}', experiment_idx: 0 },
    { variable: 'soma_SN/V', name_for_plotting: 'V', experiment_idx: 0 },
  ],
  data_items: [
    { variable: 'soma_SN/V', operands: ['soma_SN/V'], data_type: 'constant', plot_type: 'horizontal', value: 20, experiment_idx: 2 },
    { variable: 'soma_SN/V', operands: ['time', 'soma_SN/V'], data_type: 'constant', plot_type: 'vertical', value: 2.02, experiment_idx: 0 },
    { variable: 'soma_SN/V', operands: ['time', 'soma_SN/V'], data_type: 'constant', plot_type: 'None', value: 0, experiment_idx: 0 },
  ],
}

describe('obs plot helpers', () => {
  it('obsModelVar picks the non-time operand', () => {
    expect(obsModelVar({ operands: ['time', 'soma_SN/V'] })).toBe('soma_SN/V')
    expect(obsModelVar({ operands: ['Lotka_Volterra_module/x'], variable: 'x_max' })).toBe(
      'Lotka_Volterra_module/x',
    )
    expect(obsModelVar({ variable: 'var_SN/Cai' })).toBe('var_SN/Cai')
  })

  it('isPlottableOverlay skips frequency and plot_type None', () => {
    expect(isPlottableOverlay({ plot_type: 'horizontal' })).toBe(true)
    expect(isPlottableOverlay({ plot_type: 'vertical' })).toBe(true)
    expect(isPlottableOverlay({ plot_type: 'None' })).toBe(false)
    expect(isPlottableOverlay({ plot_type: 'horizontal', data_type: 'frequency' })).toBe(false)
  })

  it('derivePlotVariables unions predictions + plottable data items', () => {
    const vars = derivePlotVariables(obs)
    expect(vars.map((v) => v.qname)).toEqual(['var_SN/Cai', 'soma_SN/V'])
    expect(vars.find((v) => v.qname === 'var_SN/Cai').label).toBe('Ca_{ter}')
  })

  it('overlayItemsFor filters by experiment + variable, skipping None', () => {
    const e0 = overlayItemsFor(obs, 0, 'soma_SN/V')
    expect(e0).toHaveLength(1)
    expect(e0[0].plot_type).toBe('vertical')

    const e2 = overlayItemsFor(obs, 2, 'soma_SN/V')
    expect(e2).toHaveLength(1)
    expect(e2[0].plot_type).toBe('horizontal')

    expect(overlayItemsFor(obs, 1, 'soma_SN/V')).toHaveLength(0)
  })
})

describe('data-only obs_data (3compartment shape)', () => {
  // Bare-array obs: no prediction_items, horizontal / horizontal_from_min items.
  const obs3 = {
    has_protocol: false,
    data_items: [
      { variable: 'flow aortic root', operands: ['aortic_root/v'], data_type: 'constant', plot_type: 'horizontal', value: 1e-4 },
      { variable: 'stroke volume', operands: ['heart/q_lv'], data_type: 'constant', plot_type: 'horizontal_from_min', value: 1.04e-4 },
      { variable: 'pressure aortic root', operands: ['aortic_root/u'], data_type: 'constant', plot_type: 'horizontal', value: 16000 },
    ],
  }

  it('horizontal_from_min counts as a plottable overlay', () => {
    expect(isPlottableOverlay({ plot_type: 'horizontal_from_min' })).toBe(true)
  })

  it('derives one plot variable per referenced operand', () => {
    expect(derivePlotVariables(obs3).map((v) => v.qname)).toEqual([
      'aortic_root/v',
      'heart/q_lv',
      'aortic_root/u',
    ])
  })

  it('overlays attach by variable for the single (experiment 0) run', () => {
    const items = overlayItemsFor(obs3, 0, 'aortic_root/u')
    expect(items).toHaveLength(1)
    expect(items[0].value).toBe(16000)
  })
})

describe('controlledSeries (params_to_change)', () => {
  const pi = {
    pre_times: [0, 0, 0],
    sim_times: [[1, 2], [1, 2], [1, 1]],
    params_to_change: {
      'soma_SN/I_in': [[0, -0.15], [0, -0.15], [0.0, 'ramp_port']],
      'soma_SN/g_M': [[0.08, 0.08], [0.12, 0.12], [0.08, 0.08]],
    },
    protocol_traces: { ramp_port: { t: [0, 0.5, 1], values: [0, -0.15, -0.3] } },
  }

  it('builds a step series held over each sub-experiment', () => {
    const iin = controlledSeries(pi, 0).find((s) => s.qname === 'soma_SN/I_in')
    // sub0: 0 over [0,1]; sub1: -0.15 over [1,3]
    expect(iin.time).toEqual([0, 1, 1, 3])
    expect(iin.values).toEqual([0, 0, -0.15, -0.15])
  })

  it('uses protocol_traces for a string sub-value, offset to the sub start', () => {
    const iin = controlledSeries(pi, 2).find((s) => s.qname === 'soma_SN/I_in')
    expect(iin.time).toEqual([0, 1, 1, 1.5, 2])
    expect(iin.values).toEqual([0, 0, 0, -0.15, -0.3])
  })

  it('returns one series per controlled parameter', () => {
    expect(controlledSeries(pi, 0).map((s) => s.qname)).toEqual([
      'soma_SN/I_in',
      'soma_SN/g_M',
    ])
  })

  it('is empty without protocol_info / params_to_change', () => {
    expect(controlledSeries(null, 0)).toEqual([])
    expect(controlledSeries({ params_to_change: {} }, 0)).toEqual([])
  })
})

describe('computeFeature', () => {
  const time = [0, 1, 2, 3]
  it('computes max/min with the time of occurrence', () => {
    expect(computeFeature('max', time, [1, 5, 3, 2])).toEqual({ value: 5, at: 1 })
    expect(computeFeature('min', time, [4, 5, 1, 2])).toEqual({ value: 1, at: 2 })
  })
  it('computes mean and max_minus_min', () => {
    expect(computeFeature('mean', time, [1, 2, 3, 4]).value).toBeCloseTo(2.5)
    expect(computeFeature('max_minus_min', time, [1, 2, 3, 4]).value).toBe(3)
  })
  it('returns null for unsupported (e.g. spike frequency) operations', () => {
    expect(computeFeature('calc_spike_frequency_windowed', time, [1, 2])).toBeNull()
  })
})

describe('buildChartData calculated features', () => {
  it('plots the calculated feature beside the experimental value', () => {
    const sim = { time: [0, 1, 2], outputs: { 'aortic_root/v': [1e-4, 5e-4, 2e-4] } }
    const item = {
      name_for_plotting: 'v_{AR}',
      data_type: 'constant',
      operation: 'max',
      operands: ['aortic_root/v'],
      plot_type: 'horizontal',
      value: 4e-4,
    }
    const { datasets } = buildChartData(sim, { dataItems: [item], varLabel: 'v_{AR}' })
    const obs = datasets.find((d) => d.kind === 'obs-constant')
    const calc = datasets.find((d) => d.kind === 'calc-constant')
    expect(obs.data[0].y).toBe(4e-4) // experimental max
    expect(calc.data[0].y).toBe(5e-4) // calculated max from the trace
    expect(obs.legendStyle).toBe('dash')
    expect(calc.legendStyle).toBe('line')
    expect(calc.mathLabel).toBe('v_{AR}')
  })

  it('stepped option disables line smoothing (for controlled step series)', () => {
    const sim = { time: [0, 1, 1, 3], outputs: { 'soma_SN/I_in': [0, 0, -0.15, -0.15] } }
    const smooth = buildChartData(sim, {}).datasets.find((d) => d.kind === 'simulation')
    const stepped = buildChartData(sim, { stepped: true }).datasets.find((d) => d.kind === 'simulation')
    expect(smooth.tension).toBe(0.15)
    expect(stepped.tension).toBe(0)
  })

  it('simulation dataset carries the LaTeX varLabel and a line legend style', () => {
    const sim = { time: [0, 1], outputs: { 'aortic_root/v': [1, 2] } }
    const { datasets } = buildChartData(sim, { varLabel: 'v_{AR}' })
    const s = datasets.find((d) => d.kind === 'simulation')
    expect(s.mathLabel).toBe('v_{AR}')
    expect(s.legendStyle).toBe('line')
  })
})

describe('buildChartData reference lines', () => {
  const simResult = { time: [0, 1, 2], outputs: { 'soma_SN/V': [-80, -50, -79] } }

  it('renders a vertical line spanning the y-range at x=value', () => {
    const { datasets } = buildChartData(simResult, {
      dataItems: [{ name_for_plotting: 't_peak', plot_type: 'vertical', value: 2.02, data_type: 'constant' }],
    })
    const v = datasets.find((d) => d.kind === 'obs-vertical')
    expect(v).toBeTruthy()
    expect(v.data[0]).toEqual({ x: 2.02, y: -80 })
    expect(v.data[1]).toEqual({ x: 2.02, y: -50 })
  })

  it('renders a horizontal line for a constant overlay', () => {
    const { datasets } = buildChartData(simResult, {
      dataItems: [{ name_for_plotting: 'V_max', plot_type: 'horizontal', value: 20, data_type: 'constant' }],
    })
    expect(datasets.some((d) => d.kind === 'obs-constant' && Array.isArray(d.borderDash))).toBe(true)
  })
})

describe('series_output overlay (issue #111)', () => {
  it('attachOutputSeries attaches the transformed series by data_item index', () => {
    const allItems = [
      { variable: 'a', operands: ['m/a'] },
      { variable: 'b', operands: ['m/b'] },
    ]
    const seriesByIndex = { 1: [60, 30, 20] } // only the 2nd item has one
    const attached = attachOutputSeries([allItems[1], allItems[0]], seriesByIndex, allItems)
    expect(attached[0].output_series).toEqual([60, 30, 20])
    expect(attached[1].output_series).toBeUndefined()
    // originals untouched (clones only when a series is attached)
    expect(allItems[1].output_series).toBeUndefined()
  })

  it('attachOutputSeries is a no-op without a series map', () => {
    const items = [{ variable: 'a' }]
    expect(attachOutputSeries(items, undefined, items)).toBe(items)
  })

  it('plots the operation series_output series in place of the raw operand', () => {
    // Raw operand is the period; the operation transforms it to 60/period.
    const sim = { time: [0, 1, 2, 3], outputs: { 'heart/period': [1, 2, 3, 4] } }
    const item = {
      name_for_plotting: 'HR',
      data_type: 'constant',
      operation: 'mean_heart_rate_bpm_in_range',
      operands: ['heart/period'],
      plot_type: 'horizontal',
      value: 60,
      output_series: [60, 30, 20, 15],
    }
    const { datasets } = buildChartData(sim, { dataItems: [item], varLabel: 'HR' })
    const lines = datasets.filter((d) => d.kind === 'simulation')
    // Exactly one model line, and it carries the transformed series, not the raw.
    expect(lines).toHaveLength(1)
    expect(lines[0].data.map((p) => p.y)).toEqual([60, 30, 20, 15])
    expect(lines[0].data.map((p) => p.y)).not.toEqual([1, 2, 3, 4])
    expect(lines[0].mathLabel).toBe('HR')
  })

  it('falls back to the raw operand when no series_output is attached', () => {
    const sim = { time: [0, 1, 2, 3], outputs: { 'heart/period': [1, 2, 3, 4] } }
    const item = {
      name_for_plotting: 'HR',
      data_type: 'constant',
      operation: 'mean_heart_rate_bpm_in_range',
      operands: ['heart/period'],
      plot_type: 'horizontal',
      value: 60,
    }
    const { datasets } = buildChartData(sim, { dataItems: [item], varLabel: 'HR' })
    const line = datasets.find((d) => d.kind === 'simulation')
    expect(line.data.map((p) => p.y)).toEqual([1, 2, 3, 4])
  })
})

describe('buildExtraPlotCells', () => {
  const outputs = { 'm/x': [1, 2, 3], 'm/y': [4, 5, 6] }
  const time = [0, 1, 2]
  const extras = [
    { id: 1, groupKey: 'exp0', qname: 'm/x', label: 'm/x' },
    { id: 2, groupKey: 'exp1', qname: 'm/y', label: 'm/y' },
    { id: 3, groupKey: 'exp0', qname: 'm/y', label: 'm/y' },
  ]

  it('only returns cells whose groupKey matches the experiment', () => {
    const cells = buildExtraPlotCells(extras, 'exp0', time, outputs)
    expect(cells.map((c) => c.removeId)).toEqual([1, 3])
  })

  it('builds a single-variable simResult from the group outputs', () => {
    const [cell] = buildExtraPlotCells(extras, 'exp1', time, outputs)
    expect(cell.simResult).toEqual({ time, outputs: { 'm/y': [4, 5, 6] } })
    expect(cell.removeId).toBe(2)
    expect(cell.key).toBe('extra:2')
  })

  it('falls back to an empty series when the variable is absent', () => {
    const [cell] = buildExtraPlotCells(
      [{ id: 9, groupKey: 'exp0', qname: 'm/z', label: 'm/z' }],
      'exp0',
      time,
      outputs,
    )
    expect(cell.simResult.outputs).toEqual({ 'm/z': [] })
  })

  it('returns nothing when no extras match', () => {
    expect(buildExtraPlotCells(extras, 'data-only', time, outputs)).toEqual([])
    expect(buildExtraPlotCells(undefined, 'exp0', time, outputs)).toEqual([])
  })

  it('carries the plotted variable units onto the cell (issue #125)', () => {
    const units = { 'm/x': 'mmHg', 'm/y': 'mM' }
    const [cell] = buildExtraPlotCells(extras, 'exp1', time, outputs, units)
    expect(cell.yUnit).toBe('mM')
    expect(buildExtraPlotCells(extras, 'exp1', time, outputs)[0].yUnit).toBe('')
  })

  // Issue #124: an extra plot may set `xqname` to plot y against another
  // variable (a PV loop) instead of against time.
  it('wires outputs[xqname] in as the x series when set', () => {
    const [cell] = buildExtraPlotCells(
      [{ id: 4, groupKey: 'exp0', qname: 'm/y', xqname: 'm/x', label: 'm/y' }],
      'exp0',
      time,
      outputs,
    )
    expect(cell.simResult).toEqual({
      time,
      outputs: { 'm/y': [4, 5, 6] },
      xValues: [1, 2, 3],
    })
    expect(cell.xLabel).toBe('m/x')
    // The title is the y variable alone; the x one is named under the x axis.
    expect(cell.title).toBe('m/y')
  })

  // The x axis of a phase-plane cell is a model variable, so it takes that
  // variable's unit -- not the time unit the caller would otherwise supply.
  it('takes the x-axis units from the x variable (issues #124 + #125)', () => {
    const [cell] = buildExtraPlotCells(
      [{ id: 4, groupKey: 'exp0', qname: 'm/y', xqname: 'm/x', label: 'm/y' }],
      'exp0',
      time,
      outputs,
      { 'm/x': 'mL', 'm/y': 'mmHg' },
    )
    expect(cell.xUnit).toBe('mL')
    expect(cell.yUnit).toBe('mmHg')
  })

  it('leaves a time-series cell untouched (no x series, no x label)', () => {
    const [cell] = buildExtraPlotCells(extras, 'exp1', time, outputs)
    expect('xValues' in cell.simResult).toBe(false)
    expect('xLabel' in cell).toBe(false)
    expect('xUnit' in cell).toBe(false)
  })

  it('falls back to an empty x series when the x variable is absent', () => {
    const [cell] = buildExtraPlotCells(
      [{ id: 5, groupKey: 'exp0', qname: 'm/y', xqname: 'm/z', label: 'm/y' }],
      'exp0',
      time,
      outputs,
    )
    expect(cell.simResult.xValues).toEqual([])
  })
})

describe('unitForVars / timeUnit (issue #125)', () => {
  const units = {
    'main/t': 'second',
    'main/p_o2': 'kPa',
    'main/c_o2': 'mM',
    'main/other': 'mM',
  }

  it('returns the units of the primary variable', () => {
    expect(unitForVars(units, ['main/p_o2'])).toBe('kPa')
  })

  it('returns the shared units when every plotted variable agrees', () => {
    expect(unitForVars(units, ['main/c_o2', 'main/other'])).toBe('mM')
  })

  it('returns nothing when the plotted variables have mixed units', () => {
    expect(unitForVars(units, ['main/p_o2', 'main/c_o2'])).toBe('')
  })

  it('degrades gracefully with unknown variables or no units map', () => {
    expect(unitForVars(units, ['main/nope'])).toBe('')
    expect(unitForVars(units, [])).toBe('')
    expect(unitForVars(undefined, ['main/p_o2'])).toBe('')
  })

  it('finds the time units from a variable named time or t', () => {
    expect(timeUnit(units)).toBe('second')
    expect(timeUnit({ 'environment/time': 'ms', 'm/x': 'mM' })).toBe('ms')
    expect(timeUnit({ 'm/x': 'mM' })).toBe('')
    expect(timeUnit(undefined)).toBe('')
  })
})

// Issue #124: phase-plane plots — y against any other variable, not time.
describe('buildChartData with a non-time x axis', () => {
  const sim = {
    time: [0, 1, 2, 3],
    outputs: { 'heart/P_lv': [10, 40, 30, 10] },
    xValues: [5, 9, 7, 5],
  }

  it('plots against simResult.xValues instead of time', () => {
    const { datasets } = buildChartData(sim)
    expect(datasets[0].data).toEqual([
      { x: 5, y: 10 },
      { x: 9, y: 40 },
      { x: 7, y: 30 },
      { x: 5, y: 10 },
    ])
  })

  it('accepts an xSource option too, taking precedence over time', () => {
    const { datasets } = buildChartData(
      { time: [0, 1], outputs: { 'm/y': [3, 4] } },
      { xSource: [8, 9] },
    )
    expect(datasets[0].data).toEqual([
      { x: 8, y: 3 },
      { x: 9, y: 4 },
    ])
  })

  it('truncates to the shorter of the x and y series', () => {
    const { datasets } = buildChartData({
      time: [0, 1, 2],
      outputs: { 'm/y': [1, 2, 3] },
      xValues: [7, 8],
    })
    expect(datasets[0].data).toEqual([
      { x: 7, y: 1 },
      { x: 8, y: 2 },
    ])
  })

  it('does not smooth the loop (tension 0, like stepped series)', () => {
    expect(buildChartData(sim).datasets[0].tension).toBe(0)
  })

  // Reference lines span/cross the time axis, so they are meaningless here —
  // drop them rather than draw them at the wrong x.
  it('drops obs overlays instead of drawing them against the wrong axis', () => {
    const { datasets } = buildChartData(sim, {
      dataItems: [
        {
          variable: 'heart/P_lv',
          operands: ['heart/P_lv'],
          data_type: 'constant',
          plot_type: 'horizontal',
          operation: 'max',
          value: 42,
        },
      ],
    })
    expect(datasets).toHaveLength(1)
    expect(datasets[0].kind).toBe('simulation')
  })

  it('keeps the default time path unchanged when no x series is given', () => {
    const plain = { time: [0, 1, 2], outputs: { 'm/y': [1, 2, 3] } }
    const { datasets } = buildChartData(plain)
    expect(datasets[0].data).toEqual([
      { x: 0, y: 1 },
      { x: 1, y: 2 },
      { x: 2, y: 3 },
    ])
    expect(datasets[0].tension).toBe(0.15)
  })
})

describe('lighten / shadeForStart', () => {
  it('lighten blends toward white; t=0 is identity, t=1 is white', () => {
    expect(lighten('#5b9bd5', 0)).toBe('#5b9bd5')
    expect(lighten('#5b9bd5', 1)).toBe('#ffffff')
    expect(lighten('#000000', 0.5)).toBe('#808080')
  })

  it('shadeForStart returns the base for start 0 and lighter shades after', () => {
    // Single start -> base colour unchanged.
    expect(shadeForStart('#5b9bd5', 0, 1)).toBe('#5b9bd5')
    // First of several starts is the base; later starts are strictly lighter.
    expect(shadeForStart('#5b9bd5', 0, 4)).toBe('#5b9bd5')
    const mid = shadeForStart('#5b9bd5', 2, 4)
    const last = shadeForStart('#5b9bd5', 3, 4)
    expect(mid).not.toBe('#5b9bd5')
    // Monotonically lighter: the last start's red channel exceeds the mid one's.
    expect(parseInt(last.slice(1, 3), 16)).toBeGreaterThan(parseInt(mid.slice(1, 3), 16))
  })
})

// Saved runs overlaid for comparison (issue #126).
describe('buildChartData with saved-run overlays', () => {
  const sim = { time: [0, 1, 2], outputs: { 'm/x': [1, 2, 3] } }
  const saved = [{ prefix: 'run_a', color: '#70ad47', time: [0, 1, 2], values: [4, 5, 6] }]

  it('adds one dataset per shown run, in that run colour', () => {
    const { datasets } = buildChartData(sim, { savedSeries: saved })
    const overlay = datasets.filter((d) => d.kind === 'saved')
    expect(overlay).toHaveLength(1)
    expect(overlay[0].borderColor).toBe('#70ad47')
    expect(overlay[0].data).toEqual([
      { x: 0, y: 4 },
      { x: 1, y: 5 },
      { x: 2, y: 6 },
    ])
  })

  it('labels it with the file prefix, so the legend names the run', () => {
    const { datasets } = buildChartData(sim, { savedSeries: saved })
    const overlay = datasets.find((d) => d.kind === 'saved')
    expect(overlay.label).toBe('run_a')
    expect(overlay.mathLabel).toBe('run_a')
  })

  // The live trace is the one being read; a saved run is a backdrop.
  it('draws it dashed and thinner than the live trace', () => {
    const { datasets } = buildChartData(sim, { savedSeries: saved })
    const live = datasets.find((d) => d.kind === 'simulation')
    const overlay = datasets.find((d) => d.kind === 'saved')
    expect(overlay.borderDash).toBeTruthy()
    expect(overlay.borderWidth).toBeLessThan(live.borderWidth)
  })

  // It was recorded from a different run, whose sampling need not match.
  it('plots it against its own time base', () => {
    const { datasets } = buildChartData(sim, {
      savedSeries: [{ prefix: 'r', color: '#000', time: [10, 20], values: [1, 2] }],
    })
    const overlay = datasets.find((d) => d.kind === 'saved')
    expect(overlay.data).toEqual([
      { x: 10, y: 1 },
      { x: 20, y: 2 },
    ])
  })

  it('falls back to this run x when the saved one has no time', () => {
    const { datasets } = buildChartData(sim, {
      savedSeries: [{ prefix: 'r', color: '#000', values: [7, 8, 9] }],
    })
    expect(datasets.find((d) => d.kind === 'saved').data[0]).toEqual({ x: 0, y: 7 })
  })

  it('skips an empty saved series rather than drawing nothing visible', () => {
    const { datasets } = buildChartData(sim, {
      savedSeries: [{ prefix: 'r', color: '#000', values: [] }],
    })
    expect(datasets.some((d) => d.kind === 'saved')).toBe(false)
  })

  it('leaves the live trace untouched', () => {
    const plain = buildChartData(sim).datasets.find((d) => d.kind === 'simulation')
    const withSaved = buildChartData(sim, { savedSeries: saved }).datasets.find(
      (d) => d.kind === 'simulation',
    )
    expect(withSaved.data).toEqual(plain.data)
    expect(withSaved.borderColor).toBe(plain.borderColor)
  })

  // A saved run carries its own x series now (#150), so a phase-plane cell can
  // overlay it -- against that x, never against this run's.
  it('plots a phase-plane overlay against the saved run own x series', () => {
    const { datasets } = buildChartData(
      { ...sim, xValues: [5, 6, 7] },
      {
        savedSeries: [
          { prefix: 'r', color: '#000', time: [0, 1, 2], values: [4, 5, 6], xValues: [9, 8, 7] },
        ],
      },
    )
    const overlay = datasets.find((d) => d.kind === 'saved')
    expect(overlay.data).toEqual([
      { x: 9, y: 4 },
      { x: 8, y: 5 },
      { x: 7, y: 6 },
    ])
  })

  // Pairing this run's x with a saved run's y would draw a curve neither of
  // them followed.
  it('drops a phase-plane overlay that has no x series of its own', () => {
    const { datasets } = buildChartData(
      { ...sim, xValues: [5, 6, 7] },
      { savedSeries: saved },
    )
    expect(datasets.some((d) => d.kind === 'saved')).toBe(false)
  })

  // The regression the user hit: several ticked runs, one trace.
  it('draws every shown run, not just one', () => {
    const { datasets } = buildChartData(sim, {
      savedSeries: [
        { prefix: 'a', color: '#111', time: [0, 1, 2], values: [4, 5, 6] },
        { prefix: 'b', color: '#222', time: [0, 1, 2], values: [7, 8, 9] },
        { prefix: 'c', color: '#333', time: [0, 1, 2], values: [1, 1, 1] },
      ],
    })
    expect(datasets.filter((d) => d.kind === 'saved')).toHaveLength(3)
  })

  it('is a no-op when nothing is shown', () => {
    expect(buildChartData(sim, { savedSeries: [] }).datasets).toEqual(
      buildChartData(sim).datasets,
    )
  })
})

// Issue #126: callers look up per-variable extras by qname.
describe('buildExtraPlotCells qname', () => {
  it('exposes the model variable the cell draws', () => {
    const [cell] = buildExtraPlotCells(
      [{ id: 1, groupKey: 'exp0', qname: 'm/y', label: 'm/y' }],
      'exp0',
      [0, 1],
      { 'm/y': [1, 2] },
    )
    expect(cell.qname).toBe('m/y')
  })

  it('exposes the x variable of a phase-plane cell (#150)', () => {
    const [cell] = buildExtraPlotCells(
      [{ id: 1, groupKey: 'exp0', qname: 'm/y', xqname: 'm/x', label: 'm/y' }],
      'exp0',
      [0, 1],
      { 'm/y': [1, 2], 'm/x': [3, 4] },
    )
    expect(cell.xqname).toBe('m/x')
    // A time-series cell has none, which is what marks it as one.
    const [plain] = buildExtraPlotCells(
      [{ id: 2, groupKey: 'exp0', qname: 'm/y', label: 'm/y' }],
      'exp0',
      [0, 1],
      { 'm/y': [1, 2] },
    )
    expect(plain.xqname).toBeNull()
  })
})
