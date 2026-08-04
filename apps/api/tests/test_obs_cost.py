"""What the current parameters cost, against the loaded obs_data (issue #159).

Manual exploration had no number attached: you moved a slider, the trace moved,
and whether it moved *towards* the data was left to the eye.
"""

from __future__ import annotations

import obs_cost
import pytest


def _item(**over):
    item = {
        "variable": "pressure",
        "name_for_plotting": "u_{AR}",
        "operation": "max",
        "operands": ["a/u"],
        "value": 10.0,
        "std": 1.0,
        "weight": 1.0,
        "cost_type": "MSE",
    }
    item.update(over)
    return item


def _funcs(monkeypatch, *, ops=None, costs=None):
    monkeypatch.setattr(obs_cost, "get_operation_funcs", lambda _d=None: ops if ops is not None else {"max": max})
    monkeypatch.setattr(
        obs_cost,
        "get_cost_funcs",
        lambda _d=None: costs if costs is not None else {"MSE": lambda o, d, s, w: w * (o - d) ** 2},
    )


def test_it_scores_the_run_against_the_data(monkeypatch):
    _funcs(monkeypatch)
    out = obs_cost.evaluate([_item()], {0: {"a/u": [1.0, 9.0, 3.0]}})
    assert out["cost"] == pytest.approx(1.0)
    (entry,) = out["items"]
    assert entry["model"] == 9.0
    assert entry["observed"] == 10.0


def test_the_errors_match_what_a_calibration_saves(monkeypatch):
    """percent_error_vec.npy and std_error_vec.npy, so a manual perturbation and
    a best fit can be put on the same axes."""
    _funcs(monkeypatch)
    (entry,) = obs_cost.evaluate([_item(std=2.0)], {0: {"a/u": [9.0]}})["items"]
    assert entry["percent_error"] == pytest.approx(-10.0)
    assert entry["std_error"] == pytest.approx(-0.5)


def test_each_item_is_scored_in_its_own_experiment(monkeypatch):
    """A data_item names the experiment it belongs to; scoring it against
    another one's trace would be quietly wrong."""
    _funcs(monkeypatch)
    items = [_item(value=1.0), _item(value=5.0, experiment_idx=1)]
    out = obs_cost.evaluate(items, {0: {"a/u": [1.0]}, 1: {"a/u": [5.0]}})
    assert out["cost"] == pytest.approx(0.0)


def test_time_resolves_to_the_run_time_vector(monkeypatch):
    """`time` is an operand of every windowed or peak-timing operation, and it is
    returned beside the outputs rather than in them. Missed, every such
    observable goes unscored -- which looked like a better fit than it was."""
    _funcs(monkeypatch, ops={"first": lambda t, y: t[0]})
    item = _item(operation="first", operands=["time", "a/u"], value=0.0)
    (entry,) = obs_cost.evaluate([item], {0: {"a/u": [1.0], "time": [7.0]}})["items"]
    assert entry["model"] == 7.0


def test_the_weight_counts(monkeypatch):
    _funcs(monkeypatch)
    light = obs_cost.evaluate([_item(weight=1.0)], {0: {"a/u": [9.0]}})["cost"]
    heavy = obs_cost.evaluate([_item(weight=4.0)], {0: {"a/u": [9.0]}})["cost"]
    assert heavy == pytest.approx(4 * light)


def test_an_unrecorded_operand_leaves_that_item_unscored(monkeypatch):
    """Reported as unscored rather than as zero: an observable nobody measured
    must not look like a perfect one."""
    _funcs(monkeypatch)
    out = obs_cost.evaluate([_item(), _item(operands=["a/missing"])], {0: {"a/u": [9.0]}})
    assert [i["cost"] is None for i in out["items"]] == [False, True]
    # 1.0 over the two weighted observables: the unscorable one still counts in
    # the denominator, because CA scores it (#181).
    assert out["cost"] == pytest.approx(0.5)


def test_nothing_scorable_is_none_not_zero(monkeypatch):
    """"Perfect fit" and "could not tell" must not look the same."""
    _funcs(monkeypatch)
    assert obs_cost.evaluate([_item(operands=["a/missing"])], {0: {}}) is None


