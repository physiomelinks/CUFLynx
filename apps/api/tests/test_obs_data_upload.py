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


def test_obs_editor_object_form_round_trips(client):
    # Shape the obs Edit dialog emits: object form with protocol_info verbatim,
    # an edited constant + a preserved series item (with obs_dt). Guards format drift.
    obs = {
        "protocol_info": {"pre_times": [0.0], "sim_times": [[5]]},
        "prediction_items": [],
        "data_items": [
            {
                "variable": "x_max", "data_type": "constant", "operation": "max",
                "operands": ["m/x"], "unit": "dimensionless", "value": 30, "std": 3,
                "experiment_idx": 0, "plot_type": "horizontal",
            },
            {
                "variable": "s", "data_type": "series", "obs_dt": 0.1,
                "value": [1, 2], "std": 0.1, "experiment_idx": 0,
            },
        ],
    }
    resp = client.post("/api/obs_data/upload", json=obs)
    assert resp.status_code == 200, resp.text
    assert resp.json()["n_data_items"] == 2


def test_protocol_info_with_generated_ramp_and_pulse_traces(client):
    # Shape the protocol_info editor emits: params_to_change referencing generated
    # ramp/pulse traces present in protocol_traces.
    obs = {
        "protocol_info": {
            "pre_times": [0.0, 0.0],
            "sim_times": [[5], [4]],
            "experiment_labels": ["e0", "e1"],
            "params_to_change": {
                "m/I": [["m_I_e0s0"], [0.2]],
                "m/g": [[0.1], ["m_g_e1s0"]],
            },
            "protocol_traces": {
                "m_I_e0s0": {"t": [0, 5], "values": [0, 1]},
                "m_g_e1s0": {"t": [0, 1, 1.001, 3, 3.001, 4], "values": [0, 0, 0.5, 0.5, 0, 0]},
            },
        },
        "prediction_items": [],
        "data_items": [],
    }
    resp = client.post("/api/obs_data/upload", json=obs)
    assert resp.status_code == 200, resp.text
    assert resp.json()["n_experiments"] == 2


def test_protocol_info_missing_trace_key_returns_422(client):
    obs = {
        "protocol_info": {
            "pre_times": [0.0],
            "sim_times": [[5]],
            "params_to_change": {"m/I": [["nonexistent_trace"]]},
            "protocol_traces": {},
        },
        "data_items": [],
    }
    resp = client.post("/api/obs_data/upload", json=obs)
    assert resp.status_code == 422
    assert "trace" in resp.json()["detail"].lower()


def test_obs_editor_data_only_array_form(client):
    # Data-only files round-trip as a bare array (no protocol_info).
    obs = [
        {
            "variable": "x_max", "data_type": "constant", "operation": "max",
            "operands": ["m/x"], "unit": "dimensionless", "value": 30, "std": 3,
            "experiment_idx": 0,
        },
    ]
    resp = client.post("/api/obs_data/upload", json=obs)
    assert resp.status_code == 200, resp.text
    assert resp.json()["has_protocol"] is False


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


# ---------------------------------------------------------------------------
# operation_kwargs survive the upload (regression: they must reach CA)
# ---------------------------------------------------------------------------
def test_upload_preserves_operation_kwargs_in_stored_file(client):
    """Per-data_item ``operation_kwargs`` (#112/#113) must survive the upload into
    the obs_data.json handed to circulatory_autogen.

    The editor writes the values, but they only *do* anything if they reach CA's
    parser, which reads the field off the stored file. A whitelist-style refactor
    of the data_item parsing would silently drop them and every other kwargs test
    would still pass, so pin it here: response payload *and* file on disk.
    """
    import main

    model_id = upload_model(client, LV_MODEL_PATH)["model_id"]
    obs = json.loads(LV_OBS_DATA_PATH.read_text())
    obs["data_items"][0]["operation"] = "peak_above"
    obs["data_items"][0]["operation_kwargs"] = {"threshold": 0.9, "invert": True}

    resp = client.post(f"/api/obs_data/upload?model_id={model_id}", json=obs)
    assert resp.status_code == 200, resp.text
    assert resp.json()["data_items"][0]["operation_kwargs"] == {
        "threshold": 0.9,
        "invert": True,
    }

    # The file CA actually reads.
    stored = json.loads(main._models[model_id].obs_path.read_text())
    assert stored["data_items"][0]["operation_kwargs"] == {
        "threshold": 0.9,
        "invert": True,
    }
    # An item without kwargs stays clean (no empty map injected).
    assert "operation_kwargs" not in stored["data_items"][1]
