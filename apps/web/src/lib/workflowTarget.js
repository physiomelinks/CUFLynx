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
  identifiability: 'identifiability',
}

/** The left tab that shows the settings of a step of CA's `kind` (a reduction is made on
 * the Identifiability tab too), or null for a kind no panel makes (set, sweep). */
const KIND_TABS = { ...Object.fromEntries(Object.entries(PANEL_KINDS).map(([t, k]) => [k, t])),
  reduction: 'identifiability' }

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
  if (mode === 'new_step' && (!target.kind || target.kind === kind)) return { mode, workflow }
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
    return { mode: 'new_step', step: null,
             text: `A new step in ${name}: whatever you run next` }
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
 * Where each step sits in the workflow chart: `{id: {col, row}}`. A step is one column right
 * of the furthest-right step it takes from (so every arrow points right), and a column holds
 * its steps in the file's order.
 */
export function layoutSteps(steps) {
  const ids = new Set(steps.map((s) => s.id))
  const col = Object.fromEntries(steps.map((s) => [s.id, 0]))
  for (let pass = 0; pass <= steps.length; pass += 1) {
    let changed = false
    for (const s of steps) {
      for (const [, source] of referencesOf(s, steps)) {
        if (ids.has(source) && source !== s.id && col[s.id] < col[source] + 1) {
          col[s.id] = col[source] + 1
          changed = true
        }
      }
    }
    if (!changed) break
  }
  const rows = {}
  const out = {}
  for (const s of steps) {
    const c = col[s.id]
    rows[c] = (rows[c] ?? 0) + 1
    out[s.id] = { col: c, row: rows[c] - 1 }
  }
  return out
}
