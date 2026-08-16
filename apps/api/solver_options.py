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

The available gradient sources (FD / AD / FSA) are likewise **introspected** from
CA's discoverable ``gradient_sources`` accessor (matching its ``get_gradient``
dispatch), with a hand-coded mirror as the fallback for an older CA. CasADi AD is
gated on every CA operation_func being ``@differentiable`` (see
``param_id/differentiable.py``); that gate is applied here at runtime because CA
can't know it statically.

Like :mod:`obs_options`, this introspects CA, caches a successful introspection,
and falls back to a built-in copy of the schema when CA can't be imported.
"""

from __future__ import annotations

import copy
import json
import os
import subprocess
import sys
from pathlib import Path

from engine import _circulatory_autogen_src
from runtime_paths import default_python

# model_types CUFLynx can actually run (it code-generates python/casadi from the
# uploaded CellML; it has no 'cpp' build path), so cpp is filtered out even though
# CA's schema lists it.
#
# external_python is here because CUFLynx has no build path to provide for it
# either: the model file *is* the user's own module, so "can CUFLynx produce what
# this format needs" is trivially yes -- it uploads it and hands CA the path.
SUPPORTED_FORMATS = ("cellml", "python", "casadi_python", "external_python")

#: model_type spellings circulatory_autogen has renamed, old -> current.
#:
#: CA renamed ``cellml_only`` to ``cellml``. The CA directory is chosen at runtime
#: and can be any checkout (Settings -> CA dir), so CUFLynx meets both spellings
#: and has to translate in *both* directions:
#:
#: * inbound -- an older CA's SOLVER_SCHEMA advertises ``cellml_only``, and a
#:   dropdown built straight from it would put the retired name in front of the
#:   user and write it into new configs;
#: * outbound -- an older CA's ``parse_user_inputs_file`` exits on a model_type it
#:   does not recognise, so sending it ``cellml`` makes every calibration, SA and
#:   UQ run die at startup.
#:
#: So: one canonical spelling everywhere inside CUFLynx, translated back to
#: whatever the connected CA advertises at the moment a run config is written.
#: This is also what stops the app breaking in the window between updating the
#: two repos, which is a window every developer with a sibling checkout lands in.
MODEL_TYPE_ALIASES = {"cellml_only": "cellml"}

#: Current name -> the spelling the *connected* CA advertises. Identity unless an
#: older CA said otherwise; rebuilt on every schema introspection.
_ca_model_type_spelling: dict[str, str] = {}


def canonical_model_type(model_type: str | None) -> str | None:
    """CUFLynx's spelling of a CA model_type. Unknown names pass through."""
    if not model_type:
        return model_type
    return MODEL_TYPE_ALIASES.get(model_type, model_type)


def ca_model_type(model_type: str | None) -> str | None:
    """The spelling to write into a config the **connected** CA will parse.

    Identity for a current CA. Call this at the boundary -- a run config, an
    exported pipeline -- never inside CUFLynx, where the canonical name is the
    only one that should appear.
    """
    if not model_type:
        return model_type
    return _ca_model_type_spelling.get(model_type, model_type)


def _canonicalise_model_types(schema: dict) -> dict:
    """Rewrite a CA schema's model_type keys into CUFLynx's spelling, and record
    what that CA actually called them so :func:`ca_model_type` can translate back.
    """
    global _ca_model_type_spelling
    spelling: dict[str, str] = {}
    for old, current in MODEL_TYPE_ALIASES.items():
        if old in schema.get("model_types", []):
            spelling[current] = old
    _ca_model_type_spelling = spelling
    if not spelling:
        return schema

    out = dict(schema)
    out["model_types"] = [canonical_model_type(m) for m in schema.get("model_types", [])]
    for key in ("solvers_by_model_type", "default_solver_by_model_type"):
        if isinstance(schema.get(key), dict):
            out[key] = {canonical_model_type(m): v for m, v in schema[key].items()}
    return out

# Formats CUFLynx can run, but only when an optional third-party library is
# present (#122). aadc_python needs Matlogica's AADC, which is proprietary and
# licensed; CA imports it lazily, so without this gate the format would appear on
# the menu and fail at run time -- the same mistake UNSUPPORTED_SOLVERS avoids for
# OpenCOR. The format's solvers and methods still come from CA's schema; only
# whether it is offered at all is decided here.
#
# AADC loads a generated *python* model (aadc_python_solver_helper does
# spec_from_file_location on model_path), which resolve_model_path already
# produces for any non-cellml format -- so nothing else is needed to run it.
CONDITIONAL_FORMATS = ("aadc_python",)


def _available_formats() -> tuple:
    """SUPPORTED_FORMATS plus any conditional format whose library is present."""
    try:
        from aadc_check import aadc_status  # noqa: PLC0415

        extra = ("aadc_python",) if aadc_status().get("available") else ()
    except Exception:  # noqa: BLE001 - a probe failure must not lose the base list
        extra = ()
    return SUPPORTED_FORMATS + extra

# Solvers CUFLynx must NOT surface because it does **not** bundle OpenCOR (see
# CLAUDE.md — no OpenCOR dependency is shipped). CA's schema lists CVODE_opencor as
# a cellml solver (and its default), but that backend needs an OpenCOR runtime
# CUFLynx doesn't have; CUFLynx runs CellML through Myokit's CVODE instead. Offering
# CVODE_opencor would present a solver that can't run here, so it's filtered out of
# every payload (both the CA-introspected schema and the fallback below).
UNSUPPORTED_SOLVERS = ("CVODE_opencor",)

# solver_info keys an older CA advertises for a solver whose backend cannot
# honour them.
#
# CA used to give CVODE_myokit the shared CVODE-family field list, which carries
# MaximumNumberOfSteps. That is right for CVODE_opencor / CVODE / RK4 / PETSC,
# but Myokit's Simulation exposes only set_max_step_size, set_min_step_size and
# set_tolerance — there is no max-step-count knob, and myokit_helper never reads
# the key. Offering it renders a control that silently does nothing.
#
# Fixed upstream in CA #329, so against a current CA this is already a no-op.
# It is kept because the CA directory is user-selectable (Settings → CA dir):
# someone pointed at a pre-#329 checkout would otherwise get the dead control
# back. Retire it once CUFLynx requires a CA new enough to be certain.
#
# Shape follows UNSUPPORTED_SOLVERS above: it composes with the introspection
# rather than replacing it, so it can only ever remove a key CA offers — never
# add or rename one.
UNSUPPORTED_SOLVER_INFO_KEYS: dict[str, frozenset[str]] = {
    "CVODE_myokit": frozenset({"MaximumNumberOfSteps"}),
}

