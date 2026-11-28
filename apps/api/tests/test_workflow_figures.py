"""A workflow's figure and simulate steps (CA #705) in the Workflow view and Analysis.

CA draws the figures; CUFLynx lists the files each figure step drew (from its
``step_result.json``), serves only those, says what an arrow into a figure step carries,
and builds no tab for a figure step (it has no model). The integration test runs a simulate
step and one of CA's standard figures for real, then draws the figure again alone.
"""

import json
import shutil
import time
from pathlib import Path

import pytest

import ca_run_history
import main
import workflow_edges
import workflow_manager as workflow_mod
from conftest import LV_MODEL_PATH, LV_OBS_DATA_PATH
from test_calibration_workflow import _fake_ca

PNG = b"\x89PNG\r\n\x1a\nnot really"


def _figure_step(run_dir: Path, step_id: str, names, written_in: Path | None = None) -> Path:
    """A finished figure step as CA writes it: the files under ``<step>/figure/`` and a
    step_result naming them by absolute path, recorded where the run was (``written_in``)."""
    step_dir = run_dir / step_id
    (step_dir / "figure").mkdir(parents=True)
    for name in names:
        (step_dir / "figure" / name).write_bytes(PNG)
    recorded = (written_in or run_dir) / step_id
    (step_dir / "step_result.json").write_text(json.dumps({
        "step": step_id, "kind": "figure", "status": "done",
        "function": "standard_figures.py:sweep_traces",
        "figure_inputs": {"sweep": {"kind": "sweep", "step": "run"}},
        "figure_dir": str(recorded / "figure"),
        "figures": [str(recorded / "figure" / n) for n in names]}))
    return step_dir


def test_a_figure_steps_files_are_read_where_the_run_is_now(tmp_path):
    # the run was made elsewhere and moved here: CA's absolute paths name the old place
    step_dir = _figure_step(tmp_path / "now", "traces", ["a.png", "b.csv"],
                            written_in=tmp_path / "then")
    result = json.loads((step_dir / "step_result.json").read_text())
    # a file gone since, and a path outside the step directory, are not listed
    result["figures"] += [str(tmp_path / "then" / "traces" / "figure" / "gone.png"),
                          str(tmp_path / "then" / "elsewhere.png")]
    (tmp_path / "now" / "elsewhere.png").write_bytes(PNG)
    (step_dir / "step_result.json").write_text(json.dumps(result))
    assert ca_run_history.step_figures(str(step_dir)) == ["figure/a.png", "figure/b.csv"]
    assert ca_run_history.step_figures(str(tmp_path / "never_ran")) == []


def test_only_the_files_a_figure_step_drew_are_served(client, tmp_path, monkeypatch):
    run = tmp_path / "run"
    _figure_step(run, "traces", ["traces_exp_0.png"])
    (run / "secret.txt").write_text("not a figure")
    monkeypatch.setattr(workflow_mod.workflow, "output_dir", str(run))
    r = client.get("/api/workflow/figure", params={"step": "traces",
                                                   "name": "figure/traces_exp_0.png"})
    assert r.status_code == 200 and r.content == PNG
    assert r.headers["content-type"] == "image/png"
    for step, name in [("traces", "step_result.json"), ("traces", "../secret.txt"),
                       ("..", "secret.txt"), ("traces/..", "secret.txt"),
                       ("traces", "figure/other.png")]:
        r = client.get("/api/workflow/figure", params={"step": step, "name": name})
        assert r.status_code == 404, (step, name)


def test_describe_lists_each_done_figure_steps_files(client, monkeypatch, tmp_path):
    from test_calibration_workflow import WORKFLOW
    path = tmp_path / "study" / "workflow.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(WORKFLOW))
    _fake_ca(monkeypatch)  # its status: fit_a done, rest not run
    out = Path(client.post("/api/workflow/load", json={
        "path": str(path), "config_outputs_dir": str(tmp_path / "outputs")}).json()["output_dir"])
    _figure_step(out, "fit_a", ["fit.png"])
    _figure_step(out, "rest", ["never.png"])
    steps = client.get("/api/workflow").json()["status"]["steps"]
    assert steps["fit_a"]["figures"] == ["figure/fit.png"]
    # a step CA does not call done lists nothing, whatever is on disk
    assert "figures" not in steps["rest"]


def test_a_figure_step_has_no_tab(tmp_path):
    path = tmp_path / "wf.json"
    path.write_text(json.dumps({"workflow_name": "w", "target": {"cellml_model_path": "m.cellml"},
                                "steps": [{"id": "run", "kind": "simulate"},
                                          {"id": "traces", "kind": "figure",
                                           "function": "f.py:draw",
                                           "inputs": {"sweep": {"sweep": "run"}}}]}))
    assert main._tab_views(str(path)) == ["run", "target"]


