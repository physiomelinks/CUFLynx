"""Informative failure reporting for simulation runs (issue #138).

A failed run used to reach the browser as "AxiosError: Request failed with
status code 500" and nothing else. Two separate holes produced that:

* the solver's reason was never in the response — CA's Myokit helper catches the
  error, *prints* it and returns False, so an exception could not carry it;
* the protocol route let ``RuntimeError('Protocol simulation failed.')`` escape,
  and an unhandled exception yields a body-less 500, so the frontend had no
  ``detail`` field to show at all.

These run on the unit tier (fake helper, no Myokit); the real-solver end of it
lives in test_3compartment.py.
"""

from __future__ import annotations

import engine as engine_mod
import pytest
from conftest import LV_MODEL_PATH, FakeHelper, upload_model

SIM_BODY = {"params": {}, "sim_time": 5}


class FailingHelper(FakeHelper):
    """Fails the way CA's Myokit helper does: print the reason, return False."""

    def __init__(self, message: str, **kwargs):
        super().__init__(**kwargs)
        self.message = message

    def run(self):
        print(self.message)
        return False


class RaisingHelper(FakeHelper):
    """Fails by raising, as model compilation / unit conversion does."""

    def __init__(self, exc: Exception, printed: str = "", **kwargs):
        super().__init__(**kwargs)
        self.exc = exc
        self.printed = printed

    def run(self):
        if self.printed:
            print(self.printed)
        raise self.exc


def _install(helper):
    engine_mod.engine.helper_factory = lambda **kwargs: helper
    return helper


def _simulate(client, helper):
    _install(helper)
    model_id = upload_model(client, LV_MODEL_PATH)["model_id"]
    return client.post("/api/simulate", json={"model_id": model_id, **SIM_BODY})


CVODE_MSG = (
    "Myokit simulation failed: Function CVode() failed with flag CV_TOO_MUCH_ACC: "
    "The solver could not satisfy the accuracy demanded by the user for some "
    "internal step."
)


# ---------------------------------------------------------------------------
# The reason reaches the client
# ---------------------------------------------------------------------------
def test_failure_detail_quotes_the_solvers_own_words(client):
    resp = _simulate(client, FailingHelper(CVODE_MSG))
    assert resp.status_code == 500
    detail = resp.json()["detail"]
    assert "CV_TOO_MUCH_ACC" in detail
    assert detail != "simulation failed"


def test_failure_detail_names_the_settings_it_failed_under(client):
    """Which is what distinguishes "my MaximumStep is wrong" from "my model is"."""
    resp = _simulate(client, FailingHelper(CVODE_MSG))
    detail = resp.json()["detail"]
    assert "solver=CVODE_myokit" in detail
    assert "MaximumStep=0.001" in detail  # the default in force
    assert "dt=0.01" in detail


def test_failure_detail_suggests_what_to_change(client):
    resp = _simulate(client, FailingHelper(CVODE_MSG))
    # CV_TOO_MUCH_ACC -> loosen tolerance, not "lower MaximumStep".
    assert "rtol/atol" in resp.json()["detail"]


@pytest.mark.parametrize(
    "printed,expected",
    [
        ("Myokit simulation failed: CV_TOO_MUCH_WORK at t = 0.5", "MaximumNumberOfSteps"),
        ("Myokit simulation failed: CV_CONV_FAILURE", "converge"),
        ("Myokit simulation failed: CV_RHSFUNC_FAIL, nan in rhs", "non-finite"),
        ("Error: units conversion between mmHg and J_per_m3 failed", "unit/conversion"),
    ],
)
def test_hint_matches_the_kind_of_failure(client, printed, expected):
    detail = _simulate(client, FailingHelper(printed)).json()["detail"]
    assert expected in detail


def test_says_so_plainly_when_the_solver_gave_no_reason(client):
    """A silent False must not be dressed up as a diagnosed failure."""
    resp = _simulate(client, FailingHelper(""))
    detail = resp.json()["detail"]
    assert "no reason" in detail
    assert "server log" in detail
    # Still worth reporting the settings.
    assert "solver=CVODE_myokit" in detail


def test_progress_chatter_is_not_mistaken_for_the_reason(client):
    """CA prints progress lines too; quoting one as the cause would mislead."""
    helper = FailingHelper("Running experiments - dt=0.01\nExperiment 0 completed.")
    detail = _simulate(client, helper).json()["detail"]
    assert "Experiment 0 completed" not in detail
    assert "no reason" in detail


# ---------------------------------------------------------------------------
# Nothing escapes as a body-less 500 (the reported symptom)
# ---------------------------------------------------------------------------
def test_a_raising_helper_still_returns_a_detail(client):
    resp = _simulate(client, RaisingHelper(RuntimeError("Protocol simulation failed.")))
    assert resp.status_code == 500
    assert "detail" in resp.json()
    assert "Protocol simulation failed." in resp.json()["detail"]


def test_printed_reason_beats_an_uninformative_exception(client):
    """CA prints the cause and raises a summary, so the print is the better text."""
    helper = RaisingHelper(RuntimeError("Protocol simulation failed."), printed=CVODE_MSG)
    detail = _simulate(client, helper).json()["detail"]
    assert "CV_TOO_MUCH_ACC" in detail


def test_an_unexpected_exception_type_is_still_described(client):
    detail = _simulate(client, RaisingHelper(ZeroDivisionError("division by zero"))).json()[
        "detail"
    ]
    assert "ZeroDivisionError" in detail
    assert "division by zero" in detail


# ---------------------------------------------------------------------------
# The console keeps its output
# ---------------------------------------------------------------------------
def test_solver_output_still_reaches_the_console(client, capsys):
    """Capturing the reason must not silence the live progress of a long run."""
    _simulate(client, FailingHelper(CVODE_MSG))
    assert "CV_TOO_MUCH_ACC" in capsys.readouterr().out


def test_a_successful_run_prints_through_untouched(client, capsys):
    class ChattyHelper(FakeHelper):
        def run(self):
            print("Experiment 0 completed. Remaining: 0")
            return True

    resp = _simulate(client, ChattyHelper())
    assert resp.status_code == 200
    assert "Experiment 0 completed" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# Message building, directly
# ---------------------------------------------------------------------------
def test_reason_extraction_keeps_only_the_last_failure_block():
    captured = "\n".join(
        ["Running experiments", "Myokit simulation failed: first", "progress",
         "Myokit simulation failed: second"]
    )
    reason = engine_mod._solver_reason(captured)
    assert "second" in reason
    assert "progress" not in reason


def test_reason_extraction_caps_a_runaway_log():
    reason = engine_mod._solver_reason("failed: " + "x" * 5000)
    assert len(reason) <= engine_mod._MAX_REASON_CHARS + 3


def test_reason_extraction_is_empty_without_a_failure():
    assert engine_mod._solver_reason("Experiment 0 completed. Remaining: 0") == ""
    assert engine_mod._solver_reason("") == ""
