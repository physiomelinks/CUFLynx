"""Sensitivity-analysis run tests: the API + a real short local-SA integration run.

The unit tier drives the endpoint with a fake runner (no Myokit). The integration
test runs a real finite-difference *local* sensitivity on the 3compartment model —
the cheapest real analysis run (a few evaluations about the current point, no
sampling and no prior calibration). Mirrors ``test_calibration.py``.
"""

from __future__ import annotations

import json
import time

import pytest

import sensitivity as sensitivity_mod
from conftest import RESOURCES_DIR, upload_model

C3_MODEL_PATH = RESOURCES_DIR / "3compartment_flat.cellml"
C3_OBS_DATA_PATH = RESOURCES_DIR / "3compartment_obs_data.json"
C3_PARAMS_CSV_PATH = RESOURCES_DIR / "3compartment_params_for_id.csv"


# A fake runner: writes a minimal local-SA results.json and prints the done marker,
# so the endpoint/plumbing can be tested without Myokit.
FAKE_RUNNER = """
import json, sys
from pathlib import Path
cfg = json.loads(Path(sys.argv[1]).read_text())
Path(cfg["output_dir"], "results.json").write_text(json.dumps({
    "method": "local",
    "indices": {"local": {"out^{1,1}": {"p/a": 0.5, "p/b": -0.2}}},
    "param_names": ["p/a", "p/b"],
    "output_names": ["out^{1,1}"],
}))
print("__SENSITIVITY_DONE__", flush=True)
"""


def _install_runner(tmp_path, src) -> str:
    path = tmp_path / "fake_sensitivity_runner.py"
    path.write_text(src)
    sensitivity_mod.sensitivity.runner_path = str(path)
    return str(path)


def _setup_model_obs_params(client) -> str:
    model_id = upload_model(client, C3_MODEL_PATH)["model_id"]
    obs = json.loads(C3_OBS_DATA_PATH.read_text())
    r = client.post("/api/obs_data/upload", json={"model_id": model_id, "obs_data": obs})
    assert r.status_code == 200, r.text
    with open(C3_PARAMS_CSV_PATH, "rb") as fh:
        r = client.post(
            f"/api/params_for_id/upload?model_id={model_id}",
            files={"file": (C3_PARAMS_CSV_PATH.name, fh, "text/csv")},
        )
    assert r.status_code == 200, r.text
    return model_id


def _wait(client, job_id, timeout=15):
    offset = 0
    lines: list[str] = []
    deadline = time.time() + timeout
    while time.time() < deadline:
        s = client.get(f"/api/sensitivity/{job_id}/status?offset={offset}").json()
        lines += s["lines"]
        offset = s["next_offset"]
        if s["state"] != "running":
            return s, lines
        time.sleep(0.05)
    raise AssertionError("sensitivity did not finish; lines:\n" + "\n".join(lines))


# ---------------------------------------------------------------------------
# Unit tier (fake runner, no Myokit)
# ---------------------------------------------------------------------------
def test_sensitivity_run_requires_obs_and_params(client):
    """Without obs_data + params_for_id the run is rejected up front (422)."""
    model_id = upload_model(client, C3_MODEL_PATH)["model_id"]
    resp = client.post(
        "/api/sensitivity/run",
        json={"model_id": model_id, "settings": {"method": "local"}},
    )
    assert resp.status_code == 422
    assert "obs_data" in resp.json()["detail"]


def test_sensitivity_run_launches_and_completes(client, tmp_path):
    _install_runner(tmp_path, FAKE_RUNNER)
    model_id = _setup_model_obs_params(client)
    resp = client.post(
        "/api/sensitivity/run",
        json={
            "model_id": model_id,
            "settings": {"method": "local", "gradient_method": "FD", "nominal": "current"},
        },
    )
    assert resp.status_code == 200, resp.text
    status, lines = _wait(client, resp.json()["job_id"])
    assert status["state"] == "done", "\n".join(lines)


def test_sensitivity_status_404_for_unknown_job(client):
    assert client.get("/api/sensitivity/nope/status").status_code == 404


# ---------------------------------------------------------------------------
# Integration tier (real Myokit — short local FD sensitivity on 3compartment)
# ---------------------------------------------------------------------------
@pytest.mark.integration
def test_sensitivity_3compartment_local_fd(client, requires_simulation):
    model_id = _setup_model_obs_params(client)
    settings = {
        "method": "local",
        "gradient_method": "FD",
        "nominal": "current",  # about the model's current values -> no calibration needed
        "rel_step": 0.05,
        "dt": 0.01,
        "num_cores": 1,
    }
    resp = client.post(
        "/api/sensitivity/run", json={"model_id": model_id, "settings": settings}
    )
    assert resp.status_code == 200, resp.text

    status, lines = _wait(client, resp.json()["job_id"], timeout=600)
    assert status["state"] == "done", "\n".join(lines)

    indices = status.get("indices") or {}
    assert indices, f"no sensitivity indices returned; log:\n" + "\n".join(lines)
