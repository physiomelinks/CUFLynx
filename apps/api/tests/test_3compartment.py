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
    assert body["protocol_info"]["pre_times"] == [10.0]


def test_bare_list_obs_data_is_still_supported():
    # The data-only (bare array) format remains valid -> data-only obs.
    parsed = obs_mod.parse_obs_data(
        [
            {
                "data_item_name": "flow",
                "operands": ["aortic_root/v"],
                "data_type": "constant",
                "plot_type": "horizontal",
                "value": 1e-4,
                # `unit` and `std` are REQUIRED by CA, which now vets the
                # document at upload; the shipped 3compartment obs_data (the
                # same bare-list format) carries both.
                "unit": "m3_per_s",
                "std": 1e-5,
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


# Issue #138: a failed simulation reported nothing but "Request failed with
# status code 500".
#
# A large MaximumStep on its own does *not* fail here — CVODE adapts its own
# step down and Myokit's log_times don't force steps, so 3compartment runs fine
# at MaximumStep=1e9. What does fail is a tolerance the solver cannot hold, so
# that is what these provoke; MaximumStep still rides along in the settings,
# because "which step size was I running at?" is exactly what the message has to
# answer when a user suspects it.
UNSOLVABLE_SOLVER_INFO = {"MaximumStep": 100.0, "rtol": 1e-30, "atol": 1e-30}


def _assert_informative(detail: str) -> None:
    # Not the bare "simulation failed" this issue started from.
    assert detail.strip().lower() not in {"simulation failed", "internal server error"}
    # The solver's own words, recovered from the output CA prints and swallows.
    assert "CVode" in detail or "CV_" in detail
    # The settings it failed under, so a bad MaximumStep is distinguishable from
    # a bad model.
    assert "CVODE_myokit" in detail
    assert "MaximumStep=100.0" in detail
    # Something to do about it.
    assert "Settings" in detail


@pytest.mark.integration
def test_simulate_failure_detail_is_informative(client, requires_simulation):
    model_id = upload_model(client, C3_MODEL_PATH)["model_id"]
    cfg = client.post("/api/config", json={"solver_info": UNSOLVABLE_SOLVER_INFO})
    assert cfg.status_code == 200, cfg.text

    resp = client.post(
        "/api/simulate",
        json={
            "model_id": model_id,
            "params": {},
            "sim_time": 2.0,
            "pre_time": 10.0,
            "outputs": ["aortic_root/v"],
        },
    )
    assert resp.status_code == 500, resp.text
    _assert_informative(resp.json()["detail"])


@pytest.mark.integration
def test_protocol_failure_detail_is_informative(client, requires_simulation):
    """The protocol path is the one issue #138 was reported against.

    ``run_protocols`` raises a bare "Protocol simulation failed." while the
    helper prints the real cause, and nothing caught it — so FastAPI returned a
    body-less 500 and the frontend's `detail` lookup fell through to
    "AxiosError: Request failed with status code 500"."""
    model_id = upload_model(client, C3_MODEL_PATH)["model_id"]
    obj = json.loads(C3_OBS_DATA_PATH.read_text())
    up = client.post("/api/obs_data/upload", json={"model_id": model_id, "obs_data": obj})
    assert up.status_code == 200, up.text
    cfg = client.post("/api/config", json={"solver_info": UNSOLVABLE_SOLVER_INFO})
    assert cfg.status_code == 200, cfg.text

    resp = client.post(
        "/api/protocol/run",
        json={"model_id": model_id, "params": {}, "outputs": ["aortic_root/v"]},
    )
    assert resp.status_code == 500, resp.text
    body = resp.json()
    assert "detail" in body, body  # a body at all, unlike before
    _assert_informative(body["detail"])
