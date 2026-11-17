"""Unit tier: each analysis route adds a step to the working dir's study workflow
(``<W>/workflow/calibration_workflow.json``, written by CA's WorkflowBuilder) and runs it
alone through ``workflow_runner.py``.

A fake workflow runner stands in for the run: it records the config it was handed and,
for a calibration step, writes the files CA would, so the next run can start from it.
The real runs are in ``test_study_workflow_integration.py``.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

import calibration as calibration_mod
import main
import sensitivity as sensitivity_mod
import study_workflow
import uq as uq_mod
from conftest import (
    LV_MODEL_PATH,
    LV_OBS_DATA_PATH,
    LV_PARAMS_CSV_PATH,
    WRITE_CA_RESULTS_SRC,
    set_ca_module,
    upload_model,
)

LV = "Lotka_Volterra_module"
QNAMES = [f"{LV}/{p}" for p in ("alpha", "beta", "delta", "gamma")]
SLIDERS = dict(zip(QNAMES, (2.0, 0.4, 0.3, 1.5)))

FAKE_WORKFLOW_RUNNER = """
import json, os, sys
from pathlib import Path
""" + WRITE_CA_RESULTS_SRC + """
cfg = json.loads(Path(sys.argv[1]).read_text())
seen = Path(os.environ["SEEN_DIR"])
n = len(list(seen.glob("*.json")))
(seen / f"{n:02d}.json").write_text(json.dumps(cfg))
step = cfg["study_step"]
raw = json.loads(Path(cfg["workflow"]).read_text())
kinds = {s["id"]: s.get("kind", "calibration") for s in raw["steps"]}
for sid in step["steps"]:
    d = Path(step["run_dir"], sid)
    d.mkdir(parents=True, exist_ok=True)
    if kinds[sid] == "calibration":
        write_ca_results(str(d / "param_id" / "CMA-ES_case"),
                         [[q] for q in %(qnames)r], [1.0, 0.5, 0.25, 2.0], 0.1)
    (d / "step_result.json").write_text(json.dumps({"step": sid, "status": "done"}))
