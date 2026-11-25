"""The Progress tab's readers of CA #702's progress files, and the statuses that carry them."""

from __future__ import annotations

import json
import os
import time

import numpy as np
import pytest

import ca_run_history
import emulator as emulator_mod
import sensitivity as sensitivity_mod
import uq as uq_mod


def _progress(directory, kind="sobol", state="running", phases=None, mtime=None):
    os.makedirs(directory, exist_ok=True)
    path = os.path.join(directory, ca_run_history.PROGRESS_FILE)
    with open(path, "w") as f:
        json.dump({"schema_version": 1, "kind": kind, "state": state,
                   "started": "2026-11-26T01:00:00+00:00",
                   "updated": "2026-11-26T01:00:05+00:00",
                   "phases": phases if phases is not None else [
                       {"name": "sampling", "state": "running", "done": 3, "total": 10,
                        "failed": 1}]}, f)
    if mtime is not None:
        os.utime(path, (mtime, mtime))
    return path


# --- readers ---------------------------------------------------------------

def test_run_progress_reads_the_file(tmp_path):
    _progress(tmp_path)
    doc = ca_run_history.run_progress(str(tmp_path))
    assert doc["kind"] == "sobol" and doc["phases"][0]["done"] == 3


def test_run_progress_refuses_an_earlier_runs_file(tmp_path):
    _progress(tmp_path, mtime=time.time() - 3600)
    assert ca_run_history.run_progress(str(tmp_path), newer_than=time.time() - 60) is None
    assert ca_run_history.run_progress(str(tmp_path)) is not None


def test_a_run_whose_process_exited_still_running_reads_stopped(tmp_path):
    _progress(tmp_path, phases=[
        {"name": "sampling", "state": "done", "done": 10, "total": 10, "failed": 0},
        {"name": "analysis", "state": "running", "done": 0, "total": 2, "failed": 0}])
    doc = ca_run_history.run_progress(str(tmp_path), finished=True)
    assert doc["state"] == "stopped"
    assert [p["state"] for p in doc["phases"]] == ["done", "stopped"]


def test_run_progress_none_without_a_readable_file(tmp_path):
    assert ca_run_history.run_progress(str(tmp_path)) is None
    (tmp_path / ca_run_history.PROGRESS_FILE).write_text("{not json")
    assert ca_run_history.run_progress(str(tmp_path)) is None


def test_find_progress_picks_the_kind_asked_for(tmp_path):
    _progress(tmp_path / "sensitivity", kind="sobol")
    _progress(tmp_path / "emulators" / "m_obs", kind="emulation")
    doc = ca_run_history.find_progress(str(tmp_path), "emulation")
    assert doc["kind"] == "emulation"
    assert ca_run_history.find_progress(str(tmp_path), "profile_likelihood") is None


def test_find_progress_takes_the_newest_and_stops_at_its_depth(tmp_path):
    _progress(tmp_path / "a", kind="emulation", phases=[], mtime=time.time() - 100)
    _progress(tmp_path / "b", kind="emulation",
              phases=[{"name": "training", "state": "running", "done": 1, "total": 3,
                       "failed": 0}])
    assert ca_run_history.find_progress(str(tmp_path), "emulation")["phases"]
    _progress(tmp_path / "x" / "y" / "z" / "deep", kind="sobol")
    assert ca_run_history.find_progress(str(tmp_path), "sobol", depth=2) is None
    assert ca_run_history.find_progress(str(tmp_path), "sobol", depth=4) is not None


def _design(directory, status):
    os.makedirs(directory, exist_ok=True)
    design = np.arange(len(status) * 2, dtype=float).reshape(len(status), 2)
    np.save(os.path.join(directory, ca_run_history.SOBOL_DESIGN), design)
    np.save(os.path.join(directory, ca_run_history.SOBOL_SAMPLE_STATUS),
            np.asarray(status, dtype=np.int8))
    with open(os.path.join(directory, ca_run_history.SOBOL_DESIGN_PARAMS), "w") as f:
        json.dump({"names": ["a/k", "b/k"], "bounds": [[0, 1], [0, 2]], "num_samples": 4,
                   "sample_type": "saltelli", "rows": len(status)}, f)
    return design


