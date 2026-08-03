"""A run that returns NaN is not a successful run (issue #175).

AADC's rk4 on the stiff 3compartment model does not raise and does not return
False: it walks the state off to 1e138, then to NaN, and hands the NaNs back as
an ordinary result. The API answered **200** with 1998 of 2001 samples serialised
as JSON ``null``, ``warnings`` absent, and the user got an empty plot with no
reason for it -- indistinguishable from a model that simply has nothing to show.

The other half of the same failure: CA *did* say what was wrong, in a
``UserWarning`` naming the stiffness and two solvers that work. It reached the
server log and stopped there.

Unit tier -- fake helpers, no Myokit.
"""

from __future__ import annotations

import math
import warnings

import engine as engine_mod
import pytest
from conftest import LV_MODEL_PATH, FakeHelper, upload_model

SIM_BODY = {"params": {}, "sim_time": 5}
NAN = float("nan")


class NaNHelper(FakeHelper):
    """Returns a trace that diverged: some or all samples non-finite."""

    def __init__(self, series, **kwargs):
        super().__init__(n=len(series), **kwargs)
        self.series = list(series)

    def get_results(self, variables, flatten=False):
        return [list(self.series)]


class WarningHelper(FakeHelper):
    """Runs fine, but warns the way CA's stiffness check does."""

    def __init__(self, message: str, times: int = 1, **kwargs):
        super().__init__(**kwargs)
        self.message = message
        self.times = times

    def run(self):
        for _ in range(self.times):
            warnings.warn(self.message, UserWarning, stacklevel=2)
        return True


def _simulate(client, helper):
    engine_mod.engine.helper_factory = lambda **kwargs: helper
    model_id = upload_model(client, LV_MODEL_PATH)["model_id"]
    return client.post("/api/simulate", json={"model_id": model_id, **SIM_BODY})


# ---------------------------------------------------------------------------
# divergence_report, on its own
# ---------------------------------------------------------------------------
def test_a_finite_run_says_nothing():
    assert engine_mod.divergence_report({"a": [1.0, 2.0]}) == (False, "")


def test_no_outputs_at_all_is_not_a_divergence():
    """An empty request is not a diverged run, and must not be reported as one."""
    assert engine_mod.divergence_report({}) == (False, "")
    assert engine_mod.divergence_report({"a": []}) == (False, "")


def test_an_entirely_nonfinite_run_is_fatal():
    fatal, message = engine_mod.divergence_report({"a": [NAN, NAN], "b": [NAN, NAN]})
    assert fatal
    assert "diverged" in message
    assert "4" in message  # every sample counted, so the scale is visible


def test_infinities_count_as_diverged_too():
    """Overflow reaches inf before it reaches NaN; both mean the same thing."""
    fatal, _ = engine_mod.divergence_report({"a": [math.inf, -math.inf]})
    assert fatal


def test_nulls_count_as_diverged():
    """NaN serialises to JSON null, so a value can arrive as None."""
    fatal, _ = engine_mod.divergence_report({"a": [None, None]})
    assert fatal


def test_a_partial_divergence_is_a_warning_not_a_failure():
    """The user should see where it went wrong, so the trace is kept."""
    fatal, message = engine_mod.divergence_report({"a": [1.0, 2.0, NAN, NAN]})
    assert not fatal
    assert "2 of 4" in message
    assert "a" in message


def test_one_diverged_series_among_good_ones_is_not_fatal():
    fatal, message = engine_mod.divergence_report({"a": [1.0], "b": [NAN]})
    assert not fatal
    assert "b" in message


# ---------------------------------------------------------------------------
# Through the API
# ---------------------------------------------------------------------------
def test_an_all_nan_run_fails_instead_of_returning_200(client):
    """The bug exactly: 200 with a body full of nulls."""
    resp = _simulate(client, NaNHelper([NAN] * 2001))
    assert resp.status_code == 500
    assert "diverged" in resp.json()["detail"]


def test_the_failure_names_the_settings_that_produced_it(client):
    """Same contract as every other failure (#138): the reason is only usable
    alongside the solver and step it was produced under."""
    resp = _simulate(client, NaNHelper([NAN] * 4))
    detail = resp.json()["detail"]
    assert "solver=CVODE_myokit" in detail
    assert "dt=0.01" in detail


def test_a_partly_nan_run_still_returns_its_trace_with_a_warning(client):
    resp = _simulate(client, NaNHelper([1.0, 2.0, NAN, NAN]))
    assert resp.status_code == 200
    body = resp.json()
    assert body["warnings"]
    assert "2 of 4" in body["warnings"][0]


def test_a_clean_run_carries_no_warnings_key(client):
    """Nothing to say means nothing said -- an always-present empty list would
    train the UI to ignore the field."""
    resp = _simulate(client, FakeHelper())
    assert resp.status_code == 200
    assert "warnings" not in resp.json()


# ---------------------------------------------------------------------------
# CA's warnings reach the client
# ---------------------------------------------------------------------------
STIFF = (
    "AADC stiffness check: the model appears STIFF over the first second of "
    "simulation. AADC's tape-consistent integrators are all fixed-step and are "
    "inaccurate or unstable on stiff systems, so both the forward solve and the "
    "AADC gradient are likely wrong here."
)


def test_a_warning_from_the_solver_reaches_the_response(client):
    resp = _simulate(client, WarningHelper(STIFF))
    assert resp.status_code == 200
    assert any("STIFF" in w for w in resp.json()["warnings"])


