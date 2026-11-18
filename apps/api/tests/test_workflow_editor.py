"""Editing a workflow on its chart (``workflow_layout``, ``/api/workflow/{draft,link,position}``):
new steps put on the chart before they run, arrows drawn between steps (written into the
workflow file and checked by CA) or to a new step (kept until it is a step), and where each
box sits.

The last test is the one Finbar asked for: set up a study, then calibrate three parameters
of the model in three calibration steps, one parameter each, the later steps fixing what the
earlier ones found.
"""

from __future__ import annotations

import json
import time

import pytest

import workflow_layout
from test_study_workflow import (  # noqa: F401  (fake_runs is a fixture)
    NEW_STEP,
    _calibrate,
    _run,
    _setup,
    _steps,
    _workflow_dir,
    fake_runs,
)

MODULE = "Lotka_Volterra_module"
#: The LV fixture's bounds (resources/Lotka_Volterra_params_for_id.csv).
BOUNDS = {"alpha": (0.1, 7.0), "beta": (0.01, 2.0), "delta": (0.01, 2.0), "gamma": (0.1, 10.0)}


def _params_for(client, model_id, *names):
    """Calibrate only ``names`` from now on: the study's params_for_id, one entry each."""
    doc = {"version": 1, "params": [
        {"targets": [f"{MODULE}/{n}"], "min": BOUNDS[n][0], "max": BOUNDS[n][1]} for n in names]}
    r = client.post(f"/api/params_for_id/upload?model_id={model_id}", files={
        "file": (f"{'_'.join(names)}_params_for_id.json", json.dumps(doc), "application/json")})
    assert r.status_code == 200, r.text


def _calibrated(step: dict, work) -> list[str]:
    path = _workflow_dir(work) / step["params_for_id"]
    return [t for p in json.loads(path.read_text())["params"] for t in p["targets"]]


def _draft(client) -> str:
    r = client.post("/api/workflow/draft", json={})
    assert r.status_code == 200, r.text
    return r.json()["draft"]


def _link(client, source, target, key, **extra):
    return client.post("/api/workflow/link", json={"source": source, "target": target,
                                                   "key": key, **extra})


def test_an_arrow_between_steps_is_written_into_the_workflow_and_checked_by_ca(
        client, fake_runs, tmp_path):
    work = tmp_path / "w"
    model_id = _setup(client)
    assert _calibrate(client, model_id, work)["state"] == "done"
    _run(client, "uq", {"model_id": model_id, "settings": {
        "config_outputs_dir": str(work), "method": "mcmc", "num_steps": 20, "num_walkers": 8}})
    assert _calibrate(client, model_id, work, workflow_target=NEW_STEP)["state"] == "done"

    r = _link(client, "calibration-1", "calibration-2", "fixed_from")
    assert r.status_code == 200, r.text
    assert _steps(work)["calibration-2"]["fixed_from"] == ["calibration-1"]
    # what each kind of arrow can be is CA's
    kinds = r.json()["link_kinds"]
    assert "calibration" in kinds["fixed_from"]["sources"]
    assert "calibration" in kinds["start_from"]["users"]

    # a calibration gives a prior only when it stores its distribution, which needs MCMC
    # settings: CA says so, and nothing is guessed for it
    r = _link(client, "calibration-1", "calibration-2", "priors_from", old_key="fixed_from")
    assert r.status_code == 422
    assert "store_distribution" in r.json()["detail"]
    assert _steps(work)["calibration-2"]["fixed_from"] == ["calibration-1"]
    # a uq step's posterior is a prior, with its options; the calibration it goes to uses it
    # only when it optimises the log posterior, which the arrow asks for explicitly
    prior = {"kind": "normal", "inflate": 2.0, "parameters": [f"{MODULE}/alpha"]}
    r = _link(client, "mcmc-1", "calibration-2", "priors_from", options=prior)
    assert r.status_code == 422
    assert "ignore the priors" in r.json()["detail"]
    r = _link(client, "mcmc-1", "calibration-2", "priors_from",
              options={**prior, "objective": "likelihood"})
    assert r.status_code == 200, r.text
    step = _steps(work)["calibration-2"]
    assert step["priors_from"] == [{"step": "mcmc-1", **prior}]
    assert step["settings"]["optimiser_options"]["objective_function"] == "likelihood"

    # CA refuses what it does not take, with its reason, and the file is unchanged
    r = _link(client, "calibration-2", "calibration-1", "fixed_from")
    assert r.status_code == 422
    assert "fixed_from" not in _steps(work)["calibration-1"]
    r = _link(client, "calibration-1", "calibration-2", "sensitivity_from")
    assert r.status_code == 422

    r = client.post("/api/workflow/link/delete", json={
        "source": "mcmc-1", "target": "calibration-2", "key": "priors_from"})
    assert r.status_code == 200, r.text
    assert "priors_from" not in _steps(work)["calibration-2"]


