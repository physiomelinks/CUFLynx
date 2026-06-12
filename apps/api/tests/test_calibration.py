"""Tests for the calibration job manager + endpoints.

Unit tests inject a fake runner script (no Myokit). The integration test runs a
short real genetic-algorithm calibration on the 3compartment model.
"""

import json
import math
import time

import pytest

import calibration as calibration_mod
from conftest import (
    LV_MODEL_PATH,
    LV_OBS_DATA_PATH,
    LV_PARAMS_CSV_PATH,
    RESOURCES_DIR,
    upload_model,
)

C3_MODEL_PATH = RESOURCES_DIR / "3compartment_flat.cellml"
C3_OBS_DATA_PATH = RESOURCES_DIR / "3compartment_obs_data.json"
C3_PARAMS_CSV_PATH = RESOURCES_DIR / "3compartment_params_for_id.csv"


FAKE_RUNNER = """
import json, sys
from pathlib import Path
cfg = json.loads(Path(sys.argv[1]).read_text())
print("starting fake calibration", flush=True)
print("generation 0 cost: 1.0", flush=True)
Path(cfg["output_dir"], "results.json").write_text(
    json.dumps({"params": {"a/x": 1.5, "a/y": 2.0}, "cost": 0.25}))
print("best cost: 0.25", flush=True)
print("__CALIBRATION_DONE__", flush=True)
"""

SLOW_RUNNER = """
import sys, time
print("starting slow", flush=True)
time.sleep(30)
"""


def _install_runner(tmp_path, src) -> str:
    path = tmp_path / "fake_runner.py"
    path.write_text(src)
    calibration_mod.calibration.runner_path = str(path)
    return str(path)


def _setup_model_obs_params(client, model_path, obs_path, params_path) -> str:
    """Upload model + obs_data + params_for_id and return model_id."""
    model_id = upload_model(client, model_path)["model_id"]
    obs = json.loads(obs_path.read_text())
    r = client.post(
        "/api/obs_data/upload", json={"model_id": model_id, "obs_data": obs}
    )
    assert r.status_code == 200, r.text
    with open(params_path, "rb") as fh:
        r = client.post(
            f"/api/params_for_id/upload?model_id={model_id}",
            files={"file": (params_path.name, fh, "text/csv")},
        )
    assert r.status_code == 200, r.text
    return model_id


def _wait(client, job_id, timeout=15):
    offset = 0
    lines: list[str] = []
    deadline = time.time() + timeout
    while time.time() < deadline:
        s = client.get(f"/api/calibration/{job_id}/status?offset={offset}").json()
        lines += s["lines"]
        offset = s["next_offset"]
        if s["state"] != "running":
            return s, lines
        time.sleep(0.05)
    raise AssertionError(f"calibration did not finish; lines:\n" + "\n".join(lines))


# ---------------------------------------------------------------------------
# Unit tier (fake runner)
# ---------------------------------------------------------------------------
def test_calibration_defaults(client):
    body = client.get("/api/calibration/defaults").json()
    assert body["param_id_method"] == "genetic_algorithm"
    assert "CMA-ES" in body["methods"]
    assert body["num_cores"] == 1
    # pre_time / sim_time come from obs_data protocol_info (#13)
    assert "pre_time" not in body and "sim_time" not in body


def test_build_command_single_vs_mpiexec():
    mgr = calibration_mod.CalibrationManager()
    single = mgr.build_command({"num_cores": 1}, "/tmp/c.json")
    assert "mpiexec" not in single[0]
    assert single[-2:] == [mgr.runner_path, "/tmp/c.json"]

    parallel = mgr.build_command({"num_cores": 4}, "/tmp/c.json")
    assert "mpiexec" in parallel[0]
    assert parallel[1] == "-n" and parallel[2] == "4"
    assert parallel[-2:] == [mgr.runner_path, "/tmp/c.json"]


def test_calibration_streams_and_completes(client, tmp_path):
    _install_runner(tmp_path, FAKE_RUNNER)
    model_id = _setup_model_obs_params(
        client, LV_MODEL_PATH, LV_OBS_DATA_PATH, LV_PARAMS_CSV_PATH
    )
    resp = client.post("/api/calibration/run", json={"model_id": model_id, "settings": {}})
    assert resp.status_code == 200, resp.text
    job_id = resp.json()["job_id"]

    status, lines = _wait(client, job_id)
    assert status["state"] == "done", lines
    assert status["best_params"] == {"a/x": 1.5, "a/y": 2.0}
    assert status["cost"] == 0.25
    assert any("generation 0 cost" in ln for ln in lines)


def test_calibration_requires_obs_and_params_422(client, tmp_path):
    _install_runner(tmp_path, FAKE_RUNNER)
    model_id = upload_model(client, LV_MODEL_PATH)["model_id"]  # no obs/params
    resp = client.post("/api/calibration/run", json={"model_id": model_id, "settings": {}})
    assert resp.status_code == 422


def test_calibration_unknown_model_404(client):
    resp = client.post("/api/calibration/run", json={"model_id": "nope", "settings": {}})
    assert resp.status_code == 404


def test_calibration_busy_returns_409(client, tmp_path):
    _install_runner(tmp_path, SLOW_RUNNER)
    model_id = _setup_model_obs_params(
        client, LV_MODEL_PATH, LV_OBS_DATA_PATH, LV_PARAMS_CSV_PATH
    )
    r1 = client.post("/api/calibration/run", json={"model_id": model_id, "settings": {}})
    assert r1.status_code == 200
    r2 = client.post("/api/calibration/run", json={"model_id": model_id, "settings": {}})
    assert r2.status_code == 409
    # cancel the running job
    job_id = r1.json()["job_id"]
    assert client.post(f"/api/calibration/{job_id}/cancel").json()["cancelled"] is True


def test_calibration_status_unknown_job_404(client):
    assert client.get("/api/calibration/nope/status").status_code == 404


# ---------------------------------------------------------------------------
# Integration tier (real Myokit — short GA on 3compartment)
# ---------------------------------------------------------------------------
@pytest.mark.integration
def test_calibration_3compartment_genetic_algorithm(client, requires_simulation):
    model_id = _setup_model_obs_params(
        client, C3_MODEL_PATH, C3_OBS_DATA_PATH, C3_PARAMS_CSV_PATH
    )
    # No pre_time / sim_time: timing comes from the obs_data protocol_info (#13).
    settings = {
        "param_id_method": "genetic_algorithm",
        "num_calls_to_function": 30,
        "DEBUG": True,  # small population for a fast interactive-scale run
        "dt": 0.01,
    }
    resp = client.post(
        "/api/calibration/run", json={"model_id": model_id, "settings": settings}
    )
    assert resp.status_code == 200, resp.text
    job_id = resp.json()["job_id"]

    status, lines = _wait(client, job_id, timeout=600)
    assert status["state"] == "done", "\n".join(lines)

    best = status["best_params"]
    assert set(best) == {
        "global/q_lv_init",
        "aortic_root/C",
        "global/E_lv_A",
        "global/E_lv_B",
    }
    assert all(math.isfinite(v) for v in best.values())
    assert status["cost"] is not None and math.isfinite(status["cost"])
