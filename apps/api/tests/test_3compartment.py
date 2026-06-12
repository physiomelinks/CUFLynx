"""Tests for the 3compartment cardiovascular model fixtures:

- obs_data with a protocol_info (pre_time 10s, sim_time 2s),
- the bare-list (data-only) obs_data format is still supported,
- params_for_id with the ``global`` vessel + ``param_type`` column,
- protocol run respects the obs_data pre_time/sim_time and is pulsatile.
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
def test_obs_data_has_protocol_with_pre_and_sim_time():
    obj = json.loads(C3_OBS_DATA_PATH.read_text())
    parsed = obs_mod.parse_obs_data(obj)
    assert parsed.has_protocol is True
    assert parsed.protocol_info["pre_times"] == [10.0]
    assert parsed.protocol_info["sim_times"] == [[2]]
    assert len(parsed.data_items) == 6
    summary = parsed.summary()
    assert summary["has_protocol"] is True
    assert summary["n_experiments"] == 1
    assert summary["n_data_items"] == 6


def test_upload_3compartment_obs_returns_protocol_summary(client):
    obj = json.loads(C3_OBS_DATA_PATH.read_text())
    resp = client.post("/api/obs_data/upload", json=obj)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["has_protocol"] is True
    assert body["n_experiments"] == 1
    assert len(body["data_items"]) == 6


def test_bare_list_obs_data_is_still_supported():
    # The data-only (bare array) format remains valid -> data-only obs.
    parsed = obs_mod.parse_obs_data(
        [
            {
                "variable": "flow",
                "operands": ["aortic_root/v"],
                "data_type": "constant",
                "plot_type": "horizontal",
                "value": 1e-4,
            }
        ]
    )
    assert parsed.has_protocol is False
    assert parsed.protocol_info is None
    assert len(parsed.data_items) == 1


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
    assert all(p["param_type"] == "const" for p in params)


# ---------------------------------------------------------------------------
# Integration tier (real Myokit)
# ---------------------------------------------------------------------------
@pytest.mark.integration
def test_3compartment_protocol_respects_pre_and_sim_time(client, requires_simulation):
    model_id = upload_model(client, C3_MODEL_PATH)["model_id"]
    obj = json.loads(C3_OBS_DATA_PATH.read_text())
    up = client.post(
        "/api/obs_data/upload", json={"model_id": model_id, "obs_data": obj}
    )
    assert up.status_code == 200, up.text
    assert up.json()["has_protocol"] is True

    # No protocol_info in the body -> the uploaded obs_data drives the run,
    # including its 10 s pre_time (warm-up, stripped) and 2 s sim_time.
    resp = client.post(
        "/api/protocol/run",
        json={
            "model_id": model_id,
            "params": {},
            "outputs": ["aortic_root/v", "aortic_root/u"],
        },
    )
    assert resp.status_code == 200, resp.text
    experiments = resp.json()["experiments"]
    assert len(experiments) == 1

    time = np.array(experiments[0]["time"])
    assert abs(time[0]) < 1e-6  # pre_time stripped -> starts near 0
    assert time[-1] == pytest.approx(2.0, abs=0.05)  # sim_time 2 s

    for key in ("aortic_root/v", "aortic_root/u"):
        arr = np.array(experiments[0]["outputs"][key])
        assert arr.size > 0
        assert np.all(np.isfinite(arr))
        assert np.ptp(arr) > 0  # self-oscillating after warm-up