def test_a_fixed_arrow_names_its_parameters_only_where_ca_takes_them(
        client, fake_runs, tmp_path, monkeypatch):
    work = tmp_path / "w"
    model_id = _setup(client)
    assert _calibrate(client, model_id, work)["state"] == "done"
    assert _calibrate(client, model_id, work, workflow_target=NEW_STEP)["state"] == "done"
    monkeypatch.setattr(workflow_layout, "fixed_parameters_supported", lambda: False)
    r = _link(client, "calibration-1", "calibration-2", "fixed_from",
              options={"parameters": [f"{MODULE}/alpha"]})
    assert r.status_code == 422
    assert "fixes every value" in r.json()["detail"]
    assert client.get("/api/workflow").json()["fixed_parameters"] is False


def test_a_fixed_arrow_fixes_only_the_parameters_it_names_where_ca_takes_them(
        client, fake_runs, tmp_path):
    """CA #684: a fixed_from entry {"step", "parameters"} fixes only those values (model
    names in the source step, as a prior's parameters)."""
    if not workflow_layout.fixed_parameters_supported():
        pytest.skip("this circulatory_autogen fixes every value of a fixed_from step (CA #684)")
    work = tmp_path / "w"
    model_id = _setup(client)
    assert _calibrate(client, model_id, work)["state"] == "done"
    assert _calibrate(client, model_id, work, workflow_target=NEW_STEP)["state"] == "done"
    assert client.get("/api/workflow").json()["fixed_parameters"] is True
    r = _link(client, "calibration-1", "calibration-2", "fixed_from",
              options={"parameters": ["alpha_Lotka_Volterra_module"]})
    assert r.status_code == 200, r.text
    assert _steps(work)["calibration-2"]["fixed_from"] == [
        {"step": "calibration-1", "parameters": ["alpha_Lotka_Volterra_module"]}]
    # every value again: the bare id
    r = _link(client, "calibration-1", "calibration-2", "fixed_from")
    assert _steps(work)["calibration-2"]["fixed_from"] == ["calibration-1"]


def test_a_new_step_is_placed_and_wired_before_it_runs_and_its_run_makes_it_a_step(
        client, fake_runs, tmp_path):
    work = tmp_path / "w"
    model_id = _setup(client)
    assert _calibrate(client, model_id, work)["state"] == "done"

    draft = _draft(client)
    assert draft == "new-1"
    assert client.post("/api/workflow/position",
                       json={"node": draft, "col": 3, "row": 0.5}).status_code == 200
    assert _link(client, "calibration-1", draft, "fixed_from").status_code == 200
    described = client.get("/api/workflow").json()
    assert described["layout"]["drafts"] == [{"id": "new-1"}]
    assert described["layout"]["links"] == [
        {"source": "calibration-1", "target": "new-1", "key": "fixed_from"}]
    # nothing in the workflow file yet: a draft has no kind until it runs
    assert list(_steps(work)) == ["calibration-1"]

    status = _calibrate(client, model_id, work,
                        workflow_target={"mode": "new_step", "draft": draft})
    assert status["state"] == "done", status["lines"]
    assert _steps(work)["calibration-2"]["fixed_from"] == ["calibration-1"]
    layout = client.get("/api/workflow").json()["layout"]
    assert layout["drafts"] == [] and layout["links"] == []
    assert layout["positions"]["calibration-2"] == {"col": 3, "row": 0.5}


