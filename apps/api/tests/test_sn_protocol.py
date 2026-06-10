"""Tests for the SN model: time-varying-trace sanitisation, name resolution,
and one-plot-per-(experiment, variable) data plumbing."""

import json

import numpy as np
import pytest

import engine as engine_mod
from conftest import SN_MODEL_PATH, SN_OBS_DATA_PATH, upload_model


# ---------------------------------------------------------------------------
# Unit tier
# ---------------------------------------------------------------------------
def test_sanitize_protocol_info_replaces_string_traces():
    pi = {
        "pre_times": [0.0],
        "sim_times": [[1, 2]],
        "params_to_change": {
            "soma_SN/I_in": [[0.0, "ramp_port"]],
            "soma_SN/g_M": [[0.08, 0.08]],
        },
    }
    safe, stripped = engine_mod._sanitize_protocol_info(pi)
    assert stripped == ["soma_SN/I_in"]
    assert safe["params_to_change"]["soma_SN/I_in"] == [[0.0, 0.0]]
    # original untouched (deep copy)
    assert pi["params_to_change"]["soma_SN/I_in"][0][1] == "ramp_port"


def test_obs_upload_returns_items_for_plot_grid(client):
    obs = json.loads(SN_OBS_DATA_PATH.read_text())
    resp = client.post("/api/obs_data/upload", json=obs)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["n_experiments"] == 3
    # The frontend builds the (experiment x variable) grid from these.
    assert len(body["data_items"]) == len(obs["data_items"])
    assert len(body["prediction_items"]) == len(obs["prediction_items"])


# ---------------------------------------------------------------------------
# Integration tier (real Myokit; large flat model, slower compile)
# ---------------------------------------------------------------------------
@pytest.mark.integration
def test_sn_protocol_runs_all_experiments_with_resolved_outputs(
    client, requires_simulation
):
    model_id = upload_model(client, SN_MODEL_PATH)["model_id"]
    obs = json.loads(SN_OBS_DATA_PATH.read_text())
    up = client.post(
        "/api/obs_data/upload", json={"model_id": model_id, "obs_data": obs}
    )
    assert up.status_code == 200, up.text

    resp = client.post(
        "/api/protocol/run",
        json={
            "model_id": model_id,
            "params": {},
            "outputs": ["soma_SN/V", "var_SN/Cai"],
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()

    # All three experiments produced, each with both resolved outputs.
    assert len(body["experiments"]) == 3
    for exp in body["experiments"]:
        v = np.array(exp["outputs"]["soma_SN/V"])
        assert v.size > 0 and np.all(np.isfinite(v))
        assert len(exp["outputs"]["var_SN/Cai"]) > 0

    # The unsupported time-varying trace is reported, not silently dropped.
    assert any("soma_SN/I_in" in w for w in body.get("warnings", []))
