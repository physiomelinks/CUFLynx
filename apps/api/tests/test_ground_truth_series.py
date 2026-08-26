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
from conftest import RESOURCES_DIR


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


# --- the shipped example of one (resources/3compartment_recorded_trace.omex) ----------
#
# Everything above is about what a zero-weighted trace does to the *cost*. This is the
# artefact a user opens to see one: the 3compartment study with a measured trace of the
# same variable its scalar features are taken from, generated from the model itself and
# carried at weight 0 so it is drawn behind the simulation without being fitted.

TRACE_ARCHIVE = RESOURCES_DIR / "3compartment_recorded_trace.omex"


@pytest.mark.unit
def test_the_recorded_trace_example_exists_and_carries_one():
    import json
    import zipfile

    assert TRACE_ARCHIVE.is_file(), f"{TRACE_ARCHIVE.name} is missing from resources/"
    with zipfile.ZipFile(TRACE_ARCHIVE) as zf:
        name = next(n for n in zf.namelist() if n.endswith("_obs_data.json"))
        doc = json.loads(zf.read(name))

    traces = [i for i in doc["data_items"] if i.get("data_type") == "series"]
    assert len(traces) == 3, f"expected a trace per measured variable, got {len(traces)}"
    for trace in traces:
        assert trace["weight"] == 0, "the point of this example is that it is not fitted"
        assert trace["obs_dt"], ("a series is reconstructed on i * obs_dt; without it there "
                                 "is no time axis")
        assert len(trace["value"]) > 10, trace["value"][:5]
    # One per variable the study fits scalars from, so every feature is drawn in a cell
    # that also holds the trace it was taken from.
    assert {t["operands"][0] for t in traces} == {
        i["operands"][0] for i in doc["data_items"] if i.get("data_type") == "constant"}


@pytest.mark.unit
def test_each_feature_is_its_own_traces_statistic():
    """The property that makes this example worth looking at.

    A feature drawn from a measurement taken elsewhere sits *off* the recorded points --
    the horizontal line and the scatter disagree, and the picture invites the reader to
    conclude something about the model from what is really a mismatch between two data
    sources. Here each scalar is exactly its own trace's mean, max, min or range, so the
    line lands on the points and any daylight between them and the simulation is the
    model's.
    """
    import json
    import zipfile

    ops = {"mean": lambda v: sum(v) / len(v), "max": max, "min": min,
           "max_minus_min": lambda v: max(v) - min(v)}
    with zipfile.ZipFile(TRACE_ARCHIVE) as zf:
        name = next(n for n in zf.namelist() if n.endswith("_obs_data.json"))
        doc = json.loads(zf.read(name))

    by_variable = {i["operands"][0]: i["value"]
                   for i in doc["data_items"] if i.get("data_type") == "series"}
    checked = 0
    for item in doc["data_items"]:
        if item.get("data_type") == "series":
            continue
        samples = by_variable[item["operands"][0]]
        expected = ops[item["operation"]](samples)
        assert abs(expected - item["value"]) <= 1e-9 * max(1.0, abs(expected)), (
            f"{item['data_item_name']} is {item['value']}, but {item['operation']} of its "
            f"recorded trace is {expected} -- the drawn feature would not sit on the points")
        checked += 1
    assert checked == 6, f"expected six scalar features, checked {checked}"


@pytest.mark.unit
def test_the_recorded_trace_example_loads_whole(client):
    """It has to survive the engine's schema, not just look right: a series carries keys
    the scalars do not, and a key present on one row and absent on the rest turns that
    column to floats in CA's frame -- which it then rejects, naming the *other* rows."""
    with open(TRACE_ARCHIVE, "rb") as fh:
        resp = client.post("/api/omex/upload",
                           files={"file": (TRACE_ARCHIVE.name, fh, "application/zip")})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    obs = body["obs_data"]
    assert not obs.get("error"), obs["error"]
    assert body["warnings"] == [], body["warnings"]
    assert len(obs["data_items"]) == 9, "six scalar features plus a trace per variable"
    traces = [i for i in obs["data_items"] if i.get("data_type") == "series"]
    assert len(traces) == 3
    assert all(t["weight"] == 0 and len(t["value"]) == 101 for t in traces)
