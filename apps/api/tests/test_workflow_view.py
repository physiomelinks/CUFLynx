"""The Workflow view: which step a panel's run is, independent workflows of one working dir,
and deleting and reordering a workflow's steps (``study_workflow``, ``/api/workflow/...``).

A panel's run changes the workflow's latest step of its kind unless the user chose a new
step (or a new workflow) in the Workflow view; a run that changes a step runs it and the
steps that take from it.
"""

from __future__ import annotations

import json

import pytest

import study_workflow
from test_study_workflow import (  # noqa: F401  (fake_runs is a fixture)
    NEW_STEP,
    _calibrate,
    _run,
    _setup,
    _steps,
    _workflow,
    _workflow_dir,
    fake_runs,
)


# -- which step a run is --------------------------------------------------------------------

def test_running_the_calibration_again_changes_its_step(client, fake_runs, tmp_path):
    work = tmp_path / "w"
    model_id = _setup(client)
    assert _calibrate(client, model_id, work, start_from="current")["state"] == "done"
    status = _calibrate(client, model_id, work, start_from="current", num_calls_to_function=70)
    assert status["state"] == "done", status["lines"]

    steps = _steps(work)
    # the one calibration, changed; its new start point above it, the unused old one gone
    assert list(steps) == ["sliders-2", "calibration-1"]
    assert steps["calibration-1"]["start_from"] == "sliders-2"
    assert steps["calibration-1"]["settings"]["optimiser_options"]["num_calls_to_function"] == 70
    config = fake_runs()[-1]
    assert config["run_steps"] == ["sliders-2", "calibration-1"]
    assert config["only"] is None and config["from_step"] is None
    assert any("changes step calibration-1" in line for line in status["lines"])


def test_a_new_calibration_has_to_be_asked_for(client, fake_runs, tmp_path):
    work = tmp_path / "w"
    model_id = _setup(client)
    assert _calibrate(client, model_id, work)["state"] == "done"
    assert _calibrate(client, model_id, work, workflow_target=NEW_STEP)["state"] == "done"
    assert list(_steps(work)) == ["calibration-1", "calibration-2"]
    # the default then changes the latest of them
    assert _calibrate(client, model_id, work, num_calls_to_function=9)["state"] == "done"
    steps = _steps(work)
    assert list(steps) == ["calibration-1", "calibration-2"]
    assert steps["calibration-2"]["settings"]["optimiser_options"]["num_calls_to_function"] == 9


def test_two_calibrations_of_one_parameter_are_a_problem_the_view_shows(
        client, fake_runs, tmp_path):
    """Two steps calibrating the same parameter of the target, neither taking from the
    other. CA finds it (its message names the parameter and the steps): a CA with the
    combine step (CA #685) refuses to plan it, which stops the run until a combine step
    joins them; an older CA warns that the later replaces the earlier. The view shows CA's
    message either way, and CUFLynx does not check it again."""
    work = tmp_path / "w"
    model_id = _setup(client)
    assert _calibrate(client, model_id, work)["state"] == "done"
    assert client.get("/api/workflow").json()["problems"] == []
    assert _calibrate(client, model_id, work, workflow_target=NEW_STEP)["state"] == "done"
    described = client.get("/api/workflow").json()
    if "combine" in described["step_kinds"]:
        message = described["plan_error"]
    else:
        assert len(described["problems"]) == 1
        message = described["problems"][0]
        assert "calibrated by more than one step" in message
    assert "calibration-1" in message and "calibration-2" in message


