"""Local (derivative-based) sensitivity analysis for CellML models.

Complements the global Sobol engine in :mod:`sensitivity_runner`: instead of
sampling the whole parameter box, this perturbs each parameter about a single
*nominal* point and measures how each observable responds — a local sensitivity
analysis (CUFLynx issue #22).

Only the **finite-difference (FD)** gradient source is implemented here. FD works
for ``cellml_only`` (Myokit) models because it only needs the forward simulation,
which is exactly what the sobol engine already wires up. The two other gradient
sources the UI advertises depend on upstream circulatory_autogen support and are
not available yet:

* **AD** (CasADi automatic differentiation) — only for ``model_type:
  casadi_python`` (CUFLynx issue #9).
* **CVODES** (Myokit forward sensitivities) — not exposed by the Myokit solver
  wrapper yet (CA issue physiomelinks/circulatory_autogen#239).

Rather than re-derive obs_data / params_for_id parsing, observable extraction and
the simulation helper, we reuse the already-constructed
``SensitivityAnalysis.SA_manager`` (a ``sobol_SA`` instance): it exposes the
``ProtocolExecutor``, parsed ``obs_info`` / ``protocol_info`` / ``param_id_info``
and the operation-function table. We only add the FD loop on top.

The returned payload matches the Sobol runner's shape so the job manager and the
frontend heatmap consume it unchanged, with the index ``kind`` keyed as
``"local"`` instead of ``"S1"`` / ``"ST"``::

    {"indices": {"local": {out_name: {param: coeff}}},
     "param_names": [...], "output_names": [...]}

Each coefficient is the dimensionless relative (log-log) sensitivity
``d ln(output) / d ln(param)`` evaluated by a central difference about the
nominal point. Cells where the base output is ~0 (relative sensitivity
undefined) or a simulation failed are reported as ``None`` ("–" in the heatmap).
"""

from __future__ import annotations

import re

import numpy as np

_TINY = 1e-12


def format_output_name(name_for_plotting, exp_idx, subexp_idx, operation=None) -> str:
    """Shared sensitivity output-name label, used by both the local and Sobol paths.

    Produces ``var^{exp,subexp} [operation]`` (e.g. ``V_lv^{0,1} [max]``). When the
    operation is empty/None the ``[..]`` suffix is omitted (``V_lv^{0,1}``). The
    ``^{e,s}`` superscript is LaTeX so the frontend's ``renderMath`` typesets it.
    """
    label = f"{name_for_plotting}^{{{exp_idx},{subexp_idx}}}"
    op = (str(operation).strip() if operation is not None else "")
    if op and op.lower() != "none":
        label += f" [{op}]"
    return label


# Matches the legacy Sobol key form ``name (ExpX, SubY)`` with an optional
# trailing ``[op]`` (added by CA on name collisions) and ``#k`` dedupe suffix, so
# the Sobol path can be reformatted to match the local path without re-deriving
# from obs_info (which would risk misaligning labels with the indices columns).
_SOBOL_KEY_RE = re.compile(
    r"^(?P<name>.*?)\s*\(Exp(?P<exp>[^,]+),\s*Sub(?P<sub>[^)]+)\)"
    r"(?:\s*\[(?P<op>[^\]]*)\])?(?P<dup>\s*#\d+)?$"
)


def format_sobol_output_name(key: str) -> str:
    """Reformat one CA Sobol indices key into the shared output-name format.

    CA keys look like ``name (ExpX, SubY)``, optionally ``... [op]`` and/or
    ``... #k`` (collision dedupe). Keys that don't match (e.g. the trailing
    ``Cost`` column) are returned unchanged. The ``#k`` dedupe suffix is preserved
    so distinct columns keep distinct (and still unique) keys.
    """
    m = _SOBOL_KEY_RE.match(key)
    if not m:
        return key
    label = format_output_name(
        m.group("name").strip(), m.group("exp").strip(), m.group("sub").strip(),
        m.group("op"),
    )
    dup = m.group("dup")
    return f"{label}{dup}" if dup else label


def _bounds_point(mins: np.ndarray, maxs: np.ndarray, mode: str) -> np.ndarray:
    """A nominal point derived purely from the params_for_id bounds.

    ``geometric`` uses sqrt(min·max) where both bounds are positive (better for
    parameters spanning orders of magnitude, common in biology) and falls back
    to the arithmetic midpoint otherwise. ``midpoint`` is the plain arithmetic
    centre.
    """
    if mode == "geometric":
        both_pos = (mins > 0) & (maxs > 0)
        return np.where(both_pos, np.sqrt(np.abs(mins * maxs)), 0.5 * (mins + maxs))
    return 0.5 * (mins + maxs)


