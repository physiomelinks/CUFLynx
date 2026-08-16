"""AADC availability gating (issue #122).

AADC is optional, proprietary and licensed. CA imports it lazily, so a format
that needs it fails only once a run starts. CUFLynx therefore offers
``aadc_python`` only when the library is importable -- the same reasoning as the
OpenCOR exclusion -- and explains how to get it when it is not.
"""

from __future__ import annotations

import aadc_check
import solver_options as so


def test_status_reports_unavailable_without_the_library(monkeypatch):
    monkeypatch.setattr(aadc_check, "_importable", lambda module="aadc": False)
    monkeypatch.setattr(aadc_check, "_importable_in", lambda p, module="aadc": None)
    status = aadc_check.aadc_status()
    assert status["available"] is False
    # Not just "missing": how to obtain it, since it is licensed rather than pip-installable.
    assert "matlogica" in status["hint"].lower()
    assert status["licence_url"]


def test_available_when_either_interpreter_has_it(monkeypatch):
    """The live engine runs in-process while analysis runs in the user's Python,
    and a user may reasonably have AADC in only one."""
    monkeypatch.setattr(aadc_check, "_importable", lambda module="aadc": False)
    monkeypatch.setattr(aadc_check, "_importable_in", lambda p, module="aadc": True)
    assert aadc_check.aadc_status("/some/python")["available"] is True

    monkeypatch.setattr(aadc_check, "_importable", lambda module="aadc": True)
    monkeypatch.setattr(aadc_check, "_importable_in", lambda p, module="aadc": None)
    assert aadc_check.aadc_status()["available"] is True


def test_unknown_is_distinct_from_missing(monkeypatch):
    """No interpreter configured is not the same as "not installed there"."""
    monkeypatch.setattr(aadc_check, "_importable_in", lambda p, module="aadc": None)
    assert aadc_check.aadc_status(None)["in_analysis_python"] is None


def test_probe_does_not_import_the_licensed_library(monkeypatch):
    """Importing a licensed library can contact a licence server; a capability
    probe must not have side effects."""
    import importlib

    called = []
    monkeypatch.setattr(importlib, "import_module", lambda *a, **k: called.append(a))
    aadc_check._importable("aadc")
    assert called == []


def test_a_broken_install_counts_as_unavailable(monkeypatch):
    import importlib.util

    def boom(_name):
        raise ValueError("namespace clash")

    monkeypatch.setattr(importlib.util, "find_spec", boom)
    assert aadc_check._importable("aadc") is False


def test_a_bad_interpreter_is_unknown_not_false():
    assert aadc_check._importable_in("/no/such/python") is None
    assert aadc_check._importable_in("") is None


# ---------------------------------------------------------------------------
# Format gating
# ---------------------------------------------------------------------------
def test_the_format_is_hidden_without_the_library(client, monkeypatch):
    monkeypatch.setattr(aadc_check, "_importable", lambda module="aadc": False)
    monkeypatch.setattr(aadc_check, "_importable_in", lambda p, module="aadc": None)
    so.reset_cache()
    assert "aadc_python" not in client.get("/api/config").json()["model_formats"]


def test_the_format_appears_when_the_library_is_there(client, monkeypatch):
    monkeypatch.setattr(aadc_check, "_importable", lambda module="aadc": True)
    so.reset_cache()
    body = client.get("/api/config").json()
    formats = body["model_formats"]
    if "aadc_python" not in body.get("solvers_by_format", {}):
        # CA's schema drives which formats exist at all; an older CA without
        # aadc_python has nothing to offer and that is not this gate's business.
        return
    assert "aadc_python" in formats
    # Its solvers still come from CA, not from here.
    assert body["solvers_by_format"]["aadc_python"]


def test_config_reports_the_status_either_way(client):
    status = client.get("/api/config").json()["aadc"]
    assert set(status) >= {"available", "hint", "licence_url", "in_app"}


