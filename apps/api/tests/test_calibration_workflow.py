"""Calibration workflows in the GUI (calibration_workflow.py + the /api/workflow routes).

Everything about a workflow is CA's (libcuflynx.calibration_workflow); CUFLynx keeps
which one is open, runs it as a subprocess and loads its tabs as studies. The unit tier
injects a fake CA module and a fake runner; the integration tier runs CA's own fixture
library (libcuflynx.external_testing.workflow_library) for real.
"""

import base64
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
from conftest import (LV_MODEL_PATH, LV_OBS_DATA_PATH, LV_PARAMS_CSV_PATH, ca_model_name,
                      set_ca_module)

WORKFLOW = {
    "workflow_name": "demo",
    "target": {"module_type": "twin", "version": "v1", "parameterisation": "split"},
    "steps": [
        {"id": "fit_a", "model": {"module_type": "lin_a", "version": "v1", "parameterisation": "fit_a"},
         "obs_data": "fit_a_obs_data.json", "params_for_id": "fit_a_params_for_id.json"},
        {"id": "rest", "model": "chain_module_array.csv",
         "obs_data": "rest_obs_data.json", "params_for_id": "rest_params_for_id.json",
         "fixed_from": ["fit_a"]},
    ],
}


class UnsetParametersError(ValueError):
    """Shaped as CA's (libcuflynx.calibration_workflow.UnsetParametersError)."""

    def __init__(self, step_id, names):
        self.step_id, self.names = step_id, list(names)
        super().__init__(f'step "{step_id}": {len(names)} parameter(s) have no value: '
                         f'{", ".join(names)}.')


def _fake_ca(monkeypatch, calls=None, plan_error=None, refuse=None, model_error=None,
             incomplete=None):
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
        return {"workflow_name": "demo", "steps": [{"id": "fit_a"}, {"id": "rest"}],
                "target": json.loads(Path(path).read_text())["target"]}

    def workflow_status(path, output_dir, module_library_dirs=None):
        calls.append(("status", path, output_dir))
        return {"steps": {"fit_a": {"status": "done", "stale": False},
                          "rest": {"status": "not_run", "stale": False}}, "complete": False}

    def workflow_model(path, view, output_dir, work_dir, module_library_dirs=None):
        calls.append(("model", view, output_dir))
        if model_error:
            raise model_error
        return {"view": view, "kind": "step", "model": WORKFLOW["steps"][0]["model"],
                "step_id": view, "place": "A",
                "model_path": str(LV_MODEL_PATH), "obs_data_path": str(LV_OBS_DATA_PATH),
                "params_for_id_path": str(LV_PARAMS_CSV_PATH),
                "fixed": [{"model_name": "p_mod_A", "value": 2.0, "from_step": "fit_a"}],
                "calibrated": [], "waiting_for": [], "stale": [], "param_id_output_dir": None}

    def pack_workflow(path, dest_dir, module_library_dirs=None):
        os.makedirs(dest_dir, exist_ok=True)
        Path(dest_dir, "calibration_workflow.json").write_text(Path(path).read_text())
        return {"files": ["calibration_workflow.json"]}

    def write_calibrated_parameters_to(path, output_dir, dest, module_library_dirs=None):
        calls.append(("calibrated", output_dir, dest))
        if incomplete:
            raise FakeWorkflowError(incomplete)
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        # nested JSON, as CA writes it (utilities/parameters_file.py)
        Path(dest).write_text(json.dumps({"schema_version": 1, "global": {
            "R": {"value": 8.314, "units": "J_per_mol_K", "data_reference": "given"}},
            "C": {"x": {"value": 2.5, "units": "m", "data_reference": "calibrated"}},
            "heart": {"lv": {"E": {"value": "3e8", "units": "Pa", "data_reference": "d"}}}}))
        return dest

    for fn in (load_workflow, plan_workflow, workflow_status, workflow_model, pack_workflow,
               write_calibrated_parameters_to):
        setattr(mod, fn.__name__, fn)
    set_ca_module(monkeypatch, "calibration_workflow", mod)
    return calls


@pytest.fixture
def workflow_file(tmp_path):
    path = tmp_path / "study" / "workflow.json"
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
    _fake_ca(monkeypatch, refuse='step "rest" takes values from unknown step "nope".')
    r = client.post("/api/workflow/load", json={"path": str(workflow_file)})
    assert r.status_code == 422
    assert r.json()["detail"] == 'step "rest" takes values from unknown step "nope".'


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


