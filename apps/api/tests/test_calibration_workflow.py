"""Calibration workflows in the GUI (calibration_workflow.py + the /api/workflow routes).

Everything about a workflow is CA's (libcuflynx.calibration_workflow); CUFLynx keeps
which one is open, runs it as a subprocess and loads its tabs as studies. The unit tier
injects a fake CA module and a fake runner; the integration tier runs CA's own fixture
library (libcuflynx.external_testing.workflow_library) for real.
"""

import io
import json
import os
import sys
import time
import types
import zipfile
from pathlib import Path

import pytest

import workflow_manager as workflow_mod
import main
import omex_import
import settings_store
from conftest import LV_MODEL_PATH, LV_OBS_DATA_PATH, LV_PARAMS_CSV_PATH, set_ca_module

WORKFLOW = {
    "workflow_name": "demo",
    "target": {"module_type": "twin", "version": "v1", "instance": "split"},
    "steps": [
        {"id": "fit_a", "target": {"module_type": "lin_a", "version": "v1", "instance": "fit_a"}},
        {"id": "rest", "target": {"module_type": "twin", "version": "v1", "instance": "split"},
         "depends_on": ["fit_a"], "fixed_from": ["fit_a"]},
    ],
}


def _fake_ca(monkeypatch, calls=None, plan_error=None, refuse=None):
    """A stand-in for libcuflynx.calibration_workflow, recording what it was asked."""
    calls = calls if calls is not None else []
    mod = types.ModuleType("calibration_workflow")

    class FakeWorkflowError(ValueError):
        pass

    def load_workflow(path, module_library_dirs=None):
        calls.append(("load", path, list(module_library_dirs or [])))
        if refuse:
            raise FakeWorkflowError(refuse)
        raw = json.loads(Path(path).read_text())
        return types.SimpleNamespace(name=raw["workflow_name"])

    def plan_workflow(path, module_library_dirs=None):
        calls.append(("plan", path, list(module_library_dirs or [])))
        if plan_error:
            raise FakeWorkflowError(plan_error)
        return {"workflow_name": "demo", "steps": [{"id": "fit_a"}, {"id": "rest"}]}

    def workflow_status(path, output_dir, module_library_dirs=None):
        calls.append(("status", path, output_dir))
        return {"steps": {"fit_a": {"status": "done", "stale": False},
                          "rest": {"status": "not_run", "stale": False}}, "complete": False}

    def workflow_model(path, view, output_dir, work_dir, module_library_dirs=None):
        calls.append(("model", view, output_dir))
        return {"view": view, "kind": "step", "target": WORKFLOW["steps"][0]["target"],
                "step_id": view, "submodule_path": "A",
                "model_path": str(LV_MODEL_PATH), "obs_data_path": str(LV_OBS_DATA_PATH),
                "params_for_id_path": str(LV_PARAMS_CSV_PATH),
                "fixed": [{"model_name": "p_mod_A", "value": 2.0, "from_step": "fit_a"}],
                "calibrated": [], "waiting_for": [], "stale": [], "param_id_output_dir": None}

    for fn in (load_workflow, plan_workflow, workflow_status, workflow_model):
        setattr(mod, fn.__name__, fn)
    set_ca_module(monkeypatch, "calibration_workflow", mod)
    return calls


@pytest.fixture
def workflow_file(tmp_path):
    path = tmp_path / "instances" / "split" / "calibration_workflow.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(WORKFLOW))
    return path


@pytest.fixture
def library(tmp_path, client):
    directory = tmp_path / "library"
    directory.mkdir()
    r = client.post("/api/config", json={"module_library_dirs": [str(directory)]})
    assert r.status_code == 200, r.text
    return directory


# ---------------------------------------------------------------------------
# settings
# ---------------------------------------------------------------------------
def test_module_library_dirs_are_a_persisted_setting(client, tmp_path):
    assert client.get("/api/config").json()["module_library_dirs"] == []
    r = client.post("/api/config", json={"module_library_dirs": [str(tmp_path), " "]})
    assert r.status_code == 200, r.text
    assert r.json()["module_library_dirs"] == [str(tmp_path)]
    assert settings_store.load()["module_library_dirs"] == [str(tmp_path)]
    # a payload that does not mention it leaves it alone
    assert client.post("/api/config", json={}).json()["module_library_dirs"] == [str(tmp_path)]
    r = client.post("/api/config", json={"module_library_dirs": [str(tmp_path / "nope")]})
    assert r.status_code == 422 and "not a directory" in r.json()["detail"]


