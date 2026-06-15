"""Windows-focused backend tests + parallel-run fallback regressions.

Motivation
----------
A user on Windows hit ``AxiosError: Request failed with status code 500`` from
the frontend. The root cause was in the job managers (calibration / sensitivity
/ UQ): a multi-core request (``num_cores > 1``) makes ``build_command`` prepend
``mpiexec``, but MPI is almost never installed on a stock Windows box. The bare
``"mpiexec"`` couldn't be launched, ``subprocess.Popen`` raised
``FileNotFoundError`` (``WinError 2``), and — being uncaught — it surfaced to the
client as a bare HTTP 500.

The managers now fall back to a single core when ``mpiexec`` is absent (see each
``build_command``). These tests pin that fallback so the 500 can't regress, and
add a couple of Windows path/interpreter hardening checks.

The fallback tests force ``mpiexec`` to look absent (via ``shutil.which``) so they
are deterministic on any host, Windows or not. Run from apps/api:

    pytest tests/test_windows.py -v
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import time

import pytest
from fastapi.testclient import TestClient

import calibration as calibration_mod
import main
import sensitivity as sensitivity_mod
import uq as uq_mod
from conftest import (
    LV_MODEL_PATH,
    LV_OBS_DATA_PATH,
    LV_PARAMS_CSV_PATH,
    upload_model,
)

IS_WINDOWS = sys.platform.startswith("win")

# A trivial cross-platform runner: write a results.json superset that every
# manager's _finalize can read, then exit 0. Lets a job complete without Myokit.
_OK_RUNNER = (
    "import json, sys\n"
    "from pathlib import Path\n"
    "cfg = json.loads(Path(sys.argv[1]).read_text())\n"
    "Path(cfg['output_dir'], 'results.json').write_text(json.dumps("
    "{'params': {}, 'cost': 0.0, 'method': 'test'}))\n"
    "print('__DONE__', flush=True)\n"
)


def _force_no_mpiexec(monkeypatch):
    """Make ``shutil.which('mpiexec')`` return None everywhere (other lookups
    are delegated to the real implementation)."""
    real_which = shutil.which
    monkeypatch.setattr(
        shutil,
        "which",
        lambda name, *a, **k: None if name == "mpiexec" else real_which(name, *a, **k),
    )


def _install_runner(monkeypatch, manager, tmp_path):
    runner = tmp_path / "ok_runner.py"
    runner.write_text(_OK_RUNNER)
    monkeypatch.setattr(manager, "runner_path", str(runner))


def _setup_model_obs_params(client: TestClient) -> str:
    """Upload the Lotka-Volterra model + obs_data + params_for_id; return id."""
    model_id = upload_model(client, LV_MODEL_PATH)["model_id"]
    obs = json.loads(LV_OBS_DATA_PATH.read_text())
    r = client.post("/api/obs_data/upload", json={"model_id": model_id, "obs_data": obs})
    assert r.status_code == 200, r.text
    with open(LV_PARAMS_CSV_PATH, "rb") as fh:
        r = client.post(
            f"/api/params_for_id/upload?model_id={model_id}",
            files={"file": (LV_PARAMS_CSV_PATH.name, fh, "text/csv")},
        )
    assert r.status_code == 200, r.text
    return model_id


def _wait_terminal(client: TestClient, kind: str, job_id: str, timeout: float = 15):
    """Poll a job's status endpoint until it leaves the 'running' state."""
    deadline = time.time() + timeout
    state = "running"
    while time.time() < deadline:
        s = client.get(f"/api/{kind}/{job_id}/status").json()
        state = s["state"]
        if state != "running":
            return state
        time.sleep(0.05)
    return state


# ---------------------------------------------------------------------------
# Regression: num_cores>1 without mpiexec must NOT 500 — it falls back to 1 core
# ---------------------------------------------------------------------------
def test_calibration_parallel_falls_back_without_mpiexec(monkeypatch, tmp_path):
    _force_no_mpiexec(monkeypatch)
    _install_runner(monkeypatch, calibration_mod.calibration, tmp_path)
    client = TestClient(main.app)
    model_id = _setup_model_obs_params(client)

    resp = client.post(
        "/api/calibration/run",
        json={"model_id": model_id, "settings": {"num_cores": 4}},
    )
    assert resp.status_code == 200, resp.text
    job_id = resp.json()["job_id"]
    assert _wait_terminal(client, "calibration", job_id) == "done"


def test_sensitivity_parallel_falls_back_without_mpiexec(monkeypatch, tmp_path):
    _force_no_mpiexec(monkeypatch)
    _install_runner(monkeypatch, sensitivity_mod.sensitivity, tmp_path)
    client = TestClient(main.app)
    model_id = _setup_model_obs_params(client)

    resp = client.post(
        "/api/sensitivity/run",
        json={"model_id": model_id, "settings": {"num_cores": 4}},
    )
    assert resp.status_code == 200, resp.text
    assert "job_id" in resp.json()
    _wait_terminal(client, "sensitivity", resp.json()["job_id"])


def test_uq_parallel_falls_back_without_mpiexec(monkeypatch, tmp_path):
    _force_no_mpiexec(monkeypatch)
    _install_runner(monkeypatch, uq_mod.uq, tmp_path)
    client = TestClient(main.app)
    model_id = _setup_model_obs_params(client)

    # run_calibration_first skips the "reuse a completed calibration" precondition
    # so the request reaches uq.start().
    resp = client.post(
        "/api/uq/run",
        json={"model_id": model_id, "settings": {"num_cores": 4, "run_calibration_first": True}},
    )
    assert resp.status_code == 200, resp.text
    assert "job_id" in resp.json()
    _wait_terminal(client, "uq", resp.json()["job_id"])


# ---------------------------------------------------------------------------
# Windows hardening — these should pass on Windows today
# ---------------------------------------------------------------------------
def test_single_core_calibration_launches_and_completes(monkeypatch, tmp_path):
    """The non-parallel path (sys.executable, a real .exe on Windows) works."""
    _install_runner(monkeypatch, calibration_mod.calibration, tmp_path)
    client = TestClient(main.app)
    model_id = _setup_model_obs_params(client)
    resp = client.post("/api/calibration/run", json={"model_id": model_id, "settings": {}})
    assert resp.status_code == 200, resp.text
    assert _wait_terminal(client, "calibration", resp.json()["job_id"]) == "done"


def test_calibration_pythons_discovers_current_interpreter():
    """Interpreter discovery must include the running interpreter on Windows
    (a ``python.exe`` path), and every entry has the expected shape."""
    client = TestClient(main.app)
    body = client.get("/api/calibration/pythons").json()
    assert body["default"] == sys.executable
    for p in body["pythons"]:
        assert {"path", "version", "ready", "missing"} <= set(p)


@pytest.mark.skipif(not IS_WINDOWS, reason="Windows drive-letter path handling")
def test_fs_list_handles_windows_drive_path():
    """The in-app folder browser must handle a Windows path (e.g. the drive of
    the current working directory) without erroring."""
    client = TestClient(main.app)
    drive = os.path.splitdrive(os.getcwd())[0] + os.sep  # e.g. "C:\\"
    resp = client.get("/api/fs/list", params={"path": drive})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "entries" in body and isinstance(body["entries"], list)
    for e in body["entries"]:
        assert {"name", "path", "is_dir"} <= set(e)