def _resolve_nominal(sm, param_names, mins, maxs, settings, best_vals, best_params):
    """Pick the parameter point to linearise about, and a label for the log.

    Priority:
      1. ``best_vals`` — a fresh calibration was run first (``run_calibration_first``).
      2. ``nominal == "best_fit"`` — reuse a completed calibration's best fit
         (``best_params`` dict, keyed by qname), supplied by the API.
      3. ``nominal == "current"`` (default) — the model's current parameter
         values (``get_init_param_vals``), so sensitivity is taken about wherever
         the model currently sits.
      4. ``nominal in {"midpoint", "geometric"}`` — derived from the bounds.
    """
    if best_vals is not None:
        return np.asarray(best_vals, dtype=float), "fresh calibration best fit"

    mode = str(settings.get("nominal", "current"))
    if mode == "best_fit":
        if not best_params:
            raise RuntimeError(
                "nominal='best_fit' but no calibration best fit was supplied; "
                "run a calibration first or enable 'run_calibration_first'."
            )
        return (
            np.array([float(best_params[name]) for name in param_names], dtype=float),
            "reused calibration best fit",
        )
    if mode == "current":
        vals = sm.sim_helper.get_init_param_vals(sm.SA_info["param_names"])
        flat = [v[0] if isinstance(v, (list, tuple)) else v for v in vals]
        return np.asarray(flat, dtype=float), "current parameter values"
    return _bounds_point(mins, maxs, mode), f"{mode} of bounds"


def _output_names(sm) -> list[str]:
    """One label per observable operation, matching the Sobol output naming."""
    obs = sm.obs_info
    return [
        format_output_name(
            obs["names_for_plotting"][j],
            obs["experiment_idxs"][j],
            obs["subexperiment_idxs"][j],
            obs["operations"][j],
        )
        for j in range(len(obs["operations"]))
    ]


def _relative_coeff(deriv: float, pj: float, denom: float, rng: float) -> float | None:
    """Dimensionless relative sensitivity from a raw derivative ``dY/dP``.

    Shared by the FD and AD paths: ``d ln(Y)/d ln(P) = (dY/dP)·P/Y`` about a
    non-zero nominal; when the nominal is 0 there's no log scale, so normalise by
    the parameter range instead. Returns ``None`` when undefined (Y≈0 / non-finite).
    """
    if not (np.isfinite(deriv) and np.isfinite(denom) and abs(denom) > _TINY):
        return None
    if pj != 0.0:
        return float(deriv * pj / denom)
    return float(deriv * (rng if rng > 0 else 1.0) / denom)


def _evaluate_features(sm, param_vals: np.ndarray) -> np.ndarray:
    """Run the protocol once and reduce each observable to a scalar feature.

    Mirrors ``sobol_SA.generate_outputs_mpi``'s per-sample inner loop, but for a
    single parameter vector and without the mean-imputation of missing values
    (imputed values would corrupt finite differences). Returns an array aligned
    with ``obs_info['operations']``; failed / missing features are ``nan``.
    """
    obs = sm.obs_info
    n = len(obs["operations"])
    features = np.full(n, np.nan)

    _success, operands_outputs_dict, _, _ = sm._protocol_executor.run_protocol(
        sm.protocol_info,
        id_param_names=sm.param_id_info["param_names"],
        id_param_vals=np.asarray(param_vals, dtype=float),
        result_variables=obs["operands"],
        continue_on_failure=True,
    )

    temp_results: dict = {}
    for j in range(n):
        exp_idx = obs["experiment_idxs"][j]
        subexp_idx = obs["subexperiment_idxs"][j]
        operands_outputs = operands_outputs_dict.get((exp_idx, subexp_idx), None)
        if operands_outputs is None:
            continue
        func = sm.operation_funcs_dict[obs["operations"][j]]
        raw_kwargs = obs["operation_kwargs"][j]
        kwargs = dict(raw_kwargs) if isinstance(raw_kwargs, dict) else {}
        for k, v in list(kwargs.items()):
            if isinstance(v, str) and v in temp_results:
                kwargs[k] = temp_results[v]
        feature = func(*operands_outputs[j], **kwargs)
        temp_results[obs["names_for_plotting"][j]] = feature
        try:
            features[j] = float(feature)
        except (TypeError, ValueError):
            features[j] = np.nan
    return features