def test_a_tab_with_unset_parameters_names_the_step_and_them(
        client, monkeypatch, workflow_file, tmp_path):
    _fake_ca(monkeypatch, model_error=UnsetParametersError("sel", ["C/x", "C/y"]))
    client.post("/api/workflow/load",
                json={"path": str(workflow_file), "config_outputs_dir": str(tmp_path)})
    r = client.post("/api/workflow/view", json={"view": "sel"})
    assert r.status_code == 422, r.text
    detail = r.json()["detail"]
    # CA's message verbatim, and what the protocol editor opens on
    assert detail["message"].startswith('step "sel": 2 parameter(s) have no value')
    assert detail["unset_parameters"] == {"step_id": "sel", "names": ["C/x", "C/y"]}


def test_any_other_refusal_stays_a_plain_message(client, monkeypatch, workflow_file, tmp_path):
    _fake_ca(monkeypatch, model_error=ValueError("no such step"))
    client.post("/api/workflow/load",
                json={"path": str(workflow_file), "config_outputs_dir": str(tmp_path)})
    r = client.post("/api/workflow/view", json={"view": "sel"})
    assert r.status_code == 422 and r.json()["detail"] == "no such step"


# ---------------------------------------------------------------------------
# a step's inputs
# ---------------------------------------------------------------------------
STEP_WORKFLOW = {
    "workflow_name": "sel",
    "target": {"module_array": "whole_module_array.json", "parameters": "whole_parameters.json"},
    "steps": [
        {"id": "left", "model": {"select": ["C"]}, "obs_data": "inputs/left_obs_data.json",
         "params_for_id": "left_params_for_id.json"},
        {"id": "right", "model": {"select": ["D", "E"]}},
        {"id": "rank", "kind": "reduction", "sensitivity_from": "left"},
        {"id": "kept", "model": {"select": ["D"]}, "params_for_id_from": "rank"},
    ],
}


@pytest.fixture
def step_workflow(client, monkeypatch, tmp_path):
    _fake_ca(monkeypatch)
    directory = tmp_path / "wf"
    (directory / "inputs").mkdir(parents=True)
    path = directory / "calibration_workflow.json"
    path.write_text(json.dumps({**STEP_WORKFLOW, "workflow_name": "demo"}))
    (directory / "inputs" / "left_obs_data.json").write_text('{"protocol_info": {}}')
    r = client.post("/api/workflow/load",
                    json={"path": str(path), "config_outputs_dir": str(tmp_path / "out")})
    assert r.status_code == 200, r.text
    return path


def test_a_steps_input_path_uses_this_os_separators(monkeypatch, tmp_path):
    """A workflow file names its inputs with ``/``; the path handed back is normalised
    (Windows' normpath stands in here, turning ``/`` into ``\\``)."""
    import workflow_manager

    manager = workflow_manager.WorkflowManager()
    manager.path = str(tmp_path / "wf" / "calibration_workflow.json")
    monkeypatch.setattr(os.path, "normpath", lambda p: p.replace("/", "\\"))
    assert "/" not in manager._resolve("inputs/left_obs_data.json")


def test_a_step_s_own_obs_data_is_read_and_overwritten(client, step_workflow):
    r = client.get("/api/workflow/step_input", params={"step_id": "left", "role": "obs_data"})
    assert r.status_code == 200, r.text
    own = step_workflow.parent / "inputs" / "left_obs_data.json"
    assert r.json() == {"path": str(own), "content": '{"protocol_info": {}}'}
    edited = json.dumps({"protocol_info": {"params_to_change": {"C/x": [[1.0]]}}})
    r = client.post("/api/workflow/step_input",
                    json={"step_id": "left", "role": "obs_data", "content": edited})
    assert r.status_code == 200, r.text
    assert r.json()["path"] == str(own) and own.read_text() == edited
    # re-planned: the workflow is described again
    assert r.json()["workflow"]["loaded"] is True
    # the workflow file is untouched: it already named the file
    assert json.loads(step_workflow.read_text())["steps"][0]["obs_data"] == "inputs/left_obs_data.json"


