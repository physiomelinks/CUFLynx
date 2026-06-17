/**
 * Helpers for the Settings popup's backend-solver controls. The shapes come from
 * the /api/config payload (see apps/api/solver_options.py):
 *   { model_formats, solvers_by_format, default_solver_by_format,
 *     solver_info_schema: { <solver>: [{key,label,type,default,options?}] } }
 * Kept pure so they're unit-testable without mounting the app.
 */

/** Solver ids valid for a generated_model_format. */
export function solversForFormat(opts, format) {
  return opts?.solvers_by_format?.[format] ?? []
}

/** The default solver for a format (explicit default, else first valid). */
export function defaultSolverFor(opts, format) {
  return (
    opts?.default_solver_by_format?.[format] ??
    solversForFormat(opts, format)[0] ??
    ''
  )
}

/** The editable solver_info field descriptors for a solver. */
export function solverFields(opts, solver) {
  return opts?.solver_info_schema?.[solver] ?? []
}

/**
 * Fields to show for a given solver + selected method. A field with a `methods`
 * restriction only applies to those methods; the `method` selector and
 * unrestricted fields always show. Keeps the visible settings tied to the kwargs
 * the chosen method actually uses.
 */
export function solverFieldsForMethod(opts, solver, method) {
  return solverFields(opts, solver).filter(
    (f) => f.key === 'method' || !f.methods || f.methods.includes(method),
  )
}

/** A solver_info object seeded from a solver's schema defaults (non-null only). */
export function defaultSolverInfo(opts, solver) {
  const out = {}
  for (const f of solverFields(opts, solver)) {
    if (f.default != null) out[f.key] = f.default
  }
  return out
}

/** Distinct, meaningful operation names referenced by an obs_data's items. */
export function obsDataOperations(obsData) {
  if (!obsData) return []
  const items = [...(obsData.data_items ?? []), ...(obsData.prediction_items ?? [])]
  const ops = items
    .map((it) => it.operation)
    .filter((o) => o && String(o).toLowerCase() !== 'none')
  return [...new Set(ops)]
}

/**
 * Of the operations actually used by the obs_data, those that aren't
 * @differentiable per the backend's differentiable_operations map (an operation
 * absent from the map is treated as not-differentiable). Drives the AD gate so it
 * reflects the loaded problem rather than the whole CA registry.
 */
export function nonDifferentiableInUse(obsData, differentiableOps) {
  const map = differentiableOps || {}
  return obsDataOperations(obsData).filter((op) => map[op] !== true)
}
