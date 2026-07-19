"""Backend solver capabilities for the Settings popup + analysis-panel gating.

Surfaces the circulatory_autogen (CA) choices the UI needs to pick a
``generated_model_format`` (CA ``model_type``), its compatible ``solver``, the
``method`` for that solver, and the per-method ``solver_info`` fields — plus
whether automatic differentiation (AD) is available.

The model_type / solver / method lists are **not hardcoded here**: they're read
from CA's ``PrimitiveParsers.SOLVER_SCHEMA`` (the single source of truth used for
CA's own input validation). The per-method ``solver_info`` *fields* (dt, tols, …)
reflect which keys each backend's solver wrapper actually consumes, with the
``method`` options injected from the CA schema.

AD (CasADi) gradients are only valid when the format is ``casadi_python`` and
every CA operation_func is ``@differentiable`` (see ``param_id/differentiable.py``).

Like :mod:`obs_options`, this introspects CA, caches a successful introspection,
and falls back to a built-in copy of the schema when CA can't be imported.
"""

from __future__ import annotations

import sys
from pathlib import Path

from engine import _circulatory_autogen_src

# model_types CUFLynx can actually run (it code-generates python/casadi from the
# uploaded CellML; it has no 'cpp' build path), so cpp is filtered out even though
# CA's schema lists it.
SUPPORTED_FORMATS = ("cellml_only", "python", "casadi_python")

# Solvers CUFLynx must NOT surface because it does **not** bundle OpenCOR (see
# CLAUDE.md — no OpenCOR dependency is shipped). CA's schema lists CVODE_opencor as
# a cellml_only solver (and its default), but that backend needs an OpenCOR runtime
# CUFLynx doesn't have; CUFLynx runs CellML through Myokit's CVODE instead. Offering
# CVODE_opencor would present a solver that can't run here, so it's filtered out of
# every payload (both the CA-introspected schema and the fallback below).
UNSUPPORTED_SOLVERS = ("CVODE_opencor",)

# Used only when CA's SOLVER_SCHEMA can't be imported (mirrors it).
FALLBACK_SOLVER_SCHEMA = {
    "model_types": ["cellml_only", "python", "cpp", "casadi_python"],
    "solvers_by_model_type": {
        "cellml_only": ["CVODE_opencor", "CVODE_myokit"],
        "python": ["solve_ivp"],
        "cpp": ["CVODE", "RK4", "PETSC"],
        "casadi_python": ["casadi_integrator"],
    },
    "methods_by_solver": {
        "CVODE_opencor": ["CVODE"],
        "CVODE_myokit": ["CVODE"],
        "solve_ivp": ["RK45", "RK23", "DOP853", "Radau", "BDF", "LSODA", "forward_euler"],
        "casadi_integrator": ["cvodes", "idas", "collocation", "rk", "semi_implicit_euler", "bdf"],
    },
    "default_solver_by_model_type": {
        "cellml_only": "CVODE_opencor",
        "python": "solve_ivp",
        "cpp": "CVODE",
        "casadi_python": "casadi_integrator",
    },
}

_FALLBACK_DIFFERENTIABLE = {
    "max": True, "min": True, "mean": True, "max_minus_min": True,
    "addition": True, "subtraction": True, "multiplication": True, "division": True,
}

_NUM = "number"
_SEL = "select"

# Settings each fallback method exposes (name/type/default) — the fields CUFLynx
# historically showed. Used only when CA can't be introspected.
_FALLBACK_OPTS = [
    {"name": "num_calls_to_function", "type": "int", "default": 100, "required": True,
     "description": "Evaluation budget: maximum number of cost-function calls."},
    {"name": "cost_convergence", "type": "float", "default": 1e-3, "required": False,
     "description": "Stop once the cost drops below this value."},
    {"name": "max_patience", "type": "int", "default": 10, "required": False,
     "description": "Stop after this many iterations without improvement."},
]