# Methods CA advertises for a solver that cannot perform a *forward solve* (#175).
#
# CA's methods_by_solver mixes two kinds of thing under one key. For AADC, most
# entries name an integrator in aadc_python_solver_helper.run(); 'bdf_tape' and
# 'bdf_kernel' name neither -- they exist only in param_id/aadc_backend.py as
# _cost_and_grad_bdf_tape / _cost_and_grad_bdf_kernel, which are *gradient
# strategies* for calibration and do their own stepping. Selecting one and moving
# a slider therefore always fails with "Unknown AADC solver_info method", because
# the forward dispatch has no branch for it.
#
# Same shape and same rule as UNSUPPORTED_SOLVERS: it composes with the
# introspection and can only ever remove a method CA offers, never add or rename
# one. Note this also withdraws them as calibration gradient strategies, since one
# solver_info['method'] serves both tiers -- the alternative is offering a setting
# that breaks every live plot. Retire it once CA separates forward integrators
# from gradient strategies (circulatory_autogen#346), which is the real fix.
UNSUPPORTED_METHODS: dict[str, frozenset[str]] = {
    "aadc_semi_implicit": frozenset({"bdf_tape", "bdf_kernel"}),
}

# solver_info keys that are framework metadata rather than integrator settings.
# CA keeps these out of SOLVER_INFO_FIELDS and allows them separately.
FRAMEWORK_SOLVER_INFO_KEYS = frozenset({"solver", "method", "dt_solver", "dt"})


def _ca_excluded(key: str, solver: str) -> frozenset[str] | None:
    """Methods CA advertises for ``solver`` but leaves out of ``key``; None if it cannot say.

    Expressed as an exclusion, not a whitelist, so it keeps the property every filter here has:
    it can only ever remove a method CA itself offers, never one it does not know about. A
    whitelist would also strip anything absent from CA's list for unrelated reasons, reaching
    beyond what CA is actually asserting.

    Reading CA rather than hardcoding is the point of the schema -- the lists move as CA fixes
    methods (circulatory_autogen#348 will widen the stiff set), and a copy here would go stale
    silently. None means "this CA is too old to say", and the caller falls back.
    """
    try:
        schema = _introspect_solver_schema()
    except Exception:  # noqa: BLE001 - a CA that cannot be imported must not lose the menu
        return None
    allowed = schema.get(key)
    offered = schema.get("methods_by_solver")
    if not isinstance(allowed, dict) or solver not in allowed:
        return None
    if not isinstance(offered, dict) or solver not in offered:
        return None
    return frozenset(offered[solver]) - frozenset(allowed[solver])


def supported_methods(solver: str, methods, stiff: bool = False) -> list:
    """``methods`` less any that cannot forward-solve, and -- when ``stiff`` -- any that cannot
    be trusted on a stiff model.

    Two independent filters, both preferring CA's schema over the local fallbacks:

    ``forward_methods_by_solver`` (CA #347) lists what a plain solve can actually run.
    ``methods_by_solver`` mixes forward integrators with calibration gradient strategies, so
    without this the menu offers methods that only ever raise (#175). On an older CA the
    UNSUPPORTED_METHODS fallback covers the two known cases.

    ``stiff_suitable_methods`` (CA #347) lists what is trustworthy on a stiff model, which the
    cardiovascular models are (#177). This one matters more than it looks: the excluded methods
    are not merely slow. ``implicit_euler_ift`` completes and returns a smooth trace that is 84%
    low, and is advertised as AD-suitable, so a calibration would pick it and report clean
    convergence. Filtering is the difference between a wrong answer and no answer.
    """
    # The local list stays an always-applied floor. CA #347 drops bdf_tape/bdf_kernel from
    # methods_by_solver entirely, so a CA-derived exclusion can no longer name them -- but they
    # still arrive from configs saved before the rename, and they still cannot forward-solve.
    excluded = UNSUPPORTED_METHODS.get(solver, frozenset())
    from_ca = _ca_excluded("forward_methods_by_solver", solver)
    if from_ca:
        excluded = excluded | from_ca
    if stiff:
        not_stiff = _ca_excluded("stiff_suitable_methods", solver)
        if not_stiff:
            excluded = excluded | not_stiff
    return [m for m in methods if m not in excluded]


def unsupported_solver_info_keys(solver: str) -> frozenset[str]:
    """Keys to drop for ``solver`` even though CA advertises them."""
    return UNSUPPORTED_SOLVER_INFO_KEYS.get(solver, frozenset())


def accepted_solver_info_keys(solver: str) -> frozenset[str] | None:
    """The solver_info keys ``solver`` actually honours, or None if unknown.

    Introspected from the same schema that drives the Settings form, so the
    validation and the offered controls cannot disagree. None (rather than an
    empty set) for a solver absent from the schema: unknown must not be treated
    as "accepts nothing", which would reject every setting.
    """
    schema = get_solver_options().get("solver_info_schema") or {}
    fields = schema.get(solver)
    if not fields:
        return None
    keys = {f["key"] for f in fields if f.get("key")}
    return frozenset(keys | FRAMEWORK_SOLVER_INFO_KEYS) - unsupported_solver_info_keys(solver)


def default_solver_info(solver: str) -> dict:
    """The starting ``solver_info`` for ``solver``: every setting CA declares a default for.

    CA states those defaults in ``SOLVER_INFO_FIELDS`` (mirroring its own
    ``get_solver_info_default``) precisely so a tool can populate a settings form
    without restating them, so they are read from there. The restatement is what
    broke: CUFLynx seeded a literal ``{"MaximumStep": 0.001}``, which left the
    Settings popup's Rel./Abs. tol boxes blank while CA had declared 1e-8 for both
    (#200) — a field with a default that nothing puts into the value.

    Read from the already-filtered per-solver form schema rather than CA's
    ``get_solver_info_default(model_type)``: that one is keyed by model_type and
    answers ``cellml`` with the CVODE_opencor family — the solver CUFLynx must
    never use, plus MaximumNumberOfSteps, which myokit_helper never reads. Going
    through the same schema the form renders means the seeded values and the offered
    controls cannot disagree.

    ``dt`` is excluded because the engine owns it separately (``engine.dt``, merged
    back into the /api/config payload); a duplicate in ``solver_info`` would be a
    second place for it to be wrong. A field CA gives no default for stays unset —
    unset means the backend's own default, and inventing one here is the original
    mistake again.
    """
    fields = (get_solver_options().get("solver_info_schema") or {}).get(solver) or []
    return {
        f["key"]: f["default"]
        for f in fields
        if f.get("key") and f["key"] != "dt" and f.get("default") is not None
    }


