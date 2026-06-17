"""Unit tests for the backend solver capabilities + AD gating logic.

These exercise the pure logic; the model_type/solver/method lists come from CA's
schema (or the built-in fallback). The casadi_python AD path itself is covered by
the integration tests.
"""

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