# The reports that found these: AADC pip-installed in a venv, selected as the
# analysis interpreter, and the format still absent -- and that venv showing no
# MPI status at all.
def test_the_format_gate_consults_the_analysis_interpreter(client, monkeypatch):
    """The gate probed only the app's own interpreter, so AADC installed in the
    interpreter that actually runs the analysis counted for nothing."""
    import calibration as calib_mod

    monkeypatch.setattr(aadc_check, "_importable", lambda module="aadc": False)
    monkeypatch.setattr(
        aadc_check, "_importable_in", lambda p, module="aadc": p == "/venv/bin/python"
    )
    monkeypatch.setattr(calib_mod.calibration, "python", "/venv/bin/python")
    so.reset_cache()

    status = aadc_check.aadc_status()  # no path given: must find the configured one
    assert status["available"] is True
    assert status["in_analysis_python"] is True


def test_the_gate_falls_back_to_in_app_when_no_interpreter_is_configured(client, monkeypatch):
    import calibration as calib_mod

    monkeypatch.setattr(aadc_check, "_importable", lambda module="aadc": True)
    monkeypatch.setattr(aadc_check, "_importable_in", lambda p, module="aadc": None)
    monkeypatch.setattr(calib_mod.calibration, "python", None)
    assert aadc_check.aadc_status()["available"] is True


# Found by running 3compartment with aadc_python + AD: calibration failed with
# "solver method 'adaptive_rk45' cannot be recorded on an AADC tape". CA lists
# adaptive_rk45 first, so that is what CUFLynx defaulted to -- a default the AD
# path can never use.
def test_aadc_ad_methods_are_limited_to_what_the_tape_can_record(client):
    opts = so.get_solver_options()
    methods = opts["methods_by_solver"].get("aadc_semi_implicit")
    if not methods:
        return  # CA without AADC support; nothing to constrain
    try:
        from ca_imports import ca_import

        ab = ca_import("param_id.aadc_backend")
    except Exception:  # pragma: no cover - older CA
        return
    suitable = opts["ad_suitable_methods"].get("aadc_semi_implicit")
    assert suitable, "AADC advertises no AD-suitable methods"

    # Tape-consistent, OR one of the methods that are AD-capable without being so:
    # cost_and_grad dispatches those to their own gradient implementation *before*
    # the tape check.
    #
    # That second set is read from CA rather than named here. CA keeps it as
    # AADC_BDF_AD_METHODS beside AADC_TAPE_CONSISTENT_METHODS for exactly this --
    # naming the members here meant every method CA added to it became a failure
    # of this test rather than a fact it had learned. 'semi_implicit_signed' was
    # the second time that happened.
    allowed = set(ab.TAPE_CONSISTENT_METHODS)
    try:
        from ca_imports import ca_from  # noqa: PLC0415

        AADC_BDF_AD_METHODS = ca_from(
            "parsers.PrimitiveParsers", "AADC_BDF_AD_METHODS")

        allowed |= set(AADC_BDF_AD_METHODS)
    except ImportError:
        # A CA predating the tuple: fall back to the constants it did expose.
        for name in ("BDF_NEWTON_METHOD", "BDF_TAPE_METHOD", "BDF_KERNEL_METHOD"):
            method = getattr(ab, name, None)
            if method:
                allowed.add(method)
    assert set(suitable) <= allowed, f"{sorted(set(suitable) - allowed)} have no AD path in CA"

    # An adaptive integrator picks its steps from the state, so the recorded
    # operation sequence does not replay -- it must never be offered for AD.
    assert "adaptive_rk45" not in suitable


def test_the_aadc_default_method_is_one_the_tape_can_record(client):
    opts = so.get_solver_options()
    if not opts["methods_by_solver"].get("aadc_semi_implicit"):
        return
    default = opts["default_method_by_solver"].get("aadc_semi_implicit")
    suitable = opts["ad_suitable_methods"].get("aadc_semi_implicit") or []
    assert default in suitable, "the default method cannot be used with AD"


def test_other_solvers_ad_methods_are_untouched(client):
    """The constraint is AADC's; nothing else should be narrowed by it."""
    opts = so.get_solver_options()
    casadi = opts["ad_suitable_methods"].get("casadi_integrator")
    if casadi:
        assert "collocation" in casadi or "bdf" in casadi


