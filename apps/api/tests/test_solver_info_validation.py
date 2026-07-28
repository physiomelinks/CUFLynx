"""solver_info keys must be honoured or rejected, never silently ignored.

CA advertises MaximumNumberOfSteps for CVODE_myokit (the shared CVODE-family
field list), but myokit.Simulation has no max-step-count knob — only
set_max_step_size / set_min_step_size / set_tolerance — so myokit_helper never
reads it. The Settings form rendered a control that did nothing, and CUFLynx
seeded it into every run as a default besides.

The CA-side fix is to give CVODE_myokit its own field list; these cover the
CUFLynx side, which also has to stop hardcoding the key in four places.
"""

from __future__ import annotations

import engine as engine_mod
import pytest
import solver_options as so


# ---------------------------------------------------------------------------
# Nothing hardcodes the inert key any more
# ---------------------------------------------------------------------------
def test_default_solver_info_carries_only_settings_the_default_solver_honours():
    assert "MaximumNumberOfSteps" not in engine_mod.DEFAULT_SOLVER_INFO
    assert "MaximumStep" in engine_mod.DEFAULT_SOLVER_INFO
    so.check_solver_info(engine_mod.DEFAULT_SOLVER, dict(engine_mod.DEFAULT_SOLVER_INFO))


@pytest.mark.parametrize(
    "runner", ["calibration_runner.py", "sensitivity_runner.py", "uq_runner.py"]
)
def test_runners_do_not_seed_the_inert_key(runner):
    """The runners build solver_info for the subprocess; they used to setdefault
    it there too, so removing it from the engine default alone was not enough."""
    from pathlib import Path

    src = (Path(__file__).resolve().parents[1] / runner).read_text()
    assert 'si.setdefault("MaximumNumberOfSteps"' not in src


def test_the_offline_fallback_form_does_not_offer_it_either():
    """The fallback schema is what a CA-less install shows; it must agree."""
    schema = so._solver_info_schema(so.FALLBACK_SOLVER_SCHEMA["methods_by_solver"])
    keys = [f["key"] for f in schema["CVODE_myokit"]]
    assert "MaximumNumberOfSteps" not in keys
    assert "MaximumStep" in keys


# ---------------------------------------------------------------------------
# check_solver_info
# ---------------------------------------------------------------------------
def test_an_unhonoured_key_is_rejected_with_the_reason(client):
    with pytest.raises(ValueError) as exc:
        so.check_solver_info("CVODE_myokit", {"MaximumNumberOfSteps": 5000})
    msg = str(exc.value)
    assert "MaximumNumberOfSteps" in msg
    assert "does not support" in msg
    # Says what IS supported, so the message is actionable on its own.
    assert "MaximumStep" in msg
    # And why this particular key looks like it should work.
    assert "other CVODE backends" in msg


def test_supported_keys_pass(client):
    so.check_solver_info("CVODE_myokit", {"MaximumStep": 0.001, "rtol": 1e-8, "atol": 1e-8})


def test_framework_keys_pass(client):
    """solver/method/dt aren't integrator settings; CA allows them separately."""
    so.check_solver_info("CVODE_myokit", {"solver": "CVODE_myokit", "method": "CVODE", "dt": 0.01})


def test_an_unknown_solver_is_not_treated_as_accepting_nothing(client):
    """Otherwise an unreadable schema would reject every setting there is."""
    assert so.accepted_solver_info_keys("no_such_solver") is None
    so.check_solver_info("no_such_solver", {"whatever": 1})


def test_a_typo_is_caught_too(client):
    with pytest.raises(ValueError, match="MaxStep"):
        so.check_solver_info("CVODE_myokit", {"MaxStep": 0.001})


# ---------------------------------------------------------------------------
# filter_solver_info — for values that arrive rather than are chosen
# ---------------------------------------------------------------------------
def test_filter_drops_only_the_unhonoured_keys(client):
    out = so.filter_solver_info(
        "CVODE_myokit", {"MaximumStep": 1, "MaximumNumberOfSteps": 5000, "rtol": 1e-8}
    )
    assert out == {"MaximumStep": 1, "rtol": 1e-8}


def test_filter_leaves_an_unknown_solver_alone(client):
    si = {"anything": 1}
    assert so.filter_solver_info("no_such_solver", si) == si


# ---------------------------------------------------------------------------
# Through the API
# ---------------------------------------------------------------------------
def test_config_rejects_a_setting_the_solver_cannot_honour(client):
    resp = client.post(
        "/api/config",
        json={"solver_info": {"MaximumStep": 0.001, "MaximumNumberOfSteps": 5000}},
    )
    assert resp.status_code == 422, resp.text
    assert "MaximumNumberOfSteps" in resp.json()["detail"]


def test_config_accepts_the_settings_it_does_honour(client):
    resp = client.post(
        "/api/config", json={"solver_info": {"MaximumStep": 0.002, "rtol": 1e-6}}
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["solver_info"]["MaximumStep"] == 0.002


def test_a_rejected_config_leaves_the_engine_untouched(client):
    before = dict(engine_mod.engine.solver_info)
    client.post("/api/config", json={"solver_info": {"MaximumNumberOfSteps": 5000}})
    assert engine_mod.engine.solver_info == before


def test_the_settings_form_no_longer_offers_it(client):
    """The form is driven by the same schema as the validation, so a key that is
    rejected must not be rendered as an editable control."""
    schema = client.get("/api/config").json()["solver_info_schema"]
    keys = [f["key"] for f in schema["CVODE_myokit"]]
    assert "MaximumNumberOfSteps" not in keys
    assert "MaximumStep" in keys


# ---------------------------------------------------------------------------
# Startup restore must not hard-fail on an older saved config
# ---------------------------------------------------------------------------
def test_a_stale_persisted_setting_is_dropped_not_fatal(client, monkeypatch):
    """Rejection is for a new choice; a config saved before the key became
    unsupported must still let the app start."""
    import main as main_mod
    import settings_store

    monkeypatch.setattr(
        settings_store,
        "load",
        lambda: {
            "solver": "CVODE_myokit",
            "solver_info": {"MaximumStep": 0.001, "MaximumNumberOfSteps": 5000},
        },
    )
    main_mod._restore_persisted_settings()
    assert engine_mod.engine.solver_info == {"MaximumStep": 0.001}
