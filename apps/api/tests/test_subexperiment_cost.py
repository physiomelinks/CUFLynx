"""A data_item is scored against the subexperiment it names (issue #181).

An obs_data data_item carries both `experiment_idx` and `subexperiment_idx`, and
CA scores it against that subexperiment's own segment -- it indexes a flat list
built as `sum(num_sub_per_exp[:exp]) + sub`. CUFLynx returned one trace per
*experiment*, subexperiments joined end to end, and keyed the cost on the
experiment alone. Every item past the first subexperiment was therefore scored
against the wrong segment.

Visible on a fixture we ship: SN_simple has 3 experiments x 2 subexperiments, and
two of its spike-frequency observables share experiment 0 while expecting 0.0 and
4.0 from *different* segments. Both read 0.0.
"""

from __future__ import annotations

import json

import engine as engine_mod
import pytest
from conftest import SN_MODEL_PATH, SN_OBS_DATA_PATH, upload_model


def _sn(client):
    model_id = upload_model(client, SN_MODEL_PATH)["model_id"]
    obs = json.loads(SN_OBS_DATA_PATH.read_text())
    r = client.post("/api/obs_data/upload", json={"model_id": model_id, "obs_data": obs})
    assert r.status_code == 200, r.text
    return model_id, obs


# ---------------------------------------------------------------------------
# The keying, on its own
# ---------------------------------------------------------------------------
def _funcs(monkeypatch):
    import obs_cost

    monkeypatch.setattr(obs_cost, "get_operation_funcs", lambda _d=None: {"max": max})
    # Parameters named as CA names them: since CA #370 a cost func is handed
    # `std`/`weight` only when its signature declares them by name, so a stand-in
    # with abbreviated parameters would be handed neither.
    def mse(output, desired_mean, std, weight):
        return weight * (output - desired_mean) ** 2

    monkeypatch.setattr(obs_cost, "get_cost_funcs", lambda _d=None: {"MSE": mse})
    return obs_cost


def _item(**over):
    item = {
        "variable": "x",
        "operation": "max",
        "operands": ["a/u"],
        "value": 0.0,
        "std": 1.0,
        "weight": 1.0,
        "cost_type": "MSE",
    }
    item.update(over)
    return item


def test_each_item_is_scored_against_its_own_subexperiment(monkeypatch):
    obs_cost = _funcs(monkeypatch)
    items = [
        _item(experiment_idx=0, subexperiment_idx=0, value=1.0),
        _item(experiment_idx=0, subexperiment_idx=1, value=5.0),
    ]
    out = obs_cost.evaluate(
        items,
        {(0, 0): {"a/u": [1.0]}, (0, 1): {"a/u": [5.0]}},
    )
    # Both match their own segment, so the cost is zero. Keyed on the experiment
    # alone, the second would have read 1.0 against an expected 5.0.
    assert out["cost"] == pytest.approx(0.0)
    assert [i["cost"] for i in out["items"]] == [0.0, 0.0]


def test_the_subexperiment_is_reported_per_item(monkeypatch):
    obs_cost = _funcs(monkeypatch)
    out = obs_cost.evaluate(
        [_item(experiment_idx=1, subexperiment_idx=1)], {(1, 1): {"a/u": [0.0]}}
    )
    assert out["items"][0]["subexperiment_idx"] == 1


def test_a_run_without_segments_still_scores(monkeypatch):
    """A plain /api/simulate has no subexperiments, and an older CA cannot
    provide them -- keying must fall back rather than score nothing."""
    obs_cost = _funcs(monkeypatch)
    out = obs_cost.evaluate([_item(value=3.0)], {0: {"a/u": [3.0]}})
    assert out["cost"] == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# Binding the protocol reorders the result rows
# ---------------------------------------------------------------------------
class _Helper:
    """A helper whose variable order changes when the protocol is bound, which
    is what Myokit's does: binding `pace` rebuilds the model with the paced
    variable added, so every later row shifts by one."""

    def __init__(self):
        self.names = ["environment/time", "a/E_Na", "a/V"]
        self.bound = 0

    def set_protocol_info(self, protocol_info):
        self.bound += 1
        self.names = ["parameters/I_in", *self.names]

    def get_all_variable_names(self):
        return list(self.names)


class _Runner:
    def __init__(self):
        self.sim_helper = _Helper()
        self.variable_names = self.sim_helper.get_all_variable_names()

    def get_var2idx_dict(self):
        return {name: idx for idx, name in enumerate(self.variable_names)}