def test_a_step_edit_of_a_workflow_stored_in_the_working_dir_is_a_new_version(
        client, monkeypatch, tmp_path):
    """An uploaded workflow is a version in inputs/workflows/<wf>/<stamp>/; editing a step's
    obs_data stores the edited folder as a new version and leaves the first as it was."""
    _fake_ca(monkeypatch)
    work = tmp_path / "W"
    r = client.post("/api/workflow/upload", params={"config_outputs_dir": str(work)},
                    files={"file": ("demo.json", json.dumps({**STEP_WORKFLOW,
                                                             "workflow_name": "demo"}),
                                    "application/json")})
    assert r.status_code == 200, r.text
    first = Path(r.json()["path"])
    assert first.parent.parent == work / "inputs" / "workflows" / "demo"
    before = sorted(p.relative_to(first.parent) for p in first.parent.rglob("*"))

    edited = json.dumps({"protocol_info": {"params_to_change": {"C/x": [[1.0]]}}})
    r = client.post("/api/workflow/step_input",
                    json={"step_id": "left", "role": "obs_data", "content": edited})
    assert r.status_code == 200, r.text
    written = Path(r.json()["path"])
    assert written.read_text() == edited
    second = Path(r.json()["workflow"]["path"])
    assert second.parent != first.parent and second.parent.parent == first.parent.parent
    assert written == second.parent / "inputs" / "left_obs_data.json"
    assert sorted(p.relative_to(first.parent) for p in first.parent.rglob("*")) == before
    assert not (first.parent / "inputs" / "left_obs_data.json").exists()


def test_a_step_without_an_input_is_given_one_beside_the_workflow(client, step_workflow):
    r = client.get("/api/workflow/step_input", params={"step_id": "right", "role": "obs_data"})
    assert r.json() == {"path": None, "content": None}
    (step_workflow.parent / "right_params_for_id.json").write_text("someone else's\n")
    r = client.post("/api/workflow/step_input",
                    json={"step_id": "right", "role": "params_for_id", "content": PARAMS_DOC})
    assert r.status_code == 200, r.text
    # a file already there is never taken over
    assert Path(r.json()["path"]).name == "right_params_for_id_2.json"
    assert (step_workflow.parent / "right_params_for_id.json").read_text() == "someone else's\n"
    assert Path(r.json()["path"]).read_text() == PARAMS_DOC
    steps = json.loads(step_workflow.read_text())["steps"]
    assert steps[1]["params_for_id"] == "right_params_for_id_2.json"
    assert steps[0] == STEP_WORKFLOW["steps"][0]


PARAMS_DOC = json.dumps({"version": 1, "params": [{"targets": ["D/x"], "min": 1, "max": 2}]})
PARAMS_CSV = "vessel_name,param_name,min,max\nheart__lv D,x,1,2.5e-3\n"


def test_a_params_for_id_csv_is_written_as_the_json_ca_reads(client, step_workflow):
    """CA refuses a params_for_id CSV, so a picked CSV is written as
    ``cuflynx-upgrade params-for-id`` converts it."""
    r = client.post("/api/workflow/step_input",
                    json={"step_id": "right", "role": "params_for_id", "content": PARAMS_CSV})
    assert r.status_code == 200, r.text
    path = Path(r.json()["path"])
    assert path.name == "right_params_for_id.json"
    assert json.loads(path.read_text()) == {"version": 1, "params": [
        {"targets": ["heart/lv/x", "D/x"], "min": 1, "max": 2.5e-3}]}
    assert json.loads(step_workflow.read_text())["steps"][1]["params_for_id"] == (
        "right_params_for_id.json")


def test_a_step_naming_a_params_for_id_csv_is_pointed_at_its_json(client, step_workflow):
    raw = json.loads(step_workflow.read_text())
    raw["steps"][0]["params_for_id"] = "inputs/left_params_for_id.csv"
    step_workflow.write_text(json.dumps(raw))
    old = step_workflow.parent / "inputs" / "left_params_for_id.csv"
    old.write_text(PARAMS_CSV)
    r = client.post("/api/workflow/step_input",
                    json={"step_id": "left", "role": "params_for_id", "content": PARAMS_CSV})
    assert r.status_code == 200, r.text
    assert Path(r.json()["path"]) == step_workflow.parent / "inputs" / "left_params_for_id.json"
    assert json.loads(step_workflow.read_text())["steps"][0]["params_for_id"] == (
        "inputs/left_params_for_id.json")
    assert old.read_text() == PARAMS_CSV   # the user's CSV is left as it was


@pytest.mark.parametrize("step_id, role, content, words", [
    ("rank", "obs_data", "{}", "is a reduction"),
    ("kept", "params_for_id", "a\n", 'fits what step "rank" kept'),
    ("right", "params_for_id", "a,b\n", "neither JSON nor a params_for_id CSV"),
    ("right", "params_for_id", "{not json", "neither JSON nor a params_for_id CSV"),
    ("nope", "obs_data", "{}", 'no step "nope"'),
    ("left", "parameters", "{}", "obs_data or params_for_id"),
    ("left", "obs_data", "{not json", "not JSON"),
])
def test_inputs_a_step_cannot_take_are_refused(client, step_workflow, step_id, role, content,
                                               words):
    before = step_workflow.read_text()
    r = client.post("/api/workflow/step_input",
                    json={"step_id": step_id, "role": role, "content": content})
    assert r.status_code == 422 and words in r.json()["detail"], r.text
    assert step_workflow.read_text() == before