def test_sobol_design_keeps_every_failure_and_spreads_the_rest(tmp_path):
    status = [1] * 50 + [2, 2, 2] + [0] * 10
    design = _design(tmp_path, status)
    found = ca_run_history.sobol_design(str(tmp_path), max_points=13)
    assert found["names"] == ["a/k", "b/k"]
    assert found["counts"] == {"total": 63, "ran": 50, "failed": 3, "not_run": 10}
    assert len(found["rows"]) == 13 and found["status"].count(2) == 3
    assert 0 not in found["status"]
    # every failed row is there, with its own values
    assert [found["rows"][i] for i, s in enumerate(found["status"]) if s == 2] == \
        design[50:53].tolist()


def test_sobol_design_without_status_shows_nothing_run(tmp_path):
    _design(tmp_path, [1, 1])
    os.remove(tmp_path / ca_run_history.SOBOL_SAMPLE_STATUS)
    found = ca_run_history.sobol_design(str(tmp_path))
    assert found["rows"] == [] and found["counts"]["not_run"] == 2


def test_sobol_convergence_passes_cas_document_through(tmp_path):
    assert ca_run_history.sobol_convergence(str(tmp_path)) is None
    doc = {"n": [4, 8], "outputs": {"y": {"S1": {"a/k": [[0.5, 0.1], [0.4, None]]},
                                          "ST": {"a/k": [[0.6, 0.1], [0.5, 0.05]]}}}}
    (tmp_path / ca_run_history.SOBOL_CONVERGENCE).write_text(json.dumps(doc))
    assert ca_run_history.sobol_convergence(str(tmp_path)) == doc


def test_cas_own_writer_is_read(tmp_path):
    """The file libcuflynx writes, not a copy of its shape, is what the reader reads."""
    progress = pytest.importorskip("libcuflynx.utilities.progress")
    with progress.ProgressFile(str(tmp_path), "emulation") as pf:
        pf.phase("s1", 8, name="sampling", stage=1, method="lhs")
        pf.advance("s1", done=8, failed=1)
        pf.phase("training", 2, models=[{"name": "rbf", "state": "done", "score": 0.9,
                                         "duration_s": 1.0}], metric="r2")
    doc = ca_run_history.find_progress(str(tmp_path), "emulation")
    assert doc["state"] == "done"
    assert doc["phases"][0] == {"name": "sampling", "state": "done", "done": 8, "total": 8,
                                "failed": 1, "stage": 1, "method": "lhs"}
    assert doc["phases"][1]["models"][0]["score"] == 0.9


# --- statuses --------------------------------------------------------------

def _sa_job(manager, output_dir, **attrs):
    job = sensitivity_mod.SensitivityJob("j", str(output_dir))
    for key, value in attrs.items():
        setattr(job, key, value)
    manager._job = job
    return job


def test_sensitivity_status_carries_this_runs_progress(tmp_path):
    mgr = sensitivity_mod.SensitivityManager()
    job = _sa_job(mgr, tmp_path)
    assert mgr.status("j")["progress"] is None
    _progress(tmp_path)
    assert mgr.status("j")["progress"]["phases"][0]["failed"] == 1
    job.state = "cancelled"
    assert mgr.status("j")["progress"]["state"] == "stopped"


def test_sensitivity_status_ignores_an_earlier_runs_progress(tmp_path):
    _progress(tmp_path, state="done", mtime=time.time() - 3600)
    mgr = sensitivity_mod.SensitivityManager()
    _sa_job(mgr, tmp_path)
    assert mgr.status("j")["progress"] is None
    assert mgr.sobol_progress("j") == {"design": None, "convergence": None}