def test_a_refused_arrow_to_a_new_step_stops_its_run_and_stays_to_be_changed(
        client, fake_runs, tmp_path):
    work = tmp_path / "w"
    model_id = _setup(client)
    assert _calibrate(client, model_id, work)["state"] == "done"
    draft = _draft(client)
    # a calibration takes no sensitivity_from (a reduction does): CA says so at the run
    assert _link(client, "calibration-1", draft, "sensitivity_from").status_code == 200
    r = client.post("/api/calibration/run", json={
        "model_id": model_id, "settings": {
            "config_outputs_dir": str(work), "param_id_method": "CMA-ES",
            "num_calls_to_function": 50, "workflow_target": {"mode": "new_step", "draft": draft}}})
    assert r.status_code == 422
    assert "arrow drawn to it was refused" in r.json()["detail"]
    links = client.get("/api/workflow").json()["layout"]["links"]
    assert links == [{"source": "calibration-1", "target": "calibration-2",
                      "key": "sensitivity_from"}]


def test_renaming_or_deleting_a_step_carries_its_place_and_arrows(client, fake_runs, tmp_path):
    work = tmp_path / "w"
    model_id = _setup(client)
    assert _calibrate(client, model_id, work)["state"] == "done"
    draft = _draft(client)
    client.post("/api/workflow/position", json={"node": "calibration-1", "col": 2, "row": 0})
    _link(client, "calibration-1", draft, "fixed_from")
    r = client.post("/api/workflow/step/rename", json={"step": "calibration-1", "to": "fit_a"})
    assert r.status_code == 200, r.text
    layout = r.json()["layout"]
    assert layout["positions"]["fit_a"] == {"col": 2, "row": 0.0}
    assert layout["links"][0]["source"] == "fit_a"
    r = client.post("/api/workflow/draft/delete", json={"draft": draft})
    assert r.json()["layout"]["drafts"] == [] and r.json()["layout"]["links"] == []


def test_a_box_after_the_target_is_a_sweep_made_from_its_form(client, fake_runs, tmp_path):
    work = tmp_path / "w"
    model_id = _setup(client)
    assert _calibrate(client, model_id, work)["state"] == "done"
    r = client.post("/api/workflow/draft", json={"col": 2, "row": 0, "on_target": True})
    assert r.status_code == 200, r.text
    draft = r.json()["draft"]
    assert r.json()["layout"]["drafts"] == [{"id": draft, "on_target": True}]

    # a panel's run cannot make it: only a sweep runs on the merged target
    r = client.post("/api/calibration/run", json={"model_id": model_id, "settings": {
        "config_outputs_dir": str(work), "param_id_method": "CMA-ES",
        "num_calls_to_function": 50, "workflow_target": {"mode": "new_step", "draft": draft}}})
    assert r.status_code == 422
    assert "Simulation form" in r.json()["detail"]

    r = client.post("/api/workflow/sweep", json={
        "draft": draft, "model_id": model_id, "outputs": [f"{MODULE}/x"],
        "parameters": {f"{MODULE}/alpha": {"values": [1.0, 2.0]}}, "design": "one_at_a_time"})
    assert r.status_code == 200, r.text
    step = _steps(work)[draft]
    assert step["kind"] == "sweep" and step["on_target"] is True
    assert step["sweep"] == {"parameters": {f"{MODULE}/alpha": {"values": [1.0, 2.0]}},
                             "outputs": [f"{MODULE}/x"], "design": "one_at_a_time"}
    # CA needs a step to record at: the one the app's solver uses
    assert step["settings"]["dt"] > 0
    # the study's obs_data, written relative to the workflow file
    assert (_workflow_dir(work) / step["obs_data"]).is_file()
    assert r.json()["layout"]["drafts"] == []
    assert r.json()["layout"]["positions"][draft] == {"col": 2, "row": 0.0}


def test_a_simulation_varies_what_its_obs_data_protocol_does_and_records_its_items(
        client, fake_runs, tmp_path):
    """A Simulation box takes the study's obs_data: what varies is that protocol's
    params_to_change, so the step names no parameters; it records the variables the obs_data's
    items name."""
    work = tmp_path / "w"
    model_id = _setup(client)
    assert _calibrate(client, model_id, work)["state"] == "done"
    draft = client.post("/api/workflow/draft", json={"on_target": True}).json()["draft"]
    r = client.post("/api/workflow/sweep", json={"draft": draft, "model_id": model_id})
    assert r.status_code == 200, r.text
    step = _steps(work)[draft]
    assert step["sweep"]["parameters"] == {}
    # kept beside the workflow, which depends on it (not in the session's uploads)
    assert step["obs_data"] == f"data/{draft}_obs_data.json"
    obs = json.loads((_workflow_dir(work) / step["obs_data"]).read_text())
    named = [v for i in obs["data_items"] for v in i["operands"]]
    assert named and step["sweep"]["outputs"] == list(dict.fromkeys(named))