def _send_from_a_step_tab(client, step_workflow):
    view = client.post("/api/workflow/view", json={"view": "left"})
    assert view.status_code == 200, view.text
    r = client.post("/api/phlynx/send",
                    json={"model_id": view.json()["model_id"], "source": "as_imported"})
    assert r.status_code == 200, r.text
    return r.json()


def test_a_completed_workflow_sends_its_target_back_with_the_merged_values(
        client, step_workflow):
    (step_workflow.parent / "whole_module_array.json").write_text('[{"name": "C"}]')
    body = _send_from_a_step_tab(client, step_workflow)
    assert body["workflow_values"] == {"sent": ["whole_module_array.json",
                                                "whole_parameters.csv"]}
    members = _members(body)
    assert members["whole_module_array.json"] == b'[{"name": "C"}]'
    # CA's nested JSON as the CSV PhLynx reads: <instance>/<variable>, a global by its name
    assert members["whole_parameters.csv"].decode() == (
        "variable_name,units,value,data_reference\n"
        "R,J_per_mol_K,8.314,given\n"
        "C/x,m,2.5,calibrated\n"
        "heart/lv/E,Pa,3e8,d\n")
    assert "whole_parameters.json" not in members


def test_an_incomplete_workflow_says_why_it_sent_no_values(client, monkeypatch, step_workflow):
    _fake_ca(monkeypatch, incomplete="steps ['right'] have no current result")
    body = _send_from_a_step_tab(client, step_workflow)
    assert body["workflow_values"] == {"sent": [],
                                       "reason": "steps ['right'] have no current result"}
    assert "whole_parameters.csv" not in _members(body)


def _members(send_body):
    import base64
    with zipfile.ZipFile(io.BytesIO(base64.b64decode(send_body["base64"]))) as zf:
        return {n: zf.read(n) for n in zf.namelist()}


# ---------------------------------------------------------------------------
# archives
# ---------------------------------------------------------------------------
def test_an_archive_with_selection_steps_names_them(client):
    data = _archive({
        "model.cellml": LV_MODEL_PATH.read_bytes(),
        "calibration_workflow.json": json.dumps(STEP_WORKFLOW),
    })
    r = client.post("/api/omex/upload", files={"file": ("s.omex", data, "application/zip")})
    assert r.status_code == 200, r.text
    assert r.json()["calibration_workflow"]["select_steps"] == ["left", "right", "kept"]
    data = _archive({"model.cellml": LV_MODEL_PATH.read_bytes(),
                     "calibration_workflow.json": json.dumps(WORKFLOW)})
    r = client.post("/api/omex/upload", files={"file": ("s.omex", data, "application/zip")})
    assert r.json()["calibration_workflow"]["select_steps"] == []

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


UNSET_RUNNER = r"""
import json
E = "__WORKFLOW_EVENT__"
print(E + json.dumps({"event": "step_started", "step": "fit_a"}), flush=True)
print(E + json.dumps({"event": "step_failed", "step": "fit_a", "error": "no value"}), flush=True)
print(E + json.dumps({"event": "unset_parameters", "step": "fit_a", "names": ["C/x"]}), flush=True)
print("__WORKFLOW_FAILED__ step fit_a: no value", flush=True)
raise SystemExit(1)
"""


def test_a_run_stopped_by_unset_parameters_names_them(client, opened, tmp_path):
    _install_runner(tmp_path, UNSET_RUNNER)
    status = _wait(client, client.post("/api/workflow/run", json={}).json()["job_id"])
    assert status["state"] == "error" and status["step_states"]["fit_a"] == "failed"
    assert status["unset_parameters"] == {"step_id": "fit_a", "names": ["C/x"]}
    assert client.get("/api/workflow").json()["job"]["unset_parameters"] == {
        "step_id": "fit_a", "names": ["C/x"]}


