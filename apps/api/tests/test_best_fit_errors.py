"""The best fit's error per observable (CA #705): libcuflynx's calibration writes the
error vectors itself when it ends, and a stopped run gets them from its best so far.

A fake runner stands in for CA: it calibrates until stopped, keeping
``best_param_vals.npy`` current as CA's optimisers do, and in ``best_fit_errors_only``
mode it is the rebuilt CA object writing the vectors.
"""

from __future__ import annotations

import json
import os
import time

import pytest

import ca_run_history
import calibration as calibration_mod
import calibration_runner as cr
from conftest import (
    LV_MODEL_PATH,
    LV_OBS_DATA_PATH,
    LV_PARAMS_CSV_PATH,
    WRITE_CA_RESULTS_SRC,
    upload_model,
)

STOPPABLE_RUNNER = """
import json, os, sys, time
from pathlib import Path
import numpy as np
""" + WRITE_CA_RESULTS_SRC + """
cfg = json.loads(Path(sys.argv[1]).read_text())
sub = Path(cfg["output_dir"], "genetic_algorithm_model_obs")
if cfg.get("best_fit_errors_only"):
    Path(cfg["output_dir"], "errors_config.json").write_text(json.dumps(cfg))
    np.save(str(sub / "percent_error_vec.npy"), np.array([12.5, 3.0]))
    np.save(str(sub / "std_error_vec.npy"), np.array([1.5, 0.5]))
    np.save(str(sub / "error_vec_names.npy"), np.array(["x_max", "y_max"]))
    print("wrote the best-so-far errors", flush=True)
    print("__CALIBRATION_DONE__", flush=True)
    sys.exit(0)
write_ca_results(str(sub), [["Lotka_Volterra_module/" + p] for p in
                 ("alpha", "beta", "delta", "gamma")], [1.0, 2.0, 3.0, 4.0], 0.5)
print("generation 3 best so far", flush=True)
time.sleep(60)
"""


def _setup(client) -> str:
    model_id = upload_model(client, LV_MODEL_PATH)["model_id"]
    r = client.post("/api/obs_data/upload", json={
        "model_id": model_id, "obs_data": json.loads(LV_OBS_DATA_PATH.read_text())})
    assert r.status_code == 200, r.text
    with open(LV_PARAMS_CSV_PATH, "rb") as fh:
        r = client.post(f"/api/params_for_id/upload?model_id={model_id}",
                        files={"file": (LV_PARAMS_CSV_PATH.name, fh, "text/csv")})
    assert r.status_code == 200, r.text
    return model_id


def test_a_stopped_run_gets_its_best_so_far_error_vectors(client, tmp_path, monkeypatch):
    """Stopping terminates the runner, so CA never writes the best fit's errors: the
    manager has them written from the run's best_param_vals.npy, and says so until then."""
    runner = tmp_path / "fake_runner.py"
    runner.write_text(STOPPABLE_RUNNER)
    monkeypatch.setattr(calibration_mod.calibration, "runner_path", str(runner))
    model_id = _setup(client)
    work = tmp_path / "w"
    r = client.post("/api/calibration/run", json={
        "model_id": model_id, "settings": {"config_outputs_dir": str(work)}})
    assert r.status_code == 200, r.text
    job_id = r.json()["job_id"]
    deadline, lines, offset = time.time() + 15, [], 0
    while not any("best so far" in line for line in lines):
        assert time.time() < deadline, lines
        s = client.get(f"/api/calibration/{job_id}/status?offset={offset}").json()
        lines += s["lines"]
        offset = s["next_offset"]
        time.sleep(0.05)

    client.post(f"/api/calibration/{job_id}/cancel")
    s = client.get(f"/api/calibration/{job_id}/status").json()
    assert s["state"] == "cancelled" and s["errors_pending"] is True
    deadline = time.time() + 15
    while s["errors_pending"]:
        assert time.time() < deadline, s
        time.sleep(0.05)
        s = client.get(f"/api/calibration/{job_id}/status").json()

    assert s["state"] == "cancelled"
    assert s["percent_error"] == [12.5, 3.0] and s["std_error"] == [1.5, 0.5]
    assert s["error_labels"] == ["x_max", "y_max"]
    assert any("best-so-far errors" in line for line in s["lines"])
    # the rebuild is the run's own config, on one process
    errors_config = json.loads(next(work.rglob("errors_config.json")).read_text())
    assert errors_config["best_fit_errors_only"] is True and errors_config["num_cores"] == 1
    assert errors_config["obs_path"] and errors_config["params_path"]


def test_a_stopped_run_with_no_best_so_far_writes_no_errors(tmp_path):
    mgr = calibration_mod.CalibrationManager()
    job = calibration_mod.CalibrationJob("j", str(tmp_path), model_id="m",
                                         params_path=str(tmp_path / "none.csv"))
    job.state, job.errors_pending = "cancelled", True
    mgr._job = job
    mgr._finalize(job, code=-15)
    mgr._stopped_errors(job)
    assert job.errors_pending is False and job.percent_error is None
    assert job.errors_proc is None  # nothing was started


