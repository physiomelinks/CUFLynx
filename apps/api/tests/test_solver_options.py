"""Unit tests for the backend solver capabilities + AD gating logic.

These exercise the pure logic; the model_type/solver/method lists come from CA's
schema (or the built-in fallback). The casadi_python AD path itself is covered by
the integration tests.
"""

import sys
import types

import solver_options as so


def _build(diff):
    """Build an options payload from the fallback schema + a differentiability map."""
    return so._build_options(so.FALLBACK_SOLVER_SCHEMA, diff)


def _method_options(opts, solver):
    for field in opts["solver_info_schema"][solver]:
        if field["key"] == "method":
            return field["options"]
    return []


def test_get_solver_options_shape():
    """The payload exposes the format/solver/method/schema metadata, sourced from
    CA's schema (or the built-in fallback when CA is unavailable)."""
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


def test_cellml_only_defaults_to_myokit_not_opencor():
    """cellml_only must default to CVODE_myokit, not CA's CVODE_opencor: OpenCOR is
    a separate program most users don't have (and it isn't bundled), so defaulting
    to it makes a fresh simulate fail with 'OpenCOR ... is not available'."""
    opts = so.get_solver_options()
    if "cellml_only" in opts["model_formats"]:
        assert opts["default_solver_by_format"]["cellml_only"] == "CVODE_myokit"


def test_method_options_come_from_ca_schema():
    """The method dropdown options mirror CA's methods_by_solver (not hardcoded)."""
    opts = so.get_solver_options()
    assert _method_options(opts, "casadi_integrator") == opts["methods_by_solver"]["casadi_integrator"]
    assert _method_options(opts, "solve_ivp") == opts["methods_by_solver"]["solve_ivp"]


def test_semi_implicit_euler_only_offered_for_casadi_python():
    """The dampened semi-implicit Euler is a casadi_python integrator method; it
    must not be offered as a solve_ivp (standard python) method."""
    opts = so.get_solver_options()
    assert "semi_implicit_euler" in _method_options(opts, "casadi_integrator")
    assert "semi_implicit_euler" not in _method_options(opts, "solve_ivp")


def test_dt_offered_for_every_solver():
    opts = so.get_solver_options()
    for solver in ("CVODE_myokit", "solve_ivp", "casadi_integrator"):
        keys = [f["key"] for f in opts["solver_info_schema"][solver]]
        assert "dt" in keys


def test_casadi_tolerance_fields_restricted_to_adaptive_methods():
    """reltol/abstol/max_num_steps apply to the adaptive CasADi plugins but not to
    the fixed-step semi_implicit_euler (which uses only dt)."""
    opts = so.get_solver_options()
    for field in opts["solver_info_schema"]["casadi_integrator"]:
        if field["key"] in ("reltol", "abstol", "max_num_steps"):
            assert "semi_implicit_euler" not in field["methods"]
            assert "cvodes" in field["methods"]


def test_casadi_max_step_field_offered_for_bdf_only():
    """The bdf integrator's internal sub-step cap (max_step) is an editable setting,
    scoped to 'bdf' only (other casadi methods don't consume it)."""
    opts = so.get_solver_options()
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
    # Full introspection carries no per-method gating (CA's schema doesn't model it).
    assert "methods" not in by_key["max_step"]


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