def test_the_runner_reports_ca_s_unset_parameters(monkeypatch, tmp_path, capsys):
    import workflow_runner

    def refuse(config):
        raise UnsetParametersError("left", ["C/x"])

    monkeypatch.setattr(workflow_runner, "run", refuse)
    config = tmp_path / "config.json"
    config.write_text("{}")
    assert workflow_runner.main(["runner", str(config)]) == 1
    lines = capsys.readouterr().out.splitlines()
    event = next(json.loads(ln[len(workflow_runner.EVENT_MARKER):]) for ln in lines
                 if ln.startswith(workflow_runner.EVENT_MARKER))
    assert event == {"event": "unset_parameters", "step": "left", "names": ["C/x"]}
    assert any(ln.startswith(workflow_runner.FAIL_MARKER) for ln in lines)
    # any other error is only the failure line
    assert workflow_runner.unset_parameters(ValueError("x")) is None


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
    p_a, q_c = ca_model_name("mod/A/p"), ca_model_name("mod/C/q")
    fixed = tab["workflow_view"]["fixed"]
    assert [(f["model_name"], f["from_step"]) for f in fixed] == [(p_a, "fit_a")]
    assert fixed[0]["value"] == pytest.approx(2.0, rel=1e-4)
    assert tab["obs_data"] and not tab["obs_data"].get("error")
    assert tab["workflow_view"]["model"]["module_type"] == "chain"
    target = client.post("/api/workflow/view", json={"view": "target"}).json()
    merged = {c["model_name"]: c["value"] for c in target["workflow_view"]["calibrated"]}
    assert merged[p_a] == pytest.approx(2.0, rel=1e-4)
    assert merged[q_c] == pytest.approx(3.0, rel=1e-4)


@pytest.mark.integration
@pytest.mark.slow
def test_a_workflow_of_module_arrays_runs_from_its_own_files(
        client, requires_simulation, tmp_path):
    """A workflow outside the module library's layout: each step names a module array,
    an obs_data and a params_for_id, relative to the workflow file."""
    try:
        from ca_imports import ca_from

        build = ca_from("external_testing.workflow_library", "build_workflow_library")
        ca_from("calibration_workflow", "run_calibration_workflow")
    except ImportError:
        if os.environ.get("CUFLYNX_REQUIRE_CA_RUN"):
            raise
        pytest.skip("this circulatory_autogen has no calibration workflows (CA #541)")
    paths = build(str(tmp_path / "library"))
    if "chain_array" not in paths:
        if os.environ.get("CUFLYNX_REQUIRE_CA_RUN"):
            raise AssertionError("this circulatory_autogen predates module-array workflows")
        pytest.skip("this circulatory_autogen predates module-array workflows (CA #555)")
    assert client.post("/api/config", json={"python_path": sys.executable}).status_code == 200
    body = client.post("/api/workflow/load",
                       json={"path": paths["chain_array"],
                             "config_outputs_dir": str(tmp_path / "outputs")}).json()
    steps = {s["id"]: s for s in body["plan"]["steps"]}
    assert steps["a-alone"]["model"]["module_array"].endswith("a_only_module_array.json")
    assert steps["chain"]["fixed_from"] == [
        {"from_step": "a-alone", "model_name": "p_A", "in_step": "p_A"}]

    job = client.post("/api/workflow/run", json={}).json()["job_id"]
    status = _wait(client, job, timeout=300)
    assert status["state"] == "done", "\n".join(status["lines"][-40:])

    tab = client.post("/api/workflow/view", json={"view": "chain"}).json()
    fixed = tab["workflow_view"]["fixed"]
    assert [(f["model_name"], f["from_step"]) for f in fixed] == [("p_A", "a-alone")]
    assert tab["workflow_view"]["obs_data_path"].endswith("chain_obs.json")
    target = client.post("/api/workflow/view", json={"view": "target"}).json()
    merged = {c["model_name"]: c["value"] for c in target["workflow_view"]["calibrated"]}
    assert merged["p_A"] == pytest.approx(2.0, rel=1e-4)
    assert merged["q_C"] == pytest.approx(3.0, rel=1e-4)


def _selection_ca():
    """CA #594's fixture builder and constants, or a skip on a CA without selection steps."""
    try:
        from ca_imports import ca_from, ensure_ca_path

        ensure_ca_path()  # the configured CA dir first, as the workflow manager does
        build = ca_from("external_testing.workflow_library", "build_workflow_library")
        ca_from("calibration_workflow", "write_calibrated_parameters_to")
        constants = {name: ca_from("external_testing.workflow_library", name)
                     for name in ("OPTIMISER_SETTINGS", "Y_A", "Y_B", "Z_C", "STD_A", "STD_B")}
    except ImportError:
        if os.environ.get("CUFLYNX_REQUIRE_CA_RUN"):
            raise
        pytest.skip("this circulatory_autogen has no selection steps (CA #594)")
    return build, constants