# Calibration (param_id) methods offered when CA can't be introspected — i.e. an
# older circulatory_autogen without ``PARAM_ID_METHODS`` in its schema. Matches
# what CUFLynx historically hardcoded, so older CA behaves exactly as before.
_FALLBACK_PARAM_ID_METHODS = [
    {"value": "genetic_algorithm", "label": "Genetic algorithm", "gradient_based": False,
     "description": "", "options": [dict(o) for o in _FALLBACK_OPTS]},
    {"value": "CMA-ES", "label": "CMA-ES", "gradient_based": False,
     "description": "", "options": [dict(o) for o in _FALLBACK_OPTS]},
]

# Option blocks for the non-calibration analysis modes (sensitivity / MCMC /
# identifiability) offered when CA can't be introspected — mirrors
# PrimitiveParsers.ANALYSIS_OPTIONS so the SA/UQ panels still render their settings
# on an older CA. Same descriptor shape as a param_id method's options.
_FALLBACK_ANALYSIS_OPTIONS = {
    "sensitivity_analysis": {
        "label": "Sobol sensitivity analysis",
        "enable_flag": "do_sensitivity",
        "options_key": "sa_options",
        "options": [
            {"name": "method", "type": "enum", "default": "sobol", "required": False,
             "choices": ["sobol", "naive"],
             "description": "Sensitivity method: Sobol indices or a naive one-at-a-time sweep."},
            {"name": "sample_type", "type": "str", "default": "saltelli", "required": False,
             "description": "SALib sampling scheme (e.g. saltelli for Sobol)."},
            {"name": "num_samples", "type": "int", "default": 256, "required": True,
             "description": "Base sample count; total runs ~ num_samples*(2M+2) for Sobol."},
        ],
    },
    "mcmc": {
        "label": "MCMC posterior sampling",
        "enable_flag": "do_mcmc",
        "options_key": "mcmc_options",
        "options": [
            {"name": "num_steps", "type": "int", "default": 1000, "required": False,
             "description": "Number of MCMC steps per walker."},
            {"name": "num_walkers", "type": "int", "default": 64, "required": False,
             "description": "Number of ensemble walkers (defaults to 2 * number of parameters)."},
        ],
    },
    "identifiability_analysis": {
        "label": "Identifiability analysis",
        "enable_flag": "do_ia",
        "options_key": "ia_options",
        "options": [
            {"name": "method", "type": "enum", "default": "Laplace", "required": True,
             "choices": ["Laplace", "profile_likelihood"],
             "description": "Identifiability method: Laplace approximation or profile likelihood."},
            {"name": "sub_method", "type": "str", "default": "parabola_fit", "required": False,
             "description": "Hessian method for the Laplace approximation."},
        ],
    },
}

_cache: dict | None = None
_param_id_cache: list | None = None
_analysis_cache: dict | None = None


def reset_cache() -> None:
    """Drop the cached options (call when the CA directory changes)."""
    global _cache, _param_id_cache, _analysis_cache
    _cache = None
    _param_id_cache = None
    _analysis_cache = None


def _ca_paths() -> list[str]:
    """The sys.path entries CA's parser/operation modules need to import."""
    src = Path(_circulatory_autogen_src())
    root = src.parent  # repo root holds funcs_user/ alongside src/
    return [str(src), str(src / "param_id"), str(root / "funcs_user")]


def _ensure_ca_path() -> None:
    for p in _ca_paths():
        if p not in sys.path:
            sys.path.insert(0, p)


def _introspect_solver_schema() -> dict:
    _ensure_ca_path()
    from parsers.PrimitiveParsers import SOLVER_SCHEMA  # noqa: E402

    return SOLVER_SCHEMA


def _introspect_differentiable() -> dict[str, bool]:
    """Map each CA operation_func name -> whether it's marked @differentiable."""
    _ensure_ca_path()
    import operation_funcs  # noqa: E402
    from param_id.differentiable import is_circulatory_differentiable  # noqa: E402

    funcs = operation_funcs.get_operation_funcs_dict_for_mode("numpy")
    return {name: bool(is_circulatory_differentiable(fn)) for name, fn in funcs.items()}