def test_a_drop_in_a_gap_moves_the_boxes_right_of_it_in_one_go(client, fake_runs, tmp_path):
    work = tmp_path / "w"
    model_id = _setup(client)
    assert _calibrate(client, model_id, work)["state"] == "done"
    draft = _draft(client)
    r = client.post("/api/workflow/positions", json={"positions": {
        draft: {"col": 0, "row": 0}, "calibration-1": {"col": 1, "row": 0}}})
    assert r.status_code == 200, r.text
    assert r.json()["layout"]["positions"] == {draft: {"col": 0, "row": 0.0},
                                               "calibration-1": {"col": 1, "row": 0.0}}
    r = client.post("/api/workflow/positions", json={"positions": {"nope": {"col": 0, "row": 0}}})
    assert r.status_code == 422


def _external_copy(work):
    """The study workflow copied to a folder that is not a study workflow's
    (``<W>/project/enterocyte/``, as a workflow opened from elsewhere is), its relative input
    paths still resolving."""
    src = _workflow_dir(work) / "calibration_workflow.json"
    dst = work / "project" / "enterocyte" / "calibration_workflow.json"
    dst.parent.mkdir(parents=True)
    dst.write_text(src.read_text())
    return dst


def test_a_model_box_on_a_workflow_from_elsewhere_is_made_there_by_a_panel_run(
        client, fake_runs, tmp_path):
    """A workflow opened from elsewhere (enterocyte's, of library models) is not a study
    workflow: a run that makes one of its model boxes writes the step into its own file, on
    the box's model, and runs it in its own run directory."""
    work = tmp_path / "w"
    model_id = _setup(client)
    assert _calibrate(client, model_id, work)["state"] == "done"
    ext = _external_copy(work)
    run_dir = tmp_path / "ext_run"
    r = client.post("/api/workflow/load", json={"path": str(ext), "run_dir": "",
                                                "config_outputs_dir": str(run_dir)})
    assert r.status_code == 200, r.text
    model = json.loads(ext.read_text())["steps"][-1]["model"]
    r = client.post("/api/workflow/draft", json={"col": 1, "row": 0, "model": model})
    assert r.status_code == 200, r.text
    draft = r.json()["draft"]
    assert r.json()["layout"]["drafts"] == [{"id": draft, "model": model}]

    status = _calibrate(client, model_id, work, workflow_target={
        "mode": "new_step", "draft": draft, "workflow": str(ext)})
    assert status["state"] == "done", status["lines"]
    steps = {s["id"]: s for s in json.loads(ext.read_text())["steps"]}
    assert list(steps) == ["calibration-1", "calibration-2"]
    assert steps["calibration-2"]["model"] == model
    config = fake_runs()[-1]
    assert config["workflow"] == str(ext)
    assert config["write_calibrated"] is False
    # the study workflow in the working dir is untouched
    assert list(_steps(work)) == ["calibration-1"]


def test_an_action_box_sets_values_by_hand(client, fake_runs, tmp_path):
    work = tmp_path / "w"
    model_id = _setup(client)
    assert _calibrate(client, model_id, work)["state"] == "done"
    r = client.post("/api/workflow/draft", json={"col": 0, "row": 0, "action": "set"})
    draft = r.json()["draft"]
    assert r.json()["layout"]["drafts"] == [{"id": draft, "action": "set"}]
    assert "set" in r.json()["step_kinds"]
    r = client.post("/api/workflow/set_values", json={"draft": draft, "entries": [
        {"parameter": f"{MODULE}/gamma", "value": 1.5, "reason": ""}]})
    assert r.status_code == 422
    assert "say why" in r.json()["detail"]
    r = client.post("/api/workflow/set_values", json={"draft": draft, "entries": [
        {"parameter": f"{MODULE}/gamma", "value": 1.5, "reason": "Smith 2001, Table 2"}]})
    assert r.status_code == 200, r.text
    assert _steps(work)[draft] == {"id": draft, "kind": "set", "set": [
        {"parameter": f"{MODULE}/gamma", "value": 1.5, "reason": "Smith 2001, Table 2"}]}
    r = client.post("/api/workflow/draft", json={"action": "teleport"})
    assert r.status_code == 422


