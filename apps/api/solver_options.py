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

# Calibration (param_id) methods offered when CA can't be introspected — i.e. an
# older circulatory_autogen without ``PARAM_ID_METHODS`` in its schema. Matches
# what CUFLynx historically hardcoded, so older CA behaves exactly as before.
_FALLBACK_PARAM_ID_METHODS = [
    {"value": "genetic_algorithm", "label": "Genetic algorithm", "gradient_based": False, "description": ""},
    {"value": "CMA-ES", "label": "CMA-ES", "gradient_based": False, "description": ""},
]

_cache: dict | None = None
_param_id_cache: list | None = None


def reset_cache() -> None:
    """Drop the cached options (call when the CA directory changes)."""
    global _cache, _param_id_cache
    _cache = None
    _param_id_cache = None


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
        })
    return methods


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

    solvers_by_format = {m: list(solvers_by_model_type.get(m, [])) for m in formats}
    default_solver_by_format = {
        m: defaults.get(m) or (solvers_by_format[m][0] if solvers_by_format[m] else "")
        for m in formats
    }
    # CA's schema defaults cellml_only to CVODE_opencor, but OpenCOR is a separate
    # application most users don't have installed (and it isn't in the desktop
    # bundle) — selecting it fails with "OpenCOR solver requested but OpenCOR is not
    # available". CVODE_myokit is bundled and needs no external program, so prefer
    # it as the cellml_only default when available.
    if "CVODE_myokit" in solvers_by_format.get("cellml_only", []):
        default_solver_by_format["cellml_only"] = "CVODE_myokit"
    all_diff = bool(differentiable) and all(differentiable.values())
    return {
        "model_formats": formats,
        "solvers_by_format": solvers_by_format,
        "default_solver_by_format": default_solver_by_format,
        "methods_by_solver": {s: list(m) for s, m in methods_by_solver.items()},
        "solver_info_schema": _solver_info_schema(methods_by_solver),
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


def ad_available(model_type: str, options: dict | None = None) -> bool:
    """True when AD gradients are valid: casadi_python + all ops differentiable."""
    opts = options if options is not None else get_solver_options()
    return model_type == "casadi_python" and bool(opts.get("all_differentiable"))