def test_changing_a_calibration_reruns_the_steps_that_take_from_it(
        client, fake_runs, tmp_path):
    work = tmp_path / "w"
    model_id = _setup(client)
    assert _calibrate(client, model_id, work)["state"] == "done"
    _run(client, "sensitivity", {"model_id": model_id, "settings": {
        "config_outputs_dir": str(work), "method": "sobol", "num_samples": 32}})
    _run(client, "uq", {"model_id": model_id, "settings": {
        "config_outputs_dir": str(work), "method": "mcmc", "num_steps": 20, "num_walkers": 8}})
    assert _steps(work)["mcmc-1"]["result_from"] == "calibration-1"

    assert _calibrate(client, model_id, work)["state"] == "done"
    # the sobol step does not take from the calibration, so it does not run again
    assert fake_runs()[-1]["run_steps"] == ["calibration-1", "mcmc-1"]


def test_a_calibration_changed_to_start_from_its_own_best_fit(client, fake_runs, tmp_path):
    work = tmp_path / "w"
    model_id = _setup(client)
    assert _calibrate(client, model_id, work)["state"] == "done"
    assert _calibrate(client, model_id, work, start_from="best_fit")["state"] == "done"

    steps = _steps(work)
    assert list(steps) == ["best-fit-1", "calibration-1"]
    assert steps["calibration-1"]["start_from"] == "best-fit-1"
    assert "best fit of step calibration-1 before this run changed it" in \
        steps["best-fit-1"]["set"][0]["reason"]


def test_a_calibration_made_first_says_for_which_step_and_is_changed_with_it(
        client, fake_runs, tmp_path):
    work = tmp_path / "w"
    model_id = _setup(client)
    uq = {"config_outputs_dir": str(work), "method": "mcmc", "num_steps": 20,
          "num_walkers": 8, "run_calibration_first": True}
    _run(client, "uq", {"model_id": model_id, "settings": uq})
    steps = _steps(work)
    assert steps["calibration-1"]["description"] == "calibrated first for step mcmc-1"
    _run(client, "uq", {"model_id": model_id, "settings": {**uq, "num_calls_to_function": 9}})
    # the same two steps, changed: no second calibration left behind
    assert list(_steps(work)) == ["calibration-1", "mcmc-1"]


def test_changing_a_step_never_replaces_the_users_own_calibration(client, fake_runs, tmp_path):
    work = tmp_path / "w"
    model_id = _setup(client)
    assert _calibrate(client, model_id, work)["state"] == "done"
    uq = {"config_outputs_dir": str(work), "method": "mcmc", "num_steps": 20, "num_walkers": 8}
    _run(client, "uq", {"model_id": model_id, "settings": uq})
    assert _steps(work)["mcmc-1"]["result_from"] == "calibration-1"
    # now with a fresh calibration first: a new one, the user's calibration-1 untouched
    _run(client, "uq", {"model_id": model_id, "settings": {**uq, "run_calibration_first": True}})
    steps = _steps(work)
    assert steps["mcmc-1"]["result_from"] == "calibration-2"
    assert "description" not in steps["calibration-1"]
    assert steps["calibration-1"]["settings"]["param_id_method"] == "CMA-ES"


def test_a_chosen_step_of_another_kind_is_refused(client, fake_runs, tmp_path):
    work = tmp_path / "w"
    model_id = _setup(client)
    assert _calibrate(client, model_id, work)["state"] == "done"
    r = client.post("/api/sensitivity/run", json={"model_id": model_id, "settings": {
        "config_outputs_dir": str(work), "method": "sobol",
        "workflow_target": {"mode": "edit", "step": "calibration-1"}}})
    assert r.status_code == 422
    assert "calibration step" in r.json()["detail"]
    assert list(_steps(work)) == ["calibration-1"]


def test_the_workflow_target_never_reaches_cas_options(client, fake_runs, tmp_path):
    work = tmp_path / "w"
    model_id = _setup(client)
    _run(client, "sensitivity", {"model_id": model_id, "settings": {
        "config_outputs_dir": str(work), "method": "sobol", "workflow_target": NEW_STEP}})
    assert "workflow_target" not in _steps(work)["sobol-1"]["settings"]["sa_options"]


