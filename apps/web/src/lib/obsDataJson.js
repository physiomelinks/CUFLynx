// Pure helpers for the "Edit obs_data" dialog: split the loaded data_items into
// form-editable "constant" rows vs preserved (series/frequency/unknown-op) items,
// build the obs_data JSON from edited rows, and derive a date-versioned filename.
// No Vue here so these stay easy to unit-test. The operation/cost_type option
// lists come from circulatory_autogen at runtime; the consts below are only
// fallbacks used by the dialog when that fetch fails.

export const FALLBACK_OPERATIONS = [
  '',
  'max',
  'min',
  'mean',
  'max_minus_min',
  'addition',
  'subtraction',
  'multiplication',
  'division',
]
export const FALLBACK_COST_TYPES = ['MSE', 'AE', 'gaussian_MLE']
export const FALLBACK_DATA_TYPES = ['constant', 'series', 'frequency', 'prob_dist']
export const FALLBACK_PLOT_TYPES = [
  '',
  'horizontal',
  'vertical',
  'horizontal_from_min',
  'series',
  'frequency',
]
// The scalar data_type this form edits; other types are preserved untouched.
export const SCALAR_DATA_TYPE = 'constant'

/**
 * Partition data_items into form-editable rows and preserved items. A data_item
 * is editable only when it's a `constant` (or untyped) AND its operation is in
 * the (live, CA-sourced) `operations` list — so series/frequency items and any
 * constant with an unknown operation pass through untouched and are never
 * corrupted by the operation dropdown.
 */
export function splitItems(dataItems = [], operations = FALLBACK_OPERATIONS) {
  const editable = []
  const preserved = []
  for (const item of dataItems) {
    const type = item.data_type ?? SCALAR_DATA_TYPE
    const op = item.operation ?? ''
    if (type === SCALAR_DATA_TYPE && operations.includes(op)) editable.push(itemToRow(item))
    else preserved.push(item)
  }
  return { editable, preserved }
}

export function itemToRow(item) {
  return {
    _orig: item, // keep the full original so extra keys round-trip
    variable: item.variable ?? '',
    name_for_plotting: item.name_for_plotting ?? item.variable ?? '',
    operation: item.operation ?? '',
    // Per-data_item values for the operation's keyword args (e.g. threshold,
    // window). Cloned so edits don't mutate the source; round-tripped on save.
    operation_kwargs:
      item.operation_kwargs && typeof item.operation_kwargs === 'object'
        ? { ...item.operation_kwargs }
        : {},
    operands: Array.isArray(item.operands) ? [...item.operands] : [],
    unit: item.unit ?? 'dimensionless',
    value: item.value ?? null,
    std: item.std ?? null,
    weight: item.weight ?? 1.0,
    experiment_idx: item.experiment_idx ?? 0,
    subexperiment_idx: item.subexperiment_idx ?? 0,
    cost_type: item.cost_type ?? '',
    plot_type: item.plot_type && item.plot_type !== 'None' ? item.plot_type : '',
    // free-text provenance ("where the data came from"). CA's `source` may also be
    // a dict of file paths — only surface/edit the string form here.
    source: typeof item.source === 'string' ? item.source : '',
    // free-text comment about this data item.
    comment: typeof item.comment === 'string' ? item.comment : '',
  }
}

export function newRow(experimentIdx = 0) {
  return {
    _orig: null,
    data_type: SCALAR_DATA_TYPE,
    variable: '',
    name_for_plotting: '',
    operation: 'max',
    operation_kwargs: {},
    operands: [],
    unit: 'dimensionless',
    value: 0,
    std: 1,
    weight: 1.0,
    experiment_idx: experimentIdx,
    subexperiment_idx: 0,
    cost_type: '',
    plot_type: 'horizontal',
    source: '',
    comment: '',
  }
}

function num(v, fallback) {
  return v === '' || v == null || !Number.isFinite(Number(v)) ? fallback : Number(v)
}

