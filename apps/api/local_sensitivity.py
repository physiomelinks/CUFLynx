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

import numpy as np

_TINY = 1e-12


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
    """One label per observable operation, matching the Sobol CSV naming."""
    obs = sm.obs_info
    return [
        f"{obs['names_for_plotting'][j]} "
        f"(Exp{obs['experiment_idxs'][j]}, Sub{obs['subexperiment_idxs'][j]})"
        for j in range(len(obs["operations"]))
    ]


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


def compute_local_sensitivity(sa, settings: dict, best_vals=None, best_params=None) -> dict:
    """Central-difference local sensitivities about a nominal parameter point.

    ``sa`` is a constructed ``SensitivityAnalysis`` (built by the runner exactly
    as for the Sobol path); we drive its ``SA_manager`` evaluation machinery.
    ``best_vals`` is a fresh-calibration best-fit vector (``run_calibration_first``);
    ``best_params`` is a reused best-fit dict keyed by qname. See
    :func:`_resolve_nominal` for how the nominal point is chosen.
    """
    gradient_method = str(settings.get("gradient_method", "FD")).upper()
    if gradient_method != "FD":
        # AD / CVODES are advertised in the UI but gated upstream; fail loudly
        # rather than silently producing FD numbers under the wrong label.
        raise NotImplementedError(
            f"gradient_method '{gradient_method}' is not available yet for "
            "cellml_only models; only 'FD' (finite difference) is supported. "
            "AD needs model_type casadi_python (CUFLynx #9); CVODES needs Myokit "
            "forward sensitivities (CA issue #239)."
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

    print(
        f"Local sensitivity (finite difference, central, rel_step={h}, "
        f"nominal={nominal_source}): "
        f"{len(param_names)} params x {len(output_names)} outputs",
        flush=True,
    )

    y0 = _evaluate_features(sm, nominal)

    local: dict[str, dict[str, float | None]] = {name: {} for name in output_names}
    for k, pname in enumerate(param_names):
        pj = nominal[k]
        if pj != 0.0:
            step = abs(pj) * h
        else:
            rng = maxs[k] - mins[k]
            step = h * rng if rng > 0 else h

        p_plus = nominal.copy()
        p_plus[k] = pj + step
        p_minus = nominal.copy()
        p_minus[k] = pj - step
        yp = _evaluate_features(sm, p_plus)
        ym = _evaluate_features(sm, p_minus)
        print(f"  d/d[{pname}] evaluated", flush=True)

        for i, oname in enumerate(output_names):
            coeff = None
            denom = y0[i]
            if (
                np.isfinite(yp[i])
                and np.isfinite(ym[i])
                and np.isfinite(denom)
                and abs(denom) > _TINY
            ):
                deriv = (yp[i] - ym[i]) / (2.0 * step)
                if pj != 0.0:
                    # central diff with multiplicative step => d ln y / d ln p.
                    coeff = float(deriv * pj / denom)
                else:
                    # nominal is 0: no log scale; normalise by the param range.
                    rng = maxs[k] - mins[k]
                    coeff = float(deriv * (rng if rng > 0 else 1.0) / denom)
            local[oname][pname] = coeff

    return {
        "indices": {"local": local},
        "param_names": param_names,
        "output_names": output_names,
        "method": "local",
        "gradient_method": "FD",
        "nominal": nominal.tolist(),
        "nominal_source": nominal_source,
    }