def test_sobol_progress_reads_the_design_of_this_run(tmp_path):
    mgr = sensitivity_mod.SensitivityManager()
    _sa_job(mgr, tmp_path)
    _progress(tmp_path)
    _design(tmp_path, [1, 2, 0])
    found = mgr.sobol_progress("j")
    assert found["design"]["status"] == [1, 2] and found["convergence"] is None
    assert mgr.sobol_progress("other") is None


def test_a_reduction_of_an_earlier_sobol_run_shows_no_sobol_progress(tmp_path):
    mgr = sensitivity_mod.SensitivityManager()
    _sa_job(mgr, tmp_path, reduce_only=True)
    _progress(tmp_path)
    _design(tmp_path, [1])
    assert mgr.sobol_progress("j") == {"design": None, "convergence": None}


def test_emulator_status_finds_the_training_progress_under_the_working_dir(tmp_path):
    mgr = emulator_mod.EmulatorManager()
    job = emulator_mod.EmulatorJob("e", str(tmp_path))
    mgr._job = job
    assert mgr.status("e")["progress"] is None
    _progress(tmp_path / "emulators" / "m_obs", kind="emulation")
    assert mgr.status("e")["progress"]["kind"] == "emulation"


def test_emulator_step_progress_is_under_its_step(tmp_path):
    mgr = emulator_mod.EmulatorManager()
    job = emulator_mod.EmulatorJob("e", str(tmp_path / "wd"))
    job.study_step = {"step_dir": str(tmp_path / "steps" / "emulation-1")}
    mgr._job = job
    _progress(tmp_path / "steps" / "emulation-1" / "emulator", kind="emulation")
    assert mgr.status("e")["progress"] is not None


def test_uq_status_carries_a_profile_likelihoods_progress_only(tmp_path):
    mgr = uq_mod.UQManager()
    job = uq_mod.UQJob("u", str(tmp_path))
    mgr._job = job
    _progress(tmp_path / ca_run_history.PROFILE_LIKELIHOOD_SUBDIR, kind="profile_likelihood")
    assert mgr.status("u")["progress"] is None  # an MCMC run is not looked for
    job.profiling = True
    assert mgr.status("u")["progress"]["kind"] == "profile_likelihood"


def test_sobol_progress_route(client):
    assert client.get("/api/sensitivity/nope/sobol-progress").status_code == 404
    assert client.get("/api/param_reduction/nope/sobol-progress").status_code == 404


# --- a real Sobol run ------------------------------------------------------

@pytest.mark.integration
@pytest.mark.parametrize("as_step", [False, True], ids=["direct", "study-step"])
def test_a_real_sobol_run_reports_cas_progress_and_design(client, requires_simulation, tmp_path,
                                                          request, as_step):
    """Through the route and the real runner, run directly and as a step of the study
    workflow: the status carries the progress.json CA wrote where this manager looks for it,
    and /sobol-progress the design and convergence."""
    pytest.importorskip("libcuflynx.utilities.progress")
    if as_step:
        request.getfixturevalue("study_workflow_on")
    from test_sensitivity_run import _setup_model_obs_params, _wait

    model_id = _setup_model_obs_params(client)
    resp = client.post("/api/sensitivity/run", json={"model_id": model_id, "settings": {
        "method": "sobol", "sample_type": "saltelli", "num_samples": 8, "dt": 0.01,
        "num_cores": 1, "working_dir": str(tmp_path)}})
    assert resp.status_code == 200, resp.text
    job_id = resp.json()["job_id"]
    status, lines = _wait(client, job_id, timeout=600)
    assert status["state"] == "done", "\n".join(lines)
    assert bool(status["study_step"]) == as_step
    progress = status["progress"]
    assert progress["kind"] == "sobol" and progress["state"] == "done"
    sampling = next(p for p in progress["phases"] if p["name"] == "sampling")
    assert sampling["done"] == sampling["total"] > 0
    found = client.get(f"/api/sensitivity/{job_id}/sobol-progress").json()
    assert found["design"]["counts"]["total"] == sampling["total"]
    assert found["design"]["counts"]["not_run"] == 0
    assert found["convergence"]["n"]
