/**
 * Which step of which workflow the next run from a panel is, and what a step's panel shows.
 *
 * The server decides from the run's `workflow_target` (study_workflow.TARGET_MODES):
 *
 * - `default`: the open workflow's latest step of the panel's kind is changed and run again
 *   with the steps that take from it; a step is added only when there is none of that kind;
 * - `new_step`: a step is added: the user chose "New step" in the Workflow view, and the
 *   next run they start, of whatever kind, is that step;
 * - `edit` + `step`: that step is changed (the user clicked it in the Workflow view);
 * - `new_workflow`: a new workflow is started (the user chose "New workflow").
 *
 * So a second calibration is never made by accident: it has to be asked for.
 */

/** CA's step kind each left tab's panel makes, by the tab's id. */
export const PANEL_KINDS = {
  calibration: 'calibration',
  sensitivity: 'sensitivity',
  uq: 'uq',
  emulator: 'emulation',
  ia: 'identifiability',
}

/** The left tab that shows the settings of a step of CA's `kind` (a reduction is made on
 * the Identifiability tab too), or null for a kind no panel makes (set, sweep). */
const KIND_TABS = { ...Object.fromEntries(Object.entries(PANEL_KINDS).map(([t, k]) => [k, t])),
  reduction: 'ia' }

/** The left tab whose panel makes steps of CA's `kind`, or null. */
export function stepPanel(kind) {
  return KIND_TABS[kind ?? 'calibration'] ?? null
}

function fileSteps(described) {
  return described?.loaded ? (described.workflow?.steps ?? []) : []
}

function kindOf(step) {
  return step?.kind ?? 'calibration'
}

/**
 * The `workflow_target` a run from the panel making steps of `kind` sends, for the mode
 * the user chose (`target`). A mode chosen for another kind of step leaves this panel's
 * run on the default, so choosing "New sensitivity step" never adds a calibration.
 */
export function runTargetFor(target, kind, described) {
  const workflow = described?.loaded ? (described.path ?? undefined) : undefined
  const mode = target?.mode ?? 'default'
  if (mode === 'new_workflow') return { mode }
  // "New step" waits for whatever is run next; a kind is only given by a caller that says so
  if (mode === 'new_step' && (!target.kind || target.kind === kind)) {
    return target.draft ? { mode, workflow, draft: target.draft } : { mode, workflow }
  }
  if (mode === 'edit') {
    const step = fileSteps(described).find((s) => s.id === target.step)
    if (step && kindOf(step) === kind) return { mode, step: step.id, workflow }
  }
  return { mode: 'default', workflow }
}

/** The mode after a run of `kind` started: a new step or workflow is made once. */
export function targetAfterRun(target, kind) {
  if (target?.mode === 'new_workflow') return { mode: 'default' }
  if (target?.mode === 'new_step' && (!target.kind || target.kind === kind)) {
    return { mode: 'default' }
  }
  return target
}

const KIND_NAMES = { calibration: 'calibration', sensitivity: 'sensitivity analysis', uq: 'UQ',
  emulation: 'surrogate', identifiability: 'identifiability analysis' }

/**
 * What the next run from the panel making `kind` steps will be, for the Workflow bar's
 * highlight: `{mode: 'new_workflow' | 'new_step' | 'edit', step, text}`.
 */
export function modeSummary(target, kind, described) {
  const name = described?.workflow?.workflow_name ?? ''
  const what = KIND_NAMES[kind] ?? kind
  const sent = runTargetFor(target, kind, described)
  if (sent.mode === 'new_workflow' || !fileSteps(described).length) {
    return { mode: 'new_workflow', step: null, text: `Settings for a new workflow` }
  }
  if (sent.mode === 'new_step') {
    return { mode: 'new_step', step: sent.draft ?? null,
             text: sent.draft ? `Making ${sent.draft} in ${name}: whatever you run next`
               : `A new step in ${name}: whatever you run next` }
  }
  if (sent.mode === 'edit') {
    return { mode: 'edit', step: sent.step, text: `Changing step ${sent.step} of ${name}` }
  }
  const latest = fileSteps(described).filter((s) => kindOf(s) === kind).pop()
  if (latest) {
    return { mode: 'edit', step: latest.id, text: `Changing step ${latest.id} of ${name}` }
  }
  return { mode: 'new_step', step: null, text: `A new ${what} step in ${name}` }
}