# ---------------------------------------------------------------------------
# opening a workflow
# ---------------------------------------------------------------------------
def test_opening_a_workflow_asks_ca_with_the_configured_libraries(
        client, monkeypatch, workflow_file, library, tmp_path):
    calls = _fake_ca(monkeypatch)
    outputs = tmp_path / "outputs"
    r = client.post("/api/workflow/load",
                    json={"path": str(workflow_file), "config_outputs_dir": str(outputs)})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["loaded"] and body["workflow"]["workflow_name"] == "demo"
    assert body["output_dir"] == str(outputs / "workflows" / "demo")
    assert [s["id"] for s in body["plan"]["steps"]] == ["fit_a", "rest"]
    assert ("load", str(workflow_file), [str(library)]) in calls
    # nothing has run there yet, so there is no status to ask CA for
    assert "status" not in body
    assert client.get("/api/workflow").json()["path"] == str(workflow_file)


def test_a_workflow_ca_cannot_resolve_still_shows_what_it_says(
        client, monkeypatch, workflow_file):
    _fake_ca(monkeypatch, plan_error='no module lin_a version v1 in the module library')
    body = client.post("/api/workflow/load", json={"path": str(workflow_file)}).json()
    assert body["workflow"]["steps"][0]["id"] == "fit_a"
    assert "no module lin_a" in body["plan_error"] and "plan" not in body


def test_ca_refusals_reach_the_user_verbatim(client, monkeypatch, workflow_file):
    _fake_ca(monkeypatch, refuse='step "rest" depends_on unknown step "nope".')
    r = client.post("/api/workflow/load", json={"path": str(workflow_file)})
    assert r.status_code == 422
    assert r.json()["detail"] == 'step "rest" depends_on unknown step "nope".'


def test_an_older_ca_without_workflows_is_named(client, monkeypatch, workflow_file):
    set_ca_module(monkeypatch, "calibration_workflow", None)
    r = client.post("/api/workflow/load", json={"path": str(workflow_file)})
    assert r.status_code == 501
    assert "calibration_workflow" in r.json()["detail"]


def test_a_workflow_can_be_uploaded(client, monkeypatch):
    calls = _fake_ca(monkeypatch)
    r = client.post("/api/workflow/upload",
                    files={"file": ("calibration_workflow.json", json.dumps(WORKFLOW),
                                    "application/json")})
    assert r.status_code == 200, r.text
    path = r.json()["path"]
    assert path.startswith(str(main.UPLOAD_DIR)) and json.loads(Path(path).read_text()) == WORKFLOW
    assert calls[0][0] == "load"
    r = client.post("/api/workflow/upload", files={"file": ("x.json", "{nope", "application/json")})
    assert r.status_code == 422


def test_a_run_directory_reopens_its_workflow_and_results(
        client, monkeypatch, workflow_file, tmp_path):
    calls = _fake_ca(monkeypatch)
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "calibration_workflow.json").write_text(json.dumps(WORKFLOW))
    (run_dir / "workflow_result.json").write_text(json.dumps({"workflow_file": str(workflow_file)}))
    body = client.post("/api/workflow/load", json={"run_dir": str(run_dir)}).json()
    # the original file (its relative library dirs mean something there), this run's results
    assert body["path"] == str(workflow_file) and body["output_dir"] == str(run_dir)
    assert body["status"]["steps"]["fit_a"]["status"] == "done"
    assert ("status", str(workflow_file), str(run_dir)) in calls
    r = client.post("/api/workflow/load", json={"run_dir": str(tmp_path)})
    assert r.status_code == 422 and "not a calibration workflow run" in r.json()["detail"]
    found = client.get("/api/outputs/load", params={"dir": str(run_dir)}).json()
    assert found["workflow_run"] is True


def test_a_tab_loads_as_a_study(client, monkeypatch, workflow_file, tmp_path):
    calls = _fake_ca(monkeypatch)
    client.post("/api/workflow/load",
                json={"path": str(workflow_file), "config_outputs_dir": str(tmp_path)})
    r = client.post("/api/workflow/view", json={"view": "fit_a"})
    assert r.status_code == 200, r.text
    body = r.json()
    # the same response a dropped archive gives, named after the step
    assert body["model_id"] and body["model_filename"] == "fit_a.cellml"
    assert body["obs_data"] and not body["obs_data"].get("error")
    assert body["params_for_id"] and not body["params_for_id"].get("error")
    view = body["workflow_view"]
    assert view["view"] == "fit_a" and view["fixed"][0]["from_step"] == "fit_a"
    assert ("model", "fit_a", str(tmp_path / "workflows" / "demo")) in calls


