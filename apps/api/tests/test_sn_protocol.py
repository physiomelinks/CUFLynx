"""Tests for the SN model: time-varying trace binding (#8), name resolution,
and one-plot-per-(experiment, variable) data plumbing."""

import copy
import json

import numpy as np
import pytest

import engine as engine_mod
from conftest import SN_MODEL_PATH, SN_OBS_DATA_PATH, upload_model


# ---------------------------------------------------------------------------
# Unit tier — wiring of set_protocol_info, no Myokit
# ---------------------------------------------------------------------------
class _FakeHelper:
    def __init__(self):
        self.protocol_infos = []

    def set_protocol_info(self, protocol_info):
        self.protocol_infos.append(protocol_info)


class _FakeRunner:
    def __init__(self, **_kwargs):
        self.sim_helper = _FakeHelper()
        self.run_calls = []

    def run_protocols(self, model_path, protocol_info=None, id_param_names=None, id_param_vals=None):
        self.run_calls.append(protocol_info)
        # one experiment, one variable, three samples
        return [np.array([0.0, 1.0, 2.0])], [[np.array([10.0, 11.0, 12.0])]], [[3]]

    def get_var2idx_dict(self):
        return {"comp.x": 0}


def test_protocol_run_binds_protocol_info_with_trace(client):
    model_id = upload_model(client, SN_MODEL_PATH)["model_id"]
    runner = _FakeRunner()
    engine_mod.engine.runner_factory = lambda **kwargs: runner

    protocol_info = {
        "pre_times": [0.0],
        "sim_times": [[3]],
        "params_to_change": {"comp/I": [["ramp_port"]]},
        "protocol_traces": {"ramp_port": {"t": [0.0, 1.0], "values": [0.0, 1.0]}},
    }
    resp = client.post(
        "/api/protocol/run",
        json={"model_id": model_id, "protocol_info": protocol_info, "params": {}, "outputs": ["comp/x"]},
    )
    assert resp.status_code == 200, resp.text

    # The (un-sanitised) protocol_info, traces intact, was bound onto the helper
    # and passed to the runner — no stopgap warning.
    assert runner.sim_helper.protocol_infos == [protocol_info]
    assert runner.run_calls[0]["params_to_change"]["comp/I"] == [["ramp_port"]]
    body = resp.json()
    assert "warnings" not in body
    assert body["experiments"][0]["outputs"]["comp/x"] == [10.0, 11.0, 12.0]


def test_protocol_info_bound_once_for_same_object(client):
    """Repeated runs with the same protocol_info object don't re-bind the pace."""
    model_id = upload_model(client, SN_MODEL_PATH)["model_id"]
    runner = _FakeRunner()
    engine_mod.engine.runner_factory = lambda **kwargs: runner
    pi = {"pre_times": [0.0], "sim_times": [[3]], "params_to_change": {}}
    # Stash the same object as this model's obs so both runs reuse it.
    import obs_data as obs_mod

    main_obs = obs_mod.ObsData(protocol_info=pi, data_items=[], prediction_items=[])
    import main as main_mod

    main_mod._models[model_id].obs_data = main_obs

    for _ in range(2):
        r = client.post("/api/protocol/run", json={"model_id": model_id, "params": {}, "outputs": ["comp/x"]})
        assert r.status_code == 200, r.text
    assert len(runner.sim_helper.protocol_infos) == 1


def test_obs_upload_returns_items_for_plot_grid(client):
    obs = json.loads(SN_OBS_DATA_PATH.read_text())
    resp = client.post("/api/obs_data/upload", json=obs)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["n_experiments"] == 3
    assert len(body["data_items"]) == len(obs["data_items"])
    assert len(body["prediction_items"]) == len(obs["prediction_items"])
    # protocol_info (incl. params_to_change) is returned so the frontend can plot
    # the controlled inputs per experiment.
    assert "soma_SN/I_in" in body["protocol_info"]["params_to_change"]


# ---------------------------------------------------------------------------
# Integration tier (real Myokit; large flat model)
# ---------------------------------------------------------------------------
@pytest.mark.integration
def test_sn_protocol_runs_all_experiments_with_resolved_outputs(client, requires_simulation):
    model_id = upload_model(client, SN_MODEL_PATH)["model_id"]
    obs = json.loads(SN_OBS_DATA_PATH.read_text())
    up = client.post("/api/obs_data/upload", json={"model_id": model_id, "obs_data": obs})
    assert up.status_code == 200, up.text

    resp = client.post(
        "/api/protocol/run",
        json={"model_id": model_id, "params": {}, "outputs": ["soma_SN/V", "var_SN/Cai"]},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()

    assert len(body["experiments"]) == 3
    for exp in body["experiments"]:
        v = np.array(exp["outputs"]["soma_SN/V"])
        assert v.size > 0 and np.all(np.isfinite(v))
        assert len(exp["outputs"]["var_SN/Cai"]) > 0
    # Traces are applied now, not stripped: no stopgap warning.
    assert not body.get("warnings")


@pytest.mark.integration
def test_sn_time_varying_trace_is_actually_applied(client, requires_simulation):
    """Experiment 2 paces soma_SN/I_in via the ramp_port trace; zeroing the
    trace must change its V output, while the untraced experiments are
    unaffected."""
    model_id = upload_model(client, SN_MODEL_PATH)["model_id"]
    obs = json.loads(SN_OBS_DATA_PATH.read_text())
    client.post("/api/obs_data/upload", json={"model_id": model_id, "obs_data": obs})

    def run(protocol_info=None):
        payload = {"model_id": model_id, "params": {}, "outputs": ["soma_SN/V"]}
        if protocol_info is not None:
            payload["protocol_info"] = protocol_info
        r = client.post("/api/protocol/run", json=payload)
        assert r.status_code == 200, r.text
        return [np.array(e["outputs"]["soma_SN/V"]) for e in r.json()["experiments"]]

    with_trace = run()  # stored obs -> ramp applied

    zeroed = copy.deepcopy(obs["protocol_info"])
    for matrix in zeroed["params_to_change"].values():
        for row in matrix:
            for i, val in enumerate(row):
                if isinstance(val, str):
                    row[i] = 0.0
    without_trace = run(zeroed)

    # Experiment 2 (the only traced one) changes; experiment 0 (untraced) does not.
    assert np.max(np.abs(with_trace[2] - without_trace[2])) > 0.05
    assert np.allclose(with_trace[0], without_trace[0])