def _introspect_param_id_methods() -> list[dict]:
    """The calibration methods CA supports, from its ``PARAM_ID_METHODS`` schema.

    Raises (AttributeError/ImportError) on an older CA that has no such schema, so
    the caller degrades to :data:`_FALLBACK_PARAM_ID_METHODS`. Only the canonical
    method names become menu entries; aliases (e.g. CMAES) are accepted by CA but
    not shown. Same "introspect CA, never hardcode" pattern as the solver schema.
    """
    _ensure_ca_path()
    from parsers.PrimitiveParsers import PARAM_ID_METHODS  # noqa: E402

    methods = []
    for canonical, meta in PARAM_ID_METHODS.items():
        meta = meta or {}
        methods.append({
            "value": canonical,
            "label": meta.get("label", canonical),
            "gradient_based": bool(meta.get("gradient_based", False)),
            "description": meta.get("description", ""),
            # Per-method settings (name/type/default/choices/...), so the UI shows
            # only the fields that method actually consumes — e.g. gradient-descent
            # methods don't list max_patience.
            "options": [dict(o) for o in (meta.get("options") or [])],
        })
    return methods


def _introspect_analysis_options() -> dict:
    """The option blocks for the non-calibration analysis modes (sensitivity /
    MCMC / identifiability), from CA's ``ANALYSIS_OPTIONS`` schema.

    Raises on an older CA that has no such schema, so the caller degrades to
    :data:`_FALLBACK_ANALYSIS_OPTIONS`. Same "introspect CA, never hardcode"
    pattern as the solver and param_id schemas — so new SA/MCMC/IA options in CA
    surface in the UI automatically.
    """
    _ensure_ca_path()
    from parsers.PrimitiveParsers import ANALYSIS_OPTIONS  # noqa: E402

    out = {}
    for mode, meta in ANALYSIS_OPTIONS.items():
        meta = meta or {}
        out[mode] = {
            "label": meta.get("label", mode),
            "enable_flag": meta.get("enable_flag"),
            "options_key": meta.get("options_key"),
            "options": [dict(o) for o in (meta.get("options") or [])],
        }
    return out


def _dt_field() -> dict:
    # The fixed step for fixed-step methods (e.g. semi_implicit_euler); the output
    # sampling interval otherwise. Applies to every method.
    return {"key": "dt", "label": "Time step (dt)", "type": _NUM, "default": 0.01}


def _method_field(options, label) -> dict:
    opts = list(options)
    return {
        "key": "method", "label": label, "type": _SEL,
        "default": opts[0] if opts else "", "options": opts,
    }


# Short, familiar labels for well-known solver_info keys (CA's schema carries a
# `description`, not a UI label); anything else is prettified from its name.
_SOLVER_INFO_LABELS = {
    "MaximumStep": "Max step",
    "MaximumNumberOfSteps": "Max # steps",
    "rtol": "Rel. tol",
    "atol": "Abs. tol",
    "reltol": "Rel. tol",
    "abstol": "Abs. tol",
    "max_step": "Max step",
    "max_step_size": "Max step size",
    "max_num_steps": "Max # steps",
}


def _pretty_label(name: str) -> str:
    s = str(name).replace("_", " ").strip()
    return (s[:1].upper() + s[1:]) if s else str(name)


def _si_field_from_descriptor(desc: dict) -> dict | None:
    """Map a CA solver_info descriptor (name/type/default/choices) to a CUFLynx
    form field, or None when the compact settings form can't render it — i.e. the
    ``str``/``dict`` fields (jac, gradient_method, casadi ``options``)."""
    name = desc.get("name")
    typ = desc.get("type")
    if typ in ("str", "dict"):
        return None
    label = _SOLVER_INFO_LABELS.get(name, _pretty_label(name))
    if typ == "enum":
        return {"key": name, "label": label, "type": _SEL,
                "default": desc.get("default"), "options": list(desc.get("choices") or [])}
    if typ == "bool":
        return {"key": name, "label": label, "type": "bool", "default": desc.get("default")}
    return {"key": name, "label": label, "type": _NUM, "default": desc.get("default")}