FAKE_EMULATION_RUNNER = """
import json, sys
from pathlib import Path
cfg = json.loads(Path(sys.argv[1]).read_text())
step = cfg["study_step"]
d = Path(step["step_dir"])
bundle = d / "emulator"
bundle.mkdir(parents=True, exist_ok=True)
(bundle / "emulator_metadata.json").write_text(json.dumps({"model": "RBF", "features": []}))
(bundle / "training_data.npz").write_bytes(b"")   # CA saves the design beside it
(d / "step_result.json").write_text(json.dumps({
    "step": step["step"], "kind": "emulation", "status": "done", "emulator_dir": str(bundle)}))
print(cfg["markers"]["done"], flush=True)
"""


def test_training_a_surrogate_is_an_emulation_step_and_its_emulator_is_found(
        client, study_workflow_on, tmp_path):
    import emulator as emulator_mod

    script = tmp_path / "fake_emulation_runner.py"
    script.write_text(FAKE_EMULATION_RUNNER)
    emulator_mod.emulator.study_runner_path = str(script)
    work = tmp_path / "w"
    model_id = _setup(client)
    r = client.post("/api/emulator/train", json={"model_id": model_id, "settings": {
        "config_outputs_dir": str(work), "num_train_samples": 16}})
    assert r.status_code == 200, r.text
    job = r.json()["job_id"]
    import time
    for _ in range(300):
        s = client.get(f"/api/emulator/{job}/status").json()
        if s["state"] != "running":
            break
        time.sleep(0.05)
    assert s["state"] == "done", s
    (step,) = _steps(work).values()
    assert step["id"] == "surrogate-1" and step["kind"] == "emulation"
    assert step["settings"]["emulator_settings"]["num_train_samples"] == 16
    assert "emulator_dir" not in step["settings"]["emulator_settings"]
    # the Surrogate tab reads the step's emulator
    bundle = str(work / "workflows" / "study" / "surrogate-1" / "emulator")
    info = client.get("/api/emulator/info", params={
        "model_id": model_id, "config_outputs_dir": str(work)}).json()
    assert info["emulator_dir"] == bundle
    assert bundle in [row["dir"] for row in info["choices"]]


# -- independent workflows ------------------------------------------------------------------

def test_a_new_workflow_is_its_own_directory_and_both_are_listed(client, fake_runs, tmp_path):
    work = tmp_path / "w"
    model_id = _setup(client)
    assert _calibrate(client, model_id, work)["state"] == "done"
    status = _calibrate(client, model_id, work, workflow_target={"mode": "new_workflow"})
    assert status["state"] == "done"

    assert _workflow_dir(work) == work / "workflow" / "study-2"
    assert [s["id"] for s in _workflow(work)["steps"]] == ["calibration-1"]
    assert fake_runs()[-1]["workflow_output_dir"] == str(work / "workflows" / "study-2")
    listed = client.get("/api/workflow/list", params={"config_outputs_dir": str(work)}).json()
    assert [w["name"] for w in listed["workflows"]] == ["study-2", "study"]
    assert listed["workflows"][1]["steps"] == [{"id": "calibration-1", "kind": "calibration"}]

    # a run naming the first workflow goes to it, and changes its calibration
    first = str(work / "workflow" / "study" / "calibration_workflow.json")
    assert _calibrate(client, model_id, work, workflow_target={"workflow": first})[
        "state"] == "done"
    assert fake_runs()[-1]["workflow"] == first


def test_an_older_single_file_workflow_is_still_listed(tmp_path):
    (tmp_path / "workflow").mkdir()
    (tmp_path / "workflow" / "calibration_workflow.json").write_text(json.dumps(
        {"workflow_name": "study", "steps": [{"id": "calibration-1"}]}))
    (tmp_path / "workflow" / "calibration_workflow_old.json").write_text(json.dumps(
        {"workflow_name": "old", "steps": []}))
    names = {w["name"] for w in study_workflow.list_workflows(str(tmp_path))}
    assert names == {"study", "old"}
    path = str(tmp_path / "workflow" / "calibration_workflow.json")
    assert study_workflow.working_dir_of(path) == str(tmp_path)
    assert study_workflow.working_dir_of(str(tmp_path / "elsewhere.json")) is None