def _selection_study(client, tmp_path, *, target, step_id, select, items, params,
                     params_to_change=None, setup=None):
    """CA's fixture library with one selection step ``step_id`` on ``target`` (a module array
    in its ``arrays`` directory), loaded in the GUI. ``items`` are ``(name, operand, value,
    std)`` data items, ``params`` ``(vessel, param, min, max)`` entries; ``setup(arrays)`` writes
    any target the library lacks. Returns ``(arrays directory, load response)``."""
    build, constants = _selection_ca()
    paths = build(str(tmp_path / "library"))
    arrays = Path(paths["arrays"])
    (arrays / "data").mkdir(exist_ok=True)
    if setup:
        setup(arrays)
    obs = {"protocol_info": {"pre_times": [0.0], "sim_times": [[1.0]],
                             "params_to_change": params_to_change or {}},
           "data_items": [{"data_item_name": name, "data_type": "constant",
                           "unit": "dimensionless", "operands": [operand], "operation": "mean",
                           "weight": 1.0, "value": value, "std": std}
                          for name, operand, value, std in items],
           "prediction_items": []}
    (arrays / "data" / "step_obs.json").write_text(json.dumps(obs))
    (arrays / "data" / "step_params.json").write_text(json.dumps({"version": 1, "params": [
        {"targets": ["/".join(v.split("__") + [p])], "min": lo, "max": hi,
         "name_for_plotting": f"{p}_{v}"} for v, p, lo, hi in params]}))
    workflow_path = arrays / "selection_workflow.json"
    workflow_path.write_text(json.dumps({
        "schema_version": 1, "workflow_name": "selection",
        "module_library_dirs": [paths["modules"]], "settings": constants["OPTIMISER_SETTINGS"],
        "target": target,
        "steps": [{"id": step_id, "model": {"select": select},
                   "obs_data": "data/step_obs.json", "params_for_id": "data/step_params.json"}]}))
    assert client.post("/api/config", json={"python_path": sys.executable}).status_code == 200
    r = client.post("/api/workflow/load", json={
        "path": str(workflow_path), "config_outputs_dir": str(tmp_path / "outputs")})
    assert r.status_code == 200, r.text
    return arrays, r.json()


def _calibrate(client):
    job = client.post("/api/workflow/run", json={}).json()["job_id"]
    status = _wait(client, job, timeout=300)
    assert status["state"] == "done", "\n".join(status["lines"][-40:])


def _send_back(client):
    """What a send of the target tab to PhLynx carries back: ``(file names, {name: bytes})``."""
    target = client.post("/api/workflow/view", json={"view": "target"})
    assert target.status_code == 200, target.text
    sent = client.post("/api/phlynx/send", json={"model_id": target.json()["model_id"],
                                                 "source": "as_imported"})
    assert sent.status_code == 200, sent.text
    names = sent.json()["workflow_values"]["sent"]
    with zipfile.ZipFile(io.BytesIO(base64.b64decode(sent.json()["base64"]))) as zf:
        return names, {name: zf.read(name) for name in names}


def _parameter_rows(text):
    import csv

    return {r["variable_name"]: r for r in csv.DictReader(io.StringIO(text))}


def _target_rows(arrays, prefix):
    """The target's own ``<prefix>_parameters.json`` as rows named as the send-back's CSV
    names them (CA's ``parameter_rows``)."""
    from ca_imports import ca_from

    read, rows = ca_from("utilities.parameters_file", "read_parameters_file", "parameter_rows")
    return {r["variable_name"]: r for r in rows(read(str(arrays / f"{prefix}_parameters.json")))}


def _start(arrays, prefix):
    return {name: float(r["value"]) for name, r in _target_rows(arrays, prefix).items()}


def _values(text):
    return {name: float(r["value"]) for name, r in _parameter_rows(text).items()}


def _changed_values(arrays, files):
    """``{variable_name: (before, after)}`` for each parameter the send-back changes against
    the target's own files beside it. The module array must be byte-identical. The
    parameters CSV is every value the target's model uses (CA writes the generated
    ``<prefix>_parameters.json`` with the merged values), so it names every parameter of
    the target's own file and may add the module defaults the model takes; those are never
    marked calibrated. A changed parameter changes only its value and data_reference."""
    changed = {}
    for name, blob in files.items():
        if not name.endswith("_parameters.csv"):
            assert blob == (arrays / name).read_bytes(), f"{name} is sent back changed"
            continue
        old = _target_rows(arrays, name[:-len("_parameters.csv")])
        new = _parameter_rows(blob.decode())
        missing = [key for key in old if key not in new]
        assert not missing, f"{name} leaves out the target's {missing}"
        for key, row in new.items():
            if key not in old:
                assert not row["data_reference"].startswith("calibrated by workflow "), (
                    f"{name} marks {key} calibrated, which the target does not set")
                continue
            if row == old[key]:
                continue
            assert {k: v for k, v in row.items() if k not in ("value", "data_reference")} == {
                k: v for k, v in old[key].items() if k not in ("value", "data_reference")}
            assert row["data_reference"].startswith("calibrated by workflow ")
            changed[key] = (float(old[key]["value"]), float(row["value"]))
    return changed