# CA's SOLVER_INFO_FIELDS lists the keys a solver *accepts*, but not which of its
# *methods* actually consume them — that lives in the wrapper's run() dispatch, so
# mirror it here. Without this the form offers settings the chosen method ignores,
# and some CasADi plugins reject outright ("Unknown option: abstol" on rk /
# collocation).
#
# casadi_python_solver_helper.run() dispatches:
#   semi_implicit_euler -> _run_semi_implicit_euler  (dt only)
#   bdf / BDF           -> _run_symbolic_bdf         (dt + max_step sub-step cap)
#   anything else       -> ca.integrator() with _build_integrator_opts(), which
#                          passes reltol/abstol (rtol/atol as fallback) only for
#                          the SUNDIALS plugins, and max_num_steps/max_step_size
#                          for any plugin method.
_CASADI_CUSTOM_LOOP_METHODS = ("semi_implicit_euler", "bdf", "BDF")
_CASADI_SUNDIALS_METHODS = ("cvodes", "idas")


def _casadi_method_gates(methods: list) -> dict[str, list]:
    """Map casadi_integrator solver_info key -> the methods that consume it.

    Derived from the offered method list, so a CA that adds or drops an integrator
    stays in step (a key whose methods aren't offered gates to [] and is hidden).
    """
    plugin = [m for m in methods if m not in _CASADI_CUSTOM_LOOP_METHODS]
    sundials = [m for m in methods if m in _CASADI_SUNDIALS_METHODS]
    bdf = [m for m in methods if m in ("bdf", "BDF")]
    return {
        "reltol": sundials, "abstol": sundials, "rtol": sundials, "atol": sundials,
        "max_num_steps": plugin, "max_step_size": plugin,
        "max_step": bdf,
    }


# Solvers whose fields need per-method gating. solve_ivp is absent deliberately:
# the python helper forwards rtol/atol/max_step for every scipy method.
_METHOD_GATES_BY_SOLVER = {"casadi_integrator": _casadi_method_gates}


def _solver_info_schema_from_ca(fields_by_solver: dict, methods_by_solver: dict) -> dict:
    """Per-solver solver_info form fields introspected from CA's ``SOLVER_INFO_FIELDS``
    (the single source of truth). CA omits the framework keys, so ``method`` (from
    the solver's method menu) and ``dt`` are injected; ``str``/``dict`` fields are
    skipped.

    CA's schema doesn't model per-method applicability, so :data:`_METHOD_GATES_BY_SOLVER`
    overlays it — introspection stays the source of truth for *which fields exist*,
    while the gating says which methods each one applies to.
    """
    out = {}
    for solver, descriptors in fields_by_solver.items():
        fields = []
        methods = list(methods_by_solver.get(solver, []))
        if methods:
            label = "Integrator" if solver == "casadi_integrator" else "Method"
            fields.append(_method_field(methods, label))
        fields.append(_dt_field())
        gates = _METHOD_GATES_BY_SOLVER.get(solver, lambda _m: {})(methods)
        for desc in descriptors or []:
            field = _si_field_from_descriptor(desc)
            if field is None:
                continue
            if field["key"] in gates:
                field["methods"] = gates[field["key"]]
            fields.append(field)
        out[solver] = fields
    return out


