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
def test_protocol_run_unknown_model_returns_404(client):
    resp = client.post("/api/protocol/run", json={"model_id": "nope", "params": {}})
    assert resp.status_code == 404


def test_protocol_run_without_protocol_info_returns_422(client):
    model_id = upload_model(client, LV_MODEL_PATH)["model_id"]
    resp = client.post("/api/protocol/run", json={"model_id": model_id, "params": {}})
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Integration tier
# ---------------------------------------------------------------------------
@pytest.mark.integration
def test_protocol_run_lotka_volterra_obs_data(client, requires_simulation):
    model_id = upload_model(client, LV_MODEL_PATH)["model_id"]
    protocol_info = json.loads(LV_OBS_DATA_PATH.read_text())["protocol_info"]
    resp = client.post(
        "/api/protocol/run",
        json={
            "model_id": model_id,
            "protocol_info": protocol_info,
            "params": {},
            "outputs": ["Lotka_Volterra_module/x"],
        },
    )
    assert resp.status_code == 200, resp.text
    experiments = resp.json()["experiments"]
    assert len(experiments) == 1
    time = np.array(experiments[0]["time"])
    assert np.all(np.diff(time) > 0)
    assert "Lotka_Volterra_module/x" in experiments[0]["outputs"]


@pytest.mark.integration
def test_protocol_run_respects_pre_time(client, requires_simulation):
    model_id = upload_model(client, LV_MODEL_PATH)["model_id"]
    resp = client.post(
        "/api/protocol/run",
        json={
            "model_id": model_id,
            "protocol_info": {
                "pre_times": [1.0],
                "sim_times": [[5]],
                "params_to_change": {},
            },
            "params": {},
            "outputs": ["Lotka_Volterra_module/x"],
        },
    )
    assert resp.status_code == 200, resp.text
    time = np.array(resp.json()["experiments"][0]["time"])
    # pre_time is stripped, so the experiment time starts near 0.
    assert abs(time[0]) < 1e-6


@pytest.mark.integration
def test_protocol_run_bg_model_single_segment(client, requires_simulation):
    model_id = upload_model(client, BG_MODEL_PATH)["model_id"]
    resp = client.post(
        "/api/protocol/run",
        json={
            "model_id": model_id,
            "protocol_info": {
                "pre_times": [0.0],
                "sim_times": [[20]],
                "params_to_change": {},
            },
            "params": {},
            "outputs": ["main/p_o2"],
        },
    )
    assert resp.status_code == 200, resp.text
    exp = resp.json()["experiments"][0]
    assert "main/p_o2" in exp["outputs"]
    assert exp["time"][-1] == pytest.approx(20, abs=1.0)