SEND_BACK_FIXTURE = Path(__file__).parent / "fixtures" / "phlynx_send_back_parameters.csv"


def _fixture_form(text):
    """The send-back CSV as the committed fixture keeps it: the calibration date written as
    ``YYYY-MM-DD`` and a fitted value to 6 significant figures, so a rerun reproduces it."""
    import csv
    import re

    rows = list(csv.DictReader(io.StringIO(text)))
    out = io.StringIO()
    writer = csv.DictWriter(out, fieldnames=list(rows[0]), lineterminator="\n")
    writer.writeheader()
    for row in rows:
        short = "%.6g" % float(row["value"])
        row["value"] = row["value"] if float(short) == float(row["value"]) else short
        row["data_reference"] = re.sub(r"\(\d{4}-\d{2}-\d{2}\)", "(YYYY-MM-DD)",
                                       row["data_reference"])
        writer.writerow(row)
    return out.getvalue()


@pytest.mark.integration
@pytest.mark.slow
def test_a_phlynx_selection_asks_for_its_boundary_runs_and_goes_back_calibrated(
        client, requires_simulation, tmp_path):
    """CA #594's fixture: in the chain, C (z = x + k q^2) reads x from A through a boundary.
    C selected alone leaves x unset; given x = Y_A by a protocol, q = sqrt(Z_C - Y_A) = 3.

    The target's parameters are nested JSON (``chain_parameters.json``); what goes back is the
    CSV PhLynx reads, converted from CA's JSON send-back. That CSV is PhLynx's contract fixture
    (``fixtures/phlynx_send_back_parameters.csv``, read by PhLynx's own tests): this test fails
    when it changes, and ``CUFLYNX_REGENERATE_FIXTURES=1`` rewrites it."""
    _, constants = _selection_ca()
    arrays, body = _selection_study(
        client, tmp_path, target="chain_module_array.json", step_id="c-alone", select=["C"],
        items=[("z_c", "C/z", constants["Z_C"], 0.17)], params=[("C", "q", 0.1, 10.0)])
    assert body["plan"]["steps"][0]["selection"]["runs_as"] == "library_module"
    start = _start(arrays, "chain")
    assert start["C/q"] != pytest.approx(3.0, rel=1e-2)   # so the send-back shows a change

    # the boundary C reads from A is open: CA names it, as the target's parameters do
    r = client.post("/api/workflow/view", json={"view": "c-alone"})
    assert r.status_code == 422, r.text
    assert r.json()["detail"]["unset_parameters"] == {"step_id": "c-alone", "names": ["C/x"]}

    # the protocol editor's save: the step's own obs_data, with x set
    own = client.get("/api/workflow/step_input",
                     params={"step_id": "c-alone", "role": "obs_data"}).json()
    document = json.loads(own["content"])
    document["protocol_info"]["params_to_change"]["C/x"] = [[constants["Y_A"]]]
    r = client.post("/api/workflow/step_input",
                    json={"step_id": "c-alone", "role": "obs_data", "content": json.dumps(document)})
    assert r.status_code == 200, r.text
    assert client.post("/api/workflow/view", json={"view": "c-alone"}).status_code == 200

    _calibrate(client)
    names, files = _send_back(client)
    assert names == ["chain_module_array.json", "chain_parameters.csv"]
    changed = _changed_values(arrays, files)
    assert list(changed) == ["C/q"]
    assert changed["C/q"] == (start["C/q"], pytest.approx(3.0, rel=1e-4))
    # nothing written beside the target, and its own file unchanged
    assert not list(arrays.glob("*_calibrated_parameters.*"))
    assert _start(arrays, "chain") == start

    sent = _fixture_form(files["chain_parameters.csv"].decode())
    if os.environ.get("CUFLYNX_REGENERATE_FIXTURES"):
        SEND_BACK_FIXTURE.parent.mkdir(exist_ok=True)
        SEND_BACK_FIXTURE.write_text(sent)
    assert SEND_BACK_FIXTURE.read_text() == sent, (
        "the CSV sent back to PhLynx changed; PhLynx's tests read the committed copy. "
        "Rerun with CUFLYNX_REGENERATE_FIXTURES=1 and update PhLynx with it.")


