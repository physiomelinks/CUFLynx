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
  // Members of a loaded group, so the model-parameter pass below doesn't offer
  // them again as separate rows — they already belong to a row (#193).
  const claimed = new Set()

  for (const p of currentParams) {
    const qnames = p.qnames?.length ? [...p.qnames] : [p.qname]
    for (const q of qnames) claimed.add(q)
    const isModifier = !!(p.modifies?.length)
    byQname.set(p.qname, {
      qname: p.qname,
      // Every variable this row drives, `qname` first. One entry for an ordinary
      // row, so nothing downstream has to ask whether a row is a group. For a
      // modifier these are its *modified* qnames.
      qnames,
      // Set on a row that has been absorbed into another row's group: it keeps
      // its edits (so ungrouping restores them) but is neither shown nor saved.
      groupedInto: null,
      // Set on a row claimed as a modifier's target -- same lifecycle as
      // groupedInto, keyed by the modifier's name so deletion restores it.
      modifiedBy: null,
      // Multi-select for the Group / Create-modifier toolbar actions.
      selected: false,
      included: true,
      // The entry's identity (CA enforces uniqueness across the file); the
      // handle a modifier is referenced and labelled by.
      name: p.name ?? p.qname,
      kind: isModifier ? 'modifier' : 'free',
      operation: p.operation ?? null,
      // Per-target model default ({qname: baseline}) for the θ·baselineᵢ preview.
      baselines: p.baselines ? { ...p.baselines } : null,
      min: p.min,
      max: p.max,
      name_for_plotting: p.name_for_plotting ?? p.qname,
      param_type: p.param_type ?? null,
      initial_value: p.initial_value ?? initials[p.qname] ?? null,
      // free-text annotation/note about this parameter's range.
      comment: p.comment ?? '',
      // MCMC/UQ prior for this parameter. '' means "not stated", which CA reads
      // as its default — distinct from explicitly choosing that default, so an
      // untouched CSV keeps its column exactly as it was.
      prior: p.prior ?? '',
      // The values that prior takes, keyed by CA's column name. A bag rather
      // than named fields: which values exist is CA's vocabulary, and one it
      // grows should flow through without a change here.
      priorParams: { ...(p.prior_params ?? {}) },
      // No min/max of its own: the prior says where it lives and CA derives the
      // range. The min/max on the row are those derived values, shown but not typed.
      unbounded: !!p.unbounded,
    })
  }

  for (const qname of modelVariables.params || []) {
    if (byQname.has(qname) || claimed.has(qname)) continue
    const iv = initials[qname] ?? null
    const { min, max } = defaultRange(iv)
    byQname.set(qname, {
      qname,
      qnames: [qname],
      groupedInto: null,
      modifiedBy: null,
      selected: false,
      included: false,
      name: qname,
      kind: 'free',
      operation: null,
      baselines: null,
      min,
      max,
      name_for_plotting: qname,
      param_type: null,
      initial_value: iv,
      comment: '',
      prior: '',
      priorParams: {},
      unbounded: false,
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

/**
 * Whether `candidate` could join `row`'s group (#193 / #208).
 *
 * The same-`param_name` restriction is gone: it existed only because the CSV
 * had a single `param_name` column, and the JSON form's `targets` list has no
 * such constraint -- grouping arbitrary parameters is the point of #208. A row
 * already absorbed elsewhere, and a row that is a group itself, are still
 * excluded (merging two groups would have to decide whose range and prior
 * survive), as are modifiers and their claimed targets.
 */
export function canJoinGroup(row, candidate) {
  return (
    candidate !== row &&
    row.kind !== 'modifier' &&
    candidate.kind !== 'modifier' &&
    candidate.modifiedBy == null &&
    row.modifiedBy == null &&
    (candidate.groupedInto == null || candidate.groupedInto === row.qname) &&
    (candidate.qnames?.length ?? 1) === 1
  )
}

/**
 * Rows that could join `row`'s group.
 *
 * @param {Array<object>} rows - every row (visible or absorbed)
 * @param {object} row
 */
export function groupCandidates(rows, row) {
  return rows.filter((r) => canJoinGroup(row, r))
}

/**
 * Absorb `member` into `row`'s group: `row` now drives it too, and `member` stops
 * being a row of its own. Kept rather than deleted so unticking restores it with
 * its range, prior and note intact. No-op if it is already in the group.
 */
export function addToGroup(row, member) {
  if (row === member || row.qnames.includes(member.qname)) return
  row.qnames = [...row.qnames, member.qname]
  member.groupedInto = row.qname
}

/** The inverse: `member` becomes an independent row again. */
export function removeFromGroup(row, member) {
  row.qnames = row.qnames.filter((q) => q !== member.qname)
  member.groupedInto = null
}

/** The rows a file is written from: ticked, and not absorbed into a group or
 * claimed by a modifier. */
export function rowsToSave(rows) {
  return rows.filter((r) => r.included && !r.groupedInto && !r.modifiedBy)
}


/**
 * A unique modifier name: `scale_<shared param_name>` when every target shares
 * one, else `scale_params`, deduplicated against the existing row names.
 */
export function suggestModifierName(rows, targets) {
  const names = new Set(targets.map((q) => splitQname(q).param_name))
  const base = names.size === 1 ? `scale_${[...names][0]}` : 'scale_params'
  const taken = new Set(rows.map((r) => r.name))
  if (!taken.has(base)) return base
  let n = 2
  while (taken.has(`${base}_${n}`)) n += 1
  return `${base}_${n}`
}


/**
 * Whether these rows can become a modifier's targets: at least one, all free,
 * none already claimed by a group or another modifier -- the client-side mirror
 * of CA's no-chain / no-double-modification rules, for immediate feedback (CA
 * re-judges the file on upload).
 */
export function canCreateModifier(selectedRows) {
  return (
    selectedRows.length > 0 &&
    selectedRows.every(
      (r) =>
        r.kind !== 'modifier' &&
        r.groupedInto == null &&
        r.modifiedBy == null &&
        (r.qnames?.length ?? 1) === 1,
    )
  )
}


/**
 * Create a scale-modifier row over `selectedRows` and claim them (#208).
 *
 * θ's bounds come from the operation's vocabulary entry (CA's
 * default_min/default_max); its value column shows the identity, at which every
 * target sits at its baseline. The claimed rows keep their edits and are
 * restored by `removeModifier`. Returns the new row, already appended to
 * `rows`, or null when the selection cannot become one.
 */
export function createModifier(rows, selectedRows, opMeta = {}) {
  if (!canCreateModifier(selectedRows)) return null
  const targets = selectedRows.map((r) => r.qname)
  const name = suggestModifierName(rows, targets)
  const baselines = {}
  for (const r of selectedRows) {
    if (r.initial_value != null) baselines[r.qname] = r.initial_value
  }
  const row = {
    qname: targets[0], // the anchor: modifies[0], the slider's key
    qnames: targets,
    groupedInto: null,
    modifiedBy: null,
    selected: false,
    included: true,
    name,
    kind: 'modifier',
    operation: opMeta.value ?? 'scale',
    baselines,
    min: opMeta.default_min ?? 0.5,
    max: opMeta.default_max ?? 2.0,
    name_for_plotting: name,
    param_type: null,
    initial_value: opMeta.identity ?? 1.0,
    comment: '',
    prior: '',
    priorParams: {},
    unbounded: false,
  }
  for (const r of selectedRows) {
    r.modifiedBy = name
    // A modifier's target is definitionally in play: deleting the modifier
    // must restore it as an included row, not silently drop it from the file.
    r.included = true
    r.selected = false
  }
  rows.push(row)
  return row
}


/** Delete a modifier row and restore its claimed targets as their own rows. */
export function removeModifier(rows, modifierRow) {
  const idx = rows.indexOf(modifierRow)
  if (idx !== -1) rows.splice(idx, 1)
  for (const r of rows) {
    if (r.modifiedBy === modifierRow.name) r.modifiedBy = null
  }
}

function csvField(value) {
  const s = value == null ? '' : String(value)
  return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s
}

function numField(value) {
  return value == null || !Number.isFinite(Number(value)) ? '' : String(Number(value))
}

/**
 * Build params_for_id CSV text from the rows to write (one row per parameter —
 * a grouped row names all of its vessels in the one `vessel_name` cell). The
 * `param_type`, `prior` and `comment` columns are only emitted when at least one
 * row carries one. Column order matches the parser's expectations (vessel_name,
 * param_name, min, max, name_for_plotting[, param_type][, prior][, comment]).
 * circulatory_autogen reads columns by name and ignores unknown ones (like the
 * `comment` annotation), so the CSV stays valid for CA.
 *
 * `prior` is emitted for the same reason the others are: dropping a column the
 * user's CSV carried is data loss. It is the one that bites hardest, because CA
 * reads a missing prior as `uniform` — so rewriting the file without it silently
 * replaced every non-uniform prior with a uniform one, and the next MCMC run
 * sampled a different posterior with nothing said.
 *
 * @param {Array<object>} rows
 * @returns {string}
 */
export function buildParamsCsv(rows) {
  const withType = rows.some((r) => r.param_type != null && r.param_type !== '')
  const withComment = rows.some((r) => r.comment != null && r.comment !== '')
  const withPrior = rows.some((r) => r.prior != null && r.prior !== '')
  const withUnbounded = rows.some((r) => r.unbounded)
  // One column per prior hyper-parameter any row actually states, in a stable
  // order. Derived from the rows rather than a list of names held here, so a
  // value CA adds to a prior travels without this file knowing about it.
  const priorParamCols = [
    ...new Set(
      rows.flatMap((r) =>
        Object.entries(r.priorParams ?? {})
          .filter(([, v]) => v != null && v !== '')
          .map(([k]) => k),
      ),
    ),
  ].sort()

  const header = ['vessel_name', 'param_name', 'min', 'max', 'name_for_plotting']
  if (withType) header.push('param_type')
  if (withPrior) header.push('prior')
  if (withUnbounded) header.push('unbounded')
  header.push(...priorParamCols)
  if (withComment) header.push('comment')

  const lines = [header.join(',')]
  for (const r of rows) {
    const { param_name } = splitQname(r.qname)
    // A grouped row writes every vessel into the one `vessel_name` cell,
    // whitespace-separated — CA's own notation for "this parameter, in all of
    // these components" (#193). Writing only `r.qname`'s vessel would silently
    // dissolve the group the moment the file was re-saved.
    const vessel_name = (r.qnames?.length ? r.qnames : [r.qname])
      .map((q) => splitQname(q).vessel_name)
      .join(' ')
    const cells = [
      csvField(vessel_name),
      csvField(param_name),
      // An unbounded row writes no bounds: they were derived from its prior, and
      // writing them back would freeze a range that should follow the prior.
      r.unbounded ? '' : numField(r.min),
      r.unbounded ? '' : numField(r.max),
      csvField(r.name_for_plotting ?? r.qname),
    ]
    if (withType) cells.push(csvField(r.param_type))
    if (withPrior) cells.push(csvField(r.prior))
    if (withUnbounded) cells.push(r.unbounded ? 'true' : '')
    for (const col of priorParamCols) cells.push(csvField((r.priorParams ?? {})[col]))
    if (withComment) cells.push(csvField(r.comment))
    lines.push(cells.join(','))
  }
  return lines.join('\n') + '\n'
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