def check_solver_info(solver: str, solver_info: dict) -> None:
    """Raise ValueError if ``solver_info`` carries a key ``solver`` cannot honour.

    A setting that is quietly ignored is worse than a rejected one: the user
    changes it, nothing happens, and nothing says why. Unknown solvers pass
    through — better to run than to block on a schema we couldn't read.
    """
    allowed = accepted_solver_info_keys(solver)
    if allowed is None:
        return
    unsupported = sorted(k for k in solver_info if k not in allowed)
    if not unsupported:
        return
    inert = sorted(set(unsupported) & unsupported_solver_info_keys(solver))
    hint = ""
    if inert:
        hint = (
            f" {', '.join(inert)} is accepted by other CVODE backends but not by this one"
            " — Myokit's integrator has no such setting."
        )
    raise ValueError(
        f"solver_info contains key(s) that solver {solver!r} does not support: "
        f"{unsupported}. Supported keys: {sorted(allowed)}.{hint}"
    )


def filter_solver_info(solver: str, solver_info: dict) -> dict:
    """``solver_info`` with anything ``solver`` cannot honour removed.

    Used on values arriving from persisted settings or a runner config, where
    rejecting outright would strand a user whose saved config predates the
    solver switch.
    """
    allowed = accepted_solver_info_keys(solver)
    if allowed is None:
        return dict(solver_info)
    return {k: v for k, v in solver_info.items() if k in allowed}


# Used only when CA's SOLVER_SCHEMA can't be imported (mirrors it).
#
# external_python is mirrored here as well as read from CA, because the packaged
# app's *cold start* has no CA directory chosen yet -- and the format a user
# reaches for first, having been handed a .py, must be on the menu before they
# have configured anything. Its one degree of freedom is `user_config`, a
# free-form dict the wrapper hands the user's class untouched.
FALLBACK_SOLVER_SCHEMA = {
    "model_types": ["cellml", "python", "cpp", "casadi_python", "external_python"],
    "solvers_by_model_type": {
        "cellml": ["CVODE_opencor", "CVODE_myokit"],
        "python": ["solve_ivp"],
        "cpp": ["CVODE", "RK4", "PETSC"],
        "casadi_python": ["casadi_integrator"],
        "external_python": ["external"],
    },
    "methods_by_solver": {
        "CVODE_opencor": ["CVODE"],
        "CVODE_myokit": ["CVODE"],
        "solve_ivp": ["RK45", "RK23", "DOP853", "Radau", "BDF", "LSODA", "forward_euler"],
        "casadi_integrator": ["cvodes", "idas", "collocation", "rk", "semi_implicit_euler", "bdf"],
        # The user's class chooses its own integration; "external" is the one
        # method there is, named so the form has something honest to show.
        "external": ["external"],
    },
    "default_solver_by_model_type": {
        "cellml": "CVODE_opencor",
        "python": "solve_ivp",
        "cpp": "CVODE",
        "casadi_python": "casadi_integrator",
        "external_python": "external",
    },
}

_FALLBACK_DIFFERENTIABLE = {
    "max": True, "min": True, "mean": True, "max_minus_min": True,
    "addition": True, "subtraction": True, "multiplication": True, "division": True,
}

# Per-integrator suitability for the backend's *analytic* gradient. CA's
# SOLVER_SCHEMA now exposes these (CA #298 landed), so these are the older-CA
# fallback only. CasADi AD works with the symbolic integrators but NOT the SUNDIALS
# adjoint ones (cvodes/idas), whose adjoint sensitivity fails (CV_TOO_MUCH_WORK);
# Myokit FSA works with the CVODE integrator.
_FALLBACK_AD_SUITABLE = {
    "casadi_integrator": ["collocation", "rk", "semi_implicit_euler", "bdf"],
}
_FALLBACK_FSA_SUITABLE = {
    "CVODE_myokit": ["CVODE"],
    "CVODE_opencor": ["CVODE"],
}
# Preferred default integrator per solver (AD-suitable + stable for stiff models),
# overriding "first in the method list". bdf for casadi_python (vs cvodes).
_FALLBACK_DEFAULT_METHOD = {
    "casadi_integrator": "bdf",
}

_NUM = "number"
_SEL = "select"
# A free-form object, edited as JSON text. Only external_python's `user_config`
# uses it: that field is the *whole* of an external solver's configuration, so
# skipping it the way the other dict-typed fields are skipped would leave the
# format unconfigurable -- and check_solver_info would then reject the very key
# CA expects, since what a solver accepts is derived from these descriptors.
_JSON = "json"

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

# The params_for_id `prior` vocabulary offered when CA can't be introspected —
# mirrors PrimitiveParsers.PARAM_PRIOR_TYPES so the params editor still renders a
# picker on an older CA. `default` is what a blank/absent prior means.
_FALLBACK_PARAM_PRIOR_TYPES = {
    "default": "uniform",
    "types": [
        {"value": "uniform", "label": "Uniform", "description": "",
         "supports_unbounded": False, "support": None, "params": []},
        {"value": "exponential", "label": "Exponential", "description": "",
         "supports_unbounded": True, "support": "one_sided", "params": [
            {"name": "prior_lambda", "type": "float", "default": 1.0, "role": "rate",
             "positive": True, "description": "", "default_expr": None},
            {"name": "prior_origin", "type": "float", "default": 0.0, "role": "location",
             "positive": False, "description": "", "default_expr": "0"},
            {"name": "prior_scale", "type": "float", "default": None, "role": "scale",
             "positive": True, "description": "", "default_expr": "max / prior_lambda"},
        ]},
        {"value": "normal", "label": "Normal", "description": "",
         "supports_unbounded": True, "support": "symmetric", "params": [
            {"name": "prior_mean", "type": "float", "default": None, "role": "location",
             "positive": False, "description": "", "default_expr": "(min + max) / 2"},
            {"name": "prior_std", "type": "float", "default": None, "role": "scale",
             "positive": True, "description": "", "default_expr": "(max - min) / 6"},
        ]},
    ],
}

