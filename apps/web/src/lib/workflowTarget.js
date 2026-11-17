/**
 * Which step of which workflow the next run from a panel is, and what a step's panel shows.
 *
 * The server decides from the run's `workflow_target` (study_workflow.TARGET_MODES):
 *
 * - `default`: the open workflow's latest step of the panel's kind is changed and run again
 *   with the steps that take from it; a step is added only when there is none of that kind;
 * - `new_step`: a step is added (the user chose "New ... step" in the Workflow view);
 * - `edit` + `step`: that step is changed (the user clicked it in the Workflow view);
 * - `new_workflow`: a new workflow is started (the user chose "New workflow").
 *
 * So a second calibration is never made by accident: it has to be asked for.
 */

/** CA's step kind of each panel that makes steps, by the left tab's id. */
export const PANEL_KINDS = { calibration: 'calibration', sensitivity: 'sensitivity', uq: 'uq' }

/** The left tab whose panel makes steps of CA's `kind`, or null. */
export function stepPanel(kind) {
  const k = kind ?? 'calibration'
  return Object.keys(PANEL_KINDS).find((tab) => PANEL_KINDS[tab] === k) ?? null
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
  if (mode === 'new_step' && target.kind === kind) return { mode, workflow }
  if (mode === 'edit') {
    const step = fileSteps(described).find((s) => s.id === target.step)
    if (step && kindOf(step) === kind) return { mode, step: step.id, workflow }
  }
  return { mode: 'default', workflow }
}

/** The mode after a run of `kind` started: a new step or workflow is made once. */
export function targetAfterRun(target, kind) {
  if (target?.mode === 'new_workflow') return { mode: 'default' }
  if (target?.mode === 'new_step' && target.kind === kind) return { mode: 'default' }
  return target
}

const KIND_NAMES = { calibration: 'calibration', sensitivity: 'sensitivity analysis', uq: 'UQ' }

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
    return { mode: 'new_step', step: null, text: `A new ${what} step in ${name}` }
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

/** `[[key, step id]]`: every step `step` takes something from (CA's keys; a set entry's `of`). */
export function referencesOf(step) {
  const refs = REF_KEYS.filter((k) => typeof step[k] === 'string').map((k) => [k, step[k]])
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
    for (const [key, id] of referencesOf(s)) {
      if (id in row && row[id] !== to) out.push({ from: row[id], to, key })
    }
  })
  return out
}

/** The step ids that take something from `stepId`, by any key. */
function takers(steps, stepId) {
  return steps.filter((s) => referencesOf(s).some(([, id]) => id === stepId)).map((s) => s.id)
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
  // A calibration that only this step takes from was made for it ("run a calibration first").
  const source = byId[step?.result_from]
  const madeFirst = !!source && kindOf(source) === 'calibration'
    && takers(steps, source.id).every((id) => id === step.id)
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
  return {}
}