def _solver_info_schema(methods_by_solver: dict) -> dict:
    """Per-solver editable solver_info fields. `method` options come from CA;
    fields carry an optional `methods` restriction so the available settings track
    which solver_info keys each method actually consumes."""
    def cvode_fields():
        return [
            _dt_field(),
            {"key": "MaximumStep", "label": "Max step", "type": _NUM, "default": 0.001},
            {"key": "MaximumNumberOfSteps", "label": "Max # steps", "type": _NUM, "default": 5000},
            {"key": "rtol", "label": "Rel. tol", "type": _NUM, "default": None},
            {"key": "atol", "label": "Abs. tol", "type": _NUM, "default": None},
        ]

    ivp_methods = methods_by_solver.get("solve_ivp", FALLBACK_SOLVER_SCHEMA["methods_by_solver"]["solve_ivp"])
    casadi_methods = methods_by_solver.get(
        "casadi_integrator", FALLBACK_SOLVER_SCHEMA["methods_by_solver"]["casadi_integrator"]
    )
    # Adaptive CasADi plugins take tolerance/step-count options; the fixed-step
    # semi_implicit_euler doesn't (it uses only dt).
    casadi_adaptive = [m for m in casadi_methods if m != "semi_implicit_euler"]

    return {
        "CVODE_myokit": cvode_fields(),
        "CVODE_opencor": cvode_fields(),
        "solve_ivp": [
            _method_field(ivp_methods, "Method"),
            _dt_field(),
            # The python helper forwards these scipy solve_ivp kwargs for any method.
            {"key": "rtol", "label": "Rel. tol", "type": _NUM, "default": 1e-6},
            {"key": "atol", "label": "Abs. tol", "type": _NUM, "default": 1e-9},
            {"key": "max_step", "label": "Max step", "type": _NUM, "default": None},
        ],
        "casadi_integrator": [
            _method_field(casadi_methods, "Integrator"),
            _dt_field(),
            {"key": "reltol", "label": "Rel. tol", "type": _NUM, "default": 1e-8, "methods": casadi_adaptive},
            {"key": "abstol", "label": "Abs. tol", "type": _NUM, "default": 1e-10, "methods": casadi_adaptive},
            {"key": "max_num_steps", "label": "Max # steps", "type": _NUM, "default": None, "methods": casadi_adaptive},
            # The implicit 'bdf' integrator solves each step on an internal sub-step
            # capped at max_step (default 1e-3), then subsamples to dt. Smaller =>
            # more robust/accurate on stiff, discontinuous models (valve switches),
            # slower. Only 'bdf' consumes it.
            {"key": "max_step", "label": "Max internal step", "type": _NUM, "default": 1e-3, "methods": ["bdf"]},
        ],
    }


def _build_options(schema: dict, differentiable: dict[str, bool]) -> dict:
    formats = [m for m in schema.get("model_types", []) if m in SUPPORTED_FORMATS]
    solvers_by_model_type = schema.get("solvers_by_model_type", {})
    defaults = schema.get("default_solver_by_model_type", {})
    methods_by_solver = schema.get("methods_by_solver", {})

    def _supported(solvers):
        return [s for s in solvers if s not in UNSUPPORTED_SOLVERS]

    solvers_by_format = {m: _supported(solvers_by_model_type.get(m, [])) for m in formats}
    # If CA names an unsupported solver (e.g. CVODE_opencor) as a format's default,
    # fall back to the first solver CUFLynx can actually run for that format.
    default_solver_by_format = {}
    for m in formats:
        d = defaults.get(m)
        if not d or d in UNSUPPORTED_SOLVERS:
            d = solvers_by_format[m][0] if solvers_by_format[m] else ""
        default_solver_by_format[m] = d
    all_diff = bool(differentiable) and all(differentiable.values())
    # Prefer CA's SOLVER_INFO_FIELDS (single source of truth) when present; an older
    # CA (or the offline fallback schema) has no such key, so degrade to the curated
    # built-in form.
    fields_by_solver = schema.get("solver_info_fields_by_solver") or {}
    if fields_by_solver:
        solver_info_schema = _solver_info_schema_from_ca(fields_by_solver, methods_by_solver)
    else:
        solver_info_schema = _solver_info_schema(methods_by_solver)
    return {
        "model_formats": formats,
        "solvers_by_format": solvers_by_format,
        "default_solver_by_format": default_solver_by_format,
        "methods_by_solver": {
            s: list(m) for s, m in methods_by_solver.items() if s not in UNSUPPORTED_SOLVERS
        },
        # Filter the CA-introspected schema (not a rebuilt one) so the OpenCOR
        # exclusion composes with SOLVER_INFO_FIELDS introspection rather than
        # discarding it.
        "solver_info_schema": {
            s: fields for s, fields in solver_info_schema.items() if s not in UNSUPPORTED_SOLVERS
        },
        "differentiable_operations": dict(differentiable),
        "all_differentiable": all_diff,
    }


def _safe(fn, fallback):
    """Run an introspection, returning (value, ok); fall back on any failure."""
    try:
        return fn(), True
    except Exception:  # noqa: BLE001 - CA missing / import failure
        return fallback, False