def test_an_action_box_combines_the_calibrations_drawn_into_it(client, fake_runs, tmp_path):
    """Two calibrations of one parameter that do not take from each other: CA will not
    merge both into the target (CA #685); a combine step drawn from both gives it one value."""
    work = tmp_path / "w"
    model_id = _setup(client)
    assert _calibrate(client, model_id, work)["state"] == "done"
    r = client.post("/api/workflow/draft", json={"action": "combine"})
    if "combine" not in r.json()["step_kinds"]:
        pytest.skip("this CA has no combine step (CA #685)")
    draft = r.json()["draft"]
    assert r.json()["link_kinds"]["combine_from"]["users"] == ["combine"]
    assert _calibrate(client, model_id, work, workflow_target=NEW_STEP)["state"] == "done"
    assert "calibration-1" in " ".join(client.get("/api/workflow").json()["problems"])
    assert _link(client, "calibration-1", draft, "combine_from").status_code == 200
    r = client.post("/api/workflow/combine", json={"draft": draft})
    assert r.status_code == 422 and "two or more" in r.json()["detail"]
    assert _link(client, "calibration-2", draft, "combine_from").status_code == 200
    r = client.post("/api/workflow/combine", json={"draft": draft, "method": "median"})
    assert r.status_code == 422
    r = client.post("/api/workflow/combine", json={"draft": draft, "method": "mean"})
    assert r.status_code == 200, r.text
    assert _steps(work)[draft] == {"id": draft, "kind": "combine",
                                   "combine_from": ["calibration-1", "calibration-2"],
                                   "combine": {"method": "mean"}}
    described = r.json()
    assert not described.get("plan_error") and described["problems"] == []
    assert described["layout"]["links"] == []
    assert described["layout"]["drafts"] == []


def _wait(client, kind, job_id, seconds=420) -> dict:
    deadline = time.time() + seconds
    while time.time() < deadline:
        s = client.get(f"/api/{kind}/{job_id}/status").json()
        if s["state"] != "running":
            return s
        time.sleep(0.5)
    raise AssertionError(f"{kind} did not finish in {seconds} s")


def _three_parameters(client, run, work):
    """Set up the study, then calibrate alpha, beta and delta in three calibration steps.

    ``run(settings)`` runs one calibration. The first makes the workflow; the other two are
    put on the chart first, as new steps, and wired: the beta step fixes the alpha step's
    value, and the delta step fixes both. Each then runs with a params_for_id of its own
    parameter only.
    """
    model_id = _setup(client)
    _params_for(client, model_id, "alpha")
    status = run(model_id, {})
    assert status["state"] == "done", status["lines"]

    beta, delta = _draft(client), _draft(client)
    for source, target in (("calibration-1", beta), ("calibration-1", delta), (beta, delta)):
        assert _link(client, source, target, "fixed_from").status_code == 200

    _params_for(client, model_id, "beta")
    status = run(model_id, {"workflow_target": {"mode": "new_step", "draft": beta}})
    assert status["state"] == "done", status["lines"]
    _params_for(client, model_id, "delta")
    status = run(model_id, {"workflow_target": {"mode": "new_step", "draft": delta}})
    assert status["state"] == "done", status["lines"]

    steps = _steps(work)
    assert list(steps) == ["calibration-1", "calibration-2", "calibration-3"]
    assert [_calibrated(steps[s], work) for s in steps] == [
        [f"{MODULE}/alpha"], [f"{MODULE}/beta"], [f"{MODULE}/delta"]]
    assert steps["calibration-2"]["fixed_from"] == ["calibration-1"]
    assert steps["calibration-3"]["fixed_from"] == ["calibration-1", "calibration-2"]
    described = client.get("/api/workflow").json()
    # one parameter per step: nothing calibrated twice, nothing left waiting
    assert described["problems"] == []
    assert described["layout"]["drafts"] == [] and described["layout"]["links"] == []
    return {**described, "model_id_for_test": model_id}


def test_three_parameters_in_three_calibration_steps(client, fake_runs, tmp_path):
    work = tmp_path / "w"

    def run(model_id, extra):
        return _calibrate(client, model_id, work, **extra)

    model_id = _three_parameters(client, run, work)["model_id_for_test"]
    # changing the beta step from its panel keeps the arrow drawn to it
    _params_for(client, model_id, "beta")
    status = _calibrate(client, model_id, work, num_calls_to_function=9,
                        workflow_target={"mode": "edit", "step": "calibration-2"})
    assert status["state"] == "done", status["lines"]
    step = _steps(work)["calibration-2"]
    assert step["fixed_from"] == ["calibration-1"]
    assert step["settings"]["optimiser_options"]["num_calls_to_function"] == 9