def test_no_data_items_is_none(monkeypatch):
    _funcs(monkeypatch)
    assert obs_cost.evaluate([], {0: {"a/u": [1.0]}}) is None


def test_without_ca_there_is_no_cost(monkeypatch):
    """The cost has to be CA's own function; inventing one here would look
    authoritative while ranking parameter sets differently."""
    monkeypatch.setattr(obs_cost, "get_cost_funcs", lambda _d=None: None)
    monkeypatch.setattr(obs_cost, "get_operation_funcs", lambda _d=None: {"max": max})
    assert obs_cost.evaluate([_item()], {0: {"a/u": [9.0]}}) is None


def test_a_cost_func_that_raises_does_not_lose_the_others(monkeypatch):
    def angry(*_a):
        raise ValueError("no")

    _funcs(monkeypatch, costs={"MSE": lambda o, d, s, w: 1.0, "bad": angry})
    out = obs_cost.evaluate([_item(), _item(cost_type="bad")], {0: {"a/u": [9.0]}})
    assert out["cost"] == pytest.approx(0.5)  # 1.0 / 2 weighted observables (#181)
    assert out["items"][1]["cost"] is None


def test_a_series_returning_operation_is_not_scored(monkeypatch):
    """It has no single value to compare with an observation."""
    _funcs(monkeypatch, ops={"identity": lambda y: y})
    assert obs_cost.evaluate([_item(operation="identity")], {0: {"a/u": [1.0, 2.0]}}) is None


def test_a_non_finite_result_is_not_scored(monkeypatch):
    """A parameter driven somewhere absurd produces nan, and nan sorts oddly and
    formats worse; better no number than a meaningless one."""
    _funcs(monkeypatch, ops={"nan": lambda y: float("nan")})
    assert obs_cost.evaluate([_item(operation="nan")], {0: {"a/u": [1.0]}}) is None


@pytest.mark.integration
def test_the_run_routes_report_a_cost(client, requires_simulation):
    """End to end, with circulatory_autogen's real operation and cost functions."""
    import json

    from conftest import SN_MODEL_PATH, SN_OBS_DATA_PATH, upload_model

    model_id = upload_model(client, SN_MODEL_PATH)["model_id"]
    obs = json.loads(SN_OBS_DATA_PATH.read_text())
    assert client.post(
        "/api/obs_data/upload", json={"model_id": model_id, "obs_data": obs}
    ).status_code == 200

    resp = client.post(
        "/api/protocol/run",
        json={"model_id": model_id, "params": {}, "outputs": ["soma_SN/V"]},
    )
    assert resp.status_code == 200, resp.text
    cost = resp.json()["cost"]
    assert cost and cost["cost"] > 0
    # Every observable is scored, not only the ones that happened to be plotted:
    # the run asks for the obs operands too.
    assert all(item["cost"] is not None for item in cost["items"])


@pytest.mark.integration
def test_moving_a_parameter_moves_the_cost(client, requires_simulation):
    """The point of the number: it has to respond to the slider."""
    import json

    from conftest import SN_MODEL_PATH, SN_OBS_DATA_PATH, upload_model

    model_id = upload_model(client, SN_MODEL_PATH)["model_id"]
    client.post(
        "/api/obs_data/upload",
        json={"model_id": model_id, "obs_data": json.loads(SN_OBS_DATA_PATH.read_text())},
    )

    def cost_for(params):
        resp = client.post(
            "/api/protocol/run",
            json={"model_id": model_id, "params": params, "outputs": ["soma_SN/V"]},
        )
        assert resp.status_code == 200, resp.text
        return resp.json()["cost"]["cost"]

    base = cost_for({})
    # g_Na, not g_M: the protocol's params_to_change sets g_M per sub-experiment,
    # so a slider value for it is overridden by the protocol -- correctly, and
    # the first version of this test read that as "the cost does not respond".
    moved = cost_for({"soma_SN/g_Na": 6.0})
    assert moved != pytest.approx(base)