# -- deleting and moving steps --------------------------------------------------------------

def _raw(*steps):
    return {"workflow_name": "w", "steps": list(steps)}


def test_deleting_a_step_reconnects_what_took_from_it(study_workflow_on):
    raw = _raw({"id": "sliders-1", "kind": "set", "set": []},
               {"id": "calibration-1", "start_from": "sliders-1"},
               {"id": "mcmc-1", "kind": "uq", "result_from": "calibration-1"})
    plan = study_workflow.delete_plan(raw, "calibration-1")
    assert plan == {"deleted": ["calibration-1"], "dropped": [],
                    "rewired": [{"step": "mcmc-1", "key": "result_from",
                                 "from": "calibration-1", "to": "sliders-1"}]}


def test_a_start_point_with_nothing_above_it_is_dropped(study_workflow_on):
    raw = _raw({"id": "sliders-1", "kind": "set", "set": []},
               {"id": "calibration-1", "start_from": "sliders-1"})
    plan = study_workflow.delete_plan(raw, "sliders-1")
    assert plan["deleted"] == ["sliders-1"]
    assert plan["dropped"] == [{"step": "calibration-1", "key": "start_from",
                                "from": "sliders-1"}]


def test_a_step_with_nothing_to_reconnect_to_goes_too(study_workflow_on):
    raw = _raw({"id": "calibration-1"},
               {"id": "sobol-1", "kind": "sensitivity", "result_from": "calibration-1"},
               {"id": "reduce-1", "kind": "reduction", "sensitivity_from": "sobol-1"},
               {"id": "laplace-1", "kind": "uq"})
    plan = study_workflow.delete_plan(raw, "calibration-1")
    # the sensitivity step has no calibration or set step above it, and the reduction no
    # sensitivity step but it
    assert plan["deleted"] == ["calibration-1", "sobol-1", "reduce-1"]
    assert plan["rewired"] == [] and plan["dropped"] == []


def test_a_reference_skips_steps_of_a_kind_its_key_cannot_take(study_workflow_on):
    raw = _raw({"id": "calibration-1"},
               {"id": "sobol-1", "kind": "sensitivity", "result_from": "calibration-1"},
               {"id": "reduce-1", "kind": "reduction", "sensitivity_from": "sobol-1"},
               {"id": "calibration-2", "params_for_id_from": "reduce-1",
                "fixed_from": ["calibration-1"]},
               {"id": "mcmc-1", "kind": "uq", "result_from": "calibration-2"})
    plan = study_workflow.delete_plan(raw, "calibration-2")
    # calibration-2 takes from reduce-1 and calibration-1; a result_from takes a calibration
    assert plan["rewired"] == [{"step": "mcmc-1", "key": "result_from",
                                "from": "calibration-2", "to": "calibration-1"}]


def test_a_move_above_a_step_it_takes_from_is_refused(tmp_path):
    path = tmp_path / "calibration_workflow.json"
    path.write_text(json.dumps(_raw({"id": "calibration-1"},
                                    {"id": "mcmc-1", "kind": "uq",
                                     "result_from": "calibration-1"})))
    with pytest.raises(study_workflow.StudyWorkflowError, match="takes from"):
        study_workflow.move_step(str(path), "mcmc-1", 0)
    assert [s["id"] for s in json.loads(path.read_text())["steps"]] == [
        "calibration-1", "mcmc-1"]


def test_references_reads_every_key():
    step = {"id": "x", "result_from": "a", "fixed_from": ["b", {"step": "c"}],
            "priors_from": [{"step": "d", "kind": "mvnormal"}],
            "set": [{"parameter": "p", "scale": 2, "of": "e"}]}
    assert study_workflow.references(step) == [
        ("result_from", "a"), ("fixed_from", "b"), ("fixed_from", "c"), ("priors_from", "d"),
        ("scale_from", "e")]