function without(obj, keys) {
  return Object.fromEntries(Object.entries(obj ?? {}).filter(([k]) => !keys.includes(k)))
}

const REF_KEYS = ['result_from', 'start_from', 'sensitivity_from', 'emulator_from',
  'params_for_id_from']

/**
 * `[[key, step id]]`: every step `step` takes something from (CA's keys; a set entry's `of`).
 * With the workflow's `steps`, an `emulator_dir` that is an emulation step's emulator
 * (`.../<step id>/emulator`) is a reference to that step too.
 */
export function referencesOf(step, steps = null) {
  const refs = REF_KEYS.filter((k) => typeof step[k] === 'string').map((k) => [k, step[k]])
  if (steps && typeof step.emulator_dir === 'string') {
    const parts = step.emulator_dir.split(/[\\/]/).filter(Boolean)
    const at = parts.lastIndexOf('emulator')
    const source = at > 0 ? steps.find((s) => s.id === parts[at - 1] && kindOf(s) === 'emulation') : null
    if (source) refs.push(['emulator_dir', source.id])
  }
  for (const key of ['fixed_from', 'priors_from']) {
    for (const f of step[key] ?? []) {
      refs.push([key, typeof f === 'string' ? f : (f?.step ?? f?.from_step)])
    }
  }
  for (const e of step.set ?? []) if (typeof e?.of === 'string') refs.push(['scale_from', e.of])
  return refs
}

/** Every `{from, to, key}` reference between `steps`, by their rows. */
export function edgesOf(steps) {
  const row = Object.fromEntries(steps.map((s, i) => [s.id, i]))
  const out = []
  steps.forEach((s, to) => {
    for (const [key, id] of referencesOf(s, steps)) {
      if (id in row && row[id] !== to) out.push({ from: row[id], to, key })
    }
  })
  return out
}

/** The description the server gives a calibration made first for step `stepId`. */
export function firstDescription(stepId) {
  return `calibrated first for step ${stepId}`
}

/**
 * The point a step was started from or analysed at, as the panel's word for it: a set step
 * by its label (`sliders-` the slider values, `best-fit-` a best fit, `nominal-` the bounds'
 * midpoint or geometric mean), a calibration step its best fit. Null for none.
 */
function pointWord(sourceId, byId) {
  const source = byId[sourceId]
  if (!source) return null
  if (kindOf(source) === 'calibration') return 'best_fit'
  if (sourceId.startsWith('sliders-')) return 'current'
  if (sourceId.startsWith('best-fit-')) return 'best_fit'
  if (sourceId.startsWith('nominal-')) {
    const reason = source.set?.[0]?.reason ?? ''
    return reason.includes('geometric') ? 'geometric' : 'midpoint'
  }
  return null
}

/**
 * A step of the workflow file as the settings of the panel that makes it (the inverse of
 * the server's main._study_calibration / _study_sensitivity / _study_uq), for that panel's
 * `applySettings`. Keys the panel does not know are left alone by it.
 */
export function panelSettings(step, described) {
  const steps = fileSteps(described)
  const byId = Object.fromEntries(steps.map((s) => [s.id, s]))
  const s = step?.settings ?? {}
  const debug = { DEBUG: !!s.DEBUG }
  const kind = kindOf(step)
  if (kind === 'calibration') {
    return {
      ...without(s.optimiser_options, ['seed']),
      ...(s.param_id_method ? { param_id_method: s.param_id_method } : {}),
      start_from: pointWord(step.start_from, byId) ?? 'model',
      ...debug,
    }
  }
  // The calibration the run made first for this step ("run a calibration first"): the server
  // writes study_workflow.first_description on it. A calibration of the user's own that the
  // step is at is the previous best fit, though no other step takes from it.
  const source = byId[step?.result_from]
  const madeFirst = !!source && kindOf(source) === 'calibration'
    && source.description === firstDescription(step.id)
  if (kind === 'sensitivity') {
    const sa = without(s.sa_options, ['seed', 'output_dir'])
    const out = { ...sa, ...debug }
    if (sa.fd_rel_step != null) out.rel_step = sa.fd_rel_step
    if (sa.method === 'local') {
      out.run_calibration_first = madeFirst
      const word = madeFirst ? null : pointWord(step.result_from, byId)
      if (word) out.nominal = word
    }
    return out
  }
  if (kind === 'uq') {
    return {
      ...without(s.UQ_options, ['seed']),
      run_calibration_first: madeFirst,
      ...debug,
    }
  }
  if (kind === 'emulation') {
    return { ...without(s.emulator_settings, ['random_seed', 'emulator_dir']), ...debug }
  }
  if (kind === 'identifiability') {
    return { ia_options: { ...(s.ia_options ?? {}) }, ...debug }
  }
  if (kind === 'reduction') {
    return { param_reduction_options: { ...(s.param_reduction_options ?? step.reduction ?? {}) } }
  }
  return {}
}