def test_a_warning_raised_every_step_is_reported_once(client):
    """A stiff model warns on each step; the user needs to read it once."""
    resp = _simulate(client, WarningHelper(STIFF, times=200))
    assert len(resp.json()["warnings"]) == 1


def test_a_warning_the_environment_would_suppress_is_still_reported(client):
    """Filters are ambient state: CA installs its own, and a user can start the
    app under PYTHONWARNINGS=ignore. Neither should get to decide whether the app
    can explain a wrong-looking trace, so the run forces its own filter.

    (The narrower "does a second run still report it" reads as a test of the same
    line but is not: entering catch_warnings invalidates the once-per-site
    registry by itself, so it passes either way.)"""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        resp = _simulate(client, WarningHelper(STIFF))
    assert any("STIFF" in w for w in resp.json()["warnings"])


def test_the_same_warning_is_still_reported_on_a_second_run(client):
    """The user who reruns to check is the one most likely to be misled."""
    helper = WarningHelper(STIFF)
    first = _simulate(client, helper)
    second = _simulate(client, helper)
    assert first.json()["warnings"] == second.json()["warnings"] != []


def test_a_diverged_run_reports_the_solvers_reason_alongside_our_own(client):
    """Both halves: CA says *why* it will be wrong, we say *that* it is."""

    class Both(NaNHelper):
        def run(self):
            warnings.warn(STIFF, UserWarning, stacklevel=2)
            return True

    resp = _simulate(client, Both([1.0, NAN]))
    body = resp.json()
    assert any("STIFF" in w for w in body["warnings"])
    assert any("NaN" in w for w in body["warnings"])


# ---------------------------------------------------------------------------
# The worker path must agree with the in-process one
# ---------------------------------------------------------------------------
def test_the_worker_path_applies_the_same_check(monkeypatch):
    """It runs in another interpreter; what counts as a usable result must not
    depend on which one."""
    eng = engine_mod.SimulationEngine()
    monkeypatch.setattr(
        eng,
        "_worker_call",
        lambda *a, **k: {"result": {"time": [0.0], "outputs": {"a": [NAN]}}, "warnings": []},
    )
    with pytest.raises(engine_mod.SimulationError, match="diverged"):
        eng.simulate(
            model_id="m", model_path="p", params={}, sim_time=1.0, pre_time=0.0, outputs=["a"]
        )


def test_the_worker_path_forwards_its_warnings(monkeypatch):
    eng = engine_mod.SimulationEngine()
    monkeypatch.setattr(
        eng,
        "_worker_call",
        lambda *a, **k: {
            "result": {"time": [0.0], "outputs": {"a": [1.0]}},
            "warnings": [STIFF],
        },
    )
    result = eng.simulate(
        model_id="m", model_path="p", params={}, sim_time=1.0, pre_time=0.0, outputs=["a"]
    )
    assert result["warnings"] == [STIFF]


def test_a_protocol_runs_experiments_are_checked_too(monkeypatch):
    """A protocol run keeps its outputs per experiment, so the single-run shape
    check would have looked straight past a diverged experiment."""
    eng = engine_mod.SimulationEngine()
    monkeypatch.setattr(
        eng,
        "_worker_call",
        lambda *a, **k: {
            "result": {"experiments": [{"time": [0.0], "outputs": {"a": [NAN]}}]},
            "warnings": [],
        },
    )
    with pytest.raises(engine_mod.SimulationError, match="diverged"):
        eng.run_protocol(
            model_id="m", model_path="p", protocol_info={}, params={}, outputs=["a"]
        )


def test_unrelated_warning_noise_is_not_reported_to_the_user(client):
    """A blanket simplefilter('always') also un-ignores DeprecationWarning and
    ResourceWarning. Those are ignored by default for good reason: an unclosed
    file in some dependency is not something to show beside "your model is
    diverging", and a banner full of it teaches the user to ignore the banner."""

    class Noisy(FakeHelper):
        def run(self):
            warnings.warn("unclosed file", ResourceWarning, stacklevel=2)
            warnings.warn("obsolete api", DeprecationWarning, stacklevel=2)
            warnings.warn(STIFF, UserWarning, stacklevel=2)
            return True

    body = _simulate(client, Noisy()).json()
    assert body["warnings"] == [STIFF]


def test_an_overflow_notice_counts_as_a_solver_warning(client):
    """numpy raises RuntimeWarning for overflow, which is the first sign of the
    divergence that ends in NaN -- the earliest thing the user could act on."""

    class Overflowing(FakeHelper):
        def run(self):
            warnings.warn("overflow encountered in scalar multiply", RuntimeWarning, stacklevel=2)
            return True

    body = _simulate(client, Overflowing()).json()
    assert any("overflow" in w for w in body["warnings"])


def test_the_divergence_note_leads_the_warnings(client):
    """A real AADC run came back with seven: a licence notice, a stiffness
    check, four numpy overflow notices, and -- last -- the one saying this trace
    is unusable. The banner joins them, so order is what gets read."""

    class Both(NaNHelper):
        def run(self):
            warnings.warn("AADC is THIRD-PARTY PROPRIETARY software", UserWarning, stacklevel=2)
            warnings.warn(STIFF, UserWarning, stacklevel=2)
            return True

    body = _simulate(client, Both([1.0, NAN, NAN])).json()
    assert "NaN" in body["warnings"][0]
    assert len(body["warnings"]) == 3
