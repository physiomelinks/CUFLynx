"""Calling an operation, and saying in obs_data what it was called on.

The regression that matters most here is the range one. The CLI decides whether a
feature takes a time range by ``name.endswith("_in_range")``, which is false for
``calc_spike_count_windowed`` and the whole ``mean_in_range_*`` family -- all of
which accept ``start_frac``/``end_frac`` and therefore silently receive the
defaults. A range the user carefully drew is never applied, and nothing says so.
"""

from __future__ import annotations

import numpy as np
import pytest

from obs_extract import ObsExtractError, accepts_range, kwarg_defaults, plan_call
from obs_extract.features import evaluate

pytestmark = pytest.mark.unit


# Stand-ins with the same signatures as CA's, so these tests need no CA.
def op_t_first(t, V, series_output=False, spike_min_thresh=-10):
    return float(np.max(V))


def op_x_first(x, start_frac=0.0, end_frac=1.0, series_output=False):
    n = len(x)
    return float(np.max(x[int(n * start_frac):max(int(n * end_frac), 1)]))


def op_windowed(t, V, series_output=False, spike_min_thresh=-10,
                start_frac=0.0, end_frac=1.0):
    """Named like CA's ``calc_spike_count_windowed`` -- no ``_in_range`` suffix,
    but it takes the fractions."""
    n = len(V)
    return float(np.sum(V[int(n * start_frac):max(int(n * end_frac), 1)] > 0))


def op_late_default(x, start_frac=0.8, end_frac=1.0, series_output=False):
    """Like ``mean_in_range_minus_initial``: its own default is not 0..1."""
    n = len(x)
    return float(np.mean(x[int(n * start_frac):max(int(n * end_frac), 1)]))


def op_no_kwargs(x):
    return float(np.mean(x))


# ---------------------------------------------------------------------------
def test_operands_and_call_come_from_one_signature_walk():
    """They must agree; deriving them twice is how they stop agreeing."""
    plan = plan_call("op_t_first", op_t_first, "soma/V")
    assert plan.takes_time is True
    assert plan.operands == ["time", "soma/V"]

    plan2 = plan_call("op_x_first", op_x_first, "soma/V")
    assert plan2.takes_time is False
    assert plan2.operands == ["soma/V"]


def test_the_plan_calls_the_function_the_way_its_operands_say():
    t = np.linspace(0, 1, 10)
    x = np.arange(10, dtype=float)
    assert plan_call("a", op_t_first, "soma/V")(t, x) == 9.0
    assert plan_call("b", op_x_first, "soma/V")(t, x) == 9.0


@pytest.mark.parametrize(
    "fn,expected", [(op_x_first, True), (op_windowed, True),
                    (op_late_default, True), (op_t_first, False),
                    (op_no_kwargs, False)],
)
def test_a_range_is_offered_on_the_signature_not_the_name(fn, expected):
    """``op_windowed`` is the regression: no ``_in_range`` suffix, takes fractions."""
    assert accepts_range(fn) is expected


def test_a_windowed_operation_receives_the_configured_fractions():
    """The CLI's name rule would have left these at 0.0/1.0."""
    plan = plan_call("op_windowed", op_windowed, "soma/V",
                     kwargs={"start_frac": 0.5, "end_frac": 1.0})
    assert plan.kwargs["start_frac"] == 0.5
    assert plan.kwargs["end_frac"] == 1.0

    V = np.array([-1.0] * 5 + [1.0] * 5)
    assert plan(np.arange(10.0), V) == 5.0, "only the second half was counted"


def test_an_operations_own_defaults_are_reported_for_prefilling():
    """A GUI that pre-filled 0.0/1.0 everywhere would change what
    ``mean_in_range_minus_initial`` computes."""
    assert kwarg_defaults(op_late_default)["start_frac"] == 0.8
    assert kwarg_defaults(op_x_first)["start_frac"] == 0.0


def test_kwargs_are_filtered_to_what_the_operation_accepts():
    plan = plan_call("op_x_first", op_x_first, "soma/V",
                     kwargs={"start_frac": 0.2, "spike_min_thresh": -20,
                             "nonsense": 1})
    assert "start_frac" in plan.kwargs
    assert "spike_min_thresh" not in plan.kwargs, "the operation does not take it"
    assert plan.dropped == ["nonsense", "spike_min_thresh"]


def test_pipeline_kwargs_are_supplied_only_when_declared():
    with_thresh = plan_call("a", op_t_first, "soma/V")
    assert with_thresh.kwargs["spike_min_thresh"] == -10.0
    assert with_thresh.kwargs["series_output"] is False

    without = plan_call("b", op_no_kwargs, "soma/V")
    assert without.kwargs == {}


def test_a_user_kwarg_overrides_the_pipeline_default():
    plan = plan_call("a", op_t_first, "soma/V", kwargs={"spike_min_thresh": -30})
    assert plan.kwargs["spike_min_thresh"] == -30


def test_a_missing_operation_says_what_to_do():
    with pytest.raises(ObsExtractError, match="CA directory"):
        plan_call("gone", None, "soma/V")


def test_an_unbound_measured_variable_is_refused():
    with pytest.raises(ObsExtractError, match="needs a model variable"):
        plan_call("a", op_x_first, "")


# ---------------------------------------------------------------------------
def test_evaluate_returns_one_number():
    plan = plan_call("a", op_x_first, "soma/V")
    assert evaluate(plan, np.arange(5.0), np.arange(5.0)) == 4.0


def test_evaluate_passes_a_nan_through_for_the_caller_to_skip():
    """An operation asked for a spike time in a silent sweep has nothing to
    return; that is not an error, it is an item that is not emitted."""

    def silent(x):
        return float("nan")

    plan = plan_call("silent", silent, "soma/V")
    assert np.isnan(evaluate(plan, np.arange(3.0), np.arange(3.0)))


def test_evaluate_refuses_a_series_where_a_scalar_was_asked_for():
    def series(x):
        return np.asarray(x) * 2

    plan = plan_call("series", series, "soma/V")
    with pytest.raises(ObsExtractError, match="returned 5 values"):
        evaluate(plan, np.arange(5.0), np.arange(5.0))


# ---------------------------------------------------------------------------
@pytest.mark.integration
def test_against_cas_real_registry():
    """The stand-ins above mirror CA's signatures; this checks they still do."""
    from obs_options import get_operation_funcs

    funcs = get_operation_funcs()
    if not funcs:
        pytest.skip("circulatory_autogen is not importable here")

    windowed = funcs.get("calc_spike_count_windowed")
    if windowed is None:
        pytest.skip("this CA has no calc_spike_count_windowed")
    assert accepts_range(windowed), (
        "calc_spike_count_windowed takes start_frac/end_frac despite its name; "
        "this is the case a name-suffix rule misses")

    late = funcs.get("mean_in_range_minus_initial")
    if late is not None:
        assert kwarg_defaults(late).get("start_frac") == 0.8, (
            "its default is not 0.0; pre-filling 0.0 would change what it computes")

    max_in_range = funcs.get("max_in_range")
    if max_in_range is not None:
        plan = plan_call("max_in_range", max_in_range, "soma/V")
        assert plan.operands == ["soma/V"], "x-first, so no time operand"
