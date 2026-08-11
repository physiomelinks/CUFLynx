"""Tests for the UQ job manager + endpoints.

Unit tests inject fake runner scripts (no Myokit). Reuse-mode UQ depends on a
completed calibration, so those tests drive a fake calibration first.
"""

import json
import time

import calibration as calibration_mod
import pytest
import uq as uq_mod
from conftest import (
    LV_MODEL_PATH,
    LV_OBS_DATA_PATH,
    LV_PARAMS_CSV_PATH,
    upload_model,
)

# A fake UQ runner: writes a results.json with two per-parameter posteriors.
# The fakes persist what the real runners persist: posterior *samples* for UQ and
# circulatory_autogen's own best-fit files for calibration. The manager derives
# the histograms and the best fit from those (#210), so a fake writing a
# CUFLynx-shaped results.json would be testing a format nothing produces.
FAKE_UQ_RUNNER = """
import csv, json, sys
import numpy as np
from pathlib import Path
cfg = json.loads(Path(sys.argv[1]).read_text())
print("starting fake uq", flush=True)
out = Path(cfg["output_dir"])
out.mkdir(parents=True, exist_ok=True)
rng = np.random.default_rng(0)
samples = np.column_stack([rng.normal(1.0, 0.1, 200), rng.normal(2.0, 0.2, 200)])
np.save(str(out / "uq_posterior_samples.npy"), samples)
with open(out / "uq_param_names.csv", "w", newline="") as fh:
    csv.writer(fh).writerows([["a/x"], ["a/y"]])
print("__UQ_META__ " + json.dumps(
    {"method": cfg["settings"].get("method", "mcmc")}), flush=True)
print("__UQ_DONE__", flush=True)
"""

# A fake calibration runner that completes with best params (for reuse-mode UQ).
FAKE_CALIB_RUNNER = """
import csv, json, sys
import numpy as np
from pathlib import Path
cfg = json.loads(Path(sys.argv[1]).read_text())
out = Path(cfg["output_dir"])
out.mkdir(parents=True, exist_ok=True)
np.save(str(out / "best_param_vals.npy"), np.array([1.5, 2.0]))
np.save(str(out / "best_cost.npy"), np.array([0.25]))
with open(out / "param_names.csv", "w", newline="") as fh:
    csv.writer(fh).writerows([["a/x"], ["a/y"]])
print("__CALIBRATION_DONE__", flush=True)
"""


def _install_uq_runner(tmp_path, src=FAKE_UQ_RUNNER) -> None:
    path = tmp_path / "fake_uq_runner.py"
    path.write_text(src)
    uq_mod.uq.runner_path = str(path)


def _install_calib_runner(tmp_path, src=FAKE_CALIB_RUNNER) -> None:
    path = tmp_path / "fake_calib_runner.py"
    path.write_text(src)
    calibration_mod.calibration.runner_path = str(path)


def _setup_model_obs_params(client, model_path, obs_path, params_path) -> str:
    model_id = upload_model(client, model_path)["model_id"]
    obs = json.loads(obs_path.read_text())
    assert client.post(
        "/api/obs_data/upload", json={"model_id": model_id, "obs_data": obs}
    ).status_code == 200
    with open(params_path, "rb") as fh:
        assert client.post(
            f"/api/params_for_id/upload?model_id={model_id}",
            files={"file": (params_path.name, fh, "text/csv")},
        ).status_code == 200
    return model_id


def _wait(client, prefix, job_id, timeout=15):
    offset = 0
    lines: list[str] = []
    deadline = time.time() + timeout
    while time.time() < deadline:
        s = client.get(f"/api/{prefix}/{job_id}/status?offset={offset}").json()
        lines += s["lines"]
        offset = s["next_offset"]
        if s["state"] != "running":
            return s, lines
        time.sleep(0.05)
    raise AssertionError("job did not finish; lines:\n" + "\n".join(lines))


def _run_calibration_to_completion(client, model_id):
    r = client.post("/api/calibration/run", json={"model_id": model_id, "settings": {}})
    assert r.status_code == 200, r.text
    status, _ = _wait(client, "calibration", r.json()["job_id"])
    assert status["state"] == "done"


