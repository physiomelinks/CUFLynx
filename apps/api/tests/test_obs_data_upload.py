import json

import numpy as np
import pytest

from conftest import (
    BG_MODEL_PATH,
    LV_MODEL_PATH,
    LV_OBS_DATA_PATH,
    upload_model,
)


# ---------------------------------------------------------------------------
# Unit tier
# ---------------------------------------------------------------------------
def test_upload_valid_obs_data_returns_summary(client):
    obs = json.loads(LV_OBS_DATA_PATH.read_text())
    resp = client.post("/api/obs_data/upload", json=obs)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["n_experiments"] == 1
    assert body["n_data_items"] == 2


def test_upload_missing_protocol_info_returns_422(client):
    resp = client.post("/api/obs_data/upload", json={})
    assert resp.status_code == 422


def test_upload_series_without_obs_dt_returns_422(client):
    obs = {
        "protocol_info": {"pre_times": [0.0], "sim_times": [[5]]},
        "data_items": [
            {"variable": "x", "data_type": "series", "experiment_idx": 0}
        ],
    }
    resp = client.post("/api/obs_data/upload", json=obs)
    assert resp.status_code == 422
    assert "obs_dt" in resp.json()["detail"]


def test_upload_experiment_idx_out_of_range_returns_422(client):
    obs = {
        "protocol_info": {"pre_times": [0.0], "sim_times": [[5]]},
        "data_items": [
            {"variable": "x", "data_type": "constant", "experiment_idx": 5}
        ],
    }
    resp = client.post("/api/obs_data/upload", json=obs)
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Integration tier
# ---------------------------------------------------------------------------
@pytest.mark.integration
def test_protocol_run_uses_uploaded_obs_data(client, requires_simulation):
    model_id = upload_model(client, LV_MODEL_PATH)["model_id"]
    obs = json.loads(LV_OBS_DATA_PATH.read_text())

    up = client.post("/api/obs_data/upload", json={"model_id": model_id, "obs_data": obs})
    assert up.status_code == 200, up.text
    assert up.json()["n_experiments"] == 1

    # No protocol_info in the body -> server uses the uploaded obs_data.
    resp = client.post(
        "/api/protocol/run",
        json={
            "model_id": model_id,
            "params": {},
            "outputs": ["Lotka_Volterra_module/x"],
        },
    )
    assert resp.status_code == 200, resp.text
    experiments = resp.json()["experiments"]
    assert len(experiments) == 1
    assert "Lotka_Volterra_module/x" in experiments[0]["outputs"]
    time = np.array(experiments[0]["time"])
    assert np.all(np.diff(time) > 0)


@pytest.mark.integration
def test_protocol_run_bg_model_with_minimal_obs_data(client, requires_simulation):
    model_id = upload_model(client, BG_MODEL_PATH)["model_id"]
    obs = {
        "protocol_info": {"pre_times": [0.0], "sim_times": [[20]], "params_to_change": {}},
        "data_items": [],
    }
    up = client.post("/api/obs_data/upload", json={"model_id": model_id, "obs_data": obs})
    assert up.status_code == 200, up.text

    resp = client.post(
        "/api/protocol/run",
        json={"model_id": model_id, "params": {}, "outputs": ["main/p_o2"]},
    )
    assert resp.status_code == 200, resp.text
    assert "main/p_o2" in resp.json()["experiments"][0]["outputs"]