print(cfg["markers"]["done"], flush=True)
""" % {"qnames": QNAMES}

DIRECT_RUNNER = """
import json, sys
from pathlib import Path
print("direct runner", flush=True)
print("__CALIBRATION_FAILED__ direct", flush=True)
"""


@pytest.fixture
def fake_runs(study_workflow_on, tmp_path, monkeypatch):
    """Every manager runs the fake workflow runner; returns the configs it was handed."""
    script = tmp_path / "fake_workflow_runner.py"
    script.write_text(FAKE_WORKFLOW_RUNNER)
    seen = tmp_path / "seen"
    seen.mkdir()
    monkeypatch.setenv("SEEN_DIR", str(seen))
    for manager in (calibration_mod.calibration, sensitivity_mod.sensitivity, uq_mod.uq):
        manager.study_runner_path = str(script)

    def configs():
        return [json.loads(p.read_text()) for p in sorted(seen.glob("*.json"))]

    return configs


def _setup(client, obs=None) -> str:
    model_id = upload_model(client, LV_MODEL_PATH)["model_id"]
    obs = obs or json.loads(LV_OBS_DATA_PATH.read_text())
    r = client.post("/api/obs_data/upload", json={"model_id": model_id, "obs_data": obs})
    assert r.status_code == 200, r.text
    with open(LV_PARAMS_CSV_PATH, "rb") as fh:
        r = client.post(f"/api/params_for_id/upload?model_id={model_id}",
                        files={"file": (LV_PARAMS_CSV_PATH.name, fh, "text/csv")})
    assert r.status_code == 200, r.text
    return model_id


def _run(client, kind, payload) -> dict:
    r = client.post(f"/api/{kind}/run", json=payload)
    assert r.status_code == 200, r.text
    job_id = r.json()["job_id"]
    deadline = time.time() + 30
    while time.time() < deadline:
        s = client.get(f"/api/{kind}/{job_id}/status").json()
        if s["state"] != "running":
            return s
        time.sleep(0.05)
    raise AssertionError(f"{kind} did not finish")


def _workflow(work: Path) -> dict:
    return json.loads((work / "workflow" / "calibration_workflow.json").read_text())


def _steps(work: Path) -> dict:
    return {s["id"]: s for s in _workflow(work)["steps"]}


def _calibrate(client, model_id, work, **settings) -> dict:
    return _run(client, "calibration", {
        "model_id": model_id, "current_params": SLIDERS,
        "settings": {"config_outputs_dir": str(work), "param_id_method": "CMA-ES",
                     "num_calls_to_function": 50, **settings}})


def test_a_calibration_from_the_sliders_is_a_set_step_and_a_calibration(
        client, fake_runs, tmp_path):
    work = tmp_path / "w"
    model_id = _setup(client)
    status = _calibrate(client, model_id, work, start_from="current")
    assert status["state"] == "done", status["lines"]

    steps = _steps(work)
    assert list(steps) == ["sliders-1", "calibration-1"]
    sliders = steps["sliders-1"]
    assert sliders["kind"] == "set"
    assert {e["parameter"]: e["value"] for e in sliders["set"]} == SLIDERS
    assert {e["reason"] for e in sliders["set"]} == {"slider values in CUFLynx"}
    # only a point to start from: never merged into the target (where the CA has the key)
    assert sliders.get("point") == (True if study_workflow.point_steps() else None)
    cal = steps["calibration-1"]
    assert cal["start_from"] == "sliders-1"
    assert cal["settings"]["param_id_method"] == "CMA-ES"
    assert cal["settings"]["optimiser_options"]["num_calls_to_function"] == 50
    assert "start_from" not in cal["settings"]["optimiser_options"]
    target = _workflow(work)["target"]
    assert "cellml_model_path" in target and cal["model"] == target
    # the step's inputs are the working dir's stored versions
    assert cal["obs_data"].startswith("../inputs/obs_data/")
    assert cal["params_for_id"].startswith("../inputs/params_for_id/")

    (config,) = fake_runs()
    # the set step runs first, so the run starts from it rather than running one step
    assert config["only"] is None and config["from_step"] == "sliders-1"
    assert config["workflow_output_dir"] == str(work / "workflows" / "study")
    assert config["markers"]["done"] == "__CALIBRATION_DONE__"
    assert status["study_step"]["step"] == "calibration-1"
    assert any("Study workflow: step calibration-1" in line for line in status["lines"])
    # results are read from the step's param_id dir
    assert status["best_params"][f"{LV}/alpha"] == 1.0


def test_starting_from_the_best_fit_names_the_calibration_step(client, fake_runs, tmp_path):
    work = tmp_path / "w"
    model_id = _setup(client)
    assert _calibrate(client, model_id, work)["state"] == "done"
    assert _calibrate(client, model_id, work, start_from="best_fit")["state"] == "done"

    steps = _steps(work)
    assert list(steps) == ["calibration-1", "calibration-2"]
    assert steps["calibration-2"]["start_from"] == "calibration-1"
    assert "start_from" not in steps["calibration-1"]
    assert [c["only"] for c in fake_runs()] == ["calibration-1", "calibration-2"]


def test_the_best_so_far_of_a_stopped_calibration_is_a_set_step(client, fake_runs, tmp_path):
    work = tmp_path / "w"
    model_id = _setup(client)
    assert _calibrate(client, model_id, work)["state"] == "done"
    # stopped early: its best-so-far is recovered, but the step has no result to name
    calibration_mod.calibration._job.state = "cancelled"
    assert _calibrate(client, model_id, work, start_from="best_fit")["state"] == "done"

    steps = _steps(work)
    assert list(steps) == ["calibration-1", "best-fit-1", "calibration-2"]
    entries = steps["best-fit-1"]["set"]
    assert {e["parameter"]: e["value"] for e in entries} == dict(
        zip(QNAMES, (1.0, 0.5, 0.25, 2.0)))
    assert "calibration-1, which was stopped early" in entries[0]["reason"]
    assert steps["best-fit-1"].get("point") == (True if study_workflow.point_steps() else None)
    assert steps["calibration-2"]["start_from"] == "best-fit-1"


def test_a_sobol_analysis_is_a_sensitivity_step(client, fake_runs, tmp_path):
    work = tmp_path / "w"
    model_id = _setup(client)
    _run(client, "sensitivity", {"model_id": model_id, "settings": {
        "config_outputs_dir": str(work), "method": "sobol", "num_samples": 32}})

    (step,) = _steps(work).values()
    assert step["id"] == "sobol-1" and step["kind"] == "sensitivity"
    sa = step["settings"]["sa_options"]
    assert sa["method"] == "sobol" and sa["num_samples"] == 32 and "output_dir" not in sa
    assert "result_from" not in step
    (config,) = fake_runs()
    assert config["only"] == "sobol-1"
    assert config["markers"]["done"] == "__SENSITIVITY_DONE__"


def test_a_local_analysis_at_the_bounds_midpoint_is_at_a_set_step(client, fake_runs, tmp_path):
    work = tmp_path / "w"
    model_id = _setup(client)
    _run(client, "sensitivity", {"model_id": model_id, "settings": {
        "config_outputs_dir": str(work), "method": "local", "nominal": "midpoint",
        "gradient_method": "FD", "rel_step": 0.02}})

    steps = _steps(work)
    assert list(steps) == ["nominal-1", "local-sa-1"]
    values = {e["parameter"]: e["value"] for e in steps["nominal-1"]["set"]}
    assert values == pytest.approx({f"{LV}/alpha": 3.55, f"{LV}/beta": 1.005,
                                    f"{LV}/delta": 1.005, f"{LV}/gamma": 5.05})
    assert "midpoint of the params_for_id bounds" in steps["nominal-1"]["set"][0]["reason"]
    assert steps["nominal-1"].get("point") == (True if study_workflow.point_steps() else None)
    local = steps["local-sa-1"]
    assert local["result_from"] == "nominal-1"
    sa = local["settings"]["sa_options"]
    assert sa["method"] == "local" and sa["gradient_method"] == "FD"
    assert sa["fd_rel_step"] == 0.02


def test_a_local_analysis_at_the_sliders_and_after_a_fresh_calibration(
        client, fake_runs, tmp_path):
    work = tmp_path / "w"
    model_id = _setup(client)
    _run(client, "sensitivity", {"model_id": model_id, "current_params": SLIDERS, "settings": {
        "config_outputs_dir": str(work), "method": "local", "nominal": "current"}})
    _run(client, "sensitivity", {"model_id": model_id, "settings": {
        "config_outputs_dir": str(work), "method": "local", "run_calibration_first": True,
        "num_calls_to_function": 40}})

    steps = _steps(work)
    assert list(steps) == ["sliders-1", "local-sa-1", "calibration-1", "local-sa-2"]
    assert steps["local-sa-1"]["result_from"] == "sliders-1"
    assert steps["local-sa-2"]["result_from"] == "calibration-1"
    opts = steps["calibration-1"]["settings"]["optimiser_options"]
    assert opts["num_calls_to_function"] == 40
    assert [c["from_step"] for c in fake_runs()] == ["sliders-1", "calibration-1"]


@pytest.mark.parametrize("method", ["laplace", "mcmc"])
def test_uq_at_the_best_fit_is_a_uq_step_from_the_calibration(
        client, fake_runs, tmp_path, method):
    work = tmp_path / "w"
    model_id = _setup(client)
    assert _calibrate(client, model_id, work)["state"] == "done"
    _run(client, "uq", {"model_id": model_id, "settings": {
        "config_outputs_dir": str(work), "method": method, "num_steps": 20,
        "num_walkers": 8, "posterior_predictive_samples": 30}})

    step = _steps(work)[f"{method}-1"]
    # the step's posterior predictive check runs as the panel asked
    assert step["settings"]["UQ_options"]["posterior_predictive_samples"] == 30
    assert step["kind"] == "uq" and step["result_from"] == "calibration-1"
    assert step["settings"]["UQ_options"]["method"] == method
    assert step["settings"]["UQ_options"]["num_steps"] == 20
    if method == "laplace":
        assert step["settings"]["ia_options"] == {"method": "Laplace"}
    # MCMC and Laplace score the obs_data with an MLE cost
    obs = json.loads((work / "workflow" / step["obs_data"]).read_text())
    assert {i["cost_type"] for i in obs["data_items"]} == {"gaussian_MLE"}
    assert fake_runs()[-1]["only"] == f"{method}-1"
    assert fake_runs()[-1]["markers"]["done"] == "__UQ_DONE__"


def test_uq_with_a_fresh_calibration_runs_both_steps(client, fake_runs, tmp_path):
    work = tmp_path / "w"
    model_id = _setup(client)
    _run(client, "uq", {"model_id": model_id, "settings": {
        "config_outputs_dir": str(work), "method": "mcmc", "run_calibration_first": True}})

    steps = _steps(work)
    assert list(steps) == ["calibration-1", "mcmc-1"]
    assert steps["mcmc-1"]["result_from"] == "calibration-1"
    assert fake_runs()[-1]["from_step"] == "calibration-1"


def test_another_model_in_the_working_dir_starts_a_new_workflow(client, fake_runs, tmp_path):
    from conftest import BG_MODEL_PATH

    work = tmp_path / "w"
    model_id = _setup(client)
    assert _calibrate(client, model_id, work)["state"] == "done"
    other = upload_model(client, BG_MODEL_PATH)["model_id"]
    r = client.post("/api/obs_data/upload", json={"model_id": other, "obs_data": {
        "protocol_info": {"pre_times": [0.0], "sim_times": [[1.0]], "params_to_change": {}},
        "data_items": [{"data_item_name": "p", "data_type": "constant", "unit": "dimensionless",
                        "operation": "max", "operands": ["main/p_o2"], "value": 1.0,
                        "std": 0.1, "experiment_idx": 0, "subexperiment_idx": 0}]}})
    assert r.status_code == 200, r.text
    r = client.post(f"/api/params_for_id/upload?model_id={other}", files={"file": (
        "p.csv", b"vessel_name,param_name,min,max\nmain,alpha_o2,0.1,2\n", "text/csv")})
    assert r.status_code == 200, r.text
    assert _calibrate(client, other, work)["state"] in ("done", "error")

    assert _workflow(work)["workflow_name"] == "study-2"
    old = json.loads((work / "workflow" / "calibration_workflow_study.json").read_text())
    assert [s["id"] for s in old["steps"]] == ["calibration-1"]
    assert fake_runs()[-1]["workflow_output_dir"] == str(work / "workflows" / "study-2")


@pytest.mark.integration
def test_the_workflow_opens_in_the_workflow_tab_and_again_with_the_working_dir(
        client, fake_runs, tmp_path):
    # describing the workflow plans it, and CA reads a CellML target's constants with
    # libCellML: the integration tier has it, the unit tier does not
    pytest.importorskip("libcellml")
    import workflow_manager

    work = tmp_path / "w"
    model_id = _setup(client)
    assert _calibrate(client, model_id, work)["state"] == "done"
    path = str(work / "workflow" / "calibration_workflow.json")
    described = client.get("/api/workflow").json()
    assert described["loaded"] and described["path"] == path
    assert described["output_dir"] == str(work / "workflows" / "study")

    workflow_manager.workflow.reset()       # a new session
    found = client.get("/api/working-dir/load", params={"dir": str(work)}).json()
    assert found["study_workflow"] == {"path": path, "opened": True}
    assert client.get("/api/workflow").json()["path"] == path


def test_a_second_step_waits_for_the_running_one(client, study_workflow_on, tmp_path):
    work = tmp_path / "w"
    model_id = _setup(client)
    slow = tmp_path / "slow.py"
    slow.write_text("import time\ntime.sleep(30)\n")
    calibration_mod.calibration.study_runner_path = str(slow)
    r = client.post("/api/calibration/run", json={"model_id": model_id, "settings": {
        "config_outputs_dir": str(work), "param_id_method": "CMA-ES"}})
    assert r.status_code == 200, r.text
    r = client.post("/api/sensitivity/run", json={"model_id": model_id, "settings": {
        "config_outputs_dir": str(work), "method": "sobol"}})
    assert r.status_code == 409
    assert "calibration-1" in r.json()["detail"]
    assert list(_steps(work)) == ["calibration-1"]


def test_a_reopened_working_dirs_calibration_step_is_the_previous_best_fit(
        client, fake_runs, tmp_path):
    """In a new session no calibration has run, but the reopened study workflow has a done
    calibration step: UQ at the best fit is a uq step from it, not a 422."""
    work = tmp_path / "w"
    model_id = _setup(client)
    assert _calibrate(client, model_id, work)["state"] == "done"
    step_dir = work / "workflows" / "study" / "calibration-1"
    (step_dir / "step_result.json").write_text(json.dumps({
        "step": "calibration-1", "kind": "calibration", "status": "done",
        "finished": "2026-11-17T10:00:00",
        "param_id_output_dir": str(step_dir / "param_id" / "CMA-ES_case")}))
    calibration_mod.calibration.reset()           # a new session

    # accepted (not a 422 for want of a calibration); the fake runner stores no posterior
    _run(client, "uq", {"model_id": model_id, "settings": {
        "config_outputs_dir": str(work), "method": "mcmc", "num_steps": 20,
        "num_walkers": 8}})
    assert _steps(work)["mcmc-1"]["result_from"] == "calibration-1"
    # the direct runners take the same best fit as values
    record = main._get_model(model_id)
    assert main._previous_best_fit(record, {"config_outputs_dir": str(work)}) == dict(
        zip(QNAMES, [1.0, 0.5, 0.25, 2.0]))


def test_a_calibration_steps_tab_is_the_previous_best_fit_in_another_working_dir(
        client, fake_runs, tmp_path):
    """A study opened as a calibration step's tab (Workflow tab) shows that step's best fit,
    so MCMC there starts from it, though this session has not calibrated and the working
    dir it runs in has no calibration of its own."""
    first, second = tmp_path / "w1", tmp_path / "w2"
    model_id = _setup(client)
    assert _calibrate(client, model_id, first)["state"] == "done"
    step_dir = first / "workflows" / "study" / "calibration-1"
    calibration_mod.calibration.reset()           # a new session
    # what /api/workflow/view records for a calibration step's tab
    main._get_model(model_id).opened_calibration = {
        "results_dir": str(step_dir / "param_id"), "step": "calibration-1",
        "workflow": str(first / "workflow" / "calibration_workflow.json")}
    best = dict(zip(QNAMES, [1.0, 0.5, 0.25, 2.0]))
    record = main._get_model(model_id)
    assert main._previous_best_fit(record, {"config_outputs_dir": str(second)}) == best

    _run(client, "uq", {"model_id": model_id, "settings": {
        "config_outputs_dir": str(second), "method": "mcmc", "num_steps": 20,
        "num_walkers": 8}})
    steps = _steps(second)
    start = steps[steps["mcmc-1"]["result_from"]]
    assert start["kind"] == "set"
    assert {e["parameter"]: e["value"] for e in start["set"]} == best
    assert "calibration step calibration-1" in start["set"][0]["reason"]


def test_the_target_is_built_after_cas_cellml_model_submodule_is_imported(study_workflow_on):
    """CA's package exports a ``cellml_model`` function and has a ``cellml_model`` submodule;
    importing the submodule (CA's calibrated-CellML writer does, in the server process)
    rebinds the package's name to the module."""
    import importlib

    from ca_imports import resolved_name

    try:
        importlib.import_module(resolved_name("calibration_workflow.cellml_model"))
    except ImportError:
        pytest.skip("this libcuflynx has no calibration_workflow.cellml_model module")
    target = study_workflow.target_model(str(LV_MODEL_PATH), "cellml")
    assert target["cellml_model_path"] == str(LV_MODEL_PATH)


def test_set_steps_are_points_exactly_when_the_ca_schema_has_the_key(
        client, fake_runs, tmp_path, monkeypatch):
    model_id = _setup(client)
    for supported, work in ((True, tmp_path / "with"), (False, tmp_path / "without")):
        monkeypatch.setattr(study_workflow, "point_steps", lambda s=supported: s)
        assert _calibrate(client, model_id, work, start_from="current")["state"] == "done"
        sliders = _steps(work)["sliders-1"]
        assert sliders.get("point") == (True if supported else None)


def test_the_point_probe_reads_the_ca_workflow_schema(monkeypatch):
    import types

    def schema(properties):
        return types.SimpleNamespace(
            CALIBRATION_WORKFLOW_SCHEMA="calibration_workflow",
            load_schema=lambda name: {"$defs": {"step": {"properties": properties}}})

    set_ca_module(monkeypatch, "schemas", schema({"id": {}, "set": {}, "point": {}}))
    assert study_workflow.point_steps() is True
    set_ca_module(monkeypatch, "schemas", schema({"id": {}, "set": {}}))
    assert study_workflow.point_steps() is False
    set_ca_module(monkeypatch, "schemas", None)          # a CA with no schemas package
    assert study_workflow.point_steps() is False


# -- reading a uq step's posterior ------------------------------------------------------

def test_a_stored_distribution_reads_as_the_uq_panels_marginals(tmp_path):
    import numpy as np

    import ca_run_history

    d = tmp_path / "distribution"
    d.mkdir()
    rng = np.random.default_rng(0)
    np.save(d / "samples.npy", np.column_stack([rng.normal(2, 0.1, 500),
                                               rng.normal(5, 1.0, 500)]))
    (d / "distribution.json").write_text(json.dumps({"format_version": 1, "parameters": [
        {"targets": [["A", "p"]], "min": 0, "max": 10, "transform": "logit"},
        {"targets": [["B", "q"], ["C", "q"]], "min": 0, "max": 10, "transform": "logit"}]}))

    p, q = ca_run_history.stored_distribution(str(d))
    assert p["qname"] == "A/p" and q["qname"] == "B/q"
    assert p["mean"] == pytest.approx(2, abs=0.05) and q["std"] == pytest.approx(1, abs=0.1)
    assert sum(p["counts"]) == 500 and len(p["bins"]) == ca_run_history.NUM_BINS + 1
    assert ca_run_history.stored_distribution(str(tmp_path / "missing")) is None


def test_a_laplace_step_result_reads_as_normal_marginals():
    import ca_run_history

    (a, b) = ca_run_history.laplace_marginals([1.0, 3.0], [[0.04, 0.0], [0.0, 1.0]],
                                              ["A/p", "B/q"])
    assert (a["qname"], a["mean"], a["std"]) == ("A/p", 1.0, pytest.approx(0.2))
    assert b["q95"] == pytest.approx(3.0 + 1.6448536, rel=1e-6)
    assert ca_run_history.laplace_marginals([1.0], [[1.0, 0.0], [0.0, 1.0]]) is None


#: What CA stored for Lotka-Volterra's 4 parameters fitted to 2 observables: the inverse
#: Hessian is indefinite, every std NaN, and 1179 of 2000 draws fell outside the bounds.
LV_LAPLACE = {
    "mean": [1.3654835, 0.3988897, 0.6737314, 1.7019225],
    "covariance": [[-34.96, 0.9997, 1.3327, -16.673], [0.9997, -0.02623, -0.03189, 0.5143],
                   [1.3327, -0.03189, -0.04039, 0.6517], [-16.673, 0.5143, 0.6517, -8.2805]],
    "std": [float("nan")] * 4, "samples_outside_bounds": 1179}


def test_an_indefinite_laplace_covariance_reads_as_not_identifiable_never_std_0():
    import ca_run_history

    marginals = ca_run_history.laplace_marginals(
        LV_LAPLACE["mean"], LV_LAPLACE["covariance"], QNAMES, LV_LAPLACE["std"])
    assert [m["qname"] for m in marginals] == QNAMES
    for m, mean in zip(marginals, LV_LAPLACE["mean"]):
        assert m["not_identifiable"] == "not identifiable (covariance not positive definite)"
        assert (m["std"], m["q05"], m["q95"], m["bins"]) == (None, None, None, [])
        assert m["mean"] == m["q50"] == mean
    json.dumps(marginals, allow_nan=False)          # the status payload carries no NaN

    # per parameter: CA's NaN std, or a variance that is not positive, marks that one only
    a, b, c = ca_run_history.laplace_marginals(
        [1.0, 2.0, 3.0], [[0.04, 0, 0], [0, 1.0, 0], [0, 0, -1.0]], ["A/p", "B/q", "C/r"],
        [0.2, float("nan"), float("nan")])
    assert a["std"] == pytest.approx(0.2) and "not_identifiable" not in a
    assert b["std"] is None and c["std"] is None and "not_identifiable" in c
    (d,) = ca_run_history.laplace_marginals([1.0], [[-1.0]], ["D/s"])   # no std from CA
    assert d["std"] is None and d["not_identifiable"]


def test_a_laplace_steps_draws_outside_the_bounds_are_counted(tmp_path):
    import numpy as np

    step_dir, dist = tmp_path / "laplace-1", tmp_path / "laplace-1" / "distribution"
    dist.mkdir(parents=True)
    np.save(dist / "samples.npy", np.zeros((821, 4)))
    result = {"step": "laplace-1", "status": "done", "method": "laplace",
              "parameters": QNAMES, "laplace": LV_LAPLACE, "distribution_dir": str(dist)}
    (step_dir / "step_result.json").write_text(json.dumps(result))
    job = uq_mod.UQJob("j", str(tmp_path))
    job.study_step = {"step_dir": str(step_dir)}

    assert uq_mod.UQManager._study_outside_bounds(job) == {"count": 1179, "of": 2000}
    params = uq_mod.UQManager._study_params(job)
    assert all(p["std"] is None and p["not_identifiable"] for p in params)
    (dist / "samples.npy").unlink()
    assert uq_mod.UQManager._study_outside_bounds(job) == {"count": 1179, "of": None}
    del result["laplace"]["samples_outside_bounds"]
    (step_dir / "step_result.json").write_text(json.dumps(result))
    assert uq_mod.UQManager._study_outside_bounds(job) is None


# -- the direct runners, where a run has no workflow form ---------------------------------

def _direct(tmp_path):
    script = tmp_path / "direct.py"
    script.write_text(DIRECT_RUNNER)
    calibration_mod.calibration.runner_path = str(script)


def test_a_libcuflynx_without_the_workflow_features_keeps_the_direct_runners(
        client, monkeypatch, tmp_path):
    """An older libcuflynx (no CellML workflow model, no uq step): every run is on its direct
    runner as before, and the run log says why once."""
    import study_workflow

    monkeypatch.setenv("CUFLYNX_STUDY_WORKFLOW", "1")
    monkeypatch.setattr(study_workflow, "unavailable_reason",
                        lambda: "this libcuflynx's calibration workflows have no uq step")
    _direct(tmp_path)
    work = tmp_path / "w"
    model_id = _setup(client)
    first = _calibrate(client, model_id, work)
    second = _calibrate(client, model_id, work)

    assert "direct runner" in first["lines"]
    assert not (work / "workflow").exists()
    notes = [ln for ln in first["lines"] if ln.startswith("Study workflow:")]
    assert len(notes) == 1 and "have no uq step" in notes[0]
    assert not [ln for ln in second["lines"] if ln.startswith("Study workflow:")]
    assert first["study_step"] is None


def test_held_out_data_keeps_the_direct_calibration(client, study_workflow_on, tmp_path):
    _direct(tmp_path)
    work = tmp_path / "w"
    obs = json.loads(LV_OBS_DATA_PATH.read_text())
    obs["prediction_items"] = [{"data_item_name": "x_final", "data_type": "constant",
                                "operation": "max", "operands": [f"{LV}/x"],
                                "unit": "dimensionless", "experiment_idx": 0,
                                "value": 3.0, "std": 0.1}]
    model_id = _setup(client, obs)
    status = _calibrate(client, model_id, work)

    assert "direct runner" in status["lines"]
    assert any("held-out data" in ln for ln in status["lines"])
    assert not (work / "workflow").exists()


@pytest.mark.integration
def test_a_generated_python_model_keeps_the_direct_runner(client, study_workflow_on, tmp_path):
    # the direct runner generates the Python model with CA's generators, which import
    # scipy: the integration tier has it, the unit tier does not
    pytest.importorskip("scipy")
    import engine as engine_mod

    _direct(tmp_path)
    work = tmp_path / "w"
    model_id = _setup(client)
    engine_mod.engine.model_type = "python"
    status = _calibrate(client, model_id, work)

    assert "direct runner" in status["lines"]
    assert any("python model type" in ln for ln in status["lines"])
    assert not (work / "workflow").exists()


def test_a_run_writes_the_calibrated_values_when_it_leaves_the_workflow_complete(
        client, fake_runs, tmp_path):
    """The study's target is a CellML model, so a run writes ``<stem>_calibrated_parameters
    .json`` and the calibrated CellML beside it -- unless an earlier step has no done
    result, as CA refuses that write for an incomplete workflow."""
    work = tmp_path / "w"
    model_id = _setup(client)
    _calibrate(client, model_id, work, start_from="current")
    assert fake_runs()[-1]["write_calibrated"] is True

    # an earlier step that failed (or was cancelled) has no done result
    (work / "workflows" / "study" / "calibration-1" / "step_result.json").write_text(
        json.dumps({"step": "calibration-1", "status": "failed"}))
    _run(client, "sensitivity", {"model_id": model_id, "settings": {
        "config_outputs_dir": str(work), "method": "sobol", "num_samples": 8}})
    assert fake_runs()[-1]["write_calibrated"] is False
