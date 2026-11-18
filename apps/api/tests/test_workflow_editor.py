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