def _fd_local_sensitivity(sm, param_names, nominal, mins, maxs, output_names, h):
    """Central finite-difference local sensitivities (works for any backend that
    runs a forward simulation, including ``cellml_only``)."""
    y0 = _evaluate_features(sm, nominal)

    local: dict[str, dict[str, float | None]] = {name: {} for name in output_names}
    for k, pname in enumerate(param_names):
        pj = nominal[k]
        rng = maxs[k] - mins[k]
        step = abs(pj) * h if pj != 0.0 else (h * rng if rng > 0 else h)

        p_plus = nominal.copy()
        p_plus[k] = pj + step
        p_minus = nominal.copy()
        p_minus[k] = pj - step
        yp = _evaluate_features(sm, p_plus)
        ym = _evaluate_features(sm, p_minus)
        print(f"  d/d[{pname}] evaluated", flush=True)

        for i, oname in enumerate(output_names):
            coeff = None
            if np.isfinite(yp[i]) and np.isfinite(ym[i]):
                deriv = (yp[i] - ym[i]) / (2.0 * step)
                coeff = _relative_coeff(deriv, pj, y0[i], rng)
            local[oname][pname] = coeff
    return local


def _non_differentiable(names, funcs_dict, is_differentiable):
    """Names (de-duplicated, skipping empty/'none') whose func isn't differentiable."""
    bad = []
    for name in dict.fromkeys(names or []):
        if not name or str(name).lower() == "none":
            continue
        fn = funcs_dict.get(name) if funcs_dict else None
        if fn is None or not is_differentiable(fn):
            bad.append(name)
    return bad


def assert_ad_operations(
    operations, op_funcs_dict, is_differentiable, cost_types=None, cost_funcs_dict=None
) -> None:
    """Raise an informative error if any obs operation / cost function in use
    isn't ``@differentiable``.

    AD (CasADi symbolic execution) only works when every operation applied to the
    observables — and every cost function, when checked — is marked
    ``@differentiable``. The error names the specific offenders, grouped by kind,
    so the user knows exactly what to change. ``is_differentiable`` is injected
    (CA's ``is_circulatory_differentiable``) so this stays unit-testable.
    """
    bad_ops = _non_differentiable(operations, op_funcs_dict, is_differentiable)
    bad_costs = _non_differentiable(cost_types, cost_funcs_dict, is_differentiable)
    if not (bad_ops or bad_costs):
        return
    details = []
    if bad_ops:
        details.append(f"operation(s) {bad_ops}")
    if bad_costs:
        details.append(f"cost function(s) {bad_costs}")
    raise ValueError(
        "Automatic differentiation requires every obs_data operation and cost "
        f"function to be marked @differentiable; these are not: {' and '.join(details)}. "
        "Switch the gradient method to 'FD' (finite difference), or make the "
        "offending function(s) differentiable in circulatory_autogen."
    )


def _ad_local_sensitivity(sm, param_names, nominal, mins, maxs, output_names):
    """Exact local sensitivities via CasADi automatic differentiation.

    Only valid for ``casadi_python`` models with all-``@differentiable`` ops.
    Mirrors ``paramID.build_casadi_functions``: put the helper in AD mode with the
    symbolic parameter subset, run the protocol symbolically so observables come
    back as CasADi ``SX`` expressions, reduce them with the casadi-mode operation
    funcs, then take the analytic jacobian and evaluate it at the nominal point.
    """
    import casadi as ca  # noqa: E402 (heavy; only imported on the AD path)
    import operation_funcs as _op  # noqa: E402 (CA module, resolved via sys.path)
    from param_id.differentiable import is_circulatory_differentiable  # noqa: E402

    obs = sm.obs_info
    n = len(obs["operations"])

    # The SA manager's operation_funcs are numpy-mode; AD needs the casadi ones.
    op_funcs = _op.get_operation_funcs_dict_for_mode("casadi")
    # Fail fast (and clearly) if an operation in use can't be differentiated.
    assert_ad_operations(obs["operations"], op_funcs, is_circulatory_differentiable)

    nominal = np.asarray(nominal, dtype=float)
    # Symbolic parameter subset + AD mode (sets sim_helper.variables_symb_subset).
    sm.sim_helper._create_param_subset(sm.param_id_info["param_names"], nominal)
    p_symb = sm.sim_helper.variables_symb_subset

    success, operands_outputs_dict, _, _ = sm._protocol_executor.run_protocol(
        sm.protocol_info,
        id_param_names=sm.param_id_info["param_names"],
        id_param_vals=nominal,
        result_variables=obs["operands"],
        continue_on_failure=False,
        reset_after_experiment=False,  # AD needs solver state preserved across exps
    )
    if not success:
        raise RuntimeError("symbolic (AD) protocol run failed")

    feature_exprs = []
    temp_results: dict = {}
    for j in range(n):
        exp_idx = obs["experiment_idxs"][j]
        subexp_idx = obs["subexperiment_idxs"][j]
        operands_outputs = operands_outputs_dict.get((exp_idx, subexp_idx))
        if operands_outputs is None:
            feature_exprs.append(ca.SX(float("nan")))
            continue
        func = op_funcs[obs["operations"][j]]
        raw_kwargs = obs["operation_kwargs"][j]
        kwargs = dict(raw_kwargs) if isinstance(raw_kwargs, dict) else {}
        for k, v in list(kwargs.items()):
            if isinstance(v, str) and v in temp_results:
                kwargs[k] = temp_results[v]
        feature = func(*operands_outputs[j], **kwargs)
        temp_results[obs["names_for_plotting"][j]] = feature
        feature_exprs.append(ca.SX(feature))

    features_vec = ca.vertcat(*feature_exprs)
    jac = ca.jacobian(features_vec, p_symb)
    # The feature expressions still reference the full state/variable symbol
    # vectors (initial states, protocol input params); mirror paramID's
    # build_casadi_functions and bind those as inputs, evaluating at the helper's
    # numeric operating point so only the param subset is differentiated.
    helper = sm.sim_helper
    evaluate = ca.Function(
        "local_ad", [helper.states_symb, helper.variables_symb], [features_vec, jac]
    )
    y0_dm, jac_dm = evaluate(helper.states, helper.variables)
    y0 = np.array(y0_dm).reshape(-1)
    J = np.array(jac_dm).reshape(n, len(param_names))

    local: dict[str, dict[str, float | None]] = {name: {} for name in output_names}
    for k, pname in enumerate(param_names):
        pj = nominal[k]
        rng = maxs[k] - mins[k]
        for i, oname in enumerate(output_names):
            local[oname][pname] = _relative_coeff(float(J[i, k]), pj, float(y0[i]), rng)
    return local


