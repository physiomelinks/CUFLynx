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

# Writes history CSVs into a <case_type> subdir (like circulatory_autogen) before
# finishing, so the progress endpoint has something to parse.
HISTORY_RUNNER = """
import json, os, sys
from pathlib import Path
cfg = json.loads(Path(sys.argv[1]).read_text())
sub = Path(cfg["output_dir"], "genetic_algorithm_model_obs")
sub.mkdir(parents=True, exist_ok=True)
(sub / "best_cost_history.csv").write_text(
    "1.0, 2.0, 3.0\\n0.5, 0.7, 0.9\\n")
(sub / "best_param_vals_history.csv").write_text(
    "a x,a y\\n0.10, 0.20\\n0.30, 0.40\\n")
Path(cfg["output_dir"], "results.json").write_text(
    json.dumps({"params": {"a/x": 1.5}, "cost": 0.5}))
print("__CALIBRATION_DONE__", flush=True)
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
    # methods are introspected from CA's PARAM_ID_METHODS schema (fallback list
    # when CA lacks it): a list of {value, label, gradient_based, ...}.
    methods = body["methods"]
    assert any(m["value"] == "CMA-ES" for m in methods)
    assert all({"value", "label", "gradient_based"} <= set(m) for m in methods)
    assert body["num_cores"] == 1
    # pre_time / sim_time come from obs_data protocol_info (#13)
    assert "pre_time" not in body and "sim_time" not in body


def _force_mpiexec(monkeypatch, present: bool):
    """Pin mpiexec discovery so build_command tests don't depend on the host
    actually having (or lacking) an MPI runtime."""
    path = "/usr/bin/mpiexec" if present else None
    monkeypatch.setattr(
        calibration_mod.shutil,
        "which",
        lambda name, *a, **k: path if name == "mpiexec" else None,
    )


def test_build_command_single_vs_mpiexec(monkeypatch):
    _force_mpiexec(monkeypatch, present=True)
    mgr = calibration_mod.CalibrationManager()
    single = mgr.build_command({"num_cores": 1}, "/tmp/c.json")
    assert "mpiexec" not in single[0]
    assert single[-2:] == [mgr.runner_path, "/tmp/c.json"]

    parallel = mgr.build_command({"num_cores": 4}, "/tmp/c.json")
    assert "mpiexec" in parallel[0]
    assert parallel[1] == "-n" and parallel[2] == "4"
    assert parallel[-2:] == [mgr.runner_path, "/tmp/c.json"]


def test_build_command_uses_selected_python(monkeypatch):
    _force_mpiexec(monkeypatch, present=True)
    mgr = calibration_mod.CalibrationManager()
    cmd = mgr.build_command({"num_cores": 1, "python": "/custom/py"}, "/tmp/c.json")
    assert cmd[0] == "/custom/py"
    mpi = mgr.build_command({"num_cores": 2, "python": "/custom/py"}, "/tmp/c.json")
    assert "mpiexec" in mpi[0]
    assert "/custom/py" in mpi


def test_build_command_falls_back_to_single_core_without_mpiexec(monkeypatch):
    """num_cores>1 with no mpiexec must yield a single-core command (no mpiexec),
    not a command that launches a non-existent 'mpiexec' (-> 500)."""
    _force_mpiexec(monkeypatch, present=False)
    mgr = calibration_mod.CalibrationManager()
    cmd = mgr.build_command({"num_cores": 4}, "/tmp/c.json")
    assert "mpiexec" not in " ".join(cmd)
    assert "-n" not in cmd
    assert cmd[-2:] == [mgr.runner_path, "/tmp/c.json"]


def test_calibration_pythons_lists_interpreters(client):
    body = client.get("/api/calibration/pythons").json()
    assert "default" in body
    assert isinstance(body["pythons"], list)
    for p in body["pythons"]:
        assert {"path", "version", "ready", "missing"} <= set(p)


def test_calibration_invalid_python_returns_422(client, tmp_path):
    _install_runner(tmp_path, FAKE_RUNNER)
    model_id = _setup_model_obs_params(
        client, LV_MODEL_PATH, LV_OBS_DATA_PATH, LV_PARAMS_CSV_PATH
    )
    resp = client.post(
        "/api/calibration/run",
        json={"model_id": model_id, "settings": {"python_path": "/no/such/python"}},
    )
    assert resp.status_code == 422


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


def test_read_history_parses_subdir_and_tolerates_partial(tmp_path):
    sub = tmp_path / "genetic_algorithm_model_obs"
    sub.mkdir()
    # Two full generations plus a partially-flushed final row.
    (sub / "best_cost_history.csv").write_text(
        "0.9, 1.0, 1.1\n0.4, 0.6, 0.8\n0.3, 0.3"  # last line shorter, still parses
    )
    (sub / "best_param_vals_history.csv").write_text(
        "global q_lv_init,aortic_root C\n0.75, 0.30\n1.00, 0.29\n1.00,"  # trailing
    )
    hist = calibration_mod._read_history(str(tmp_path))
    assert hist["param_names"] == ["global q_lv_init", "aortic_root C"]
    # cost: all rows parse (variable width tolerated); best is column 0.
    assert [row[0] for row in hist["cost_history"]] == [0.9, 0.4, 0.3]
    # params: only full-width rows kept (trailing "1.00," has wrong width).
    assert hist["param_history"] == [[0.75, 0.30], [1.00, 0.29]]


def test_read_history_missing_files_returns_empty(tmp_path):
    hist = calibration_mod._read_history(str(tmp_path))
    assert hist == {"param_names": [], "cost_history": [], "param_history": []}


def test_calibration_progress_endpoint(client, tmp_path):
    _install_runner(tmp_path, HISTORY_RUNNER)
    model_id = _setup_model_obs_params(
        client, LV_MODEL_PATH, LV_OBS_DATA_PATH, LV_PARAMS_CSV_PATH
    )
    resp = client.post("/api/calibration/run", json={"model_id": model_id, "settings": {}})
    job_id = resp.json()["job_id"]
    _wait(client, job_id)

    prog = client.get(f"/api/calibration/{job_id}/progress").json()
    assert prog["param_names"] == ["a x", "a y"]
    assert [row[0] for row in prog["cost_history"]] == [1.0, 0.5]
    assert prog["param_history"] == [[0.10, 0.20], [0.30, 0.40]]


def test_calibration_progress_unknown_job_404(client):
    assert client.get("/api/calibration/nope/progress").status_code == 404


def test_calibration_honors_config_outputs_dir(client, tmp_path):
    _install_runner(tmp_path, FAKE_RUNNER)
    out = tmp_path / "my_outputs"
    model_id = _setup_model_obs_params(
        client, LV_MODEL_PATH, LV_OBS_DATA_PATH, LV_PARAMS_CSV_PATH
    )
    resp = client.post(
        "/api/calibration/run",
        json={"model_id": model_id, "settings": {"config_outputs_dir": str(out)}},
    )
    assert resp.status_code == 200, resp.text
    status, _ = _wait(client, resp.json()["job_id"])
    assert status["state"] == "done"
    # Runner wrote results.json into the configured dir (proves it was used).
    assert (out / "results.json").exists()


def test_calibration_config_outputs_dir_must_be_absolute(client, tmp_path):
    _install_runner(tmp_path, FAKE_RUNNER)
    model_id = _setup_model_obs_params(
        client, LV_MODEL_PATH, LV_OBS_DATA_PATH, LV_PARAMS_CSV_PATH
    )
    resp = client.post(
        "/api/calibration/run",
        json={"model_id": model_id, "settings": {"config_outputs_dir": "relative/dir"}},
    )
    assert resp.status_code == 422


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