export function rowToItem(row) {
  const out = { ...(row._orig ?? {}) } // preserve extra keys (operation_kwargs, …)
  out.data_type = SCALAR_DATA_TYPE
  out.variable = row.variable
  out.name_for_plotting = row.name_for_plotting || row.variable
  out.operands = [...(row.operands ?? [])]
  out.unit = row.unit
  out.value = num(row.value, 0)
  out.std = num(row.std, 0)
  out.weight = num(row.weight, 1.0)
  out.experiment_idx = num(row.experiment_idx, 0)
  out.subexperiment_idx = num(row.subexperiment_idx, 0)
  out.plot_type = row.plot_type || 'None'
  if (row.operation) out.operation = row.operation
  else delete out.operation
  // Persist the operation's keyword-arg values (threaded to CA at run time). Drop
  // when there are none, or when no operation is selected (kwargs have no meaning).
  const kw = row.operation_kwargs && typeof row.operation_kwargs === 'object' ? row.operation_kwargs : {}
  if (row.operation && Object.keys(kw).length) out.operation_kwargs = { ...kw }
  else delete out.operation_kwargs
  if (row.cost_type) out.cost_type = row.cost_type
  else delete out.cost_type
  // Write the text source, but never clobber a legacy dict source (file paths).
  if (row.source) out.source = row.source
  else if (typeof out.source === 'string') delete out.source
  if (row.comment) out.comment = row.comment
  else delete out.comment
  return out
}

export function predToRow(pred) {
  return {
    _orig: pred,
    variable: pred.variable ?? '',
    unit: pred.unit ?? 'dimensionless',
    name_for_plotting: pred.name_for_plotting ?? pred.variable ?? '',
    experiment_idx: pred.experiment_idx ?? 0,
  }
}

export function newPredRow(experimentIdx = 0) {
  return {
    _orig: null,
    variable: '',
    unit: 'dimensionless',
    name_for_plotting: '',
    experiment_idx: experimentIdx,
  }
}

export function predRowToItem(row) {
  const out = { ...(row._orig ?? {}) }
  out.variable = row.variable
  out.unit = row.unit
  out.name_for_plotting = row.name_for_plotting || row.variable
  out.experiment_idx = num(row.experiment_idx, 0)
  return out
}

/**
 * Build the obs_data payload. With a protocol_info present, emit the object form
 * `{protocol_info, prediction_items, data_items}` (protocol_info passed through
 * verbatim). Without one (data-only), emit a bare array of data_items — the
 * backend requires protocol_info for the object form, and data-only files have
 * no prediction_items.
 */
export function buildObsData({ protocolInfo, editableRows, preservedItems, predictionRows }) {
  const data_items = [
    ...(editableRows ?? []).map(rowToItem),
    ...(preservedItems ?? []),
  ]
  if (protocolInfo != null) {
    return {
      protocol_info: protocolInfo,
      prediction_items: (predictionRows ?? []).map(predRowToItem),
      data_items,
    }
  }
  return data_items
}

export function experimentIdxMax(experimentCount) {
  return Math.max(0, (experimentCount || 1) - 1)
}

function yymmdd(date = new Date()) {
  const yy = String(date.getFullYear()).slice(-2)
  const mm = String(date.getMonth() + 1).padStart(2, '0')
  const dd = String(date.getDate()).padStart(2, '0')
  return `${yy}${mm}${dd}`
}

/**
 * `<stem>_<yymmdd>.json`. Stem = loaded obs filename minus `.json`, else
 * `<modelName>_obs_data`. The date suffix keeps the original from being
 * overwritten.
 */
export function versionedJsonName(loadedFilename, modelName, date = new Date()) {
  const stem = loadedFilename
    ? loadedFilename.replace(/\.json$/i, '')
    : `${modelName ?? 'model'}_obs_data`
  return `${stem}_${yymmdd(date)}.json`
}
