// Pure helpers for writing the params_for_id JSON form from the editor's rows.
// The CSV cannot express an entry whose targets have different names, nor a
// modifier at all -- so from the modifier feature on, the editor *saves* JSON
// (the upload endpoint content-sniffs, and CA branches on the .json suffix the
// backend now stores). CSV remains a load-only legacy format.
//
// Keys are restricted to CA's closed PARAMS_FOR_ID_ENTRY_KEYS: an invented key
// makes the whole file unreadable by CA (its resolver refuses unknown keys).

import { splitQname } from './paramsCsv'

const VERSION = 1

function cleanText(value) {
  const s = value == null ? '' : String(value).trim()
  return s || null
}

/**
 * One saved row as a params_for_id JSON entry.
 *
 * A free row writes `targets` (every member qname, order preserved -- CA's
 * baselines pair by index); a modifier row writes `modifies` + `operation` and
 * never `targets` (CA refuses an entry carrying both). An unbounded row omits
 * its bounds: they were derived from the prior and writing them back would
 * freeze a range that should follow it.
 */
function rowToEntry(row) {
  const qnames = row.qnames?.length ? [...row.qnames] : [row.qname]
  const entry = { name: row.name || row.qname }
  if (row.kind === 'modifier') {
    entry.modifies = qnames
    entry.operation = row.operation || 'scale'
  } else {
    entry.targets = qnames
  }
  if (row.unbounded) {
    entry.unbounded = true
  } else {
    if (Number.isFinite(Number(row.min))) entry.min = Number(row.min)
    if (Number.isFinite(Number(row.max))) entry.max = Number(row.max)
  }
  const nfp = cleanText(row.name_for_plotting)
  // The qname as a label is the editor's display default, not an authored name.
  if (nfp && nfp !== row.qname) entry.name_for_plotting = nfp
  const ptype = cleanText(row.param_type)
  if (ptype) entry.param_type = ptype
  const prior = cleanText(row.prior)
  if (prior) entry.prior = prior
  const priorParams = {}
  for (const [key, value] of Object.entries(row.priorParams ?? {})) {
    const text = cleanText(value)
    if (text != null) priorParams[key] = text
  }
  if (Object.keys(priorParams).length) entry.prior_params = priorParams
  const comment = cleanText(row.comment)
  if (comment) entry.comment = comment
  return entry
}

/**
 * The params_for_id document the editor saves: `{version, defaults, params}`.
 * `rows` are the rows to write (see paramsCsv.rowsToSave).
 */
export function rowsToDoc(rows) {
  return { version: VERSION, defaults: {}, params: rows.map(rowToEntry) }
}

/** yymmdd for the current local date (mirrors obsDataJson). */
function yymmdd(date = new Date()) {
  const yy = String(date.getFullYear()).slice(-2)
  const mm = String(date.getMonth() + 1).padStart(2, '0')
  const dd = String(date.getDate()).padStart(2, '0')
  return `${yy}${mm}${dd}`
}

/**
 * `<stem>_<yymmdd>.json`. The stem is the loaded file's name minus its
 * extension -- a study loaded from CSV saves as JSON from here on, keeping its
 * stem so the lineage stays visible.
 */
export function versionedJsonName(loadedFilename, modelName, date = new Date()) {
  const stem = loadedFilename
    ? loadedFilename.replace(/\.(csv|json)$/i, '')
    : `${modelName ?? 'model'}_params_for_id`
  return `${stem}_${yymmdd(date)}.json`
}

export { splitQname }
