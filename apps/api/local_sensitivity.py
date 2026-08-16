"""Local (derivative-based) sensitivity analysis for CellML models.

Complements the global Sobol engine in :mod:`sensitivity_runner`: instead of
sampling the whole parameter box, this perturbs each parameter about a single
*nominal* point and measures how each observable responds — a local sensitivity
analysis (CUFLynx issue #22).

Three gradient sources are available, per the loaded model:

* **FD** (finite difference) — any backend; only needs the forward simulation the
  sobol engine already wires up. Implemented here directly.
* **AD** (CasADi automatic differentiation) — ``model_type: casadi_python`` with
  all-``@differentiable`` ops. Implemented here directly.
* **FSA** (Myokit CVODES forward sensitivities) — ``cellml`` + ``CVODE_myokit``.
  Delegated to circulatory_autogen's backend-agnostic
  ``OpencorParamID.get_observable_sensitivities`` (CA #283/#284), driven through a
  ``do_ad`` ``CVS0DParamID`` engine the runner builds; we only normalise + relabel
  its output to match the FD/AD payload.

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


def _resolve_nominal(pid, param_names, mins, maxs, settings, best_vals, best_params,
                     current_params=None):
    """Pick the parameter point to linearise about, and a label for the log.

    Priority:
      1. ``best_vals`` — a fresh calibration was run first (``run_calibration_first``).
      2. ``nominal == "best_fit"`` — reuse a completed calibration's best fit
         (``best_params`` dict, keyed by qname), supplied by the API.
      3. ``nominal == "current"`` (default) — the current parameter values. When the
         UI passes ``current_params`` (the live slider values), sensitivity is taken
         about exactly those; otherwise it falls back to the model's built-in init
         values (``get_init_param_vals``). Fixes local SA ignoring the sliders (#65).
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
        first_members = [
            n[0] if isinstance(n, (list, tuple)) else n
            for n in pid.param_id_info["param_names"]
        ]
        vals = pid.sim_helper.get_init_param_vals(first_members)
        nominal = np.asarray(
            [v[0] if isinstance(v, (list, tuple)) else v for v in vals], dtype=float
        )
        # A modifier slot's nominal is theta, but get_init_param_vals returns the
        # anchor's *physical* model default -- overwrite modifier slots with the
        # operation's identity (theta = 1 for scale), CA's own rule
        # (apply_modifier_identity_nominals, used by its param-id nominals). The
        # slider override below still wins: analysisDict puts theta at the anchor.
        try:
            from ca_imports import ca_from  # noqa: PLC0415

            apply_modifier_identity_nominals = ca_from(
                "parsers.PrimitiveParsers", "apply_modifier_identity_nominals")
            apply_modifier_identity_nominals(getattr(pid, "param_id_info", None) or {}, nominal)
        except ImportError:  # a CA predating modifiers has none to overwrite
            pass
        if current_params:
            applied = 0
            for i, name in enumerate(param_names):
                val = current_params.get(name)
                if val is not None:
                    nominal[i] = float(val)
                    applied += 1
            if applied:
                return nominal, "current parameter values (from sliders)"
        return nominal, "current parameter values (model defaults)"
    return _bounds_point(mins, maxs, mode), f"{mode} of bounds"


def relative_coeff(deriv: float, pj: float, denom: float, rng: float) -> float | None:
    """Dimensionless relative sensitivity from a raw derivative ``dY/dP``.

    Shared by the FD and AD paths: ``d ln(Y)/d ln(P) = (dY/dP)·P/Y`` about a
    non-zero nominal; when the nominal is 0 there's no log scale, so normalise by
    the parameter range instead. Returns ``None`` when undefined (Y≈0 / non-finite).

    Public, and shared with :mod:`cost_sensitivity` (#188), which normalises the
    cost gradient the same way. Two panels reporting "relative sensitivity" have
    to mean the same thing by it, or a user comparing them is comparing nothing.
    """
    if not (np.isfinite(deriv) and np.isfinite(denom) and abs(denom) > _TINY):
        return None
    if pj != 0.0:
        return float(deriv * pj / denom)
    return float(deriv * (rng if rng > 0 else 1.0) / denom)


def resolve_gradient_method(settings: dict, model_type: str) -> str:
    """The gradient source to use, in CUFLynx's vocabulary (FD / AD / FSA).

    Accepts circulatory_autogen's spellings too. Its API -- and its schema's
    default -- use ''/'ANALYTIC'/'AUTO' for "this backend's analytic arm",
    because a backend has only one. CUFLynx names the arm so the menu can offer,
    disable and report it; rejecting CA's word for the same choice is what made a
    defaulted run fail with "gradient_method 'AUTO' is not available".

    Public because it is the *one* rule for "what will this run actually do":
    the runner must call it to decide whether the FSA engine is needed, rather
    than testing the raw string -- resolving 'AUTO' only after that decision is
    how 'AUTO' came to demand an engine nobody had built.
    """
    method = str(settings.get("gradient_method", "FD") or "").upper()
    if method == "CVODES":
        method = "FSA"  # legacy alias -> the Myokit forward-sensitivity path
    if method in ("", "AUTO", "ANALYTIC"):
        # circulatory_autogen's own spelling of "this backend's analytic arm" --
        # and the default its schema hands back, so it arrives here whenever the
        # panel is seeded from CA rather than from a user's choice. CUFLynx names
        # the arm instead (so the menu can offer, disable and report it), but
        # rejecting CA's word for the same thing made a defaulted run fail with
        # "gradient_method 'AUTO' is not available" -- true of the name, and
        # quite wrong about the capability. Resolved to the arm CA would pick.
        return "AD" if model_type in LOCAL_GRADIENT_SUPPORT["AD"] else "FSA"
    if method not in ("FD", "AD", "FSA"):
        raise NotImplementedError(
            f"gradient_method '{method}' is not available; use 'FD' (finite "
            "difference), 'AD' (casadi_python), or 'FSA' (cellml + CVODE_myokit)."
        )
    return method


def _check_ad_operations() -> None:
    """Refuse an AD run whose obs operations are not all ``@differentiable``.

    circulatory_autogen already raises this, naming the offending operation, the
    moment its casadi-mode operation table is built -- so all that is missing is
    what to do about it. Enriching CA's message beats restating its check: the
    registry is CA's, and a copy here would be another thing to keep in step.
    """
    import operation_funcs as _op  # noqa: PLC0415 (CA module, resolved via sys.path)

    try:
        _op.get_operation_funcs_dict_for_mode("casadi")
    except ValueError as exc:
        raise ValueError(
            f"{exc} Switch the gradient method to 'FD' (finite difference), or mark "
            f"the operation @differentiable in circulatory_autogen."
        ) from exc


def _ca_feature_values(pid, nominal) -> dict:
    """Nominal feature value ``Y`` per observable label, for the log-log denominator.

    Uses the param-id engine's own const-observable evaluation so the keys line up
    exactly with ``get_observable_sensitivities``' labels (``pid._observable_label``).
    """
    _, operands_list, _ = pid.get_cost_obs_and_pred_from_params(
        np.asarray(nominal, dtype=float), reset=True, only_one_exp=0
    )
    if not operands_list or operands_list[0] is None:
        raise RuntimeError("Local sensitivity nominal simulation failed to converge.")
    const = np.asarray(pid.get_obs_output_dict(operands_list[0])["const"], dtype=float)
    c2o = pid.obs_info["const_idx_to_obs_idx"]
    return {pid._observable_label(o): float(const[k]) for k, o in enumerate(c2o)}


def _ca_local_sensitivity(
    pid, param_names, nominal, mins, maxs, gradient_method=None, rel_step=None
):
    """Local sensitivities via circulatory_autogen's backend-agnostic accessor
    ``OpencorParamID.get_observable_sensitivities``.

    **All three gradient sources come through here.** CA implements each of them
    -- FD (``fd_backend``), AD (``casadi_backend``, which flattens grouped and
    modifier rows to their member constants and folds the jacobian back per
    calibrated variable itself) and FSA (``fsa_backend``) -- behind one call with
    one return shape. CUFLynx used to reimplement the FD loop and the CasADi
    jacobian, which is why it had to mirror CA's flatten/fold contract and why
    tightening that contract (CA #390) broke the AD path.

    What is left here is only what CA does not answer: the *nominal point* (CA's
    own local SA hardcodes the model defaults), the *normalisation* (CA's
    relative index is unsigned, and the sign is half the answer), and the *label*
    spelling the heatmap shares with the Sobol path.

    CA returns the raw ``d(feature)/d(param)`` per const (scalar) observable, keyed
    by entry label; this normalises to the dimensionless ``d ln(Y)/d ln(P)`` via
    :func:`relative_coeff` and relabels with :func:`format_output_name`. Returns
    ``(local, output_names)``.
    """
    nominal = np.asarray(nominal, dtype=float)
    # `rel_step` is passed explicitly rather than left to CA's default: CUFLynx's
    # is 1e-2 and CA's fd_rel_step is 1e-3, and per CA's own measurement the two
    # differ by up to 48% on a rough functional -- a silent change of answer.
    kwargs = {}
    if gradient_method:
        kwargs["gradient_method"] = gradient_method
    if rel_step is not None:
        kwargs["fd_rel_step"] = float(rel_step)
    # The nominal features FIRST, while the helper is still numeric. CA's CasADi
    # arm leaves the simulation helper in AD mode (a symbolic parameter subset),
    # so a numeric evaluation afterwards comes back as an SX expression and the
    # reduction blows up in numpy. Order is the whole fix, and it costs nothing
    # for the other two arms.
    y0 = _ca_feature_values(pid, nominal)  # {obs_label: Y}
    try:
        sens = pid.get_observable_sensitivities(nominal, **kwargs)
    except TypeError:  # a CA whose accessor predates the two arguments
        sens = pid.get_observable_sensitivities(nominal)
    obs = pid.obs_info
    # CA keys its columns by *entry label*, not by the first member's qname: a
    # grouped entry is 'a/E+b/E' and a modifier is its own name, because the
    # sensitivity is d/dtheta over all of that entry's members. Looking them up
    # by qname misses every such entry and reports an empty cell -- which reads
    # as "no sensitivity" rather than "asked the wrong question".
    try:
        from ca_imports import ca_from  # noqa: PLC0415

        param_entry_labels = ca_from("parsers.PrimitiveParsers", "param_entry_labels")
        labels = list(param_entry_labels(pid.param_id_info))
    except Exception:  # noqa: BLE001 - a CA predating labels keys by qname
        labels = list(param_names)

    local: dict[str, dict[str, float | None]] = {}
    output_names: list[str] = []
    for _k, obs_idx in enumerate(obs["const_idx_to_obs_idx"]):
        label = pid._observable_label(obs_idx)
        oname = format_output_name(
            obs["names_for_plotting"][obs_idx],
            obs["experiment_idxs"][obs_idx],
            obs["subexperiment_idxs"][obs_idx],
            obs["operations"][obs_idx],
        )
        output_names.append(oname)
        denom = y0.get(label, float("nan"))
        deriv_map = sens.get(label, {})
        row: dict[str, float | None] = {}
        for j, pname in enumerate(param_names):
            rng = maxs[j] - mins[j]
            # Reported under CUFLynx's own key (the entry's first member, which
            # is the slider's key) but read out under CA's label for it.
            entry_label = labels[j] if j < len(labels) else pname
            deriv = deriv_map.get(entry_label)
            if deriv is None:
                deriv = deriv_map.get(pname, np.nan)
            row[pname] = relative_coeff(float(deriv), nominal[j], denom, rng)
        local[oname] = row
    return local, output_names


#: Which model formats each gradient method supports **in this module**.
#:
#: Not a statement about the backends' capabilities: circulatory_autogen offers
#: AD for aadc_python too (a tape, not a CasADi graph), and calibration uses it.
#: This path builds the jacobian from CasADi SX expressions, so only
#: casadi_python works *here*. Kept beside the implementation whose limitation it
#: describes, and surfaced to the UI so the option is not offered and then
#: refused.
LOCAL_GRADIENT_SUPPORT = {
    "FD": None,  # any backend: it just runs forward simulations
    "AD": ("casadi_python",),
    "FSA": ("cellml",),
}


def local_gradient_sources(sources, model_type: str) -> list:
    """``sources`` (CA's gradient_sources) narrowed to what local SA implements.

    Unsupported entries are marked rather than dropped, so the menu still shows
    that a gradient exists for this backend and says why it is unavailable
    *here* -- dropping it silently would read as "this backend has no AD".
    """
    out = []
    for src in sources or []:
        value = str(src.get("value", "")).upper()
        allowed = LOCAL_GRADIENT_SUPPORT.get(value)
        if allowed is None or model_type in allowed:
            out.append({**src, "disabled_here": False})
            continue
        out.append({
            **src,
            "disabled_here": True,
            "reason": (
                f"local sensitivity's {value} path needs generated_model_format "
                f"{' or '.join(allowed)}; the current format is {model_type}"
            ),
        })
    return out


def compute_local_sensitivity(
    sa, settings: dict, best_vals=None, best_params=None,
    model_type: str = "cellml", engine=None, current_params=None,
) -> dict:
    """Local sensitivities ``d ln(Y)/d ln(P)`` about a nominal parameter point.

    ``sa`` is a constructed ``SensitivityAnalysis`` (built by the runner exactly
    as for the Sobol path); we drive its ``SA_manager`` evaluation machinery.
    ``best_vals`` is a fresh-calibration best-fit vector (``run_calibration_first``);
    ``best_params`` is a reused best-fit dict keyed by qname. See
    :func:`_resolve_nominal` for how the nominal point is chosen.

    **Every gradient source is computed by circulatory_autogen**, through the one
    backend-agnostic accessor ``get_observable_sensitivities`` on the ``engine``
    (a ``do_ad`` ``CVS0DParamID`` the runner builds): ``FD`` central differences,
    ``AD`` the CasADi jacobian (``casadi_python`` only), ``FSA`` Myokit CVODES
    forward sensitivities (``cellml`` + ``CVODE_myokit``). ``CVODES`` is
    accepted as a legacy alias for ``FSA``.

    CUFLynx reimplemented the FD loop and the CasADi jacobian until #210's
    follow-up. That is why it had to mirror CA's flatten/fold contract for
    grouped and modifier rows, and why CA #390 tightening that contract broke the
    AD path. What is left here is the nominal point, the normalisation and the
    labels -- the three things CA does not answer.
    """
    gradient_method = resolve_gradient_method(settings, model_type)
    if gradient_method == "AD" and model_type not in LOCAL_GRADIENT_SUPPORT["AD"]:
        raise NotImplementedError(
            "Local sensitivity's AD path is CasADi-specific -- it builds the jacobian "
            "from CasADi SX expressions -- so it needs generated_model_format "
            f"'casadi_python'; current format is {model_type!r}. Other backends' AD "
            "(AADC's tape, for instance) is a different mechanism this path does not "
            "implement, though calibration can use it. Use 'FD' here, or switch the "
            "format to casadi_python in Settings."
        )
    if gradient_method == "FSA" and model_type not in LOCAL_GRADIENT_SUPPORT["FSA"]:
        raise NotImplementedError(
            "FSA (Myokit CVODES forward sensitivities) requires generated_model_format "
            f"'cellml' with solver 'CVODE_myokit'; current format is {model_type!r}."
        )

    if engine is None:
        raise RuntimeError(
            "Local sensitivity needs a param-id engine; the runner must build one "
            "(internal error)."
        )
    # Everything comes from the param-id engine, nothing from the Sobol sampling
    # manager -- which is what CA's own run_local_sensitivity does, and the reason
    # is #216. Both objects parse the same study and each owns a simulation
    # helper; reading the study from one and the sensitivities from the other
    # realises both, so a local SA compiled the model twice. CA made
    # sobol_SA.sim_helper lazy for exactly this, and touching it here defeated
    # that. `sa` is now unused on this path.
    pid = engine.param_id
    # Modifier baselines are resolved once here, against the sim helper's
    # pristine defaults -- the same idempotent call CA's param-id and Sobol
    # paths make at setup (a no-op without modifiers). The chain rule refuses to
    # run on unresolved baselines.
    try:
        from ca_imports import ca_from  # noqa: PLC0415

        resolve_modifier_baselines = ca_from(
            "parsers.PrimitiveParsers", "resolve_modifier_baselines")
        resolve_modifier_baselines(pid.param_id_info, pid.sim_helper)
    except ImportError:  # a CA predating modifiers has none to resolve
        pass
    param_names = [
        name[0] if isinstance(name, (list, tuple)) else name
        for name in pid.param_id_info["param_names"]
    ]
    mins = np.asarray(pid.param_id_info["param_mins"], dtype=float)
    maxs = np.asarray(pid.param_id_info["param_maxs"], dtype=float)
    nominal, nominal_source = _resolve_nominal(
        pid, param_names, mins, maxs, settings, best_vals, best_params, current_params
    )
    h = float(settings.get("rel_step", 0.01))
    if gradient_method == "AD":
        _check_ad_operations()
    # The output names are CA's. It answers for the scalar (const) observables
    # only, and a series row has no local sensitivity to report -- listing one
    # with an empty cell reads as "no sensitivity" rather than "not a question
    # this can answer".
    local, output_names = _ca_local_sensitivity(
        pid, param_names, nominal, mins, maxs,
        gradient_method=gradient_method, rel_step=h,
    )

    source = {
        "AD": "AD jacobian",
        "FSA": "Myokit CVODES forward sensitivities",
    }.get(gradient_method, f"finite difference, rel_step={h}")
    print(
        f"Local sensitivity ({source}, nominal={nominal_source}): "
        f"{len(param_names)} params x {len(output_names)} outputs",
        flush=True,
    )

    return {
        "indices": {"local": local},
        "param_names": param_names,
        "output_names": output_names,
        "method": "local",
        "gradient_method": gradient_method,
        "nominal": nominal.tolist(),
        "nominal_source": nominal_source,
    }