def get_solver_options(refresh: bool = False) -> dict:
    """The solver/format/method capabilities payload, sourced from CA's schema.

    Caches a successful introspection; returns fallbacks (uncached) when CA is
    unavailable so a later CA-dir change can still succeed.
    """
    global _cache
    if _cache is not None and not refresh:
        return _cache
    schema, ok_schema = _safe(_introspect_solver_schema, FALLBACK_SOLVER_SCHEMA)
    diff, ok_diff = _safe(_introspect_differentiable, dict(_FALLBACK_DIFFERENTIABLE))
    opts = _build_options(schema, diff)
    if ok_schema and ok_diff:
        _cache = opts
    return opts


def get_param_id_methods(refresh: bool = False) -> list[dict]:
    """Calibration methods from CA's ``PARAM_ID_METHODS`` schema (introspected, not
    hardcoded), each ``{value, label, gradient_based, description}``.

    Degrades to :data:`_FALLBACK_PARAM_ID_METHODS` on an older CA that lacks the
    schema, so calibration keeps working. Caches a successful introspection;
    returns the fallback uncached so a later CA-dir change can still pick it up.
    """
    global _param_id_cache
    if _param_id_cache is not None and not refresh:
        return _param_id_cache
    methods, ok = _safe(_introspect_param_id_methods, [dict(m) for m in _FALLBACK_PARAM_ID_METHODS])
    if ok:
        _param_id_cache = methods
    return methods


def get_analysis_options(refresh: bool = False) -> dict:
    """Analysis-mode option blocks from CA's ``ANALYSIS_OPTIONS`` schema
    (introspected, not hardcoded), keyed by mode ('sensitivity_analysis', 'mcmc',
    'identifiability_analysis'). Each value carries ``label``/``enable_flag``/
    ``options_key`` and the per-mode ``options`` descriptors the SA/UQ panels render.

    Degrades to :data:`_FALLBACK_ANALYSIS_OPTIONS` on an older CA that lacks the
    schema. Caches a successful introspection; returns the fallback uncached so a
    later CA-dir change can still pick it up.
    """
    global _analysis_cache
    if _analysis_cache is not None and not refresh:
        return _analysis_cache
    opts, ok = _safe(
        _introspect_analysis_options,
        {k: dict(v, options=[dict(o) for o in v["options"]]) for k, v in _FALLBACK_ANALYSIS_OPTIONS.items()},
    )
    if ok:
        _analysis_cache = opts
    return opts


def analysis_mode_options(mode: str) -> list[dict]:
    """The option descriptors for a single analysis mode; [] for an unknown mode."""
    return get_analysis_options().get(mode, {}).get("options", [])


def gradient_sources(model_type: str, solver: str, all_differentiable: bool) -> list[dict]:
    """Gradient sources available for the current model, for the calibration UI's
    gradient-source menu (a gradient method's do_ad choice).

    circulatory_autogen has no static schema for this — it derives the source from
    ``do_ad`` + ``model_type`` + ``solver`` (see PrimitiveParsers' do_ad/FSA
    checks), so this mirrors those rules:
      - casadi_python (+ all ops differentiable): symbolic CasADi AD
      - cellml_only + CVODE_myokit: Myokit CVODES forward sensitivity (FSA)
      - otherwise: finite differences only
    Finite difference is always available. Kept in step with CA's logic; if CA ever
    exposes a gradient-source schema, introspect that instead (like the solvers).
    """
    sources = [{"value": "FD", "label": "Finite difference"}]
    if model_type == "casadi_python" and all_differentiable:
        sources.append({"value": "AD", "label": "Automatic differentiation (CasADi)"})
    elif model_type == "cellml_only" and solver == "CVODE_myokit":
        sources.append({"value": "FSA", "label": "Forward sensitivity (Myokit CVODES)"})
    return sources


def ad_available(model_type: str, options: dict | None = None) -> bool:
    """True when AD gradients are valid: casadi_python + all ops differentiable."""
    opts = options if options is not None else get_solver_options()
    return model_type == "casadi_python" and bool(opts.get("all_differentiable"))