def test_an_arrow_into_a_figure_says_what_it_reads(tmp_path, monkeypatch):
    monkeypatch.setattr(workflow_edges, "_input_kinds",
                        lambda: {"sweep": "the arrays of sweep_results.npz"})
    workflow = {"steps": [{"id": "run", "kind": "simulate"},
                          {"id": "draw", "kind": "figure", "function": "f.py:draw",
                           "inputs": {"curves": {"sweep": "run"}, "obs": {"obs_data": "run"}}}]}
    out = workflow_edges.edge_detail(str(tmp_path), workflow, "run", "draw", "input:curves")
    assert out["input"] == {"name": "curves", "kind": "sweep",
                            "description": "the arrays of sweep_results.npz"}
    assert out["what"] == "sweep of run, as the figure's curves"
    assert not out["recorded"] and "run has not run yet" in out["note"]
    (tmp_path / "run").mkdir()
    (tmp_path / "run" / "step_result.json").write_text(json.dumps({"step": "run"}))
    out = workflow_edges.edge_detail(str(tmp_path), workflow, "run", "draw", "input:obs")
    assert out["input"]["kind"] == "obs_data" and out["recorded"] and out["note"] == ""


# ---------------------------------------------------------------------------
# integration: a simulate step and a standard figure, run by CA
# ---------------------------------------------------------------------------
def _wait(client, job_id, timeout=240):
    deadline = time.time() + timeout
    while time.time() < deadline:
        s = client.get(f"/api/workflow/{job_id}/status").json()
        if s["state"] != "running":
            return s
        time.sleep(0.2)
    raise AssertionError("the workflow did not finish")


@pytest.mark.integration
def test_a_figure_step_is_drawn_shown_and_drawn_again_alone(client, requires_simulation,
                                                            tmp_path):
    from ca_imports import ca_from, ca_import
    builder = ca_from("calibration_workflow", "WorkflowBuilder")
    cellml_model = ca_from("calibration_workflow", "cellml_model")
    if not hasattr(builder, "add_figure"):
        pytest.skip("this libcuflynx has no figure steps (CA #705)")
    figures = ca_import("calibration_workflow.figures")
    for f in (LV_MODEL_PATH, LV_OBS_DATA_PATH):
        shutil.copy(f, tmp_path)
    wf_dir = tmp_path / "wf"
    script = figures.write_standard_figures(str(wf_dir / "figures"))
    wf = builder("figs", target=cellml_model(str(tmp_path / LV_MODEL_PATH.name)),
                 directory=str(wf_dir), settings={"dt": 0.01})
    wf.add_simulate("run", obs_data=str(tmp_path / LV_OBS_DATA_PATH.name),
                    outputs=["Lotka_Volterra_module/x"])
    wf.add_figure("traces", function=f"{script}:sweep_traces",
                  inputs={"sweep": {"sweep": "run"}})
    path = str(wf.write())
    r = client.post("/api/workflow/load",
                    json={"path": path, "config_outputs_dir": str(tmp_path / "out")})
    assert r.status_code == 200, r.text
    out = Path(r.json()["output_dir"])

    status = _wait(client, client.post("/api/workflow/run", json={}).json()["job_id"])
    assert status["state"] == "done", status
    described = client.get("/api/workflow").json()
    plan = {s["id"]: s for s in described["plan"]["steps"]}
    assert plan["traces"]["kind"] == "figure" and plan["traces"]["depends_on"] == ["run"]
    steps = described["status"]["steps"]
    assert steps["run"]["status"] == "done" and "figures" not in steps["run"]
    assert steps["traces"]["figures"] == ["figure/traces_exp_0.png"]
    r = client.get("/api/workflow/figure", params={"step": "traces",
                                                   "name": "figure/traces_exp_0.png"})
    assert r.status_code == 200 and r.content.startswith(b"\x89PNG")

    # Run only the figure: drawn again from the simulation already there
    drawn = (out / "traces" / "figure" / "traces_exp_0.png").stat().st_mtime_ns
    sim = (out / "run" / "step_result.json").stat().st_mtime_ns
    status = _wait(client, client.post("/api/workflow/run", json={"only": "traces"}).json()["job_id"])
    assert status["state"] == "done", status
    assert (out / "traces" / "figure" / "traces_exp_0.png").stat().st_mtime_ns > drawn
    assert (out / "run" / "step_result.json").stat().st_mtime_ns == sim
