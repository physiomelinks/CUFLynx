"""Tests for the 3compartment cardiovascular model fixtures:

- data-only obs_data (bare JSON array, no protocol_info),
- params_for_id with the ``global`` vessel + ``param_type`` column,
- self-driven (no-protocol) simulation producing pulsatile flow/pressure.
"""

import json

import numpy as np
import pytest

import obs_data as obs_mod
from conftest import (
    RESOURCES_DIR,
    upload_model,
)

C3_MODEL_PATH = RESOURCES_DIR / "3compartment_flat.cellml"
C3_OBS_DATA_PATH = RESOURCES_DIR / "3compartment_obs_data.json"
C3_PARAMS_CSV_PATH = RESOURCES_DIR / "3compartment_params_for_id.csv"


# ---------------------------------------------------------------------------
# Unit tier
# ---------------------------------------------------------------------------
def test_parse_bare_list_obs_data_is_data_only():
    obj = json.loads(C3_OBS_DATA_PATH.read_text())
    assert isinstance(obj, list)
    parsed = obs_mod.parse_obs_data(obj)
    assert parsed.has_protocol is False
    assert parsed.protocol_info is None
    assert len(parsed.data_items) == 6
    summary = parsed.summary()
    assert summary["has_protocol"] is False
    assert summary["n_data_items"] == 6
    assert summary["n_experiments"] == 1  # all items default to experiment 0


def test_upload_bare_list_obs_data(client):
    obj = json.loads(C3_OBS_DATA_PATH.read_text())
    resp = client.post("/api/obs_data/upload", json=obj)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["has_protocol"] is False
    assert body["n_data_items"] == 6
    assert len(body["data_items"]) == 6


def test_upload_3compartment_model_metadata(client):
    data = upload_model(client, C3_MODEL_PATH)
    assert data["name"] == "CardiovascularSystem"
    assert data["variable_count"] > 0


def test_params_for_id_global_vessel_qnames(client):
    with open(C3_PARAMS_CSV_PATH, "rb") as fh:
        resp = client.post(
            "/api/params_for_id/upload",
            files={"file": (C3_PARAMS_CSV_PATH.name, fh, "text/csv")},
        )
    assert resp.status_code == 200, resp.text
    params = resp.json()["params"]
    qnames = {p["qname"] for p in params}
    assert qnames == {
        "global/q_lv_init",
        "aortic_root/C",
        "global/E_lv_A",
        "global/E_lv_B",
    }
    # param_type column is captured.
    assert all(p["param_type"] == "const" for p in params)


# ---------------------------------------------------------------------------
# Integration tier (real Myokit)
# ---------------------------------------------------------------------------
@pytest.mark.integration
def test_3compartment_simulate_is_pulsatile(client, requires_simulation):
    model_id = upload_model(client, C3_MODEL_PATH)["model_id"]
    resp = client.post(
        "/api/simulate",
        json={
            "model_id": model_id,
            "params": {},
            "sim_time": 2.0,
            "pre_time": 0.0,
            "outputs": ["aortic_root/v", "aortic_root/u", "heart/q_lv"],
        },
    )
    assert resp.status_code == 200, resp.text
    outputs = resp.json()["outputs"]
    for key in ("aortic_root/v", "aortic_root/u", "heart/q_lv"):
        arr = np.array(outputs[key])
        assert arr.size > 0
        assert np.all(np.isfinite(arr))
        # The closed-loop model self-oscillates, so each signal varies in time.
        assert np.ptp(arr) > 0


@pytest.mark.integration
def test_3compartment_data_only_obs_then_manual_run(client, requires_simulation):
    """Upload the bare-list obs_data, then a manual simulate of the referenced
    variables still succeeds (no protocol needed)."""
    model_id = upload_model(client, C3_MODEL_PATH)["model_id"]
    obj = json.loads(C3_OBS_DATA_PATH.read_text())
    up = client.post(
        "/api/obs_data/upload", json={"model_id": model_id, "obs_data": obj}
    )
    assert up.status_code == 200, up.text
    assert up.json()["has_protocol"] is False

    resp = client.post(
        "/api/simulate",
        json={
            "model_id": model_id,
            "params": {},
            "sim_time": 2.0,
            "outputs": ["aortic_root/v"],
        },
    )
    assert resp.status_code == 200, resp.text
    assert len(resp.json()["outputs"]["aortic_root/v"]) > 0
