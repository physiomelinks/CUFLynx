// Pure helpers for the "Edit params_for_id" dialog: merge the loaded CSV params
// with the model's other parameters, build a params_for_id CSV from the edited
// rows, and derive a date-versioned filename. No Vue here so these stay easy to
// unit-test and reuse.

/**
 * Default min/max for a model parameter that wasn't in the loaded CSV: ±10% of
 * its initial value. Falls back to [0, 1] when the initial value is unknown or
 * zero (a percentage of 0 collapses to a zero-width range). Sign-safe — `base`
 * is non-negative so `min < max` for negative initial values too.
 *
 * @param {number|null|undefined} initialValue
 * @returns {{min: number, max: number}}
 */
export function defaultRange(initialValue) {
  if (initialValue == null || initialValue === 0) return { min: 0, max: 1 }
  const base = 0.1 * Math.abs(initialValue)
  return { min: initialValue - base, max: initialValue + base }
}

/**
 * Merge the loaded CSV's params (pre-included) with the model's other
 * parameters (available to add). Each result row is
 * `{ qname, included, min, max, name_for_plotting, param_type, initial_value }`.
 * CSV entries win on conflict; rows are sorted included-first then by qname.
 *
 * @param {Array<object>} currentParams - loaded ParamEntry dicts ([] if none)
 * @param {{params?: string[], initial_values?: Record<string, number>}} modelVariables
 */
export function mergedRows(currentParams = [], modelVariables = {}) {
  const initials = modelVariables.initial_values || {}
  const byQname = new Map()

  for (const p of currentParams) {
    byQname.set(p.qname, {
      qname: p.qname,
      included: true,
      min: p.min,
      max: p.max,
      name_for_plotting: p.name_for_plotting ?? p.qname,
      param_type: p.param_type ?? null,
      initial_value: p.initial_value ?? initials[p.qname] ?? null,
      // free-text annotation/note about this parameter's range.
      comment: p.comment ?? '',
    })
  }

  for (const qname of modelVariables.params || []) {
    if (byQname.has(qname)) continue
    const iv = initials[qname] ?? null
    const { min, max } = defaultRange(iv)
    byQname.set(qname, {
      qname,
      included: false,
      min,
      max,
      name_for_plotting: qname,
      param_type: null,
      initial_value: iv,
      comment: '',
    })
  }

  return [...byQname.values()].sort((a, b) => {
    if (a.included !== b.included) return a.included ? -1 : 1
    return a.qname.localeCompare(b.qname)
  })
}

/** Split a `vessel/param` qname on the LAST slash (param_name has no slash). */
export function splitQname(qname) {
  const i = qname.lastIndexOf('/')
  return i === -1
    ? { vessel_name: '', param_name: qname }
    : { vessel_name: qname.slice(0, i), param_name: qname.slice(i + 1) }
}

function csvField(value) {
  const s = value == null ? '' : String(value)
  return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s
}

function numField(value) {
  return value == null || !Number.isFinite(Number(value)) ? '' : String(Number(value))
}

/**
 * Build params_for_id CSV text from the rows to write (one row per qname). The
 * `param_type` and `comment` columns are only emitted when at least one row
 * carries one. Column order matches the parser's expectations (vessel_name,
 * param_name, min, max, name_for_plotting[, param_type][, comment]).
 * circulatory_autogen reads columns by name and ignores unknown ones (like the
 * `comment` annotation), so the CSV stays valid for CA.
 *
 * @param {Array<object>} rows
 * @returns {string}
 */
export function buildParamsCsv(rows) {
  const withType = rows.some((r) => r.param_type != null && r.param_type !== '')
  const withComment = rows.some((r) => r.comment != null && r.comment !== '')
  const header = ['vessel_name', 'param_name', 'min', 'max', 'name_for_plotting']
  if (withType) header.push('param_type')
  if (withComment) header.push('comment')

  const lines = [header.join(',')]
  for (const r of rows) {
    const { vessel_name, param_name } = splitQname(r.qname)
    const cells = [
      csvField(vessel_name),
      csvField(param_name),
      numField(r.min),
      numField(r.max),
      csvField(r.name_for_plotting ?? r.qname),
    ]
    if (withType) cells.push(csvField(r.param_type))
    if (withComment) cells.push(csvField(r.comment))
    lines.push(cells.join(','))
  }
  return lines.join('\n') + '\n'
}

/**
 * Build a "parameter values" CSV from a saved slider snapshot. Columns are
 * `vessel_name,param_name,value,name_for_plotting` — the qname is split the same
 * way as params_for_id (last slash), so the file reads like a params_for_id CSV
 * but records the locked-in values instead of ranges (issue #106).
 *
 * @param {Array<{qname: string, value: number, name_for_plotting?: string}>} rows
 * @returns {string}
 */
export function buildParamValuesCsv(rows) {
  const header = ['vessel_name', 'param_name', 'value', 'name_for_plotting']
  const lines = [header.join(',')]
  for (const r of rows) {
    const { vessel_name, param_name } = splitQname(r.qname)
    lines.push(
      [
        csvField(vessel_name),
        csvField(param_name),
        numField(r.value),
        csvField(r.name_for_plotting ?? r.qname),
      ].join(','),
    )
  }
  return lines.join('\n') + '\n'
}

/** `<modelName>_param_values_<yymmdd>.csv` for a saved-snapshot export. */
export function snapshotFilename(modelName, date = new Date()) {
  return `${modelName ?? 'model'}_param_values_${yymmdd(date)}.csv`
}

/** yymmdd for the current local date. */
function yymmdd(date = new Date()) {
  const yy = String(date.getFullYear()).slice(-2)
  const mm = String(date.getMonth() + 1).padStart(2, '0')
  const dd = String(date.getDate()).padStart(2, '0')
  return `${yy}${mm}${dd}`
}

/**
 * `<stem>_<yymmdd>.csv`. The stem is the loaded CSV's name (minus `.csv`), or
 * `<modelName>_params_for_id` when no CSV was loaded. The date suffix keeps the
 * original file from being overwritten.
 */
export function versionedFilename(loadedFilename, modelName, date = new Date()) {
  const stem = loadedFilename
    ? loadedFilename.replace(/\.csv$/i, '')
    : `${modelName ?? 'model'}_params_for_id`
  return `${stem}_${yymmdd(date)}.csv`
}