def compute_local_sensitivity(
    sa, settings: dict, best_vals=None, best_params=None, model_type: str = "cellml_only"
) -> dict:
    """Local sensitivities ``d ln(Y)/d ln(P)`` about a nominal parameter point.

    ``sa`` is a constructed ``SensitivityAnalysis`` (built by the runner exactly
    as for the Sobol path); we drive its ``SA_manager`` evaluation machinery.
    ``best_vals`` is a fresh-calibration best-fit vector (``run_calibration_first``);
    ``best_params`` is a reused best-fit dict keyed by qname. See
    :func:`_resolve_nominal` for how the nominal point is chosen.

    The gradient source is ``settings['gradient_method']``: ``FD`` (finite
    difference, any backend) or ``AD`` (exact CasADi jacobian, ``casadi_python``
    only). ``CVODES`` is still gated upstream (CA issue #239).
    """
    gradient_method = str(settings.get("gradient_method", "FD")).upper()
    if gradient_method not in ("FD", "AD"):
        raise NotImplementedError(
            f"gradient_method '{gradient_method}' is not available yet; use 'FD' "
            "(finite difference) or 'AD' (casadi_python). CVODES needs Myokit "
            "forward sensitivities (CA issue #239)."
        )
    if gradient_method == "AD" and model_type != "casadi_python":
        raise NotImplementedError(
            "AD gradients require generated_model_format 'casadi_python' (set it in "
            f"the Settings popup); current format is {model_type!r}."
        )

    sm = sa.SA_manager
    param_names = [
        name[0] if isinstance(name, list) else name
        for name in sm.SA_info["param_names"]
    ]
    mins = np.asarray(sm.param_id_info["param_mins"], dtype=float)
    maxs = np.asarray(sm.param_id_info["param_maxs"], dtype=float)
    nominal, nominal_source = _resolve_nominal(
        sm, param_names, mins, maxs, settings, best_vals, best_params
    )
    h = float(settings.get("rel_step", 0.01))
    output_names = _output_names(sm)

    source = "AD jacobian" if gradient_method == "AD" else f"finite difference, rel_step={h}"
    print(
        f"Local sensitivity ({source}, nominal={nominal_source}): "
        f"{len(param_names)} params x {len(output_names)} outputs",
        flush=True,
    )

    if gradient_method == "AD":
        local = _ad_local_sensitivity(sm, param_names, nominal, mins, maxs, output_names)
    else:
        local = _fd_local_sensitivity(sm, param_names, nominal, mins, maxs, output_names, h)

    return {
        "indices": {"local": local},
        "param_names": param_names,
        "output_names": output_names,
        "method": "local",
        "gradient_method": gradient_method,
        "nominal": nominal.tolist(),
        "nominal_source": nominal_source,
    }
