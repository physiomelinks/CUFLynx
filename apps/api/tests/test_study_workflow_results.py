"""Unit tier: the panels read a study workflow step's results from what CA wrote for it.

* a local sensitivity step: libcuflynx's signed d ln Y / d ln p (``local_sensitivity_signed
  .csv``, labelled as its Sobol indices), not the unsigned relative file beside it;
* a uq step: its posterior predictive check (``<step>/posterior_predictive``: coverage and
  the predictive band), or why it failed;
* reopening a working dir: each panel from the latest done step of its kind, unless a direct
  run's results are newer.

The step directories are written here in CA's layout; the real runs are in
``test_study_workflow_integration.py``.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import numpy as np
import pytest

import ca_run_history
import load_outputs
import sensitivity as sensitivity_mod
import uq as uq_mod
from conftest import write_ca_results

LV = "Lotka_Volterra_module"
QNAMES = [f"{LV}/{p}" for p in ("alpha", "beta", "delta", "gamma")]
OUTPUTS = ["x_{max} (max) (Exp0, Sub0)", "y_{max} (max) (Exp0, Sub0)"]


def _csv(path: Path, header: list, rows: list) -> None:
    import csv  # noqa: PLC0415

    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        csv.writer(fh).writerows([header, *rows])


def _step(run: Path, step_id: str, result: dict, finished: str) -> Path:
    directory = run / step_id
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "step_result.json").write_text(json.dumps(
        {"step": step_id, "status": "done", "finished": finished, **result}))
    return directory


def _local_step(run: Path, finished="2026-11-17T10:00:00") -> Path:
    sens = run / "local-sa-1" / "sensitivity"
    # the unsigned relative file CA also writes, by the param-id engine's labels
    _csv(sens / "local_sensitivity_relative.csv", ["output", *QNAMES],
         [["x_{max} (max)", 0.5, 0.5, 0.5, 0.5], ["y_{max} (max)", 0.5, 0.5, 0.5, 0.5]])
    signed = sens / ca_run_history.LOCAL_SIGNED_FILE
    _csv(signed, ["output", *QNAMES],
         [[OUTPUTS[0], -0.25, 0.75, "nan", 1.0], [OUTPUTS[1], 0.3, -0.7, 0.8, -0.4]])
    return _step(run, "local-sa-1", {"kind": "sensitivity", "method": "local",
                                     "sensitivity_dir": str(sens),
                                     "local_sensitivity_signed": str(signed),
                                     "nominal": {"source": "sliders-1", "parameters": QNAMES,
                                                 "values": [2.0, 0.4, 0.3, 1.5]}}, finished)


def _write_predictive(directory: Path) -> dict:
    directory.mkdir(parents=True, exist_ok=True)
    truth, std = np.array([10.0, 20.0]), np.array([1.0, 2.0])
    preds = truth[None, :] + np.linspace(-1, 1, 12)[:, None] * std[None, :]
    np.savez(directory / ca_run_history.PREDICTIVE_FILE, thetas=np.zeros((12, 4)),
             predictions=preds, ground_truth=truth, std=std,
             labels=np.array(OUTPUTS, dtype=object))
    coverage = {"coverage": {"num_observables": 2, "levels": {
        "0.95": {"predictive_coverage": 1.0, "sample_interval_coverage": 0.9}}},
        "num_samples": 12}
    (directory / ca_run_history.PREDICTIVE_COVERAGE_FILE).write_text(json.dumps(coverage))
    return coverage


def _uq_step(run: Path, finished="2026-11-17T10:00:00", predictive=True) -> Path:
    directory = run / "mcmc-1"
    dist = directory / "distribution"
    dist.mkdir(parents=True, exist_ok=True)
    np.save(dist / "samples.npy", np.random.default_rng(0).normal(1.0, 0.1, size=(200, 4)))
    (dist / "distribution.json").write_text(json.dumps(
        {"parameters": [{"targets": [q.split("/")]} for q in QNAMES]}))
    result = {"kind": "uq", "method": "mcmc", "parameters": QNAMES,
              "distribution_dir": str(dist), "uq_output_dir": str(directory / "uq")}
    if predictive:
        pp = directory / "posterior_predictive"
        _write_predictive(pp)
        result.update({"posterior_predictive_dir": str(pp),
                       "posterior_predictive_summary": {"num_samples": 12}})
    else:
        result["posterior_predictive_summary"] = {"error": "the model failed at every draw"}
    return _step(run, "mcmc-1", result, finished)


# -- signed local sensitivity -------------------------------------------------------------

def test_a_local_step_reads_the_signed_coefficients_with_the_sobol_labels(tmp_path):
    step = _local_step(tmp_path)
    data = ca_run_history.step_local_sensitivity(ca_run_history.step_result(str(step)))
    assert data["output_names"] == OUTPUTS
    assert data["param_names"] == QNAMES
    local = data["indices"]["local"]
    assert local[OUTPUTS[0]][f"{LV}/alpha"] == pytest.approx(-0.25)
    assert local[OUTPUTS[1]][f"{LV}/beta"] == pytest.approx(-0.7)
    assert local[OUTPUTS[0]][f"{LV}/delta"] is None      # NaN in CA's file: undefined at Y = 0


def test_a_step_from_an_older_libcuflynx_reads_the_relative_file(tmp_path):
    step = _local_step(tmp_path)
    result = ca_run_history.step_result(str(step))
    del result["local_sensitivity_signed"]
    data = ca_run_history.step_local_sensitivity(result)
    assert data["output_names"] == ["x_{max} (max)", "y_{max} (max)"]


def test_the_sensitivity_panel_shows_a_local_steps_signed_coefficients(tmp_path):
    step = _local_step(tmp_path)
    job = sensitivity_mod.SensitivityJob("j", str(step / "sensitivity"))
    job.study_step = {"step_dir": str(step)}
    job.meta = {"method": "local", "gradient_method": "FD"}
    from sensitivity_runner import DONE_MARKER  # noqa: PLC0415

    job.lines = [DONE_MARKER]
    sensitivity_mod.SensitivityManager()._finalize(job, 0)
    assert job.state == "done", job.error
    assert job.output_names == OUTPUTS
    assert job.indices["local"][OUTPUTS[0]][f"{LV}/alpha"] == pytest.approx(-0.25)
    assert job.nominal == [2.0, 0.4, 0.3, 1.5]


def test_a_reopened_local_step_is_signed_too(tmp_path):
    step = _local_step(tmp_path)
    found = load_outputs.step_outputs(ca_run_history.step_result(str(step)))
    assert found["sensitivity"]["local"]["output_names"] == OUTPUTS


# -- the uq step's posterior predictive check ---------------------------------------------

def _finished_uq_job(step: Path) -> uq_mod.UQJob:
    from uq_runner import DONE_MARKER  # noqa: PLC0415

    job = uq_mod.UQJob("j", str(step / "uq"))
    job.study_step = {"step_dir": str(step)}
    job.method = "mcmc"
    job.lines = [DONE_MARKER]
    manager = uq_mod.UQManager()
    manager._job = job
    manager._finalize(job, 0)
    return manager, job


def test_a_uq_steps_posterior_predictive_check_reaches_the_panel(tmp_path):
    step = _uq_step(tmp_path)
    manager, job = _finished_uq_job(step)
    assert job.state == "done", job.error
    assert [p["qname"] for p in job.params] == QNAMES
    assert job.coverage["coverage"]["levels"]["0.95"]["predictive_coverage"] == 1.0
    band = manager.posterior_predictive("j")
    assert band["available"] and band["labels"] == OUTPUTS
    assert band["median"] == pytest.approx([0.0, 0.0])
    assert band["lo"][0] < 0 < band["hi"][0]
    assert job.warning is None


def test_a_uq_steps_failed_check_is_said_not_shown(tmp_path):
    step = _uq_step(tmp_path, predictive=False)
    manager, job = _finished_uq_job(step)
    assert job.state == "done"
    assert job.coverage is None
    assert manager.posterior_predictive("j")["available"] is False
    assert "posterior predictive check failed: the model failed at every draw" in job.warning


# -- reopening the working dir ------------------------------------------------------------

def _study(work: Path, steps=("calibration-1", "sobol-1", "local-sa-1", "mcmc-1"),
           finished="2026-11-17T10:00:00") -> Path:
    (work / "workflow").mkdir(parents=True, exist_ok=True)
    (work / "workflow" / "calibration_workflow.json").write_text(json.dumps(
        {"workflow_name": "study", "steps": [{"id": s} for s in steps]}))
    run = work / "workflows" / "study"
    case = run / "calibration-1" / "param_id" / "CMA-ES_calibration-1_obs"
    write_ca_results(str(case), [[q] for q in QNAMES], [2.5, 0.35, 0.45, 1.4], 0.25)
    calibrated = run / "calibration-1" / "calibration-1_calibrated.cellml"
    calibrated.write_text("<model/>")
    _step(run, "calibration-1", {"kind": "calibration", "method": "CMA-ES",
                                 "param_id_output_dir": str(case),
                                 "calibrated_model": str(calibrated)}, finished)
    _csv(run / "sobol-1" / "sensitivity" / "all_outputs_n8_Sobol_indices.csv",
         ["Parameter", *[f"{k}_{o}" for o in OUTPUTS for k in ("S1", "ST")]],
         [[q, 0.1, 0.2, 0.3, 0.4] for q in QNAMES])
    _step(run, "sobol-1", {"kind": "sensitivity", "method": "sobol",
                           "sensitivity_dir": str(run / "sobol-1" / "sensitivity")}, finished)
    _local_step(run, finished)
    _uq_step(run, finished)
    # a set step and a step that has not finished fill nothing
    _step(run, "sliders-1", {"kind": "set"}, finished)
    (run / "calibration-2").mkdir()
    return run


def test_reopening_a_working_dir_fills_every_panel_from_its_study_workflow(tmp_path):
    work = tmp_path / "w"
    run = _study(work)
    found = load_outputs.load_outputs(str(work))

    assert found["study_steps"] == {"calibration": "calibration-1", "sobol": "sobol-1",
                                    "local": "local-sa-1", "uq": "mcmc-1"}
    assert set(found["found"]) >= {"calibration", "sensitivity", "uq"}, found
    assert found["calibration"]["best"]["params"] == dict(
        zip(QNAMES, [2.5, 0.35, 0.45, 1.4]))
    assert found["calibration"]["calibrated_model"] == str(
        run / "calibration-1" / "calibration-1_calibrated.cellml")
    assert found["sensitivity"]["sobol"]["output_names"] == OUTPUTS
    assert found["sensitivity"]["local"]["indices"]["local"][OUTPUTS[0]][f"{LV}/alpha"] == (
        pytest.approx(-0.25))
    uq = found["uq"]
    assert [p["qname"] for p in uq["params"]] == QNAMES and uq["method"] == "mcmc"
    assert uq["coverage"]["coverage"]["levels"]["0.95"]["predictive_coverage"] == 1.0
    assert uq["posterior_predictive"]["available"] and uq["has_posterior_predictive"]


def test_the_latest_done_step_of_a_kind_wins(tmp_path):
    work = tmp_path / "w"
    run = _study(work)
    case = run / "calibration-3" / "param_id" / "CMA-ES_calibration-3_obs"
    write_ca_results(str(case), [[q] for q in QNAMES], [9.0, 9.0, 9.0, 9.0], 0.5)
    _step(run, "calibration-3", {"kind": "calibration", "param_id_output_dir": str(case)},
          "2026-11-17T11:00:00")
    found = load_outputs.load_outputs(str(work))
    assert found["study_steps"]["calibration"] == "calibration-3"
    assert found["calibration"]["best"]["params"][f"{LV}/alpha"] == 9.0


def test_a_direct_run_newer_than_the_step_wins(tmp_path):
    work = tmp_path / "w"
    _study(work, finished="2000-01-01T00:00:00")
    # a direct calibration at the working dir's top level, written now
    write_ca_results(str(work / "CMA-ES_lv_obs"), [[q] for q in QNAMES], [7.0, 7.0, 7.0, 7.0])
    found = load_outputs.load_outputs(str(work))
    assert "calibration" not in found["study_steps"]
    assert found["calibration"]["best"]["params"][f"{LV}/alpha"] == 7.0
    # the panels the direct run did not fill still come from the steps
    assert found["study_steps"]["sobol"] == "sobol-1"


def test_a_step_newer_than_the_direct_run_wins(tmp_path):
    work = tmp_path / "w"
    write_ca_results(str(work / "CMA-ES_lv_obs"), [[q] for q in QNAMES], [7.0, 7.0, 7.0, 7.0])
    past = time.time() - 3600
    for name in os.listdir(work / "CMA-ES_lv_obs"):
        os.utime(work / "CMA-ES_lv_obs" / name, (past, past))
    _study(work, finished=time.strftime("%Y-%m-%dT%H:%M:%S"))
    found = load_outputs.load_outputs(str(work))
    assert found["study_steps"]["calibration"] == "calibration-1"
    assert found["calibration"]["best"]["params"][f"{LV}/alpha"] == 2.5


def test_a_working_dir_without_a_study_workflow_reads_its_direct_runs(tmp_path):
    work = tmp_path / "w"
    write_ca_results(str(work / "CMA-ES_lv_obs"), [[q] for q in QNAMES], [7.0, 7.0, 7.0, 7.0])
    found = load_outputs.load_outputs(str(work))
    assert "study_steps" not in found
    assert found["calibration"]["best"]["params"][f"{LV}/alpha"] == 7.0