/**
 * What an external figure (the workflow's `external_figures`: {id, from, reads, writes, ...})
 * takes from, as `[{from, files}]`: a step when a file it reads is under that step's run
 * folder (`energy_cost/sweep/...`), another figure when it reads a file that figure writes
 * (`figure:<id>`), else the target (`workflow_result.json`, the calibrated model).
 */
export function figureReads(figure, steps, figures) {
  const ids = new Set(steps.map((s) => s.id))
  const writer = {}
  for (const f of figures) for (const w of f.writes ?? []) writer[w] = `figure:${f.id}`
  const out = {}
  for (const read of figure.reads ?? []) {
    const first = String(read).split(/[\\/]/)[0]
    const from = writer[read] && writer[read] !== `figure:${figure.id}` ? writer[read]
      : ids.has(first) ? first : 'target'
    ;(out[from] ??= []).push(read)
  }
  if (!Object.keys(out).length && figure.from === 'target') out.target = []
  return Object.entries(out).map(([from, files]) => ({ from, files }))
}

/**
 * Where each step sits in the workflow chart: `{id: {col, row}}`. A step is one column right
 * of the furthest-right step it takes from (so every arrow points right), and a column holds
 * its steps in the file's order.
 */
export function layoutSteps(steps, positions = {}, pending = []) {
  const ids = new Set(steps.map((s) => s.id))
  // a box is never left of the column it was put in (positions), nor of what it takes from
  const col = Object.fromEntries(steps.map((s) => [s.id, Math.max(0, positions[s.id]?.col ?? 0)]))
  const refs = (s) => [...referencesOf(s, steps),
    ...pending.filter((l) => l.target === s.id).map((l) => [l.key, l.source])]
  for (let pass = 0; pass <= steps.length; pass += 1) {
    let changed = false
    for (const s of steps) {
      for (const [, source] of refs(s)) {
        if (ids.has(source) && source !== s.id && col[s.id] < col[source] + 1) {
          col[s.id] = col[source] + 1
          changed = true
        }
      }
    }
    if (!changed) break
  }
  // within a column, by the order a box was put at (positions' row), else the file's
  const key = (s, i) => positions[s.id]?.row ?? i
  const order = steps.map((s, i) => [s, key(s, i), i]).sort((a, b) => a[1] - b[1] || a[2] - b[2])
  const rows = {}
  const out = {}
  for (const [s, k] of order) {
    const c = col[s.id]
    rows[c] = (rows[c] ?? 0) + 1
    out[s.id] = { col: c, row: rows[c] - 1, key: k }
  }
  return out
}

/**
 * What an arrow from `source` to `target` can pass, by CA's rules (describe's link_kinds:
 * {key: {sources, users}}): the keys whose sources include the source's kind and whose users
 * include the target's. A new step (a draft) has no kind yet, so any.
 */
export function linkChoices(linkKinds, source, target) {
  const kind = (s) => (s && !s.draft ? kindOf(s) : null)
  return Object.entries(linkKinds ?? {})
    .filter(([, k]) => !kind(source) || k.sources.includes(kind(source)))
    .filter(([, k]) => !kind(target) || !k.users.length || k.users.includes(kind(target)))
    .map(([key]) => key)
}

/** An arrow's entry in its target step (a list key's object with its options), or null. */
export function linkEntry(step, source, key) {
  const value = step?.[key]
  if (typeof value === 'string') return value === source ? {} : null
  for (const e of Array.isArray(value) ? value : []) {
    if (e === source) return {}
    if (e && typeof e === 'object' && (e.step ?? e.from_step) === source) {
      const { step: _s, from_step: _f, ...options } = e
      return options
    }
  }
  return null
}