# Reported after picking aadc_python: every live simulation failed, including
# the first one after a restart, because the choice is persisted. The app was
# unusable on open until the format was changed back by hand.
def test_a_backend_this_process_cannot_run_falls_back_for_live_plots(monkeypatch):
    import engine as engine_mod

    monkeypatch.setattr(engine_mod, "backend_importable", lambda fmt: fmt != "aadc_python")
    eng = engine_mod.SimulationEngine()
    eng.model_type, eng.solver = "aadc_python", "aadc_semi_implicit"

    model_type, solver, fell_back = eng.live_backend()
    assert model_type != "aadc_python"
    assert solver != "aadc_semi_implicit"
    # Named, so the caller can tell the user rather than quietly substituting.
    assert fell_back == "aadc_python"


def test_a_runnable_backend_is_left_alone(monkeypatch):
    import engine as engine_mod

    monkeypatch.setattr(engine_mod, "backend_importable", lambda fmt: True)
    eng = engine_mod.SimulationEngine()
    eng.model_type, eng.solver = "casadi_python", "casadi_integrator"
    assert eng.live_backend() == ("casadi_python", "casadi_integrator", None)


def test_nothing_importable_keeps_the_configured_choice(monkeypatch):
    """So the failure names the real problem rather than a substitute we also
    cannot run."""
    import engine as engine_mod

    monkeypatch.setattr(engine_mod, "backend_importable", lambda fmt: False)
    eng = engine_mod.SimulationEngine()
    eng.model_type, eng.solver = "aadc_python", "aadc_semi_implicit"
    assert eng.live_backend() == ("aadc_python", "aadc_semi_implicit", None)


def test_the_helper_cache_is_keyed_on_the_backend_actually_used(client, fake_helper, monkeypatch):
    """Falling back must not hand back a helper compiled for the format we could
    not run."""
    import engine as engine_mod

    assert isinstance(engine_mod.engine._helpers, dict)
    # Keys are (model_id, model_type, solver) tuples, not bare model ids.
    eng = engine_mod.SimulationEngine()
    eng._helpers[("m", "cellml", "CVODE_myokit")] = object()
    assert ("m", "cellml", "CVODE_myokit") in eng._helpers


def test_backend_importable_does_not_import_the_library(tmp_path, monkeypatch):
    """Probing must not pull a heavy or licensed library into the API process.

    Tested with a module that explodes if imported, rather than by naming a real
    backend: the first version asserted `backend_importable("python") is True`,
    which is a fact about whether scipy happens to be installed. CI's unit job
    installs no scipy, so it failed there while passing everywhere scipy exists
    -- and it never checked the thing the docstring promises.
    """
    import sys

    import engine as engine_mod

    canary = tmp_path / "cuflynx_probe_canary.py"
    canary.write_text("raise RuntimeError('backend_importable imported me')\n")
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.setitem(engine_mod._BACKEND_MODULE, "canary_format", "cuflynx_probe_canary")

    # Found on the path...
    assert engine_mod.backend_importable("canary_format") is True
    # ...and never executed. Importing it would raise, and the caller swallows
    # ImportError, so a probe that imports shows up as either an error or a
    # False -- both of which fail this.
    assert "cuflynx_probe_canary" not in sys.modules


def test_an_unknown_format_is_assumed_runnable():
    """We do not have a module name for it, so refusing would block a backend
    circulatory_autogen supports and we simply have not heard of."""
    import engine as engine_mod

    assert engine_mod.backend_importable("no_such_format") is True


def test_a_backend_whose_library_is_absent_is_reported_absent(monkeypatch):
    import engine as engine_mod

    monkeypatch.setitem(
        engine_mod._BACKEND_MODULE, "canary_format", "cuflynx_no_such_module_anywhere"
    )
    assert engine_mod.backend_importable("canary_format") is False


def test_a_backend_whose_library_is_present_is_reported_present(monkeypatch):
    """Pinned to the standard library, so the answer does not depend on what the
    machine running the tests happens to have installed."""
    import engine as engine_mod

    monkeypatch.setitem(engine_mod._BACKEND_MODULE, "canary_format", "json")
    assert engine_mod.backend_importable("canary_format") is True