@pytest.mark.integration
def test_three_parameters_in_three_calibration_steps_run_by_ca(
        client, study_workflow_on, tmp_path):
    """The same, run by CA for real: each step fits its one parameter with the earlier
    steps' values fixed, and the target carries all three."""
    pytest.importorskip("libcellml")
    work = tmp_path / "w"

    def run(model_id, extra):
        r = client.post("/api/calibration/run", json={
            "model_id": model_id, "settings": {
                "config_outputs_dir": str(work), "param_id_method": "CMA-ES",
                "num_calls_to_function": 40, **extra}})
        assert r.status_code == 200, r.text
        return _wait(client, "calibration", r.json()["job_id"])

    described = _three_parameters(client, run, work)
    status = described["status"]
    assert status["complete"] is True, status
    run_dir = work / "workflows" / "study"
    for step_id, name in (("calibration-1", "alpha"), ("calibration-2", "beta"),
                          ("calibration-3", "delta")):
        result = json.loads((run_dir / step_id / "step_result.json").read_text())
        assert [c["param"] for c in result["calibrated"]] == [name], result
    third = json.loads((run_dir / "calibration-3" / "step_result.json").read_text())
    fixed = {(f["param"], f["from_step"]) for f in third["fixed"]}
    assert {("alpha", "calibration-1"), ("beta", "calibration-2")} <= fixed, third["fixed"]
    # each fixed value is the best fit of the step that calibrated it
    first = json.loads((run_dir / "calibration-1" / "step_result.json").read_text())
    alpha = next(f["value"] for f in third["fixed"] if f["param"] == "alpha")
    assert alpha == pytest.approx(first["calibrated"][0]["value"])


@pytest.mark.integration
def test_two_calibrations_of_one_parameter_combined_by_cost_run_by_ca(
        client, study_workflow_on, tmp_path):
    """Run by CA (CA #685): a second calibration of alpha, unrelated to the first, runs, and
    the view says the target cannot take both; a combine box drawn from both is made, and
    re-running the second runs the combine after it, which chooses the lower cost."""
    pytest.importorskip("libcellml")
    import workflow_layout as wl  # noqa: PLC0415
    if "combine" not in wl.step_kinds():
        pytest.skip("this CA has no combine step (CA #685)")
    work = tmp_path / "w"

    def run(model_id, extra):
        r = client.post("/api/calibration/run", json={
            "model_id": model_id, "settings": {
                "config_outputs_dir": str(work), "param_id_method": "CMA-ES",
                "num_calls_to_function": 30, **extra}})
        assert r.status_code == 200, r.text
        return _wait(client, "calibration", r.json()["job_id"])

    model_id = _setup(client)
    _params_for(client, model_id, "alpha")
    assert run(model_id, {})["state"] == "done"
    second = run(model_id, {"workflow_target": {"mode": "new_step"}})
    assert second["state"] == "done", second["lines"]
    assert "combine" in " ".join(client.get("/api/workflow").json()["problems"])
    assert list(_steps(work)) == ["calibration-1", "calibration-2"]

    draft = client.post("/api/workflow/draft", json={"action": "combine"}).json()["draft"]
    for source in ("calibration-1", "calibration-2"):
        assert _link(client, source, draft, "combine_from").status_code == 200
    r = client.post("/api/workflow/combine", json={"draft": draft})
    assert r.status_code == 200, r.text
    assert not r.json().get("plan_error") and r.json()["problems"] == []

    status = run(model_id, {"workflow_target": {"mode": "edit", "step": "calibration-2"}})
    assert status["state"] == "done", status["lines"]
    run_dir = work / "workflows" / "study"
    costs = {s: json.loads((run_dir / s / "step_result.json").read_text())["best_cost"]
             for s in ("calibration-1", "calibration-2")}
    combined = json.loads((run_dir / draft / "step_result.json").read_text())
    assert combined["chosen_from"] == min(costs, key=costs.get)
    assert combined["best_cost"] == pytest.approx(min(costs.values()))
    status = client.get("/api/workflow").json()["status"]["steps"]
    assert status[draft]["chosen_from"] == combined["chosen_from"]
