"""Unit tests for the backend solver capabilities + AD gating logic.

These exercise the pure logic; the model_type/solver/method lists come from CA's
schema (or the built-in fallback). The casadi_python AD path itself is covered by
the integration tests.
"""

import sys
import types

import pytest

import solver_options as so

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
    # CA really does default cellml_only to the OpenCOR solver, so this exercises
    # the substitution branch rather than assuming it away.
    "default_solver_by_model_type": {
        "cellml_only": "CVODE_opencor",
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
    assert set(opts["model_formats"]) <= {"cellml_only", "python", "casadi_python"}
    for solver in ("CVODE_myokit", "solve_ivp", "casadi_integrator"):
        assert solver in opts["solver_info_schema"]


@BOTH_SCHEMAS
def test_cvode_opencor_not_offered_because_no_opencor_bundled(schema):
    """CUFLynx does not bundle OpenCOR, so CVODE_opencor must never be surfaced:
    not as a selectable solver, not as the cellml_only default, and not in the
    solver_info schema. CellML runs through Myokit's CVODE instead.

    Both schemas name CVODE_opencor as a cellml_only solver *and* its default, so
    each exercises the substitution branch."""
    opts = so._build_options(schema, {"max": True})
    for solvers in opts["solvers_by_format"].values():
        assert "CVODE_opencor" not in solvers
    assert "CVODE_opencor" not in opts["solver_info_schema"]
    assert "CVODE_opencor" not in opts["methods_by_solver"]
    # cellml_only falls back to the Myokit CVODE that CUFLynx can actually run.
    assert opts["default_solver_by_format"]["cellml_only"] == "CVODE_myokit"
    assert "CVODE_myokit" in opts["solvers_by_format"]["cellml_only"]


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
    assert so.ad_available("cellml_only", diff_all) is False


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
    # cellml_only + CVODE_myokit: FSA; other solver / model types: FD only.
    assert _values(so.gradient_sources("cellml_only", "CVODE_myokit", True)) == ["FD", "FSA"]
    assert _values(so.gradient_sources("cellml_only", "CVODE_opencor", True)) == ["FD"]
    assert _values(so.gradient_sources("python", "solve_ivp", True)) == ["FD"]

    # Descriptors carry the do_ad / requires_all_differentiable flags the UI + gate use.
    ad = next(s for s in so.gradient_sources("casadi_python", None, True) if s["value"] == "AD")
    assert ad["do_ad"] is True and ad["requires_all_differentiable"] is True
    fsa = next(s for s in so.gradient_sources("cellml_only", "CVODE_myokit", True)
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
    assert _values(so.gradient_sources("cellml_only", "CVODE_myokit", True, "CVODE")) == ["FD", "FSA"]
    assert _values(so.gradient_sources("cellml_only", "CVODE_myokit", True, "other")) == ["FD"]


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
    monkeypatch.setitem(sys.modules, "parsers.PrimitiveParsers", fake_mod)
    monkeypatch.setattr(so, "_ensure_ca_path", lambda: None)

    # Gate keeps the requires_all_differentiable source when all ops differentiable...
    assert _values(so.gradient_sources("casadi_python", "casadi_integrator", True)) == ["FD", "AD"]
    assert calls["args"] == ("casadi_python", "casadi_integrator")
    # ...and drops it otherwise.
    assert _values(so.gradient_sources("casadi_python", "casadi_integrator", False)) == ["FD"]


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
    monkeypatch.setitem(sys.modules, "parsers.PrimitiveParsers", fake_mod)
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
        "mcmc": {
            "label": "MCMC", "enable_flag": "do_mcmc", "options_key": "mcmc_options",
            "options": [{"name": "num_steps", "type": "int", "default": 5000}],
        },
    }
    fake_mod = types.SimpleNamespace(ANALYSIS_OPTIONS=fake)
    monkeypatch.setitem(sys.modules, "parsers.PrimitiveParsers", fake_mod)
    monkeypatch.setattr(so, "_ensure_ca_path", lambda: None)
    so.reset_cache()

    ao = so.get_analysis_options(refresh=True)
    assert set(ao) == {"sensitivity_analysis", "mcmc"}
    assert ao["mcmc"]["options_key"] == "mcmc_options"
    assert so.analysis_mode_options("mcmc")[0]["name"] == "num_steps"
    # num_steps default flows through untouched from CA.
    assert so.analysis_mode_options("mcmc")[0]["default"] == 5000


def test_analysis_options_fall_back_for_older_ca(monkeypatch):
    """An older CA without ANALYSIS_OPTIONS (introspection raises) degrades to the
    built-in blocks so the SA/UQ panels still render."""
    def _boom():
        raise ImportError("cannot import name 'ANALYSIS_OPTIONS'")

    monkeypatch.setattr(so, "_introspect_analysis_options", _boom)
    so.reset_cache()
    ao = so.get_analysis_options(refresh=True)
    assert {"sensitivity_analysis", "mcmc", "identifiability_analysis"} <= set(ao)
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