# ---------------------------------------------------------------------------
# The number must be the same number the calibration reports (issue #181)
#
# Reported as: after a calibration, "Reset to best fit" puts the calibration's
# own parameters on the sliders, and the output-plots cost still disagrees with
# the calibration panel's. Same parameters, same data, two different numbers --
# so one of them is computed differently, and it was this one.
#
# circulatory_autogen aggregates in paramID.get_cost_obs_and_pred_from_params:
#
#     cost += sub_cost                       # over experiments x subexperiments
#     ...
#     cost = cost / float(weighted_obs_denominator)
#
# i.e. the *mean* contribution per weighted observable, where the denominator
# counts every observable with a non-zero weight. This summed instead, so it was
# larger by exactly the number of observables -- which is why it looked like a
# plausible cost rather than an obviously broken one.
# ---------------------------------------------------------------------------
def test_the_cost_is_the_mean_per_observable_not_the_sum(monkeypatch):
    """The reported mismatch, at its smallest: two observables, each off by 2."""
    _funcs(monkeypatch)
    items = [_item(value=10.0), _item(value=10.0, operands=["a/v"])]
    out = obs_cost.evaluate(items, {0: {"a/u": [8.0], "a/v": [8.0]}})
    # Each contributes (8-10)^2 = 4. CA divides by the 2 weighted observables.
    assert out["cost"] == pytest.approx(4.0)
    assert [e["cost"] for e in out["items"]] == [pytest.approx(4.0)] * 2


def test_one_observable_is_unaffected_by_the_normalisation(monkeypatch):
    """Which is why the bug survived: the single-observable case is exact, and
    every hand-checked example was a single observable."""
    _funcs(monkeypatch)
    out = obs_cost.evaluate([_item(value=10.0)], {0: {"a/u": [8.0]}})
    assert out["cost"] == pytest.approx(4.0)


def test_a_zero_weighted_observable_is_excluded_entirely(monkeypatch):
    """CA skips weight == 0 rather than scoring it: `if weight_entry != 0`. It
    must leave the denominator too, or turning an observable off would change
    the cost of the ones left on."""
    _funcs(monkeypatch)
    items = [_item(value=10.0), _item(value=0.0, weight=0.0, operands=["a/v"])]
    out = obs_cost.evaluate(items, {0: {"a/u": [8.0], "a/v": [1e6]}})
    assert out["cost"] == pytest.approx(4.0)
    assert out["items"][1]["cost"] is None


def test_a_zero_weight_is_not_read_as_a_default(monkeypatch):
    """`item.get("weight") or 1.0` turns a deliberate 0 into a 1 -- the one
    coercion that silently reverses the user's intent."""
    _funcs(monkeypatch)
    out = obs_cost.evaluate([_item(value=10.0, weight=0.0)], {0: {"a/u": [8.0]}})
    assert out is None  # nothing weighted, so nothing to report


def test_the_denominator_spans_experiments(monkeypatch):
    """CA's denominator is global -- summed over every experiment and
    subexperiment -- not per experiment."""
    _funcs(monkeypatch)
    items = [_item(value=10.0), _item(value=10.0, experiment_idx=1)]
    out = obs_cost.evaluate(items, {0: {"a/u": [8.0]}, 1: {"a/u": [8.0]}})
    assert out["cost"] == pytest.approx(4.0)


def test_an_unscorable_observable_still_counts_against_the_mean(monkeypatch):
    """It counts in CA's denominator, because CA scores it. Dropping it from
    ours as well would report a *better* cost than the calibration for the same
    parameters -- the same class of disagreement, in the flattering direction."""
    _funcs(monkeypatch)
    items = [_item(value=10.0), _item(value=10.0, operands=["not/recorded"])]
    out = obs_cost.evaluate(items, {0: {"a/u": [8.0]}})
    assert out["cost"] == pytest.approx(2.0)  # 4 / 2, not 4 / 1
    assert out["incomplete"] is True


def test_a_complete_score_is_not_flagged_incomplete(monkeypatch):
    _funcs(monkeypatch)
    out = obs_cost.evaluate([_item(value=10.0)], {0: {"a/u": [8.0]}})
    assert out["incomplete"] is False