# The modifier-operation vocabulary offered when CA can't be introspected —
# mirrors PrimitiveParsers.PARAM_MODIFIER_OPERATIONS so the params editor still
# offers the one operation CA has always had. `identity` is the θ at which every
# target sits at its baseline (what a fresh modifier slider is set to).
_FALLBACK_PARAM_MODIFIER_OPERATIONS = {
    "default": "scale",
    "operations": [
        {"value": "scale", "label": "Scale",
         "description": "one calibrated multiplier applied to every target's default value",
         "applies_to": "value", "dimensionless": True,
         "default_min": 0.5, "default_max": 2.0, "identity": 1.0,
         "inputs": {}, "user_defined": False},
    ],
}

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
    "uq": {
        "label": "Uncertainty quantification",
        "enable_flag": "do_uq",
        "options_key": "UQ_options",
        "options": [
            {"name": "method", "type": "enum", "default": "mcmc", "required": False,
             "choices": ["mcmc"],
             "description": "Uncertainty-quantification method."},
            {"name": "library", "type": "enum", "default": "emcee", "required": False,
             "choices": ["emcee"],
             "description": "Sampler backend for method=mcmc."},
            {"name": "num_steps", "type": "int", "default": 1000, "required": False,
             "description": "Number of MCMC steps per walker."},
            {"name": "num_walkers", "type": "int", "default": 64, "required": False,
             "description": "Number of ensemble walkers (defaults to 2 * number of parameters)."},
            {"name": "burn_in", "type": "float", "default": 0.5, "required": False,
             "description": "Samples discarded before the chain is used."},
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
_prior_cache: dict | None = None
# {output_dir: vocabulary}. The modifier vocabulary depends on the output dir (a
# user's own modifier file lives under it), and both keys are asked for in normal
# use, so this is a map rather than a single slot — same rule as obs_options'
# cache, but without the thrash a one-entry cache would have here.
_modifier_cache: dict | None = None


def reset_cache() -> None:
    """Drop the cached options (call when the CA directory changes)."""
    global _cache, _param_id_cache, _analysis_cache, _prior_cache, _modifier_cache
    _cache = None
    _param_id_cache = None
    _analysis_cache = None
    _prior_cache = None
    _modifier_cache = None


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


def _introspect_param_prior_types() -> dict:
    """The params_for_id ``prior`` vocabulary, from CA's ``PARAM_PRIOR_TYPES``.

    Raises on a CA predating the schema, so the caller degrades to
    :data:`_FALLBACK_PARAM_PRIOR_TYPES`. Same "introspect CA, never hardcode"
    pattern as the solver and param_id schemas: CA decides what a prior may be,
    and a prior it grows shows up in the params editor without a change here.
    """
    _ensure_ca_path()
    from parsers.PrimitiveParsers import (  # noqa: E402
        DEFAULT_PARAM_PRIOR_TYPE,
        PARAM_PRIOR_TYPES,
    )

    try:
        from parsers.PrimitiveParsers import prior_supports_unbounded as supports_unbounded  # noqa: E402
    except ImportError:  # a CA predating unbounded parameters
        def supports_unbounded(_name):
            return False

    return {
        "default": DEFAULT_PARAM_PRIOR_TYPE,
        "types": [
            {
                "value": name,
                "label": (meta or {}).get("label", name),
                # What the distribution actually is (where the normal is centred,
                # what the exponential's rate is) -- worth surfacing, because it
                # was previously only discoverable by reading CA's likelihood.
                "description": (meta or {}).get("description", ""),
                # The values this prior takes, each a params_for_id column. The
                # editor renders exactly these, so a prior CA grows a knob for
                # gains the field here without a change in this repo.
                # Whether this prior can stand in for a parameter's range (it
                # declares both a centre and a width). CA decides; the editor only
                # offers the tickbox where CA would accept it.
                "supports_unbounded": bool(supports_unbounded(name)),
                # Symmetric priors straddle their centre; one-sided ones decay away
                # from an origin. The editor does not use it yet, but it travels with
                # the rest so a consumer need not infer it from the prior's name.
                "support": (meta or {}).get("support"),
                "params": [
                    {
                        "name": spec.get("name"),
                        "role": spec.get("role"),
                        # What a blank field resolves to, as an expression over the
                        # row's min/max and sibling params. CA states it once and
                        # computes from it; the editor evaluates the same string to
                        # show the number, rather than restating the formula.
                        "default_expr": spec.get("default_expr"),
                        "type": spec.get("type", "float"),
                        "default": spec.get("default"),
                        "positive": bool(spec.get("positive", False)),
                        "description": spec.get("description", ""),
                    }
                    for spec in ((meta or {}).get("params") or [])
                ],
            }
            for name, meta in PARAM_PRIOR_TYPES.items()
        ],
    }


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
            # Emulation is the one mode with two flags: `enable_flag`
            # (do_emulation) trains, `use_flag` (use_emulator) makes the other
            # analyses evaluate what was trained. Absent on every other mode
            # (CA #333), so a UI reads it as "this mode has a use step".
            "use_flag": meta.get("use_flag"),
            "options_key": meta.get("options_key"),
            "options": [dict(o) for o in (meta.get("options") or [])],
        }
    return out


_MODEL_PROBE = (
    "import sys, json;"
    "sys.path.insert(0, {src!r});"
    "from emulators.emulator_trainer import emulator_model_names;"
    "print(json.dumps([str(n) for n in emulator_model_names()]))"
)


def _models_from_interpreter(python: str, src: str) -> list[str]:
    """Ask another interpreter what emulators it has. [] if it cannot answer."""
    try:
        out = subprocess.run(
            [python, "-c", _MODEL_PROBE.format(src=src)],
            capture_output=True,
            text=True,
            timeout=60,
        )
        if out.returncode != 0:
            return []
        return [str(name) for name in json.loads(out.stdout.strip().splitlines()[-1])]
    except Exception:  # noqa: BLE001 - no autoemulate there either, or a broken interpreter
        return []


#: Probing costs seconds (autoemulate pulls in torch), and the answer only changes when the
#: interpreter or the CA directory does -- which is exactly the cache key.
_MODEL_CACHE: dict[tuple, list[str]] = {}

#: What has to be installed for CA to have any emulator models at all. CA declares it as the
#: optional ``emulation`` extra (not ``dev``) because autoemulate pulls torch / gpytorch /
#: pyro-ppl / lightgbm and pins the interpreter, so it is never present by accident -- which is
#: why "no models" needs an explanation rather than a shrug.
AUTOEMULATE_REQUIREMENT = 'autoemulate>=2.1,<3'
#: autoemulate's own interpreter pin. A conda env built for something else (FEniCSx, say) is
#: routinely outside it, and `pip install` then fails for a reason worth stating up front.
AUTOEMULATE_PYTHON_RANGE = ">=3.10,<3.13"


def _probe_models(python: str | None = None) -> tuple[list[str], str | None]:
    """``(model names, the interpreter that was probed)``.

    The single detection path behind both :func:`emulator_models` and
    :func:`emulator_availability`, so "which models" and "why none" can never be answered by
    two differently-behaved probes.

    The interpreter is ``python`` -- the **configured analysis interpreter**, passed in by the
    caller because only it knows which one the runners were given -- falling back to
    :func:`default_python`. That is the interpreter that will do the training. It is None only
    in the frozen app with nothing configured, where training happens in the bundle's own
    environment; the answer is then whatever *this* process can import.
    """
    src = None
    try:
        _ensure_ca_path()
        src = os.environ.get("CIRCULATORY_AUTOGEN_SRC")
    except Exception:  # noqa: BLE001 - no CA configured at all
        src = None

    python = python or default_python()
    if python and src:
        key = (python, src)
        if key not in _MODEL_CACHE:
            _MODEL_CACHE[key] = _models_from_interpreter(python, src)
        if _MODEL_CACHE[key]:
            return list(_MODEL_CACHE[key]), python

    return _models_in_process(), python


def _models_in_process() -> list[str]:
    """The emulator names *this* interpreter can see, or [].

    The fallback arm of :func:`_probe_models`, split out so it is one named thing
    a test can stand in for -- the unit tier has to be able to describe "the
    environment has no autoemulate" without needing an environment that hasn't.
    """
    try:
        from emulators.emulator_trainer import emulator_model_names  # noqa: PLC0415

        return [str(name) for name in emulator_model_names()]
    except Exception:  # noqa: BLE001 - older CA, or no autoemulate here either
        return []


def emulator_models(python: str | None = None) -> list[str]:
    """The emulator names CA's ``emulator_settings.models`` accepts.

    A runtime registry, not a schema: the list is whatever the *installed* autoemulate
    registers, so it is discovered through CA's accessor rather than hardcoded here (the same
    rule as the cost/operation funcs).

    Asked of ``python`` -- the **configured analysis interpreter**, passed in by the caller
    because only it knows which one the runners were given -- because that is the interpreter
    that will do the training. autoemulate is an optional extra with heavy dependencies, and it is routinely
    installed in the CA venv a user points CUFLynx at while the API itself runs on a plain
    system python. Probing in-process then answered "no models" about an interpreter that was
    never going to train anything -- and the panel, which offers a menu only when the list is
    non-empty, fell back to free text on a machine where the menu was perfectly knowable.

    Falls back to this process when there is no external interpreter configured (the frozen app
    trains in its own), and returns empty when neither can answer. Empty is honest but mute,
    which is the whole of the complaint :func:`emulator_availability` answers: the panel
    degraded from a menu to a free-text box and said nothing about why.
    """
    return _probe_models(python)[0]


def _ca_dir_hint() -> str:
    """The configured circulatory_autogen checkout, for the ``pip install -e`` hint.

    Its *root*, not ``src/`` -- that is where the pyproject declaring the ``emulation`` extra
    lives. A placeholder when no CA directory is configured, so the sentence still reads.
    """
    src = os.environ.get("CIRCULATORY_AUTOGEN_SRC")
    return str(Path(src).parent) if src else "<circulatory_autogen>"


def emulator_availability(python: str | None = None) -> dict:
    """Whether an emulator could be trained at all, and if not, what to do about it.

    ``{"models", "available", "interpreter", "unavailable_reason"}``. Same probe as
    :func:`emulator_models` -- ``available`` is exactly "that probe found names", because CA's
    ``emulator_model_names()`` returns [] when autoemulate is absent and non-empty when it is
    not. What is added is the *reason*, which is what nothing could say before: a user who
    pointed Settings at a conda env built for FEniCSx saw the Emulator panel's model dropdown
    quietly become a free-text box, with no way to find out that the env lacked autoemulate.

    Three distinguishable causes, because they have three different fixes:

    * this circulatory_autogen has no emulators at all (``supported`` False) -- update CA;
    * an interpreter is configured and cannot import autoemulate -- install it *there*;
    * none is configured and this process cannot import it either -- choose one in Settings.

    Cheap on purpose: the panel polls this. The model probe is cached per
    (interpreter, CA dir) and the analysis-options introspection is cached outright, so a poll
    spawns no subprocess.
    """
    # Cannot train against a CA that has no emulators, whatever the interpreter has.
    supported = bool(get_analysis_options().get("emulation", {}).get("options"))
    models, interpreter = _probe_models(python)
    available = bool(supported and models)

    if available:
        reason = None
    elif not supported:
        reason = (
            "This circulatory_autogen has no emulation support: its analysis options "
            "declare no emulation mode. Point Settings at a newer circulatory_autogen "
            "to train emulators."
        )
    elif interpreter:
        reason = (
            f"The analysis interpreter {interpreter} cannot import autoemulate, which is "
            f"what provides the emulator models, so there is nothing to train. Install it "
            f'there with: {interpreter} -m pip install "{AUTOEMULATE_REQUIREMENT}" '
            f"(autoemulate requires Python {AUTOEMULATE_PYTHON_RANGE}). Installing "
            f"circulatory_autogen itself with its optional emulation extra does the same: "
            f'pip install -e "{_ca_dir_hint()}[emulation]". Or choose an interpreter that '
            f"already has it in Settings."
        )
    else:
        reason = (
            f"CUFLynx's own environment cannot import autoemulate, which is what provides "
            f"the emulator models, and no analysis interpreter is configured. Choose one in "
            f"Settings that has autoemulate installed, or install it there with: "
            f'pip install "{AUTOEMULATE_REQUIREMENT}" (autoemulate requires Python '
            f"{AUTOEMULATE_PYTHON_RANGE}). Installing circulatory_autogen itself with its "
            f'optional emulation extra does the same: pip install -e '
            f'"{_ca_dir_hint()}[emulation]".'
        )

    return {
        "models": models,
        "available": available,
        "interpreter": interpreter,
        "unavailable_reason": reason,
    }


def _dt_field() -> dict:
    # The fixed step for fixed-step methods (e.g. semi_implicit_euler); the output
    # sampling interval otherwise. Applies to every method.
    return {"key": "dt", "label": "Time step (dt)", "type": _NUM, "default": 0.01}


def _method_field(options, label, default=None) -> dict:
    opts = list(options)
    chosen = default if (default in opts) else (opts[0] if opts else "")
    return {
        "key": "method", "label": label, "type": _SEL,
        "default": chosen, "options": opts,
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
    "user_config": "User config (JSON)",
}

# dict-typed solver_info fields the form renders anyway, as JSON. An allow-list
# rather than "render every dict": casadi's `options` is a plugin's own option
# bag with its own failure modes, and offering it as free text would invite
# settings that fail inside CasADi. `user_config` is different in kind — the
# external wrapper passes it straight to the user's class and interprets none of
# it, so the user is the only one who could fill it in.
_JSON_DICT_FIELDS = frozenset({"user_config"})


def _pretty_label(name: str) -> str:
    s = str(name).replace("_", " ").strip()
    return (s[:1].upper() + s[1:]) if s else str(name)


def _si_field_from_descriptor(desc: dict) -> dict | None:
    """Map a CA solver_info descriptor (name/type/default/choices) to a CUFLynx
    form field, or None when the compact settings form can't render it — i.e. the
    ``str``/``dict`` fields (jac, gradient_method, casadi ``options``), except the
    dict fields named in :data:`_JSON_DICT_FIELDS`."""
    name = desc.get("name")
    typ = desc.get("type")
    if typ == "dict" and name in _JSON_DICT_FIELDS:
        return {"key": name, "label": _SOLVER_INFO_LABELS.get(name, _pretty_label(name)),
                "type": _JSON, "default": desc.get("default")}
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


def _solver_info_schema_from_ca(
    fields_by_solver: dict, methods_by_solver: dict, default_method_by_solver: dict | None = None
) -> dict:
    """Per-solver solver_info form fields introspected from CA's ``SOLVER_INFO_FIELDS``
    (the single source of truth). CA omits the framework keys, so ``method`` (from
    the solver's method menu) and ``dt`` are injected; ``str``/``dict`` fields are
    skipped.

    CA's schema doesn't model per-method applicability, so :data:`_METHOD_GATES_BY_SOLVER`
    overlays it — introspection stays the source of truth for *which fields exist*,
    while the gating says which methods each one applies to.
    """
    default_method_by_solver = default_method_by_solver or {}
    out = {}
    for solver, descriptors in fields_by_solver.items():
        fields = []
        methods = list(methods_by_solver.get(solver, []))
        if methods:
            label = "Integrator" if solver == "casadi_integrator" else "Method"
            fields.append(_method_field(methods, label, default_method_by_solver.get(solver)))
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


def _solver_info_schema(methods_by_solver: dict, default_method_by_solver: dict | None = None) -> dict:
    """Per-solver editable solver_info fields. `method` options come from CA;
    fields carry an optional `methods` restriction so the available settings track
    which solver_info keys each method actually consumes."""
    default_method_by_solver = default_method_by_solver or {}
    def cvode_fields():
        return [
            _dt_field(),
            {"key": "MaximumStep", "label": "Max step", "type": _NUM, "default": 0.001},
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
    external_methods = methods_by_solver.get(
        "external", FALLBACK_SOLVER_SCHEMA["methods_by_solver"]["external"]
    )

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
            _method_field(casadi_methods, "Integrator", default_method_by_solver.get("casadi_integrator")),
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
        # The user's own solver class. dt still applies (the wrapper passes it to
        # update_times), and everything else it might want is `user_config`,
        # which CA hands over untouched — so this is the whole form.
        "external": [
            _method_field(external_methods, "Method"),
            _dt_field(),
            {"key": "user_config", "label": "User config (JSON)", "type": _JSON, "default": None},
        ],
    }


def _apply_aadc_tape_constraint(ad_suitable: dict, methods_by_solver: dict) -> tuple:
    """Narrow AADC's AD-suitable methods to the ones its tape can record.

    ``aadc_backend`` refuses any method outside TAPE_CONSISTENT_METHODS -- an
    adaptive integrator picks its steps from the state, so the recorded sequence
    of operations does not replay -- but SOLVER_SCHEMA does not carry that, and
    CA lists ``adaptive_rk45`` first, which is what CUFLynx defaulted to.

    Returns ``(ad_suitable, default_method_additions)``. Both are left untouched
    when CA already advertises AADC's AD methods, or when the constraint cannot
    be read: guessing would be worse than the status quo.
    """
    ad_suitable = dict(ad_suitable)
    defaults: dict[str, str] = {}
    for solver, methods in methods_by_solver.items():
        if not str(solver).startswith("aadc") or ad_suitable.get(solver):
            continue
        try:
            from param_id.aadc_backend import TAPE_CONSISTENT_METHODS  # noqa: PLC0415
        except Exception:  # noqa: BLE001 - older CA / no AADC support
            continue
        allowed = [m for m in methods if m in TAPE_CONSISTENT_METHODS]
        if not allowed:
            continue
        ad_suitable[solver] = allowed
        # Default to a method that works for both a plain run and the AD path,
        # so the gradient source does not have to be chosen before the method.
        defaults[solver] = allowed[0]
    return ad_suitable, defaults


def _build_options(schema: dict, differentiable: dict[str, bool]) -> dict:
    supported = _available_formats()
    formats = [m for m in schema.get("model_types", []) if m in supported]
    solvers_by_model_type = schema.get("solvers_by_model_type", {})
    defaults = schema.get("default_solver_by_model_type", {})
    # Filtered once, here, so every consumer below -- the method dropdown, the
    # solver_info form's per-method gating, the AD-suitability lists -- agrees
    # about which methods exist. A method offered by one and not another is how
    # a dead setting survives.
    methods_by_solver = {
        s: supported_methods(s, m) for s, m in schema.get("methods_by_solver", {}).items()
    }

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
    # Per-integrator gradient suitability + preferred default method: from CA's
    # schema (CA #298, landed), else the built-in fallbacks on an older CA.
    ad_suitable = {
        s: supported_methods(s, m)
        for s, m in (schema.get("ad_suitable_methods") or dict(_FALLBACK_AD_SUITABLE)).items()
    }
    # CA enforces a tape constraint for AADC that its schema does not advertise:
    # aadc_backend.TAPE_CONSISTENT_METHODS. Without it we defaulted to the first
    # method CA lists, adaptive_rk45, which can never be taped -- so choosing
    # AADC + AD failed with "cannot be recorded on an AADC tape" every time.
    # Read the constraint from CA rather than restating it here, so it cannot
    # drift from what CA actually enforces.
    ad_suitable, default_method_extra = _apply_aadc_tape_constraint(ad_suitable, methods_by_solver)
    fsa_suitable = {
        s: supported_methods(s, m)
        for s, m in (schema.get("fsa_suitable_methods") or dict(_FALLBACK_FSA_SUITABLE)).items()
    }
    default_method = dict(schema.get("default_method_by_solver") or _FALLBACK_DEFAULT_METHOD)
    # A default CUFLynx has just withdrawn is not a usable default either.
    for solver, method in list(default_method.items()):
        if method not in supported_methods(solver, [method]):
            offered = methods_by_solver.get(solver) or []
            default_method[solver] = offered[0] if offered else ""
    # A default the AD path cannot use is not a usable default.
    for solver, method in default_method_extra.items():
        default_method.setdefault(solver, method)
    # Prefer CA's SOLVER_INFO_FIELDS (single source of truth) when present; an older
    # CA (or the offline fallback schema) has no such key, so degrade to the curated
    # built-in form.
    fields_by_solver = schema.get("solver_info_fields_by_solver") or {}
    if fields_by_solver:
        solver_info_schema = _solver_info_schema_from_ca(fields_by_solver, methods_by_solver, default_method)
    else:
        solver_info_schema = _solver_info_schema(methods_by_solver, default_method)
    return {
        "model_formats": formats,
        "solvers_by_format": solvers_by_format,
        "default_solver_by_format": default_solver_by_format,
        "methods_by_solver": {
            s: list(m) for s, m in methods_by_solver.items() if s not in UNSUPPORTED_SOLVERS
        },
        # Filter the CA-introspected schema (not a rebuilt one) so the OpenCOR
        # exclusion composes with SOLVER_INFO_FIELDS introspection rather than
        # discarding it. Per-key exclusion rides along the same way, so the form
        # and check_solver_info can never disagree about what a solver accepts.
        "solver_info_schema": {
            s: [f for f in fields if f.get("key") not in unsupported_solver_info_keys(s)]
            for s, fields in solver_info_schema.items()
            if s not in UNSUPPORTED_SOLVERS
        },
        "differentiable_operations": dict(differentiable),
        "all_differentiable": all_diff,
        # Per-integrator suitability for the backend's analytic gradient, so the UI
        # can offer AD/FSA only for a suitable integrator and warn otherwise (#298).
        "ad_suitable_methods": {s: list(m) for s, m in ad_suitable.items() if s not in UNSUPPORTED_SOLVERS},
        "fsa_suitable_methods": {s: list(m) for s, m in fsa_suitable.items() if s not in UNSUPPORTED_SOLVERS},
        "default_method_by_solver": dict(default_method),
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
    # One canonical model_type spelling from here inwards, whichever CA answered
    # (or none at all) -- see MODEL_TYPE_ALIASES. Deliberately here rather than
    # inside the introspection, so the fallback schema goes through it too.
    schema = _canonicalise_model_types(schema)
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


def _introspect_param_modifier_operations(output_dir: str | None = None) -> dict:
    """The modifier vocabulary, from CA's registry of modifier functions.

    Raises on a CA predating modifiers, so the caller degrades to
    :data:`_FALLBACK_PARAM_MODIFIER_OPERATIONS`. Same "introspect CA, never
    hardcode" pattern as the priors: CA decides what a modifier may do, and one
    it grows shows up in the params editor without a change here.

    ``output_dir`` is where the user's own ``modifier_funcs_user.py`` lives, if
    any (CA #383). Passing it means a modifier the user wrote in the GUI is
    offered alongside CA's built-ins, exactly as ``obs_options`` does for
    operations and costs — otherwise they could save one and never select it.
    """
    _ensure_ca_path()
    from parsers.PrimitiveParsers import (  # noqa: E402
        DEFAULT_PARAM_MODIFIER_OPERATION,
        param_modifier_operations,
    )

    import user_funcs

    external_path = user_funcs.external_path("modifier", output_dir)
    try:
        registry = param_modifier_operations(external_path)
    except TypeError:  # older CA whose registry predates the external_path arg
        registry = param_modifier_operations()

    return {
        "default": DEFAULT_PARAM_MODIFIER_OPERATION,
        "operations": [
            {
                "value": name,
                "label": (meta or {}).get("label", name.capitalize()),
                "description": (meta or {}).get("description", ""),
                "applies_to": (meta or {}).get("applies_to"),
                "dimensionless": bool((meta or {}).get("dimensionless", False)),
                # Bounds a fresh modifier is offered (θ is dimensionless for
                # scale, so CA can state universal defaults); `identity` is the
                # θ at which every target sits at its baseline.
                "default_min": (meta or {}).get("default_min"),
                "default_max": (meta or {}).get("default_max"),
                "identity": (meta or {}).get("identity"),
                # Extra model constants this modifier needs, ``{name: 'float' |
                # 'list'}``: the entry supplies a qname (or several) per input
                # and CA resolves them to their model defaults once at setup.
                # `remainder`'s ``subtract`` is the motivating case.
                "inputs": dict((meta or {}).get("inputs") or {}),
                "user_defined": bool((meta or {}).get("user_defined", False)),
            }
            for name, meta in registry.items()
        ],
    }


def get_param_modifier_operations(
    refresh: bool = False, output_dir: str | None = None
) -> dict:
    """The modifier vocabulary from CA's registry (introspected, not hardcoded).

    Degrades to :data:`_FALLBACK_PARAM_MODIFIER_OPERATIONS` on a CA predating
    modifiers. Caches a successful introspection (keyed on ``output_dir``, since
    the user's own modifier file lives under it); returns the fallback uncached
    so a later CA-dir change can still pick it up.
    """
    global _modifier_cache
    # Keyed by output_dir rather than holding one entry: the UI asks both with a
    # directory (the params editor, which wants the user's own modifiers) and
    # without (everything else), and a single slot would thrash between the two
    # and re-introspect CA on every alternating call.
    if _modifier_cache is None:
        _modifier_cache = {}
    if not refresh and output_dir in _modifier_cache:
        return _modifier_cache[output_dir]
    ops, ok = _safe(
        lambda: _introspect_param_modifier_operations(output_dir),
        copy.deepcopy(_FALLBACK_PARAM_MODIFIER_OPERATIONS),
    )
    if ok:
        _modifier_cache[output_dir] = ops
    return ops


def get_param_prior_types(refresh: bool = False) -> dict:
    """The params_for_id ``prior`` vocabulary from CA's ``PARAM_PRIOR_TYPES``
    schema (introspected, not hardcoded): ``{default, types: [{value, label,
    description}]}``.

    Degrades to :data:`_FALLBACK_PARAM_PRIOR_TYPES` on a CA predating the schema,
    so the params editor still offers the three priors CA has always understood.
    Caches a successful introspection; returns the fallback uncached so a later
    CA-dir change can still pick it up.
    """
    global _prior_cache
    if _prior_cache is not None and not refresh:
        return _prior_cache
    priors, ok = _safe(
        _introspect_param_prior_types,
        # Deep, not dict(t): each type now carries a `params` list, and a shallow
        # copy would hand every caller the same one to mutate.
        copy.deepcopy(_FALLBACK_PARAM_PRIOR_TYPES),
    )
    if ok:
        _prior_cache = priors
    return priors


def _normalise_uq_mode_key(opts: dict) -> dict:
    """Present CA's UQ block under 'uq' whichever CA is installed.

    CA renamed the mode from 'mcmc' to 'uq' (with options_key UQ_options and
    enable_flag do_uq) once MCMC became one method of uncertainty quantification
    rather than the whole of it. CUFLynx keys off 'uq' internally, so an older CA
    that still reports 'mcmc' is mapped here -- one place, rather than every panel
    and runner having to know which CA it is talking to.
    """
    if "uq" in opts or "mcmc" not in opts:
        return opts
    legacy = dict(opts.pop("mcmc"))
    legacy.setdefault("label", "Uncertainty quantification")
    opts["uq"] = legacy
    return opts


def get_analysis_options(refresh: bool = False) -> dict:
    """Analysis-mode option blocks from CA's ``ANALYSIS_OPTIONS`` schema
    (introspected, not hardcoded), keyed by mode ('sensitivity_analysis', 'uq',
    'identifiability_analysis'). Each value carries ``label``/``enable_flag``/
    ``options_key`` and the per-mode ``options`` descriptors the SA/UQ panels render.

    An older CA reporting the pre-rename 'mcmc' mode is normalised to 'uq', so the
    rest of CUFLynx has one spelling to know about.

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
    opts = _normalise_uq_mode_key(opts)
    if ok:
        _analysis_cache = opts
    return opts


def analysis_mode_options(mode: str) -> list[dict]:
    """The option descriptors for a single analysis mode; [] for an unknown mode."""
    return get_analysis_options().get(mode, {}).get("options", [])


def _introspect_gradient_sources(
    model_type: str, solver: str | None, method: str | None = None
) -> tuple[list[dict], bool]:
    """The gradient sources CA reports for ``model_type`` + ``solver`` (+ integrator
    ``method``), from its discoverable ``gradient_sources`` accessor — the single
    source of truth for the AD/FSA/FD dispatch, matching ``get_gradient``.

    Returns ``(descriptors, method_gated_by_ca)``. CA's accessor gained the
    per-integrator ``method`` gate in issue #298; when it accepts ``method`` we hand
    it over so the rule lives in exactly one place, and the caller skips its local
    mirror. On an older CA (no ``method`` parameter) we call the two-arg form and
    report False so the caller applies :func:`_method_supports_analytic_gradient`.

    Raises (ImportError/AttributeError) on an older CA that has no such accessor,
    so the caller degrades to :func:`_fallback_gradient_sources`. Same "introspect
    CA, never hardcode" pattern as the solver/param_id/analysis schemas.
    """
    import inspect  # noqa: E402 - only needed for the capability probe

    _ensure_ca_path()
    from parsers.PrimitiveParsers import gradient_sources as ca_gradient_sources  # noqa: E402

    if "method" in inspect.signature(ca_gradient_sources).parameters:
        return [dict(d) for d in ca_gradient_sources(model_type, solver, method)], True
    return [dict(d) for d in ca_gradient_sources(model_type, solver)], False


def _fallback_gradient_sources(model_type: str, solver: str | None) -> list[dict]:
    """Hand-coded mirror of CA's ``get_gradient`` dispatch, used only when CA lacks
    the ``gradient_sources`` accessor (older CA). Emits the same descriptor shape as
    the accessor so the runtime ``all_differentiable`` gate applies uniformly.

    Finite difference is always available. casadi_python adds symbolic CasADi AD
    (requires every op @differentiable — enforced by the caller's gate);
    aadc_python adds AADC AD (no differentiability gate); cellml + CVODE_myokit
    adds Myokit CVODES forward sensitivity (FSA).
    """
    sources = [{
        "value": "FD", "label": "Finite difference", "do_ad": False,
        "requires_all_differentiable": False,
        "description": "Finite-difference (numerical) gradient. Always available.",
    }]
    if model_type == "casadi_python":
        sources.append({
            "value": "AD", "label": "Automatic differentiation (CasADi)", "do_ad": True,
            "requires_all_differentiable": True,
            "description": "Symbolic CasADi automatic differentiation.",
        })
    elif model_type == "aadc_python":
        sources.append({
            "value": "AD", "label": "Automatic differentiation (AADC)", "do_ad": True,
            "requires_all_differentiable": False,
            "description": "AADC automatic differentiation.",
        })
    elif model_type == "cellml" and solver == "CVODE_myokit":
        sources.append({
            "value": "FSA", "label": "Forward sensitivity (Myokit CVODES)", "do_ad": True,
            "requires_all_differentiable": False,
            "description": "Myokit CVODES forward sensitivity analysis.",
        })
    return sources


def _method_supports_analytic_gradient(solver: str | None, method: str | None, value: str) -> bool:
    """Whether the selected integrator (``method``) supports the analytic gradient
    source ``value`` (AD/FSA) for ``solver`` — the per-integrator gate from CA's
    ad_suitable_methods / fsa_suitable_methods (CA #298).

    Only used when CA's ``gradient_sources`` accessor can't do the gate itself
    (older CA); a current CA is handed ``method`` and gates it at the source.

    A solver absent from the suitability table isn't gated (e.g. AADC AD) → suitable.
    ``method`` None (unknown/not yet chosen) → suitable, so the source still shows.
    """
    if value not in ("AD", "FSA") or not method:
        return True
    opts = get_solver_options()
    table = opts.get("ad_suitable_methods") if value == "AD" else opts.get("fsa_suitable_methods")
    suitable = (table or {}).get(solver)
    return True if suitable is None else method in suitable


def gradient_sources(
    model_type: str, solver: str | None, all_differentiable: bool, method: str | None = None
) -> list[dict]:
    """Gradient sources available for the current model, for the calibration /
    sensitivity gradient-source menus.

    Introspects CA's discoverable ``gradient_sources`` accessor (falling back to a
    hand-coded mirror of ``get_gradient`` on an older CA), passing the selected
    integrator ``method`` so CA applies its own per-integrator suitability gate —
    an analytic source (AD/FSA) is dropped when the integrator can't produce it,
    e.g. CasADi AD with the SUNDIALS adjoint integrators cvodes/idas (CA #298).
    Only when CA can't do that gate (older CA) do we apply our local mirror, so the
    rule is never duplicated against a CA that owns it.

    On top we apply the one gate CA can't know statically: the runtime
    ``all_differentiable`` gate — any source flagged ``requires_all_differentiable``
    (CasADi AD) is dropped when not every operation is @differentiable.

    Each descriptor carries ``value``/``label``/``do_ad``/``requires_all_differentiable``/
    ``description``.
    """
    result, ok = _safe(lambda: _introspect_gradient_sources(model_type, solver, method), None)
    if not ok or result is None:
        descriptors, gated_by_ca = _fallback_gradient_sources(model_type, solver), False
    else:
        descriptors, gated_by_ca = result
    return [
        d for d in descriptors
        if (all_differentiable or not d.get("requires_all_differentiable"))
        and (gated_by_ca or _method_supports_analytic_gradient(solver, method, d["value"]))
    ]


def ad_available(model_type: str, options: dict | None = None) -> bool:
    """True when symbolic AD gradients are valid for ``model_type`` — i.e. CA's
    gradient sources include an ``AD`` source once the ``all_differentiable`` gate
    is applied (casadi_python + all ops differentiable, or aadc_python).

    Defers to :func:`gradient_sources` (CA's list, gated) rather than re-encoding
    the rule. ``solver`` is irrelevant to AD (it's model-type-determined, unlike
    FSA), so it's left unset; this flag is deliberately AD-only — FSA is surfaced
    through the gradient-source menu, not here.
    """
    opts = options if options is not None else get_solver_options()
    all_diff = bool(opts.get("all_differentiable"))
    sources = gradient_sources(model_type, None, all_diff)
    return any(s.get("value") == "AD" for s in sources)
