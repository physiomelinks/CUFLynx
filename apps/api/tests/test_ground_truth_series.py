"""Recorded traces carried at weight 0, for drawing rather than fitting.

The SN_full obs_data files now carry the measured response of every experiment as
a ``series`` data_item with ``weight: 0`` -- the whole Vm or I_tot trace, not a
scalar reduced from it. The point is to have something to draw behind the model
on an output-vs-time plot: without it a simulated trace has nothing to be
compared against, and only the scalar features (a max, a spike frequency) appear
on the plot as isolated horizontal lines.

A zero weight is what keeps that free. CA drops a zero-weighted item from the
cost *and* from the denominator, so adding these traces must not move the number
the calibration minimises. This pins that: the panel still lists them, and the
cost is identical whether they are there or not.
"""

from __future__ import annotations

import obs_cost
import pytest


def _scalar(**over):
    item = {
        "data_item_name": "V_max",
        "operation": "max",
        "operands": ["soma_SN/V_sensed"],
        "value": 10.0,
        "std": 1.0,
        "weight": 1.0,
        "cost_type": "MSE",
        "experiment_idx": 0,
        "subexperiment_idx": 0,
    }
    item.update(over)
    return item


def _ground_truth(**over):
    """A recorded trace as the generators now emit it."""
    item = {
        "data_item_name": "Vgt_{Baseline0}",
        "trace_name_for_plotting": "Vgt_{Baseline0}",
        "data_type": "series",
        "operation": None,
        "operands": ["time", "soma_SN/V_sensed"],
        "unit": "milliV",
        "weight": 0,
        "std": 1.0,
        "plot_type": "series",
        "experiment_idx": 0,
        "subexperiment_idx": 0,
    }
    item.update(over)
    return item


def _mse(output, desired_mean, std, weight):
    return weight * (output - desired_mean) ** 2


@pytest.fixture
def funcs(monkeypatch):
    monkeypatch.setattr(obs_cost, "get_operation_funcs", lambda _d=None: {"max": max})
    monkeypatch.setattr(obs_cost, "get_cost_funcs", lambda _d=None: {"MSE": _mse})


OUTPUTS = {0: {"soma_SN/V_sensed": [1.0, 7.0, 3.0]}}


def test_a_ground_truth_trace_does_not_change_the_cost(funcs):
    """The reason weight 0 was chosen over leaving them out of the file."""
    without = obs_cost.evaluate([_scalar()], OUTPUTS)
    with_gt = obs_cost.evaluate([_scalar(), _ground_truth()], OUTPUTS)

    assert without is not None and with_gt is not None
    assert with_gt["cost"] == without["cost"]


def test_it_does_not_enter_the_denominator(funcs):
    """A mean over one more item would dilute the cost even at zero weight."""
    without = obs_cost.evaluate([_scalar()], OUTPUTS)
    with_gt = obs_cost.evaluate([_scalar(), _ground_truth()], OUTPUTS)

    assert with_gt["n_weighted"] == without["n_weighted"] == 1


def test_it_is_still_listed_so_the_panel_can_draw_it(funcs):
    """Excluded from the cost is not the same as excluded from the view -- being
    able to draw these is the entire reason they are in the file."""
    result = obs_cost.evaluate([_scalar(), _ground_truth()], OUTPUTS)

    labels = [entry["label"] for entry in result["items"]]
    assert "Vgt_{Baseline0}" in labels


def test_the_listed_entry_carries_no_cost(funcs):
    result = obs_cost.evaluate([_scalar(), _ground_truth()], OUTPUTS)

    entry = next(e for e in result["items"] if e["label"] == "Vgt_{Baseline0}")
    assert entry["cost"] is None
    assert entry["model"] is None


def test_many_ground_truth_traces_still_change_nothing(funcs):
    """One per experiment is what the generators emit, so the count grows with
    the study; the cost must not drift as it does."""
    without = obs_cost.evaluate([_scalar()], OUTPUTS)
    traces = [_ground_truth(data_item_name="Vgt_{exp%d}" % i, experiment_idx=0)
              for i in range(8)]
    with_gt = obs_cost.evaluate([_scalar(), *traces], OUTPUTS)

    assert with_gt["cost"] == without["cost"]
    assert with_gt["n_weighted"] == 1
    assert len(with_gt["items"]) == 9


def test_a_weighted_series_is_not_treated_as_ground_truth(funcs):
    """The subprotocol config can still ask for a *fitted* series. Weight is the
    only thing separating the two, so a non-zero one must still count."""
    fitted = _ground_truth(data_item_name="V_fitted", weight=1.0, value=7.0,
                           operation="max", operands=["soma_SN/V_sensed"])
    result = obs_cost.evaluate([_scalar(), fitted], OUTPUTS)

    assert result["n_weighted"] == 2


def test_a_run_of_only_ground_truth_scores_nothing(funcs):
    """Not zero -- "nothing to score" and "scores perfectly" must not look the
    same, which is why evaluate returns None rather than a cost of 0."""
    assert obs_cost.evaluate([_ground_truth()], OUTPUTS) is None