@pytest.fixture
def described(monkeypatch):
    """Describing the workflow plans it, and CA reads a CellML target's constants with
    libCellML (the integration tier has it)."""
    pytest.importorskip("libcellml")


def test_a_run_from_a_step_opened_as_the_study_changes_that_step(
        client, fake_runs, described, tmp_path):
    """A block clicked in the chart loads the step as the study (a packed copy of its model);
    a run from it names the workflow's own target, so it changes the step in the same
    workflow rather than starting study-2 for a model it has not seen."""
    import workflow_manager

    work = tmp_path / "w"
    model_id = _setup(client)
    assert _calibrate(client, model_id, work)["state"] == "done"
    # the fake runner records only status; CA's view also reads the step's values
    step_dir = work / "workflows" / "study" / "calibration-1"
    (step_dir / "step_result.json").write_text(json.dumps({
        "step": "calibration-1", "kind": "calibration", "status": "done",
        "calibrated": [], "fixed": [], "finished": "2026-11-18T12:00:00",
        "param_id_output_dir": str(step_dir / "param_id" / "CMA-ES_case")}))
    r = client.post("/api/workflow/view", json={"view": "calibration-1",
                                                "config_outputs_dir": str(work)})
    assert r.status_code == 200, r.text
    opened = r.json()["model_id"]
    assert opened != model_id
    status = _run(client, "calibration", {"model_id": opened, "settings": {
        "config_outputs_dir": str(work), "param_id_method": "CMA-ES",
        "num_calls_to_function": 33}})
    assert status["state"] == "done", status
    listed = client.get("/api/workflow/list", params={"config_outputs_dir": str(work)}).json()
    assert [w["name"] for w in listed["workflows"]] == ["study"]
    steps = _steps(work)
    assert list(steps) == ["calibration-1"]
    assert steps["calibration-1"]["settings"]["optimiser_options"]["num_calls_to_function"] == 33
    assert workflow_manager.workflow.loaded


def test_a_view_opened_for_a_new_step_has_its_model_only(
        client, fake_runs, described, tmp_path):
    """A model box's Calibrate opens a step's model as the study without that step's
    obs_data and params_for_id: a new step brings its own."""
    work = tmp_path / "w"
    model_id = _setup(client)
    assert _calibrate(client, model_id, work)["state"] == "done"
    step_dir = work / "workflows" / "study" / "calibration-1"
    (step_dir / "step_result.json").write_text(json.dumps({
        "step": "calibration-1", "kind": "calibration", "status": "done",
        "calibrated": [], "fixed": [], "finished": "2026-11-18T12:00:00",
        "param_id_output_dir": str(step_dir / "param_id" / "CMA-ES_case")}))
    whole = client.post("/api/workflow/view", json={"view": "calibration-1",
                                                    "config_outputs_dir": str(work)}).json()
    assert whole["obs_data"] and whole["params_for_id"]
    r = client.post("/api/workflow/view", json={"view": "calibration-1", "inputs": False,
                                                "config_outputs_dir": str(work)})
    assert r.status_code == 200, r.text
    assert r.json()["model_id"] and not r.json()["obs_data"] and not r.json()["params_for_id"]


def test_a_step_opened_as_the_study_keeps_its_file_names_and_is_named_by_its_model(tmp_path):
    import main

    obs = tmp_path / "krapivinsky1998_obs_data.json"
    obs.write_text("{}")
    params = tmp_path / "krapivinsky1998_params_for_id.json"
    params.write_text("{}")
    view = {"obs_data_path": str(obs), "params_for_id_path": str(params),
            "model": {"module_type": "i_K1", "version": "v1", "parameterisation": "krapivinsky1998"}}
    (o, p) = main._view_inputs(view, "kir")
    assert o[0] == "krapivinsky1998_obs_data.json" and p[0] == "krapivinsky1998_params_for_id.json"
    assert main._view_model_name(view, "kir") == "kir: i_K1 · krapivinsky1998"
    # a name that does not say which file it is gets the step's prefix, as the importer
    # tells an archive's files apart by name
    data = tmp_path / "targets.json"
    data.write_text("{}")
    (o, _) = main._view_inputs({"obs_data_path": str(data)}, "kir")
    assert o[0] == "kir_obs_data.json"
    assert main._view_model_name({"model": {"cellml_model_path": "../m/LV.cellml"}}, "c-1") == "c-1: LV.cellml"