def test_only_this_runs_error_vectors_count(tmp_path):
    import numpy as np

    sub = tmp_path / "genetic_algorithm_m_obs"
    sub.mkdir()
    np.save(sub / "best_param_vals.npy", np.array([1.0]))
    assert not ca_run_history.has_error_vectors(str(tmp_path))
    np.save(sub / "percent_error_vec.npy", np.array([1.0]))
    old = time.time() - 100
    os.utime(sub / "percent_error_vec.npy", (old, old))
    assert ca_run_history.has_error_vectors(str(tmp_path))
    # an earlier run's vectors, which an older libcuflynx leaves in place
    assert not ca_run_history.has_error_vectors(str(tmp_path), newer_than=time.time())


class _ErrorsParamID:
    """A CA param_id object; ``writes``: its run() writes the error vectors itself (a
    libcuflynx with save_best_fit_errors)."""

    rank = 0

    def __init__(self, output_dir, writes):
        self.output_dir = output_dir
        self.calls = []
        if writes:
            self.save_best_fit_errors = lambda: self.calls.append("save")

    def simulate_with_best_param_vals(self):
        self.calls.append("simulate")

    def plot_outputs(self):
        self.calls.append("plot")
        self.write()

    def write(self):
        import numpy as np

        np.save(os.path.join(self.output_dir, "percent_error_vec.npy"), np.array([4.0]))
        np.save(os.path.join(self.output_dir, "std_error_vec.npy"), np.array([0.5]))


def test_the_runner_reads_the_error_vectors_ca_wrote_without_plotting(tmp_path):
    param_id = _ErrorsParamID(str(tmp_path), writes=True)
    param_id.write()  # what run() wrote
    errors = cr._generate_error_vectors(param_id, str(tmp_path))
    assert param_id.calls == []  # no second simulation, no figures
    assert errors["percent_error"] == [4.0] and errors["std_error"] == [0.5]


def test_an_older_libcuflynx_still_gets_its_error_vectors_from_plotting(tmp_path):
    param_id = _ErrorsParamID(str(tmp_path), writes=False)
    errors = cr._generate_error_vectors(param_id, str(tmp_path))
    assert param_id.calls == ["simulate", "plot"]
    assert errors["percent_error"] == [4.0]


def test_a_libcuflynx_without_the_writer_builds_nothing_for_a_stopped_run(monkeypatch):
    monkeypatch.setattr(cr, "_param_id_class", lambda: type("Old", (), {}))
    monkeypatch.setattr(cr, "_build_param_id", lambda *a, **k: pytest.fail("built"))
    assert cr.run({"best_fit_errors_only": True}) == {"rank": 0, "errors": None}


def _writes_errors():
    try:
        return hasattr(cr._param_id_class(), "save_best_fit_errors")
    except ImportError:
        return False


def _status_until(client, job_id, done, timeout):
    deadline = time.time() + timeout
    while True:
        s = client.get(f"/api/calibration/{job_id}/status").json()
        if done(s):
            return s
        assert time.time() < deadline, "\n".join(s["lines"][-40:])
        time.sleep(0.2)


@pytest.mark.integration
def test_real_finished_and_stopped_calibrations_have_their_error_vectors(
        client, requires_simulation, tmp_path):
    if not _writes_errors():
        pytest.skip("this libcuflynx's calibration does not write its error vectors")
    model_id = _setup(client)

    # finished: CA's run() wrote them, from one simulation at the best fit
    work = tmp_path / "done"
    r = client.post("/api/calibration/run", json={"model_id": model_id, "settings": {
        "config_outputs_dir": str(work), "param_id_method": "CMA-ES",
        "num_calls_to_function": 30, "dt": 0.01}})
    assert r.status_code == 200, r.text
    s = _status_until(client, r.json()["job_id"], lambda s: s["state"] != "running", 300)
    assert s["state"] == "done", "\n".join(s["lines"][-40:])
    assert s["percent_error"] and len(s["percent_error"]) == len(s["error_labels"])
    assert not list(work.rglob("*.png"))  # no reconstruction figures drawn for them

    # stopped partway: written afterwards from its best so far
    work = tmp_path / "stopped"
    r = client.post("/api/calibration/run", json={"model_id": model_id, "settings": {
        "config_outputs_dir": str(work), "param_id_method": "CMA-ES",
        "num_calls_to_function": 100000, "dt": 0.01}})
    job_id = r.json()["job_id"]
    deadline = time.time() + 300
    while not list(work.rglob("best_param_vals.npy")):
        assert time.time() < deadline, "no best so far was saved"
        time.sleep(0.2)
    client.post(f"/api/calibration/{job_id}/cancel")
    s = _status_until(client, job_id, lambda s: not s["errors_pending"], 300)
    assert s["state"] == "cancelled"
    assert s["percent_error"] and len(s["percent_error"]) == len(s["error_labels"]), \
        "\n".join(s["lines"][-40:])


def test_the_rebuilt_object_writes_the_errors_from_the_runs_config(monkeypatch):
    built = {}

    class _New:
        rank = 0

        def save_best_fit_errors(self):
            return {"percent_error": "/r/percent_error_vec.npy"}

    monkeypatch.setattr(cr, "_param_id_class", lambda: _New)
    monkeypatch.setattr(cr, "_build_param_id",
                        lambda config, **k: built.update(config=config, **k) or _New())
    config = {"best_fit_errors_only": True, "output_dir": "/w"}
    assert cr.run(config) == {"rank": 0, "errors": {"percent_error": "/r/percent_error_vec.npy"}}
    assert built == {"config": config, "announce": False}