# ---------------------------------------------------------------------------
def test_uq_defaults(client):
    body = client.get("/api/uq/defaults").json()
    assert body["method"] == "mcmc"
    assert body["methods"] == ["mcmc", "laplace"]
    assert body["run_calibration_first"] is False
    assert body["cost_type"] == "gaussian_MLE"


def test_uq_build_command_single_vs_mpiexec(monkeypatch):
    monkeypatch.setattr(
        uq_mod.shutil,
        "which",
        lambda name, *a, **k: "/usr/bin/mpiexec" if name == "mpiexec" else None,
    )
    mgr = uq_mod.UQManager()
    single = mgr.build_command({"num_cores": 1}, "/tmp/c.json")
    assert "mpiexec" not in single[0]
    parallel = mgr.build_command({"num_cores": 3}, "/tmp/c.json")
    assert "mpiexec" in parallel[0] and parallel[1:3] == ["-n", "3"]


def test_uq_build_command_falls_back_to_single_core_without_mpiexec(monkeypatch):
    monkeypatch.setattr(uq_mod.shutil, "which", lambda *a, **k: None)
    mgr = uq_mod.UQManager()
    cmd = mgr.build_command({"num_cores": 3}, "/tmp/c.json")
    assert "mpiexec" not in " ".join(cmd)
    assert "-n" not in cmd


def test_uq_requires_obs_and_params_422(client, tmp_path):
    _install_uq_runner(tmp_path)
    model_id = upload_model(client, LV_MODEL_PATH)["model_id"]  # no obs/params
    resp = client.post("/api/uq/run", json={"model_id": model_id, "settings": {}})
    assert resp.status_code == 422


def test_uq_reuse_without_calibration_422(client, tmp_path):
    """Default (reuse) mode errors informatively when no calibration has completed."""
    _install_uq_runner(tmp_path)
    model_id = _setup_model_obs_params(
        client, LV_MODEL_PATH, LV_OBS_DATA_PATH, LV_PARAMS_CSV_PATH
    )
    resp = client.post("/api/uq/run", json={"model_id": model_id, "settings": {}})
    assert resp.status_code == 422
    assert "calibration" in resp.json()["detail"].lower()


def test_uq_run_calibration_first_completes(client, tmp_path):
    """Self-contained mode needs no prior calibration."""
    _install_uq_runner(tmp_path)
    model_id = _setup_model_obs_params(
        client, LV_MODEL_PATH, LV_OBS_DATA_PATH, LV_PARAMS_CSV_PATH
    )
    resp = client.post(
        "/api/uq/run",
        json={"model_id": model_id, "settings": {"run_calibration_first": True}},
    )
    assert resp.status_code == 200, resp.text
    status, lines = _wait(client, "uq", resp.json()["job_id"])
    assert status["state"] == "done", lines
    assert status["method"] == "mcmc"
    assert [p["qname"] for p in status["params"]] == ["a/x", "a/y"]
    # Binned by the manager from the samples the run persisted, so the histogram
    # is derived rather than round-tripped: edges = counts + 1, and the summary
    # matches the distribution the fake sampled from.
    posterior = status["params"][0]
    assert len(posterior["bins"]) == len(posterior["counts"]) + 1
    assert posterior["mean"] == pytest.approx(1.0, abs=0.05)
    assert posterior["std"] == pytest.approx(0.1, abs=0.03)


def test_uq_reuse_after_calibration_completes(client, tmp_path):
    _install_calib_runner(tmp_path)
    _install_uq_runner(tmp_path)
    model_id = _setup_model_obs_params(
        client, LV_MODEL_PATH, LV_OBS_DATA_PATH, LV_PARAMS_CSV_PATH
    )
    _run_calibration_to_completion(client, model_id)

    resp = client.post(
        "/api/uq/run",
        json={"model_id": model_id, "settings": {"method": "laplace"}},
    )
    assert resp.status_code == 200, resp.text
    status, lines = _wait(client, "uq", resp.json()["job_id"])
    assert status["state"] == "done", lines
    assert status["method"] == "laplace"


def test_uq_unknown_model_404(client):
    assert client.post(
        "/api/uq/run", json={"model_id": "nope", "settings": {}}
    ).status_code == 404


def test_uq_status_unknown_job_404(client):
    assert client.get("/api/uq/nope/status").status_code == 404