def _abc_target(arrays):
    """CA's test_several_instances_run_as_the_array_they_form target: A feeds C, B alone."""
    def record(name, module_type, inp=(), out=()):
        return {"name": name, "module_type": module_type, "module_subtype": "v1",
                "parameterisation": "default", "inp_instances": list(inp),
                "out_instances": list(out)}

    (arrays / "abc_module_array.json").write_text(json.dumps(
        [record("A", "lin_a", out=["C"]), record("C", "lin_c", inp=["A"]),
         record("B", "lin_b")], indent=1))
    leaf = {"units": "dimensionless", "data_reference": "x"}
    (arrays / "abc_parameters.json").write_text(json.dumps({
        "schema_version": 1, "A": {"p": {"value": 1.5, **leaf}},
        "B": {"p": {"value": 2.5, **leaf}}, "C": {"q": {"value": 1.0, **leaf}}}))


@pytest.mark.integration
@pytest.mark.slow
def test_several_selected_instances_go_back_calibrated_and_nothing_else(
        client, requires_simulation, tmp_path):
    """A and B selected from A -> C, B: calibrated as the array they form, y = 2 p^2 for A
    and y = 3 p^2 for B, so p_A = sqrt(Y_A / 2) and p_B = sqrt(Y_B / 3)."""
    _, constants = _selection_ca()
    arrays, body = _selection_study(
        client, tmp_path, target="abc_module_array.json", step_id="ab", select=["A", "B"],
        items=[("y_a", "A/y", constants["Y_A"], constants["STD_A"]),
               ("y_b", "B/y", constants["Y_B"], constants["STD_B"])],
        params=[("A", "p", 0.1, 5.0), ("B", "p", 0.1, 5.0)], setup=_abc_target)
    assert body["plan"]["steps"][0]["selection"] == {"runs_as": "module_array",
                                                     "records": ["A", "B"]}
    start = _start(arrays, "abc")

    _calibrate(client)
    names, files = _send_back(client)
    assert names == ["abc_module_array.json", "abc_parameters.csv"]
    changed = _changed_values(arrays, files)     # the module array is byte-identical
    assert sorted(changed) == ["A/p", "B/p"]     # C/q keeps the target's value
    expected = {"A/p": (constants["Y_A"] / 2) ** 0.5, "B/p": (constants["Y_B"] / 3) ** 0.5}
    for name, value in expected.items():
        assert changed[name] == (start[name], pytest.approx(value, rel=1e-4))
        assert start[name] != pytest.approx(value, rel=1e-2)
    assert _values(files["abc_parameters.csv"].decode())["C/q"] == start["C/q"]


def _sys_target(arrays):
    """CA's test_a_nested_instance_takes_its_super_instances_values target: one chain, sys."""
    (arrays / "sys_module_array.json").write_text(json.dumps([
        {"name": "sys", "module_type": "chain", "module_subtype": "v1",
         "parameterisation": "default", "inp_instances": [], "out_instances": []}], indent=1))
    (arrays / "sys_parameters.json").write_text(json.dumps({"schema_version": 1, "sys": {
        "C": {"q": {"value": 2.0, "units": "dimensionless", "data_reference": "host"}}}}))


@pytest.mark.integration
@pytest.mark.slow
def test_a_selected_nested_instance_goes_back_into_its_super_instance(
        client, requires_simulation, tmp_path):
    """sys/C selected from a chain instance sys: q = sqrt(Z_C - Y_A) lands on sys/C/q."""
    _, constants = _selection_ca()
    arrays, body = _selection_study(
        client, tmp_path, target="sys_module_array.json", step_id="sys-c", select=["sys/C"],
        items=[("z_c", "sys__C/z", constants["Z_C"], 0.17)],
        params=[("sys__C", "q", 0.1, 10.0)],
        params_to_change={"sys__C/x": [[constants["Y_A"]]]}, setup=_sys_target)
    start = _start(arrays, "sys")

    _calibrate(client)
    names, files = _send_back(client)
    assert names == ["sys_module_array.json", "sys_parameters.csv"]
    changed = _changed_values(arrays, files)
    assert list(changed) == ["sys/C/q"]
    expected = (constants["Z_C"] - constants["Y_A"]) ** 0.5
    assert changed["sys/C/q"] == (start["sys/C/q"], pytest.approx(expected, rel=1e-4))


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