# ---------------------------------------------------------------------------
# archives
# ---------------------------------------------------------------------------
def _archive(members):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, blob in members.items():
            zf.writestr(name, blob)
    return buf.getvalue()


def test_an_archive_s_workflow_is_offered_and_never_taken_for_obs_data(client):
    data = _archive({
        "model.cellml": LV_MODEL_PATH.read_bytes(),
        "calibration_workflow.json": json.dumps(WORKFLOW),
    })
    parts = omex_import.unpack(data)
    assert parts["calibration_workflow"][0] == "calibration_workflow.json"
    assert parts["obs"] is None
    r = client.post("/api/omex/upload", files={"file": ("s.omex", data, "application/zip")})
    assert r.status_code == 200, r.text
    offered = r.json()["calibration_workflow"]
    assert offered["filename"] == "calibration_workflow.json"
    assert json.loads(Path(offered["path"]).read_text()) == WORKFLOW
    # and with a real obs_data beside it, that one is the obs_data
    data = _archive({
        "model.cellml": LV_MODEL_PATH.read_bytes(),
        "soma_calibration_workflow.json": json.dumps(WORKFLOW),
        "rest_obs_data.json": LV_OBS_DATA_PATH.read_bytes(),
    })
    parts = omex_import.unpack(data)
    assert parts["obs"][0] == "rest_obs_data.json"
    assert parts["calibration_workflow"][0] == "soma_calibration_workflow.json"


# ---------------------------------------------------------------------------
# running (fake runner)
# ---------------------------------------------------------------------------
FAKE_RUNNER = r"""
import json, sys
from pathlib import Path
cfg = json.loads(Path(sys.argv[1]).read_text())
E = "__WORKFLOW_EVENT__"
def ev(**k): print(E + json.dumps(k), flush=True)
ev(event="workflow_started", order=["fit_a", "rest"], running=["fit_a", "rest"])
ev(event="step_started", step="fit_a", index=0, total=2)
print("calibrating fit_a ...", flush=True)
ev(event="step_finished", step="fit_a", index=0, total=2, best_cost=0.1, duration_s=1.0)
ev(event="step_started", step="rest", index=1, total=2)
if cfg.get("only") == "rest":
    ev(event="step_failed", step="rest", index=1, total=2, error="the data said no")
    print("__WORKFLOW_FAILED__ step rest: the data said no", flush=True)
    sys.exit(1)
ev(event="step_finished", step="rest", index=1, total=2, best_cost=0.2, duration_s=1.0)
ev(event="workflow_finished", complete=True)
import os
Path(cfg["output_dir"], "seen_config.json").write_text(json.dumps({**cfg, "cwd": os.getcwd()}))
print("__WORKFLOW_DONE__", flush=True)
"""

SLOW_RUNNER = r"""
import json, time
print("__WORKFLOW_EVENT__" + json.dumps({"event": "step_started", "step": "fit_a"}), flush=True)
time.sleep(30)
"""


def _install_runner(tmp_path, src):
    path = tmp_path / "fake_workflow_runner.py"
    path.write_text(src)
    workflow_mod.workflow.runner_path = str(path)


def _wait(client, job_id, timeout=15):
    deadline = time.time() + timeout
    while time.time() < deadline:
        s = client.get(f"/api/workflow/{job_id}/status").json()
        if s["state"] != "running":
            return s
        time.sleep(0.05)
    raise AssertionError("the workflow did not finish")


@pytest.fixture
def opened(client, monkeypatch, workflow_file, tmp_path, library):
    _fake_ca(monkeypatch)
    body = client.post("/api/workflow/load",
                       json={"path": str(workflow_file),
                             "config_outputs_dir": str(tmp_path / "outputs")}).json()
    return body


def test_a_run_reports_each_step(client, opened, tmp_path, library):
    _install_runner(tmp_path, FAKE_RUNNER)
    r = client.post("/api/workflow/run", json={"from_step": "fit_a"})
    assert r.status_code == 200, r.text
    status = _wait(client, r.json()["job_id"])
    assert status["state"] == "done", status
    assert status["step_states"] == {"fit_a": "done", "rest": "done"}
    assert [e["event"] for e in status["events"]][-1] == "workflow_finished"
    seen = json.loads(Path(opened["output_dir"], "seen_config.json").read_text())
    assert seen["workflow"] == opened["path"] and seen["from_step"] == "fit_a"
    assert seen["module_library_dirs"] == [str(library)]
    # never the server's current directory, which a live Myokit compile moves into a
    # temporary build directory and then deletes
    assert seen["cwd"] == opened["output_dir"]
    # the runner's config went to a temp dir, not the run directory
    assert sorted(os.listdir(opened["output_dir"])) == ["seen_config.json"]