def test_binding_the_protocol_refreshes_the_variable_map():
    """Without the refresh every variable reads as its neighbour: on SN_simple
    `soma_SN/V` came back as `E_Na`, 145 mV out, and the joined traces did not
    match CA's own. run_protocols refreshes it; the executor cannot."""
    runner = _Runner()
    engine_mod.bind_protocol(runner, {"sim_times": [[1.0]]})

    assert runner.get_var2idx_dict()["a/V"] == 3, (
        "the variable map still indexes the pre-binding row order"
    )


def test_binding_marks_the_protocol_as_applied():
    """CA's own marker, so a later run_protocols on this runner does not rebuild
    the simulation to bind what is already bound."""
    runner = _Runner()
    protocol_info = {"sim_times": [[1.0]]}
    engine_mod.bind_protocol(runner, protocol_info)

    assert runner._applied_protocol_info is protocol_info
    assert runner.sim_helper.bound == 1


# ---------------------------------------------------------------------------
# End to end, on the fixture that shows it
# ---------------------------------------------------------------------------
@pytest.mark.integration
def test_sn_subexperiments_are_scored_separately(client, requires_simulation):
    engine_mod.engine.reset()
    engine_mod.engine.model_type, engine_mod.engine.solver = "cellml_only", "CVODE_myokit"
    model_id, obs = _sn(client)

    r = client.post("/api/protocol/run", json={"model_id": model_id, "params": {}})
    assert r.status_code == 200, r.text
    body = r.json()

    # 3 experiments x 2 subexperiments: the plots still get one trace each, and
    # the segments come back beside them.
    assert len(body["experiments"]) == 3
    assert len(body["subexperiments"]) == 6
    assert [(s["experiment_idx"], s["subexperiment_idx"]) for s in body["subexperiments"]] == [
        (0, 0), (0, 1), (1, 0), (1, 1), (2, 0), (2, 1),
    ]

    segs = {(s["experiment_idx"], s["subexperiment_idx"]): s["outputs"] for s in body["subexperiments"]}
    by_key = {(i["experiment_idx"], i["subexperiment_idx"], i["operation"]): i
              for i in body["cost"]["items"]}

    # The two segments of experiment 0 differ -- params_to_change steps
    # soma_SN/I_in from 0 to -0.15 between them -- so scoring against the wrong
    # one is observable at all.
    assert max(segs[(0, 0)]["soma_SN/V"]) != max(segs[(0, 1)]["soma_SN/V"])

    # Each item reads the segment it names, not the joined experiment trace.
    item = by_key[(0, 1, "max")]
    assert item["model"] == pytest.approx(max(segs[(0, 1)]["soma_SN/V"]))

    # `time` is the segment's own clock. Keyed on the experiment, this item was
    # scored against the joined clock -- which starts a whole pre_time earlier,
    # putting the peak an experiment-length away from the 2.02 s expected.
    peak = by_key[(0, 1, "first_peak_time")]["model"]
    t = segs[(0, 1)]["time"]
    assert t[0] <= peak <= t[-1], (
        f"first peak at {peak} lies outside its own segment's window "
        f"[{t[0]}, {t[-1]}], so the operand clock is still the joined one"
    )


@pytest.mark.integration
def test_the_joined_traces_match_cas_own(client, requires_simulation, tmp_path):
    """The join is a port of ProtocolRunner.run_protocols, because that returns
    only the joined form and the cost needs the segments. Ported code drifts, so
    this asserts the two agree on a fixture with real subexperiments."""
    import numpy as np

    engine_mod.engine.reset()
    engine_mod.engine.model_type, engine_mod.engine.solver = "cellml_only", "CVODE_myokit"
    model_id, obs = _sn(client)
    protocol_info = obs["protocol_info"]

    r = client.post(
        "/api/protocol/run",
        json={"model_id": model_id, "params": {}, "outputs": ["soma_SN/V"]},
    )
    assert r.status_code == 200, r.text
    ours = r.json()["experiments"]

    # CA's own path, on the same cached runner.
    runner = next(iter(engine_mod.engine._runners.values()))
    t_list, res_list, _ = runner.run_protocols(
        "", protocol_info=protocol_info, id_param_names=None, id_param_vals=None
    )
    var2idx = runner.get_var2idx_dict()
    key = engine_mod._resolve_output_key(var2idx, "soma_SN/V")
    for exp_idx, exp in enumerate(ours):
        theirs = res_list[exp_idx][var2idx[key]]
        assert np.allclose(exp["outputs"]["soma_SN/V"], theirs), f"experiment {exp_idx} differs"