def test_renaming_a_step_renames_its_references_and_keeps_its_results(
        client, fake_runs, tmp_path):
    work = tmp_path / "w"
    model_id = _setup(client)
    assert _calibrate(client, model_id, work, start_from="current")["state"] == "done"
    _run(client, "uq", {"model_id": model_id, "settings": {
        "config_outputs_dir": str(work), "method": "mcmc", "num_steps": 20, "num_walkers": 8}})
    path = str(_workflow_dir(work) / "calibration_workflow.json")
    run = work / "workflows" / "study"
    assert (run / "calibration-1" / "step_result.json").is_file()

    study_workflow.rename_step(path, "calibration-1", "fit_lv", str(run))
    steps = _steps(work)
    assert list(steps) == ["sliders-1", "fit_lv", "mcmc-1"]
    assert steps["mcmc-1"]["result_from"] == "fit_lv"       # the reference follows
    assert steps["fit_lv"]["start_from"] == "sliders-1"
    # CA finds a step's results by its id: the folder moves with it
    assert (run / "fit_lv" / "step_result.json").is_file()
    assert not (run / "calibration-1").exists()

    for bad, why in (("mcmc-1", "already called"), ("MCMC-1", "already called"),
                     ("has space", "cannot name a step"), ("", "cannot name a step")):
        with pytest.raises(study_workflow.StudyWorkflowError, match=why):
            study_workflow.rename_step(path, "fit_lv", bad, str(run))
    assert list(_steps(work)) == ["sliders-1", "fit_lv", "mcmc-1"]


def test_the_delete_route_asks_before_a_cascade_and_moves_reorder(
        client, fake_runs, described, tmp_path):
    work = tmp_path / "w"
    model_id = _setup(client)
    assert _calibrate(client, model_id, work)["state"] == "done"
    _run(client, "sensitivity", {"model_id": model_id, "settings": {
        "config_outputs_dir": str(work), "method": "sobol", "num_samples": 32}})
    _run(client, "uq", {"model_id": model_id, "settings": {
        "config_outputs_dir": str(work), "method": "mcmc", "num_steps": 20, "num_walkers": 8}})

    r = client.post("/api/workflow/step/delete", json={"step": "calibration-1"})
    assert r.status_code == 200, r.text
    assert r.json()["applied"] is False
    assert r.json()["deleted"] == ["calibration-1", "mcmc-1"]
    assert list(_steps(work)) == ["calibration-1", "sobol-1", "mcmc-1"]

    r = client.post("/api/workflow/step/move", json={"step": "sobol-1", "index": 2})
    assert r.status_code == 200, r.text
    assert list(_steps(work)) == ["calibration-1", "mcmc-1", "sobol-1"]
    r = client.post("/api/workflow/step/move", json={"step": "mcmc-1", "index": 0})
    assert r.status_code == 422 and "takes from" in r.json()["detail"]

    r = client.post("/api/workflow/step/delete", json={"step": "calibration-1",
                                                      "confirm": True})
    assert r.status_code == 200 and r.json()["applied"] is True
    assert list(_steps(work)) == ["sobol-1"]
    assert [s["id"] for s in r.json()["workflow"]["workflow"]["steps"]] == ["sobol-1"]
    # the results stay where CA wrote them
    assert (work / "workflows" / "study" / "calibration-1").is_dir()