def test_a_failed_step_is_reported(client, opened, tmp_path):
    _install_runner(tmp_path, FAKE_RUNNER)
    job = client.post("/api/workflow/run", json={"only": "rest"}).json()["job_id"]
    status = _wait(client, job)
    assert status["state"] == "error"
    assert status["step_states"]["rest"] == "failed"
    assert "the data said no" in status["error"]


def test_one_run_at_a_time_and_it_can_be_cancelled(client, opened, tmp_path):
    _install_runner(tmp_path, SLOW_RUNNER)
    job = client.post("/api/workflow/run", json={}).json()["job_id"]
    assert client.post("/api/workflow/run", json={}).status_code == 409
    assert client.post("/api/workflow/close").status_code == 409
    assert client.post(f"/api/workflow/{job}/cancel").json() == {"cancelled": True}
    assert _wait(client, job)["state"] == "cancelled"
    assert client.post("/api/workflow/run", json={"from_step": "a", "only": "b"}).status_code == 422


def test_running_needs_an_open_workflow(client):
    r = client.post("/api/workflow/run", json={})
    assert r.status_code == 422 and "no workflow is open" in r.json()["detail"]
    assert client.get("/api/workflow").json() == {"loaded": False}


# ---------------------------------------------------------------------------
# integration: CA's own fixture library, for real
# ---------------------------------------------------------------------------
@pytest.mark.integration
@pytest.mark.slow
def test_a_real_workflow_runs_and_its_tabs_show_the_fixed_values(
        client, requires_simulation, tmp_path):
    try:
        from ca_imports import ca_from

        build = ca_from("external_testing.workflow_library", "build_workflow_library")
        ca_from("calibration_workflow", "run_calibration_workflow")
    except ImportError:
        if os.environ.get("CUFLYNX_REQUIRE_CA_RUN"):
            raise
        pytest.skip("this circulatory_autogen has no calibration workflows (CA #541)")
    paths = build(str(tmp_path / "library"))
    assert client.post("/api/config", json={"module_library_dirs": [paths["modules"]],
                                            "python_path": sys.executable}).status_code == 200
    body = client.post("/api/workflow/load",
                       json={"path": paths["chain_rest"],
                             "config_outputs_dir": str(tmp_path / "outputs")}).json()
    assert [s["id"] for s in body["plan"]["steps"]] == ["fit_a", "rest"]

    job = client.post("/api/workflow/run", json={}).json()["job_id"]
    status = _wait(client, job, timeout=300)
    assert status["state"] == "done", "\n".join(status["lines"][-40:])
    assert status["step_states"] == {"fit_a": "done", "rest": "done"}

    described = client.get("/api/workflow").json()
    assert described["status"]["complete"]

    r = client.post("/api/workflow/view", json={"view": "rest"})
    assert r.status_code == 200, r.text
    tab = r.json()
    fixed = tab["workflow_view"]["fixed"]
    assert [(f["model_name"], f["from_step"]) for f in fixed] == [("p_mod_A", "fit_a")]
    assert fixed[0]["value"] == pytest.approx(2.0, rel=1e-4)
    assert tab["obs_data"] and not tab["obs_data"].get("error")
    target = client.post("/api/workflow/view", json={"view": "target"}).json()
    merged = {c["model_name"]: c["value"] for c in target["workflow_view"]["calibrated"]}
    assert merged["p_mod_A"] == pytest.approx(2.0, rel=1e-4)
    assert merged["q_mod_C"] == pytest.approx(3.0, rel=1e-4)


def test_the_configured_ca_dir_is_put_on_the_path_before_ca_is_asked(monkeypatch):
    """Found running CUFLynx against a CA worktree from a venv with an editable
    libcuflynx: ca_from imports whatever is importable, so without this the editable
    install answered, the configured CA dir was never consulted, and a CA that has
    workflows was reported as one that does not."""
    order = []
    monkeypatch.setattr(workflow_mod, "ensure_ca_path", lambda: order.append("path"))
    monkeypatch.setattr(workflow_mod, "ca_from",
                        lambda module, name: order.append(("import", module, name)) or object())
    workflow_mod.ca_workflow("load_workflow")
    assert order == ["path", ("import", "calibration_workflow", "load_workflow")]
