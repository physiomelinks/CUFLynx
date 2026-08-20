"""Unit tests for the backend solver capabilities + AD gating logic.

These exercise the pure logic; the model_type/solver/method lists come from CA's
schema (or the built-in fallback). The casadi_python AD path itself is covered by
the integration tests.
"""

import sys
import types

import pytest

import solver_options as so
from conftest import set_ca_module

# A CA-shaped schema: mirrors circulatory_autogen's SOLVER_SCHEMA *including*
# solver_info_fields_by_solver, which is what selects the introspected form
# builder. Field descriptors match CA's real ones (name/type/default).
#
# Why this exists: _build_options has two form builders, and which one runs
# depends on whether the schema carries solver_info_fields_by_solver. Tests that
# went through get_solver_options() therefore exercised whichever path the *host
# machine* produced — the fallback on CI (no circulatory_autogen sibling), the
# introspected one on a dev box. That blind spot hid a real regression, so the
# invariant tests below run against both schemas explicitly.
CA_SCHEMA = {
    "model_types": ["cellml", "python", "cpp", "casadi_python"],
    "solvers_by_model_type": {
        "cellml": ["CVODE_opencor", "CVODE_myokit"],
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
    # CA really does default cellml to the OpenCOR solver, so this exercises
    # the substitution branch rather than assuming it away.
    "default_solver_by_model_type": {
        "cellml": "CVODE_opencor",
        "python": "solve_ivp",
        "cpp": "CVODE",
        "casadi_python": "casadi_integrator",
    },
    "solver_info_fields_by_solver": {
        "CVODE_opencor": [
            {"name": "MaximumStep", "type": "float", "default": 0.001},
            {"name": "MaximumNumberOfSteps", "type": "int", "default": 5000},
            {"name": "rtol", "type": "float", "default": 1e-8},
            {"name": "atol", "type": "float", "default": 1e-8},
        ],
        "CVODE_myokit": [
            {"name": "MaximumStep", "type": "float", "default": 0.001},
            {"name": "MaximumNumberOfSteps", "type": "int", "default": 5000},
            {"name": "rtol", "type": "float", "default": 1e-8},
            {"name": "atol", "type": "float", "default": 1e-8},
        ],
        "solve_ivp": [
            {"name": "rtol", "type": "float", "default": 1e-8},
            {"name": "atol", "type": "float", "default": 1e-8},
            {"name": "max_step", "type": "float", "default": 0.001},
            {"name": "vectorized", "type": "bool", "default": False},
            {"name": "dense_output", "type": "bool", "default": False},
            {"name": "jac", "type": "str", "default": None},  # not renderable -> skipped
        ],
        "casadi_integrator": [
            {"name": "max_step_size", "type": "float", "default": 0.001},
            {"name": "max_step", "type": "float", "default": 0.001},
            {"name": "max_num_steps", "type": "int", "default": 5000},
            {"name": "reltol", "type": "float", "default": 1e-8},
            {"name": "abstol", "type": "float", "default": 1e-10},
            {"name": "rtol", "type": "float", "default": None},
            {"name": "atol", "type": "float", "default": None},
            {"name": "options", "type": "dict", "default": None},  # not renderable -> skipped
        ],
    },
}

# Both form builders. Every invariant below must hold on each, whatever the host
# machine has installed.
BOTH_SCHEMAS = pytest.mark.parametrize(
    "schema",
    [so.FALLBACK_SOLVER_SCHEMA, CA_SCHEMA],
    ids=["curated-fallback", "ca-introspected"],
)


def _build(diff):
    """Build an options payload from the fallback schema + a differentiability map."""
    return so._build_options(so.FALLBACK_SOLVER_SCHEMA, diff)


def _method_options(opts, solver):
    for field in opts["solver_info_schema"][solver]:
        if field["key"] == "method":
            return field["options"]
    return []


def test_get_solver_options_entry_point_works():
    """Smoke test for the real entry point: whichever path this machine takes
    (introspected when circulatory_autogen is importable, fallback otherwise), it
    returns a well-formed payload. The per-path invariants are covered by the
    parametrized tests below."""
    opts = so.get_solver_options()
    assert set(opts) >= {
        "model_formats",
        "solvers_by_format",
        "default_solver_by_format",
        "methods_by_solver",
        "solver_info_schema",
        "differentiable_operations",
        "all_differentiable",
    }
    assert opts["solvers_by_format"]["python"] == ["solve_ivp"]
    assert opts["solvers_by_format"]["casadi_python"] == ["casadi_integrator"]
    # CUFLynx can't run CA's 'cpp' backend, so it isn't offered as a format.
    assert "cpp" not in opts["model_formats"]
    # aadc_python is conditional on a licensed library, so it may or may not be
    # here; external_python needs nothing installed and is always offered.
    assert set(opts["model_formats"]) <= {
        "cellml", "python", "casadi_python", "external_python",
    }
    for solver in ("CVODE_myokit", "solve_ivp", "casadi_integrator"):
        assert solver in opts["solver_info_schema"]


@BOTH_SCHEMAS
def test_cvode_opencor_not_offered_because_no_opencor_bundled(schema):
    """CUFLynx does not bundle OpenCOR, so CVODE_opencor must never be surfaced:
    not as a selectable solver, not as the cellml default, and not in the
    solver_info schema. CellML runs through Myokit's CVODE instead.

    Both schemas name CVODE_opencor as a cellml solver *and* its default, so
    each exercises the substitution branch."""
    opts = so._build_options(schema, {"max": True})
    for solvers in opts["solvers_by_format"].values():
        assert "CVODE_opencor" not in solvers
    assert "CVODE_opencor" not in opts["solver_info_schema"]
    assert "CVODE_opencor" not in opts["methods_by_solver"]
    # cellml falls back to the Myokit CVODE that CUFLynx can actually run.
    assert opts["default_solver_by_format"]["cellml"] == "CVODE_myokit"
    assert "CVODE_myokit" in opts["solvers_by_format"]["cellml"]


@BOTH_SCHEMAS
def test_method_options_come_from_ca_schema(schema):
    """The method dropdown options mirror CA's methods_by_solver (not hardcoded)."""
    opts = so._build_options(schema, {"max": True})
    assert _method_options(opts, "casadi_integrator") == opts["methods_by_solver"]["casadi_integrator"]
    assert _method_options(opts, "solve_ivp") == opts["methods_by_solver"]["solve_ivp"]


@BOTH_SCHEMAS
def test_semi_implicit_euler_only_offered_for_casadi_python(schema):
    """The dampened semi-implicit Euler is a casadi_python integrator method; it
    must not be offered as a solve_ivp (standard python) method."""
    opts = so._build_options(schema, {"max": True})
    assert "semi_implicit_euler" in _method_options(opts, "casadi_integrator")
    assert "semi_implicit_euler" not in _method_options(opts, "solve_ivp")


@BOTH_SCHEMAS
def test_dt_offered_for_every_solver(schema):
    """dt is a framework key CA's schema omits, so both builders must inject it."""
    opts = so._build_options(schema, {"max": True})
    for solver in ("CVODE_myokit", "solve_ivp", "casadi_integrator"):
        keys = [f["key"] for f in opts["solver_info_schema"][solver]]
        assert "dt" in keys


def test_casadi_tolerance_fields_restricted_to_adaptive_methods():
    """reltol/abstol/max_num_steps apply to the adaptive CasADi plugins but not to
    the fixed-step semi_implicit_euler (which uses only dt).

    Built from the fallback schema rather than get_solver_options(): the curated
    form is only reached when CA carries no solver_info_fields_by_solver, so going
    through get_solver_options() would silently test the *other* builder on a
    machine that has a circulatory_autogen checkout."""
    opts = so._build_options(so.FALLBACK_SOLVER_SCHEMA, {"max": True})
    for field in opts["solver_info_schema"]["casadi_integrator"]:
        if field["key"] in ("reltol", "abstol", "max_num_steps"):
            assert "semi_implicit_euler" not in field["methods"]
            assert "cvodes" in field["methods"]


def test_casadi_max_step_field_offered_for_bdf_only():
    """The bdf integrator's internal sub-step cap (max_step) is an editable setting,
    scoped to 'bdf' only (other casadi methods don't consume it)."""
    opts = so._build_options(so.FALLBACK_SOLVER_SCHEMA, {"max": True})
    fields = opts["solver_info_schema"]["casadi_integrator"]
    assert "bdf" in _method_options(opts, "casadi_integrator")
    max_step = next((f for f in fields if f["key"] == "max_step"), None)
    assert max_step is not None, "casadi_integrator should expose a max_step setting"
    assert max_step["methods"] == ["bdf"]
    assert max_step["default"] == 1e-3  # matches the CA helper's default sub-step cap


def test_solver_info_introspected_from_ca_schema():
    """When CA's SOLVER_SCHEMA carries solver_info_fields_by_solver, the form is
    built from it (full introspection, CA as source of truth): framework keys
    (method, dt) are injected, enum->select, bool->bool, and the str/dict fields
    the compact form can't render are skipped."""
    schema = {
        "model_types": ["casadi_python"],
        "solvers_by_model_type": {"casadi_python": ["casadi_integrator"]},
        "methods_by_solver": {"casadi_integrator": ["cvodes", "bdf"]},
        "default_solver_by_model_type": {"casadi_python": "casadi_integrator"},
        "solver_info_fields_by_solver": {
            "casadi_integrator": [
                {"name": "max_step_size", "type": "float", "default": 0.001},
                {"name": "max_step", "type": "float", "default": 1e-3},
                {"name": "some_flag", "type": "bool", "default": False},
                {"name": "mode", "type": "enum", "default": "a", "choices": ["a", "b"]},
                {"name": "opts", "type": "dict", "default": None},  # not renderable -> skipped
                {"name": "jac", "type": "str", "default": None},    # not renderable -> skipped
            ],
        },
    }
    opts = so._build_options(schema, {"max": True})
    by_key = {f["key"]: f for f in opts["solver_info_schema"]["casadi_integrator"]}
    assert by_key["method"]["type"] == "select" and by_key["method"]["options"] == ["cvodes", "bdf"]
    assert "dt" in by_key
    assert by_key["max_step"]["default"] == 1e-3
    assert by_key["some_flag"]["type"] == "bool"
    assert by_key["mode"]["type"] == "select" and by_key["mode"]["options"] == ["a", "b"]
    assert "opts" not in by_key and "jac" not in by_key
    # CA's schema doesn't model per-method applicability, so CUFLynx overlays it:
    # max_step is the bdf sub-step cap, max_step_size is a plugin option (so it is
    # gated to the plugin methods, i.e. not bdf).
    assert by_key["max_step"]["methods"] == ["bdf"]
    assert by_key["max_step_size"]["methods"] == ["cvodes"]


def test_ca_introspected_fields_keep_per_method_gating():
    """Regression: the CA-introspection path must gate fields by method just like
    the curated form.

    Introspection is the source of truth for *which* fields exist, but CA's
    SOLVER_INFO_FIELDS says nothing about which methods consume them. Without the
    overlay every casadi field showed for every method, so selecting the fixed-step
    semi_implicit_euler offered reltol/abstol/max_num_steps it never reads, and
    rk/collocation offered tolerances CasADi rejects outright ("Unknown option:
    abstol"). Gates mirror casadi_python_solver_helper.run()'s dispatch."""
    methods = ["cvodes", "idas", "collocation", "rk", "semi_implicit_euler", "bdf"]
    schema = {
        "model_types": ["casadi_python"],
        "solvers_by_model_type": {"casadi_python": ["casadi_integrator"]},
        "methods_by_solver": {"casadi_integrator": methods},
        "default_solver_by_model_type": {"casadi_python": "casadi_integrator"},
        "solver_info_fields_by_solver": {
            "casadi_integrator": [
                {"name": "max_step_size", "type": "float", "default": 0.001},
                {"name": "max_step", "type": "float", "default": 1e-3},
                {"name": "max_num_steps", "type": "int", "default": 5000},
                {"name": "reltol", "type": "float", "default": 1e-8},
                {"name": "abstol", "type": "float", "default": 1e-10},
                {"name": "rtol", "type": "float", "default": None},
                {"name": "atol", "type": "float", "default": None},
            ],
        },
    }
    by_key = {f["key"]: f
              for f in so._build_options(schema, {"max": True})["solver_info_schema"]["casadi_integrator"]}

    # Tolerances reach ca.integrator only for the SUNDIALS plugins.
    for key in ("reltol", "abstol", "rtol", "atol"):
        assert by_key[key]["methods"] == ["cvodes", "idas"], key

    # Plugin options: every method that goes through ca.integrator() — so not the
    # custom run loops (semi_implicit_euler, bdf).
    for key in ("max_num_steps", "max_step_size"):
        assert by_key[key]["methods"] == ["cvodes", "idas", "collocation", "rk"], key

    # The bdf sub-step cap is consumed only by _run_symbolic_bdf.
    assert by_key["max_step"]["methods"] == ["bdf"]

    # dt applies to every method, so it stays ungated.
    assert "methods" not in by_key["dt"]


def test_method_gates_track_the_offered_method_list():
    """A CA that doesn't offer a gated method must not leave it dangling: gates are
    computed from the offered methods, so an absent one yields an empty gate (the
    field is then hidden for every method) rather than a stale reference."""
    schema = {
        "model_types": ["casadi_python"],
        "solvers_by_model_type": {"casadi_python": ["casadi_integrator"]},
        "methods_by_solver": {"casadi_integrator": ["cvodes"]},  # no bdf offered
        "default_solver_by_model_type": {"casadi_python": "casadi_integrator"},
        "solver_info_fields_by_solver": {
            "casadi_integrator": [{"name": "max_step", "type": "float", "default": 1e-3}],
        },
    }
    by_key = {f["key"]: f
              for f in so._build_options(schema, {"max": True})["solver_info_schema"]["casadi_integrator"]}
    assert by_key["max_step"]["methods"] == []


def test_solver_info_falls_back_to_curated_without_ca_fields():
    """An older CA (or the offline fallback schema) whose SOLVER_SCHEMA lacks
    solver_info_fields_by_solver keeps the curated, per-method-gated form."""
    opts = so._build_options(so.FALLBACK_SOLVER_SCHEMA, {"max": True})
    max_step = next(f for f in opts["solver_info_schema"]["casadi_integrator"]
                    if f["key"] == "max_step")
    assert max_step["methods"] == ["bdf"]  # curated gating preserved


def test_ad_available_requires_casadi_python_and_all_differentiable():
    diff_all = _build({"max": True, "min": True})
    assert diff_all["all_differentiable"] is True
    # Only casadi_python unlocks AD, even when everything is differentiable.
    assert so.ad_available("casadi_python", diff_all) is True
    assert so.ad_available("python", diff_all) is False
    assert so.ad_available("cellml", diff_all) is False


def test_ad_unavailable_when_an_operation_is_not_differentiable():
    diff_mixed = _build({"max": True, "spike_freq": False})
    assert diff_mixed["all_differentiable"] is False
    # A single non-@differentiable op disables AD even for casadi_python.
    assert so.ad_available("casadi_python", diff_mixed) is False


def test_ad_available_introspects_when_no_options_passed(monkeypatch):
    """ad_available() falls back to get_solver_options() when not given a payload."""
    monkeypatch.setattr(so, "get_solver_options", lambda: _build({"max": False}))
    assert so.ad_available("casadi_python") is False


def test_ad_available_true_for_aadc_python(monkeypatch):
    """AADC AD is not gated on all-differentiable, so ad_available is True for
    aadc_python even when some ops aren't @differentiable."""
    monkeypatch.setattr(so, "_introspect_gradient_sources", _boom_gradient_sources)
    assert so.ad_available("aadc_python", _build({"max": False})) is True


# ---------------------------------------------------------------------------
# Gradient sources — introspected from CA's `gradient_sources` accessor, with a
# hand-coded fallback mirror of get_gradient for older CA.
# ---------------------------------------------------------------------------
def _boom_gradient_sources(*_a, **_k):
    raise ImportError("cannot import name 'gradient_sources'")


def _values(sources):
    return [s["value"] for s in sources]


def test_gradient_sources_fallback_mirrors_get_gradient(monkeypatch):
    """On an older CA (no gradient_sources accessor) the hand-coded fallback stands
    in, matching CA's get_gradient dispatch, and the runtime all_differentiable gate
    drops CasADi AD when an op isn't @differentiable."""
    monkeypatch.setattr(so, "_introspect_gradient_sources", _boom_gradient_sources)

    # casadi_python: AD present only when all ops differentiable (gate).
    assert _values(so.gradient_sources("casadi_python", "casadi_integrator", True)) == ["FD", "AD"]
    assert _values(so.gradient_sources("casadi_python", "casadi_integrator", False)) == ["FD"]
    # aadc_python: AD present regardless of the differentiability gate.
    assert _values(so.gradient_sources("aadc_python", None, False)) == ["FD", "AD"]
    # cellml + CVODE_myokit: FSA; other solver / model types: FD only.
    assert _values(so.gradient_sources("cellml", "CVODE_myokit", True)) == ["FD", "FSA"]
    assert _values(so.gradient_sources("cellml", "CVODE_opencor", True)) == ["FD"]
    assert _values(so.gradient_sources("python", "solve_ivp", True)) == ["FD"]

    # Descriptors carry the do_ad / requires_all_differentiable flags the UI + gate use.
    ad = next(s for s in so.gradient_sources("casadi_python", None, True) if s["value"] == "AD")
    assert ad["do_ad"] is True and ad["requires_all_differentiable"] is True
    fsa = next(s for s in so.gradient_sources("cellml", "CVODE_myokit", True)
               if s["value"] == "FSA")
    assert fsa["do_ad"] is True and fsa["requires_all_differentiable"] is False


def test_gradient_sources_gated_by_integrator_suitability(monkeypatch):
    """An analytic source (AD/FSA) is dropped when the selected integrator can't
    produce it: CasADi AD with the SUNDIALS adjoint integrators (cvodes/idas), or
    FSA with a non-CVODE integrator (CA issue #298). method=None doesn't gate."""
    monkeypatch.setattr(so, "_introspect_gradient_sources", _boom_gradient_sources)
    monkeypatch.setattr(so, "get_solver_options", lambda refresh=False: {
        "ad_suitable_methods": so._FALLBACK_AD_SUITABLE,
        "fsa_suitable_methods": so._FALLBACK_FSA_SUITABLE,
    })

    # casadi AD: gated out for cvodes/idas, kept for the symbolic integrators.
    assert _values(so.gradient_sources("casadi_python", "casadi_integrator", True, "cvodes")) == ["FD"]
    assert _values(so.gradient_sources("casadi_python", "casadi_integrator", True, "idas")) == ["FD"]
    assert _values(so.gradient_sources("casadi_python", "casadi_integrator", True, "bdf")) == ["FD", "AD"]
    assert _values(so.gradient_sources("casadi_python", "casadi_integrator", True, "semi_implicit_euler")) == ["FD", "AD"]
    # No method given -> not gated (source still offered).
    assert _values(so.gradient_sources("casadi_python", "casadi_integrator", True, None)) == ["FD", "AD"]
    # FSA: kept for CVODE, dropped for anything else.
    assert _values(so.gradient_sources("cellml", "CVODE_myokit", True, "CVODE")) == ["FD", "FSA"]
    assert _values(so.gradient_sources("cellml", "CVODE_myokit", True, "other")) == ["FD"]


def test_solver_options_expose_suitability_and_default_method():
    """The options payload carries the per-integrator suitability maps + the
    preferred default integrator, and casadi_integrator's method field defaults to
    bdf (AD-suitable) rather than the first (cvodes)."""
    opts = so._build_options(so.FALLBACK_SOLVER_SCHEMA, {"max": True})
    assert opts["ad_suitable_methods"]["casadi_integrator"] == ["collocation", "rk", "semi_implicit_euler", "bdf"]
    assert opts["fsa_suitable_methods"]["CVODE_myokit"] == ["CVODE"]
    assert opts["default_method_by_solver"]["casadi_integrator"] == "bdf"
    method_field = next(f for f in opts["solver_info_schema"]["casadi_integrator"] if f["key"] == "method")
    assert method_field["default"] == "bdf"


def test_suitability_from_ca_schema_when_present():
    """When CA's schema declares the suitability maps, they win over the fallback."""
    schema = dict(so.FALLBACK_SOLVER_SCHEMA)
    schema["ad_suitable_methods"] = {"casadi_integrator": ["bdf"]}
    schema["default_method_by_solver"] = {"casadi_integrator": "semi_implicit_euler"}
    opts = so._build_options(schema, {"max": True})
    assert opts["ad_suitable_methods"]["casadi_integrator"] == ["bdf"]
    assert opts["default_method_by_solver"]["casadi_integrator"] == "semi_implicit_euler"
    method_field = next(f for f in opts["solver_info_schema"]["casadi_integrator"] if f["key"] == "method")
    assert method_field["default"] == "semi_implicit_euler"


def test_gradient_sources_introspects_ca_accessor(monkeypatch):
    """When CA exposes a `gradient_sources` accessor, its descriptors are used
    verbatim (not the hand-coded mirror), with the all_differentiable gate applied
    on the CUFLynx side."""
    calls = {}

    def fake_gradient_sources(model_type, solver):
        calls["args"] = (model_type, solver)
        return [
            {"value": "FD", "label": "FD", "do_ad": False,
             "requires_all_differentiable": False, "description": ""},
            {"value": "AD", "label": "AD (CasADi)", "do_ad": True,
             "requires_all_differentiable": True, "description": ""},
        ]

    fake_mod = types.SimpleNamespace(gradient_sources=fake_gradient_sources)
    set_ca_module(monkeypatch, "parsers.PrimitiveParsers", fake_mod)
    monkeypatch.setattr(so, "_ensure_ca_path", lambda: None)

    # Gate keeps the requires_all_differentiable source when all ops differentiable...
    assert _values(so.gradient_sources("casadi_python", "casadi_integrator", True)) == ["FD", "AD"]
    assert calls["args"] == ("casadi_python", "casadi_integrator")
    # ...and drops it otherwise.
    assert _values(so.gradient_sources("casadi_python", "casadi_integrator", False)) == ["FD"]


def test_gradient_sources_delegates_method_gate_to_ca(monkeypatch):
    """CA's accessor owns the per-integrator gate (CA #298 landed), so CUFLynx hands
    it ``method`` and uses CA's answer verbatim instead of re-applying its local
    mirror — the two rules can't drift. A local table that would gate everything out
    proves CA's answer is the one that wins."""
    calls = {}

    def fake_gradient_sources(model_type, solver=None, method=None):
        calls["args"] = (model_type, solver, method)
        srcs = [{"value": "FD", "label": "FD", "do_ad": False,
                 "requires_all_differentiable": False, "description": ""}]
        if method != "cvodes":  # CA's own per-integrator gate
            srcs.append({"value": "AD", "label": "AD (CasADi)", "do_ad": True,
                         "requires_all_differentiable": False, "description": ""})
        return srcs

    set_ca_module(monkeypatch, "parsers.PrimitiveParsers", types.SimpleNamespace(gradient_sources=fake_gradient_sources))
    monkeypatch.setattr(so, "_ensure_ca_path", lambda: None)
    monkeypatch.setattr(so, "get_solver_options", lambda refresh=False: {
        "ad_suitable_methods": {"casadi_integrator": []},  # would gate AD out entirely
        "fsa_suitable_methods": {},
    })

    # method is forwarded, and CA's verdict (AD allowed for bdf) stands.
    assert _values(so.gradient_sources("casadi_python", "casadi_integrator", True, "bdf")) == ["FD", "AD"]
    assert calls["args"] == ("casadi_python", "casadi_integrator", "bdf")
    # CA gates AD out for the adjoint integrator.
    assert _values(so.gradient_sources("casadi_python", "casadi_integrator", True, "cvodes")) == ["FD"]


def test_gradient_sources_local_gate_when_ca_accessor_lacks_method(monkeypatch):
    """An older CA whose ``gradient_sources`` predates the ``method`` parameter can't
    gate per integrator, so CUFLynx's local mirror does it instead."""

    def fake_gradient_sources(model_type, solver=None):  # no `method` parameter
        return [
            {"value": "FD", "label": "FD", "do_ad": False,
             "requires_all_differentiable": False, "description": ""},
            {"value": "AD", "label": "AD (CasADi)", "do_ad": True,
             "requires_all_differentiable": False, "description": ""},
        ]

    set_ca_module(monkeypatch, "parsers.PrimitiveParsers", types.SimpleNamespace(gradient_sources=fake_gradient_sources))
    monkeypatch.setattr(so, "_ensure_ca_path", lambda: None)
    monkeypatch.setattr(so, "get_solver_options", lambda refresh=False: {
        "ad_suitable_methods": {"casadi_integrator": ["bdf"]},
        "fsa_suitable_methods": {},
    })

    assert _values(so.gradient_sources("casadi_python", "casadi_integrator", True, "bdf")) == ["FD", "AD"]
    assert _values(so.gradient_sources("casadi_python", "casadi_integrator", True, "cvodes")) == ["FD"]


# ---------------------------------------------------------------------------
# Calibration (param_id) methods — introspected from CA, with a fallback.
# ---------------------------------------------------------------------------
def test_param_id_methods_from_ca_schema(monkeypatch):
    """When CA exposes PARAM_ID_METHODS, its methods (canonical names + metadata)
    are surfaced instead of a hardcoded list. Patches the CA import so the real
    introspection body runs against a fake schema."""
    fake_schema = {
        "genetic_algorithm": {"label": "GA", "gradient_based": False, "description": "d1"},
        "sp_minimize": {"label": "L-BFGS-B", "gradient_based": True},
        "CMA-ES": {"label": "CMA-ES", "aliases": ["cmaes"], "gradient_based": False},
    }
    fake_mod = types.SimpleNamespace(PARAM_ID_METHODS=fake_schema)
    set_ca_module(monkeypatch, "parsers.PrimitiveParsers", fake_mod)
    monkeypatch.setattr(so, "_ensure_ca_path", lambda: None)
    so.reset_cache()

    methods = so.get_param_id_methods(refresh=True)

    values = [m["value"] for m in methods]
    assert values == ["genetic_algorithm", "sp_minimize", "CMA-ES"]
    assert "cmaes" not in values  # alias is accepted by CA but not shown in the menu
    sp = next(m for m in methods if m["value"] == "sp_minimize")
    assert sp["gradient_based"] is True and sp["label"] == "L-BFGS-B"


def test_param_id_methods_fall_back_for_older_ca(monkeypatch):
    """An older CA without PARAM_ID_METHODS (introspection raises) must not break
    calibration — degrade to the built-in list."""
    def _boom():
        raise ImportError("cannot import name 'PARAM_ID_METHODS'")

    monkeypatch.setattr(so, "_introspect_param_id_methods", _boom)
    so.reset_cache()
    methods = so.get_param_id_methods(refresh=True)
    assert [m["value"] for m in methods] == ["genetic_algorithm", "CMA-ES"]
    assert all(m["gradient_based"] is False for m in methods)


def test_calibration_defaults_route_uses_introspected_methods(client, monkeypatch):
    monkeypatch.setattr(so, "_introspect_param_id_methods",
                        lambda: [{"value": "bayesian", "label": "Bayes",
                                  "gradient_based": False, "description": ""}])
    so.reset_cache()
    body = client.get("/api/calibration/defaults").json()
    assert body["methods"] == [{"value": "bayesian", "label": "Bayes",
                                "gradient_based": False, "description": ""}]


def test_analysis_options_from_ca_schema(monkeypatch):
    """When CA exposes ANALYSIS_OPTIONS, the SA/MCMC/IA option blocks (and their
    per-mode option descriptors) are surfaced instead of hardcoded lists."""
    fake = {
        "sensitivity_analysis": {
            "label": "SA", "enable_flag": "do_sensitivity", "options_key": "sa_options",
            "options": [{"name": "num_samples", "type": "int", "default": None, "required": True}],
        },
        "uq": {
            "label": "Uncertainty quantification", "enable_flag": "do_uq",
            "options_key": "UQ_options",
            "options": [{"name": "num_steps", "type": "int", "default": 5000}],
        },
    }
    fake_mod = types.SimpleNamespace(ANALYSIS_OPTIONS=fake)
    set_ca_module(monkeypatch, "parsers.PrimitiveParsers", fake_mod)
    monkeypatch.setattr(so, "_ensure_ca_path", lambda: None)
    so.reset_cache()

    ao = so.get_analysis_options(refresh=True)
    assert set(ao) == {"sensitivity_analysis", "uq"}
    assert ao["uq"]["options_key"] == "UQ_options"
    assert so.analysis_mode_options("uq")[0]["name"] == "num_steps"
    # num_steps default flows through untouched from CA.
    assert so.analysis_mode_options("uq")[0]["default"] == 5000


def test_a_pre_rename_ca_mcmc_mode_is_normalised_to_uq(monkeypatch):
    """CA renamed the mode from 'mcmc' to 'uq' once MCMC became one method of uncertainty
    quantification rather than the whole of it. CUFLynx keys off 'uq' internally, so a CA that
    still reports 'mcmc' is mapped in one place rather than every panel and runner having to
    know which CA it is talking to."""
    monkeypatch.setattr(so, "_introspect_analysis_options",
                        lambda: {"mcmc": {
                            "label": "MCMC posterior sampling", "enable_flag": "do_mcmc",
                            "options_key": "mcmc_options",
                            "options": [{"name": "num_steps", "type": "int", "default": 42}]}})
    so.reset_cache()

    ao = so.get_analysis_options(refresh=True)
    assert "uq" in ao and "mcmc" not in ao, "the legacy key should be presented as 'uq'"
    # Its own options_key/enable_flag are preserved: they are what that CA actually reads.
    assert ao["uq"]["options_key"] == "mcmc_options"
    assert so.analysis_mode_options("uq")[0]["default"] == 42


def test_analysis_options_fall_back_for_older_ca(monkeypatch):
    """An older CA without ANALYSIS_OPTIONS (introspection raises) degrades to the
    built-in blocks so the SA/UQ panels still render."""
    def _boom():
        raise ImportError("cannot import name 'ANALYSIS_OPTIONS'")

    monkeypatch.setattr(so, "_introspect_analysis_options", _boom)
    so.reset_cache()
    ao = so.get_analysis_options(refresh=True)
    assert {"sensitivity_analysis", "uq", "identifiability_analysis"} <= set(ao)
    names = [o["name"] for o in ao["sensitivity_analysis"]["options"]]
    assert "num_samples" in names and "sample_type" in names


def test_sensitivity_defaults_route_exposes_ca_options(client, monkeypatch):
    monkeypatch.setattr(so, "_introspect_analysis_options",
                        lambda: {"sensitivity_analysis": {
                            "label": "SA", "enable_flag": "do_sensitivity",
                            "options_key": "sa_options",
                            "options": [{"name": "num_samples", "type": "int", "default": 512}]}})
    so.reset_cache()
    body = client.get("/api/sensitivity/defaults").json()
    assert body["options"] == [{"name": "num_samples", "type": "int", "default": 512}]


def test_uq_defaults_route_exposes_ca_mcmc_options(client, monkeypatch):
    monkeypatch.setattr(so, "_introspect_analysis_options",
                        lambda: {"mcmc": {
                            "label": "MCMC", "enable_flag": "do_mcmc",
                            "options_key": "mcmc_options",
                            "options": [{"name": "num_steps", "type": "int", "default": 3000}]}})
    so.reset_cache()
    body = client.get("/api/uq/defaults").json()
    assert body["mcmc_options"] == [{"name": "num_steps", "type": "int", "default": 3000}]


# ---------------------------------------------------------------------------
# Methods CA advertises that cannot forward-solve (issue #175)
#
# CA's methods_by_solver mixes forward integrators with calibration gradient
# strategies. 'bdf_tape' and 'bdf_kernel' are the latter: they live in
# param_id/aadc_backend.py and have no branch in the AADC helper's run(), so
# choosing one and moving a slider always raised "Unknown AADC solver_info
# method 'bdf_tape'".
# ---------------------------------------------------------------------------
AADC_SCHEMA = {
    "model_types": ["cellml", "aadc_python"],
    "solvers_by_model_type": {
        "cellml": ["CVODE_myokit"],
        "aadc_python": ["aadc_semi_implicit"],
    },
    "methods_by_solver": {
        "aadc_semi_implicit": [
            "bdf_tape", "bdf_kernel", "semi_implicit", "implicit_newton", "rk4",
        ],
    },
    "ad_suitable_methods": {"aadc_semi_implicit": ["bdf_tape", "rk4"]},
    "default_solver_by_model_type": {"aadc_python": "aadc_semi_implicit"},
    # The default CA ships is itself one CUFLynx cannot forward-solve here, so
    # this also covers the substitution.
    "default_method_by_solver": {"aadc_semi_implicit": "bdf_tape"},
    # Present so the CA-introspected form builder runs, which is the one that
    # renders the method dropdown from methods_by_solver.
    "solver_info_fields_by_solver": {
        "aadc_semi_implicit": [
            {"name": "tol", "type": "float", "default": 1e-8},
            {"name": "threads", "type": "int", "default": 4},
        ],
    },
}


def test_a_gradient_strategy_is_not_offered_as_an_integrator():
    opts = so._build_options(AADC_SCHEMA, {"max": True})
    methods = opts["methods_by_solver"]["aadc_semi_implicit"]
    assert "bdf_tape" not in methods and "bdf_kernel" not in methods
    # Only those two: the rest of CA's list is untouched.
    assert methods == ["semi_implicit", "implicit_newton", "rk4"]


def test_the_withdrawn_methods_leave_the_ad_lists_too():
    """One list saying a method exists and another saying it is AD-suitable is
    how a dead setting survives a filter."""
    opts = so._build_options(AADC_SCHEMA, {"max": True})
    assert opts["ad_suitable_methods"]["aadc_semi_implicit"] == ["rk4"]


def test_a_withdrawn_default_is_replaced_by_one_that_runs():
    """CA defaults aadc_semi_implicit to a method CUFLynx has just removed;
    leaving it would make the *default* configuration the broken one."""
    opts = so._build_options(AADC_SCHEMA, {"max": True})
    assert opts["default_method_by_solver"]["aadc_semi_implicit"] == "semi_implicit"


def test_the_dropdown_and_the_method_list_agree():
    opts = so._build_options(AADC_SCHEMA, {"max": True})
    assert (
        _method_options(opts, "aadc_semi_implicit")
        == opts["methods_by_solver"]["aadc_semi_implicit"]
    )


def test_only_the_named_solver_is_filtered():
    """The exclusion is per solver: it must not reach into another solver that
    happens to have a method of the same name."""
    assert so.supported_methods("casadi_integrator", ["bdf_tape", "bdf"]) == ["bdf_tape", "bdf"]
    assert so.supported_methods("aadc_semi_implicit", ["bdf_tape", "rk4"]) == ["rk4"]


@BOTH_SCHEMAS
def test_the_exclusion_does_not_disturb_the_ordinary_schemas(schema):
    """It can only ever remove a method CA offers -- never rename or add one."""
    before = {s: list(m) for s, m in schema.get("methods_by_solver", {}).items()}
    opts = so._build_options(schema, {"max": True})
    for solver, methods in before.items():
        if solver in so.UNSUPPORTED_SOLVERS:
            continue  # dropped wholesale for its own reason (no OpenCOR bundled)
        assert opts["methods_by_solver"][solver] == methods


# ---------------------------------------------------------------------------
# Forward-solvable and stiff-suitable method filtering (#175, #177)
# ---------------------------------------------------------------------------

def test_forward_filter_prefers_cas_schema_over_the_local_fallback(monkeypatch):
    """CA #347 publishes forward_methods_by_solver; read it rather than hardcoding.

    methods_by_solver mixes forward integrators with calibration gradient strategies, so a menu
    built from it offers methods that can only raise (#175). CA's list also moves -- #347 adds
    semi_implicit_signed, which is gradient-only until its forward integrator lands -- so a copy
    here would go stale silently.
    """
    monkeypatch.setattr(so, "_introspect_solver_schema", lambda: {
        "methods_by_solver": {"aadc_semi_implicit": [
            "semi_implicit", "rk4", "semi_implicit_signed", "bdf_tape"]},
        "forward_methods_by_solver": {"aadc_semi_implicit": ["semi_implicit", "rk4"]},
    })
    got = so.supported_methods(
        "aadc_semi_implicit", ["semi_implicit", "rk4", "semi_implicit_signed", "bdf_tape"])
    assert got == ["semi_implicit", "rk4"]


def test_forward_filter_falls_back_when_ca_is_too_old_or_absent(monkeypatch):
    """A CA without the key, or none at all, must still lose the two known-bad methods."""
    older = ["adaptive_rk45", "semi_implicit", "bdf_tape", "bdf_kernel", "rk4"]

    monkeypatch.setattr(so, "_introspect_solver_schema",
                        lambda: {"methods_by_solver": {}})
    assert so.supported_methods("aadc_semi_implicit", older) == [
        "adaptive_rk45", "semi_implicit", "rk4"]

    def boom():
        raise ImportError("no CA on the path")

    monkeypatch.setattr(so, "_introspect_solver_schema", boom)
    assert so.supported_methods("aadc_semi_implicit", older) == [
        "adaptive_rk45", "semi_implicit", "rk4"]


def test_stiff_filter_drops_the_method_that_is_wrong_without_failing(monkeypatch):
    """#177. The excluded methods are not merely slow.

    implicit_euler_ift completes and returns a smooth trace that is 84% low on 3compartment, and
    it is advertised as AD-suitable -- so a calibration picks it and reports clean convergence.
    Filtering it is the difference between a wrong answer and no answer.
    """
    monkeypatch.setattr(so, "_introspect_solver_schema", lambda: {
        "methods_by_solver": {
            "aadc_semi_implicit": ["adaptive_rk45", "semi_implicit",
                                   "implicit_euler_ift", "implicit_newton", "rk4"]},
        "forward_methods_by_solver": {
            "aadc_semi_implicit": ["adaptive_rk45", "semi_implicit",
                                   "implicit_euler_ift", "implicit_newton", "rk4"]},
        "stiff_suitable_methods": {
            "aadc_semi_implicit": ["semi_implicit", "implicit_newton"]},
    })
    offered = ["adaptive_rk45", "semi_implicit", "implicit_euler_ift", "implicit_newton", "rk4"]

    assert so.supported_methods("aadc_semi_implicit", offered) == offered
    assert so.supported_methods("aadc_semi_implicit", offered, stiff=True) == [
        "semi_implicit", "implicit_newton"]


def test_stiff_filter_is_a_noop_when_ca_cannot_say(monkeypatch):
    """Better to offer everything than to invent a stiff set CA has not published."""
    monkeypatch.setattr(so, "_introspect_solver_schema", lambda: {
        "methods_by_solver": {"aadc_semi_implicit": ["semi_implicit", "rk4"]},
        "forward_methods_by_solver": {"aadc_semi_implicit": ["semi_implicit", "rk4"]},
    })
    assert so.supported_methods(
        "aadc_semi_implicit", ["semi_implicit", "rk4"], stiff=True) == ["semi_implicit", "rk4"]


# ---------------------------------------------------------------------------
# modifier `operation` vocabulary
# ---------------------------------------------------------------------------
def test_param_modifier_operations_are_introspected_from_ca(monkeypatch):
    """CA owns what a modifier may do. An operation CA grows ('calculate' is the
    expected next one) must appear here without a change in CUFLynx."""
    fake_mod = types.SimpleNamespace(
        DEFAULT_PARAM_MODIFIER_OPERATION="scale",
        param_modifier_operations=lambda: {
            "scale": {"description": "one calibrated multiplier", "applies_to": "value",
                      "dimensionless": True, "default_min": 0.5, "default_max": 2.0,
                      "identity": 1.0},
            "calculate": {"description": "a user function computes the targets",
                          "applies_to": "value", "dimensionless": False,
                          "default_min": None, "default_max": None, "identity": None},
        },
    )
    set_ca_module(monkeypatch, "parsers.PrimitiveParsers", fake_mod)
    monkeypatch.setattr(so, "_ensure_ca_path", lambda: None)
    so.reset_cache()

    ops = so.get_param_modifier_operations(refresh=True)

    assert ops["default"] == "scale"
    assert [o["value"] for o in ops["operations"]] == ["scale", "calculate"]
    by_value = {o["value"]: o for o in ops["operations"]}
    # default bounds + identity travel: they seed a fresh modifier's θ slider.
    assert by_value["scale"]["default_min"] == 0.5
    assert by_value["scale"]["identity"] == 1.0
    assert by_value["calculate"]["identity"] is None


def test_a_user_authored_modifier_is_offered_alongside_cas_own(monkeypatch, tmp_path):
    """The user's own modifier file is passed to CA's registry.

    Without this a modifier saved in the GUI could never be selected in the
    params editor -- the save would look like it worked and the entry would be
    unreachable. Mirrors what obs_options does for operations and costs.
    """
    seen = {}

    def _registry(external_path=None):
        seen["external_path"] = external_path
        ops = {"scale": {"description": "one calibrated multiplier", "identity": 1.0}}
        if external_path:
            ops["my_remainder"] = {
                "description": "remainder of a total",
                "inputs": {"subtract": "list"},
                "user_defined": True,
            }
        return ops

    fake_mod = types.SimpleNamespace(
        DEFAULT_PARAM_MODIFIER_OPERATION="scale",
        param_modifier_operations=_registry,
    )
    set_ca_module(monkeypatch, "parsers.PrimitiveParsers", fake_mod)
    monkeypatch.setattr(so, "_ensure_ca_path", lambda: None)

    import user_funcs

    monkeypatch.setattr(user_funcs, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(
        user_funcs, "_circulatory_autogen_src", lambda: str(tmp_path / "ca" / "src")
    )
    user_funcs.save_user_func(
        "modifier",
        None,
        "@modifier_func(inputs={'subtract': 'list'})\n"
        "def my_remainder(theta, baseline, subtract):\n"
        "    return theta - sum(subtract)\n",
    )
    so.reset_cache()

    ops = so.get_param_modifier_operations(refresh=True, output_dir=str(tmp_path))

    assert seen["external_path"] == str(
        tmp_path / "user_funcs" / "modifier_funcs_user.py"
    )
    by_value = {o["value"]: o for o in ops["operations"]}
    assert "my_remainder" in by_value
    # `inputs` is what lets the editor ask for the qnames the function needs.
    assert by_value["my_remainder"]["inputs"] == {"subtract": "list"}
    assert by_value["my_remainder"]["user_defined"] is True
    assert by_value["scale"]["user_defined"] is False


def test_param_modifier_operations_fall_back_for_older_ca(monkeypatch):
    """A CA predating modifiers still gets the one operation it will grow into,
    so the editor renders (and the parser refuses unknown ops by name)."""
    def _boom():
        raise ImportError("cannot import name 'param_modifier_operations'")

    monkeypatch.setattr(so, "_introspect_param_modifier_operations", _boom)
    so.reset_cache()
    ops = so.get_param_modifier_operations(refresh=True)
    assert ops["default"] == "scale"
    assert [o["value"] for o in ops["operations"]] == ["scale"]
    assert ops["operations"][0]["identity"] == 1.0


def test_the_config_route_carries_the_modifier_vocabulary(client):
    body = client.get("/api/config").json()
    ops = body["param_modifier_operations"]
    assert ops["default"] == "scale"
    assert any(o["value"] == "scale" for o in ops["operations"])


# ---------------------------------------------------------------------------
# params_for_id `prior` vocabulary
# ---------------------------------------------------------------------------
def test_param_prior_types_are_introspected_from_ca(monkeypatch):
    """CA owns what a prior may be. A prior CA grows must appear here without a
    change in CUFLynx, which is the whole point of introspecting rather than
    hardcoding the list."""
    fake_mod = types.SimpleNamespace(
        PARAM_PRIOR_TYPES={
            "uniform": {"label": "Uniform", "description": "flat", "params": []},
            "lognormal": {"label": "Log-normal", "description": "new in CA", "params": [
                {"name": "prior_sigma", "type": "float", "default": 1.0,
                 "positive": True, "description": "Shape."},
            ]},
        },
        DEFAULT_PARAM_PRIOR_TYPE="uniform",
    )
    set_ca_module(monkeypatch, "parsers.PrimitiveParsers", fake_mod)
    monkeypatch.setattr(so, "_ensure_ca_path", lambda: None)
    so.reset_cache()

    priors = so.get_param_prior_types(refresh=True)

    assert priors["default"] == "uniform"
    assert [p["value"] for p in priors["types"]] == ["uniform", "lognormal"]
    assert priors["types"][1]["label"] == "Log-normal"
    # The description travels too: what the distribution *is* was previously only
    # discoverable by reading CA's likelihood.
    assert priors["types"][1]["description"] == "new in CA"
    # The values a prior takes travel too, so the editor renders exactly the
    # fields CA declares rather than a list held in CUFLynx.
    assert priors["types"][1]["params"] == [
        {"name": "prior_sigma", "type": "float", "default": 1.0, "role": None,
         "default_expr": None, "positive": True, "description": "Shape."},
    ]
    # A CA predating unbounded parameters has no prior_supports_unbounded to ask,
    # so the tickbox is simply not offered rather than guessed at.
    assert priors["types"][1]["supports_unbounded"] is False
    assert priors["types"][0]["params"] == []


def test_param_prior_types_fall_back_for_older_ca(monkeypatch):
    """A CA predating PARAM_PRIOR_TYPES must still get a picker offering the three
    priors CA has always understood, rather than no picker at all."""
    def _boom():
        raise ImportError("cannot import name 'PARAM_PRIOR_TYPES'")

    monkeypatch.setattr(so, "_introspect_param_prior_types", _boom)
    so.reset_cache()
    priors = so.get_param_prior_types(refresh=True)
    assert priors["default"] == "uniform"
    assert [p["value"] for p in priors["types"]] == ["uniform", "exponential", "normal"]
    # The fallback still says what each prior takes, so the fields render on a CA
    # predating the schema.
    by_value = {p["value"]: p for p in priors["types"]}
    assert [f["name"] for f in by_value["normal"]["params"]] == ["prior_mean", "prior_std"]
    assert by_value["uniform"]["params"] == []


def test_the_config_route_carries_the_prior_vocabulary(client, monkeypatch):
    """The params editor reads the vocabulary from /api/config; without it there
    the picker cannot render and the column would be dropped again."""
    monkeypatch.setattr(
        so, "_introspect_param_prior_types",
        lambda: {"default": "uniform",
                 "types": [{"value": "uniform", "label": "Uniform", "description": ""}]},
    )
    so.reset_cache()
    body = client.get("/api/config").json()
    assert body["param_prior_types"]["default"] == "uniform"
    assert body["param_prior_types"]["types"][0]["value"] == "uniform"


# ---------------------------------------------------------------------------
# The seeded solver_info carries CA's declared defaults (#200)
# ---------------------------------------------------------------------------
# The Settings popup binds each field to the current solver_info *value*, not to
# the descriptor's `default`. So a default CA declares but nothing seeds renders
# as an empty box -- which is what happened to Rel./Abs. tol, because CUFLynx
# seeded the literal {"MaximumStep": 0.001}. Worse than cosmetic: the form
# offered 1e-8 as the default while the run used Myokit's own looser one.

@BOTH_SCHEMAS
def test_every_declared_default_is_seeded_into_solver_info(schema, monkeypatch):
    """No field may be offered with a default that the seed then leaves unset.

    Stated over the whole schema rather than over rtol/atol by name: naming the two
    keys would pass again the moment CA declares a default for a third.
    """
    monkeypatch.setattr(so, "_introspect_solver_schema", lambda: schema)
    monkeypatch.setattr(so, "_introspect_differentiable", lambda: {"max": True})
    so.reset_cache()
    form = so.get_solver_options(refresh=True)["solver_info_schema"]
    for solver, fields in form.items():
        seeded = so.default_solver_info(solver)
        for f in fields:
            if f["key"] == "dt" or f.get("default") is None:
                continue
            assert f["key"] in seeded, (
                f"{solver}.{f['key']} is offered with a default nothing seeds"
            )
            assert seeded[f["key"]] == f["default"]


def _ca_schema_with_fields(fields_by_solver: dict) -> dict:
    return dict(CA_SCHEMA, solver_info_fields_by_solver=fields_by_solver)


def test_the_seed_takes_the_tolerances_from_ca_rather_than_a_local_copy(monkeypatch):
    """The numbers seeded are CA's, so a CA that retunes its tolerances retunes ours."""
    monkeypatch.setattr(so, "_introspect_solver_schema", lambda: _ca_schema_with_fields({
        "CVODE_myokit": [
            {"name": "MaximumStep", "type": "float", "default": 0.001},
            {"name": "rtol", "type": "float", "default": 1.25e-9},
            {"name": "atol", "type": "float", "default": 3.5e-11},
        ],
    }))
    monkeypatch.setattr(so, "_introspect_differentiable", lambda: {"max": True})
    so.reset_cache()
    so.get_solver_options(refresh=True)
    assert so.default_solver_info("CVODE_myokit") == {
        "method": "CVODE", "MaximumStep": 0.001, "rtol": 1.25e-9, "atol": 3.5e-11,
    }


def test_the_seed_omits_dt_and_anything_ca_gives_no_default_for(monkeypatch):
    """dt belongs to the engine (a run parameter, merged into /api/config on its
    own), and a field with no declared default stays unset rather than invented."""
    monkeypatch.setattr(so, "_introspect_solver_schema", lambda: _ca_schema_with_fields({
        "CVODE_myokit": [
            {"name": "MaximumStep", "type": "float", "default": 0.001},
            {"name": "rtol", "type": "float", "default": None},
        ],
    }))
    monkeypatch.setattr(so, "_introspect_differentiable", lambda: {"max": True})
    so.reset_cache()
    so.get_solver_options(refresh=True)
    seeded = so.default_solver_info("CVODE_myokit")
    assert seeded == {"method": "CVODE", "MaximumStep": 0.001}


def test_a_key_the_solver_cannot_honour_is_not_seeded(monkeypatch):
    """The seed rides the filtered form schema, so an inert key CA advertises for
    CVODE_myokit cannot come back in through the default (see
    UNSUPPORTED_SOLVER_INFO_KEYS) -- and check_solver_info would reject it."""
    monkeypatch.setattr(so, "_introspect_solver_schema", lambda: _ca_schema_with_fields({
        "CVODE_myokit": [
            {"name": "MaximumStep", "type": "float", "default": 0.001},
            {"name": "MaximumNumberOfSteps", "type": "int", "default": 5000},
        ],
    }))
    monkeypatch.setattr(so, "_introspect_differentiable", lambda: {"max": True})
    so.reset_cache()
    so.get_solver_options(refresh=True)
    seeded = so.default_solver_info("CVODE_myokit")
    assert "MaximumNumberOfSteps" not in seeded
    so.check_solver_info("CVODE_myokit", seeded)


def test_an_unknown_solver_seeds_nothing_rather_than_raising():
    assert so.default_solver_info("no_such_solver") == {}


def test_the_config_route_reports_a_value_for_every_field_it_offers(client):
    """End to end: the payload the Settings popup renders must not carry a control
    with a declared default and nothing to show in it (#200)."""
    body = client.get("/api/config").json()
    fields = body["solver_info_schema"][body["solver"]]
    missing = [
        f["key"] for f in fields
        if f.get("default") is not None and body["solver_info"].get(f["key"]) is None
    ]
    assert missing == []


# ---------------------------------------------------------------------------
# The model_type rename (cellml_only -> cellml)
# ---------------------------------------------------------------------------
# The CA directory is chosen at runtime and can be any checkout, so CUFLynx meets
# both spellings. It keeps one canonical name internally and translates back at
# the moment a config is written for CA -- these pin both directions, because
# each failure is invisible from the other side: an untranslated *inbound* name
# puts a retired option in the Settings dropdown, and an untranslated *outbound*
# one makes every calibration die in CA's parser before it starts.

def _legacy_ca_schema():
    """CA_SCHEMA as an older circulatory_autogen reports it."""
    schema = {k: v for k, v in CA_SCHEMA.items()}
    schema["model_types"] = ["cellml_only" if m == "cellml" else m for m in schema["model_types"]]
    for key in ("solvers_by_model_type", "default_solver_by_model_type"):
        schema[key] = {
            ("cellml_only" if m == "cellml" else m): v for m, v in CA_SCHEMA[key].items()
        }
    return schema


def test_a_current_ca_needs_no_translation_in_either_direction(monkeypatch):
    monkeypatch.setattr(so, "_introspect_solver_schema", lambda: dict(CA_SCHEMA))
    monkeypatch.setattr(so, "_introspect_differentiable", lambda: {"max": True})
    so.reset_cache()
    opts = so.get_solver_options(refresh=True)

    assert "cellml" in opts["model_formats"]
    assert so.ca_model_type("cellml") == "cellml"


def test_an_older_ca_is_presented_under_the_current_name(monkeypatch):
    """Inbound: the retired spelling must never reach the Settings dropdown, or
    the GUI keeps writing it into new configs and the rename never finishes."""
    monkeypatch.setattr(so, "_introspect_solver_schema", lambda: _legacy_ca_schema())
    monkeypatch.setattr(so, "_introspect_differentiable", lambda: {"max": True})
    so.reset_cache()
    opts = so.get_solver_options(refresh=True)

    assert "cellml" in opts["model_formats"]
    assert "cellml_only" not in opts["model_formats"]
    # And the format still carries its solvers and its default -- a rename that
    # loses the keyed-off entries leaves the dropdown with an empty format.
    assert opts["solvers_by_format"]["cellml"] == ["CVODE_myokit"]
    assert opts["default_solver_by_format"]["cellml"] == "CVODE_myokit"


def test_an_older_ca_gets_a_run_config_in_its_own_spelling(monkeypatch):
    """Outbound: that CA's parse_user_inputs_file exits on a model_type it does
    not know, so a run config saying `cellml` would kill every calibration."""
    monkeypatch.setattr(so, "_introspect_solver_schema", lambda: _legacy_ca_schema())
    monkeypatch.setattr(so, "_introspect_differentiable", lambda: {"max": True})
    so.reset_cache()
    so.get_solver_options(refresh=True)

    assert so.ca_model_type("cellml") == "cellml_only"
    # Only the renamed one is translated; everything else is passed straight on.
    assert so.ca_model_type("casadi_python") == "casadi_python"
    assert so.ca_model_type("external_python") == "external_python"
    assert so.ca_model_type("") == ""
    assert so.ca_model_type(None) is None


def test_the_old_spelling_is_accepted_wherever_a_stored_setting_can_carry_it():
    """Settings persist across upgrades, so a config written before the rename
    still names the old format. It is canonicalised rather than rejected."""
    assert so.canonical_model_type("cellml_only") == "cellml"
    assert so.canonical_model_type("cellml") == "cellml"
    assert so.canonical_model_type("casadi_python") == "casadi_python"
    assert so.canonical_model_type("nonsense") == "nonsense"
    assert so.canonical_model_type("") == ""
    assert so.canonical_model_type(None) is None


def test_the_ca_spelling_is_worked_out_before_the_first_run_config(monkeypatch):
    """Outbound translation must not depend on something having asked for the
    solver options first.

    ``_ca_model_type_spelling`` started as ``{}``, which is indistinguishable from
    "asked, and this CA uses the current names" -- so ``ca_model_type`` was the
    identity function until some caller happened to run ``get_solver_options()``.
    A run config written before that went out saying ``cellml`` to a CA that only
    accepts ``cellml_only``, and every calibration, SA and UQ run against it died
    at startup. It now introspects on first use.
    """
    monkeypatch.setattr(so, "_introspect_solver_schema", lambda: _legacy_ca_schema())
    monkeypatch.setattr(so, "_introspect_differentiable", lambda: {"max": True})
    so.reset_cache()  # nothing has been introspected yet, as at process start

    assert so.ca_model_type("cellml") == "cellml_only"


def test_the_ca_spelling_is_forgotten_when_the_ca_directory_changes(monkeypatch):
    """It is a fact about the connected CA, so it is a cache like the other five
    -- and it was the one ``reset_cache`` did not clear, so a new CA dir (and, in
    the test process, the next test) inherited the previous one's answer."""
    monkeypatch.setattr(so, "_introspect_solver_schema", lambda: _legacy_ca_schema())
    monkeypatch.setattr(so, "_introspect_differentiable", lambda: {"max": True})
    so.reset_cache()
    so.get_solver_options(refresh=True)
    assert so.ca_model_type("cellml") == "cellml_only"

    # The user points Settings -> "CA dir" at a current checkout.
    monkeypatch.setattr(so, "_introspect_solver_schema", lambda: dict(CA_SCHEMA))
    so.reset_cache()

    assert so.ca_model_type("cellml") == "cellml"


def test_an_unreadable_ca_leaves_the_spelling_alone(monkeypatch):
    """No CA at all: there is nothing to translate against, so the canonical name
    is the only honest answer -- and it must not raise on the way to it."""
    def _boom():
        raise RuntimeError("no CA")

    monkeypatch.setattr(so, "_introspect_solver_schema", _boom)
    monkeypatch.setattr(so, "_introspect_differentiable", _boom)
    so.reset_cache()

    assert so.ca_model_type("cellml") == "cellml"


class TestEmulationUnavailableReasonNamesTheRightCause:
    """"No emulation mode in the schema" has two causes, and they have opposite fixes.

    ``get_analysis_options()`` degrades to a fallback that predates emulators, so a
    failure to introspect circulatory_autogen at all looked identical to a genuinely old
    CA -- and the message sent the user to change a CA directory that was never the
    cause. In the packaged app the CA is bundled and cannot be old, so that reading was
    wrong every time it appeared.
    """

    def _reason(self, monkeypatch, *, options, introspected):
        monkeypatch.setattr(so, "get_analysis_options",
                            lambda *a, **k: {"emulation": {"options": options}} if options else {})
        monkeypatch.setattr(so, "analysis_options_introspected", lambda: introspected)
        monkeypatch.setattr(so, "_probe_models", lambda python: ([], None))
        return so.emulator_availability(None)["unavailable_reason"]

    def test_a_genuinely_old_ca_is_told_to_update(self, monkeypatch):
        reason = self._reason(monkeypatch, options=[], introspected=True)
        assert "predates emulator training" in reason
        assert "0.4.0" in reason

    def test_an_unreadable_ca_does_not_blame_the_ca_version(self, monkeypatch):
        reason = self._reason(monkeypatch, options=[], introspected=False)
        assert "could not be read" in reason
        assert "the environment the analysis runs in" in reason
        # The old message's advice, which was wrong for this cause.
        assert "newer circulatory_autogen" not in reason

    def test_a_capable_ca_without_autoemulate_talks_about_the_interpreter(self, monkeypatch):
        monkeypatch.setattr(so, "get_analysis_options",
                            lambda *a, **k: {"emulation": {"options": [{"name": "model"}]}})
        monkeypatch.setattr(so, "analysis_options_introspected", lambda: True)
        monkeypatch.setattr(so, "_probe_models", lambda python: ([], "/envs/x/bin/python"))
        reason = so.emulator_availability("/envs/x/bin/python")["unavailable_reason"]
        assert "autoemulate" in reason
        assert "/envs/x/bin/python" in reason


@pytest.mark.unit
def test_a_failed_introspection_is_logged(monkeypatch, caplog):
    """The user-facing reasons say "the server log has the import error" -- so it must.

    ``_safe`` swallowed every exception silently, which is right for the expected case
    (an older CA legitimately has no such schema) and wrong for a real import failure:
    v0.4.1's packaged app told users to go and read a log line that was never written.
    """
    import logging

    import solver_options

    def boom():
        raise ImportError("no module named 'somethingimportant'")

    with caplog.at_level(logging.DEBUG, logger="solver_options"):
        value, ok = solver_options._safe(boom, {"fallback": True})

    assert (value, ok) == ({"fallback": True}, False)
    assert any("somethingimportant" in r.getMessage() or
               (r.exc_info and "somethingimportant" in str(r.exc_info[1]))
               for r in caplog.records), (
        "the swallowed exception was not logged, so the advice to read the server log "
        "sends the user to an empty file."
    )
    # The line has to say *which* introspection, or a log with several of them in it
    # cannot be read. Bare `fn.__name__` prints "<lambda>" for the call sites that bind
    # an argument, which is why those pass functools.partial.
    assert any("boom" in r.getMessage() for r in caplog.records), (
        "the log line does not name the introspection that failed."
    )


@pytest.mark.unit
def test_the_log_names_an_introspection_that_had_an_argument_bound(caplog):
    """``functools.partial``, not ``lambda``: "<lambda> failed" identifies nothing.

    Two of the seven call sites bind an argument (the output dir, the gradient triple),
    and both used a lambda -- so the log line the user is told to go and read named the
    wrong thing in exactly the cases where several introspections are in flight.
    """
    import functools
    import logging

    import solver_options

    def introspect_something(arg):
        raise ImportError("nope")

    with caplog.at_level(logging.DEBUG, logger="solver_options"):
        solver_options._safe(functools.partial(introspect_something, "x"), None)

    # The whole message, not a substring: a bare partial still *contains* the name
    # inside its repr ("functools.partial(<function introspect_something at 0x...>,
    # 'x')"), so a laxer assertion passes against the unfixed code.
    assert any("introspection introspect_something failed" in r.getMessage()
               for r in caplog.records), (
        "a bound introspection logs as <lambda>/partial(...) instead of its own name: "
        + "; ".join(r.getMessage() for r in caplog.records)
    )


@pytest.mark.unit
def test_the_reason_carries_the_import_error(monkeypatch):
    """"It could not be read" is not actionable; the exception that caused it is.

    The packaged app has no console, and the swallowed exception is debug-level because
    the expected fallback is not a problem -- so pointing the user at the server log,
    as v0.4.1 did, points them at something they cannot open.
    """
    import solver_options

    monkeypatch.setattr(solver_options, "_analysis_cache", None)
    monkeypatch.setattr(solver_options, "_analysis_error", None)
    monkeypatch.setattr(solver_options, "_introspect_analysis_options",
                        lambda: (_ for _ in ()).throw(ImportError("No module named 'torch'")))

    assert solver_options.analysis_options_introspected() is False
    detail = solver_options.analysis_options_error()
    assert detail and "No module named 'torch'" in detail, detail


@pytest.mark.unit
def test_only_the_analysis_introspection_writes_the_analysis_reason(monkeypatch):
    """The reason belongs to that introspection, not to whichever ``_safe`` ran last.

    ``_safe`` has seven callers; ``/api/emulator/defaults`` is a sync ``def``, so FastAPI
    runs it in a threadpool, and ``App.vue`` fires six fetches at startup. Recorded in one
    "last error anywhere" global, any other introspection succeeding concurrently blanked
    this one's error and any other failing one substituted its own.

    Asserted on the global directly, and deliberately so. Going through
    ``analysis_options_error()`` proves nothing: a failed introspection is never cached,
    so that call re-runs it and overwrites the damage before the assertion can see it --
    which is why this is a *concurrency* bug and why the single-threaded version of this
    test passed against the broken code.
    """
    import solver_options

    monkeypatch.setattr(solver_options, "_analysis_error", "the analysis import error")

    solver_options._safe(lambda: "fine", None)
    solver_options._safe(lambda: (_ for _ in ()).throw(ImportError("unrelated")), None)

    assert solver_options._analysis_error == "the analysis import error", (
        "an unrelated introspection wrote the analysis-options reason, so under the "
        "app's concurrent startup fetches the user sees the wrong error, or none."
    )


@pytest.mark.unit
def test_a_new_ca_directory_does_not_inherit_the_old_ones_diagnosis(monkeypatch):
    """``_analysis_error`` is paired with ``_analysis_cache`` and must be cleared with it."""
    import solver_options

    monkeypatch.setattr(solver_options, "_analysis_cache", None)
    monkeypatch.setattr(solver_options, "_analysis_error", None)
    monkeypatch.setattr(solver_options, "_introspect_analysis_options",
                        lambda: (_ for _ in ()).throw(ImportError("No module named 'torch'")))
    assert solver_options.analysis_options_error()

    solver_options.reset_cache()

    assert solver_options._analysis_error is None, (
        "a stale reason survives a CA-dir change, so the next failure can be reported "
        "with the previous directory's error."
    )


class TestTheBundledAutoemulateIsNotBlamedForBeingAbsent:
    """Two bundles land on the same "no models, no interpreter" branch.

    The ordinary Linux asset genuinely has no autoemulate. The ``-full`` asset ships it --
    and that is the asset whose users came for the emulator, so "install autoemulate" is
    wrong exactly where it is most likely to be read. It also sent this session's
    investigation after a missing package that was present the whole time.
    """

    def _reason(self, monkeypatch, *, autoemulate):
        monkeypatch.setattr(so, "get_analysis_options",
                            lambda *a, **k: {"emulation": {"options": [{"key": "x"}]}})
        monkeypatch.setattr(so, "analysis_options_introspected", lambda: True)
        monkeypatch.setattr(so, "_probe_models", lambda python: ([], None))  # no models, bundled python
        monkeypatch.setattr(so, "_autoemulate_importable", lambda: autoemulate)
        return so.emulator_availability(None)["unavailable_reason"]

    @pytest.mark.unit
    def test_the_full_bundle_is_not_told_to_install_what_it_ships(self, monkeypatch):
        reason = self._reason(monkeypatch, autoemulate=True)

        assert "bundled" in reason
        assert "pip install" not in reason, (
            "the -full bundle ships autoemulate; telling this user to install it is advice "
            "for a problem they do not have"
        )
        assert "reopen" in reason.lower(), "no action the user can actually take"

    @pytest.mark.unit
    def test_a_bundle_without_autoemulate_still_says_so(self, monkeypatch):
        reason = self._reason(monkeypatch, autoemulate=False)

        assert "pip install" in reason, (
            "the ordinary bundle really has no autoemulate, and installing it is the fix"
        )

    @pytest.mark.unit
    def test_the_probe_does_not_import_autoemulate(self, monkeypatch):
        """It runs on a path that is already failing, and importing autoemulate drags torch."""
        called = []
        monkeypatch.setattr(so.importlib, "import_module",
                            lambda *a, **k: called.append(a) or (_ for _ in ()).throw(
                                AssertionError("imported autoemulate to answer a message")))

        so._autoemulate_importable()

        assert not called


@pytest.mark.unit
def test_the_autoemulate_probe_reports_what_find_spec_says(monkeypatch):
    """The branching tests stub this out, so without this nothing covers the probe itself.

    Driven through ``find_spec`` rather than the real environment: asserting on whether
    *this* machine has autoemulate would pass or fail depending on the venv, which is the
    kind of test that is green on CI for the wrong reason.
    """
    monkeypatch.setattr(so.importlib.util, "find_spec",
                        lambda name: object() if name == "autoemulate" else None)
    assert so._autoemulate_importable() is True

    monkeypatch.setattr(so.importlib.util, "find_spec", lambda name: None)
    assert so._autoemulate_importable() is False

    def broken(name):
        raise ValueError("half-installed autoemulate")

    monkeypatch.setattr(so.importlib.util, "find_spec", broken)
    assert so._autoemulate_importable() is False, (
        "a broken install must read as unusable, not raise on a path that is already "
        "reporting a failure"
    )
