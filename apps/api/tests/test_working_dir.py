"""The working dir holds every input and output of a study.

Uploads land under ``<W>/inputs/<kind>/<yyyy-mm-dd_HHMMSS>/``, one dated folder per
content, and an edit never rewrites an earlier version; a run needs a working dir and is
given the copies there; a working dir zipped and moved elsewhere opens again from its manifest;
``working_dir`` and ``/api/working-dir/*`` are the wire names, with the old ones still
accepted.
"""
from __future__ import annotations

import csv
import json
import os
import re
import shutil
import zipfile
from pathlib import Path

import numpy as np
import pytest

import calibration as calibration_mod
import load_outputs
import main
import sensitivity as sensitivity_mod
import user_funcs as uf
import wire_aliases
import working_dir
from conftest import LV_MODEL_PATH, LV_OBS_DATA_PATH, LV_PARAMS_CSV_PATH, upload_model
from engine import engine

VALID_OP = (
    "def spread(x, series_output=False):\n"
    "    if series_output:\n"
    "        return x\n"
    "    return float(np.max(x) - np.min(x))\n"
)

USER_INPUTS = """\
model_type: cellml
file_prefix: Lotka_Volterra_forced
dt: 0.005
solver_info:
  solver: CVODE_myokit
  method: CVODE
  MaximumStep: 0.0005
"""


@pytest.fixture(autouse=True)
def solver_state(tmp_path, monkeypatch):
    """The engine and the env are process-wide: put back whatever a test adopts."""
    monkeypatch.setenv("CUFLYNX_CONFIG_DIR", str(tmp_path / "cfg"))
    for var in ("CUFLYNX_MODEL_TYPE", "CUFLYNX_SOLVER", "CUFLYNX_SOLVER_INFO"):
        monkeypatch.setenv(var, os.environ.get(var, ""))
    before = (engine.dt, engine.solver, engine.model_type, dict(engine.solver_info))
    engine.dt, engine.solver = 0.01, "CVODE_myokit"
    engine.solver_info = {"MaximumStep": 0.001}
    yield
    engine.dt, engine.solver, engine.model_type = before[:3]
    engine.solver_info = before[3]


STAMP = re.compile(r"^\d{4}-\d{2}-\d{2}_\d{6}(_\d+)?$")


def _stored(work, kind, name):
    """The only stored version of ``name`` in ``<work>/inputs/<kind>/<stamp>/``."""
    [path] = sorted((Path(work) / "inputs" / kind).glob(f"*/{name}"))
    assert STAMP.match(path.parent.name), path
    assert working_dir.latest(work, kind, name) == path
    return path


def _study(client, work=None):
    """Model, obs_data and params_for_id uploaded, the model naming ``work``."""
    with open(LV_MODEL_PATH, "rb") as fh:
        resp = client.post("/api/models/upload",
                           params={"working_dir": str(work)} if work else {},
                           files={"file": (LV_MODEL_PATH.name, fh, "application/xml")})
    assert resp.status_code == 200, resp.text
    model_id = resp.json()["model_id"]
    obs = json.loads(LV_OBS_DATA_PATH.read_text())
    resp = client.post("/api/obs_data/upload", json={"model_id": model_id, "obs_data": obs})
    assert resp.status_code == 200, resp.text
    with open(LV_PARAMS_CSV_PATH, "rb") as fh:
        resp = client.post(f"/api/params_for_id/upload?model_id={model_id}",
                           files={"file": (LV_PARAMS_CSV_PATH.name, fh, "text/csv")})
    assert resp.status_code == 200, resp.text
    return model_id


# --- uploads land under <W>/inputs/ ----------------------------------------------------

def test_an_uploaded_model_obs_data_and_params_land_in_inputs(client, tmp_path):
    work = tmp_path / "W"
    model_id = _study(client, work)

    model = _stored(work, "model", "Lotka_Volterra_forced.cellml")
    obs = _stored(work, "obs_data", "Lotka_Volterra_forced_obs_data.json")
    params = _stored(work, "params_for_id", "Lotka_Volterra_forced_params_for_id.json")
    assert model.read_bytes() == LV_MODEL_PATH.read_bytes()
    assert json.loads(obs.read_text())["data_items"]
    assert json.loads(params.read_text())["params"]
    record = main._models[model_id]
    assert record.obs_path == obs and record.params_path == params


def test_an_omex_lands_in_inputs_with_its_parts(client, tmp_path):
    import io

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("lv.cellml", LV_MODEL_PATH.read_bytes())
        zf.writestr("lv_obs_data.json", LV_OBS_DATA_PATH.read_bytes())
        zf.writestr("lv_params_for_id.csv", LV_PARAMS_CSV_PATH.read_bytes())
    work = tmp_path / "W"
    resp = client.post("/api/omex/upload", params={"working_dir": str(work)},
                       files={"file": ("study.omex", buf.getvalue(), "application/zip")})
    assert resp.status_code == 200, resp.text

    assert _stored(work, "model", "lv.cellml").is_file()
    assert _stored(work, "model", "lv.omex").read_bytes() == buf.getvalue()
    assert _stored(work, "obs_data", "lv_obs_data.json").is_file()
    assert _stored(work, "params_for_id", "lv_params_for_id.json").is_file()


def test_a_user_inputs_yaml_is_kept_not_only_its_solver_settings(client, tmp_path):
    work = tmp_path / "W"
    resp = client.post("/api/user_inputs/upload", params={"working_dir": str(work)},
                       files={"file": ("user_inputs.yaml", USER_INPUTS.encode(), "text/yaml")})
    assert resp.status_code == 200, resp.text
    kept = _stored(work, "user_inputs", "user_inputs.yaml")
    assert kept.read_text() == USER_INPUTS
    assert resp.json()["user_inputs_path"] == str(kept)


def test_user_funcs_are_saved_in_inputs_funcs(client, tmp_path, monkeypatch):
    monkeypatch.setattr(uf, "_circulatory_autogen_src", lambda: str(tmp_path / "ca"))
    work = tmp_path / "W"
    resp = client.post("/api/operation_funcs",
                       json={"source": VALID_OP, "working_dir": str(work)})
    assert resp.status_code == 200, resp.text
    assert "def spread" in _stored(work, "funcs", "operation_funcs_user.py").read_text()


def _save_obs(client, model_id, work, obs):
    """The obs_data editor's Save: the edited document, the working dir and a filename."""
    resp = client.post("/api/obs_data/upload",
                       params={"working_dir": str(work), "filename": "LV_obs_261031.json"},
                       json={"model_id": model_id, "obs_data": obs})
    assert resp.status_code == 200, resp.text
    return resp.json()["saved_path"]


def test_an_edit_of_the_obs_data_is_a_new_dated_version_and_leaves_the_earlier_one(
        client, tmp_path):
    work = tmp_path / "W"
    model_id = _study(client, work)
    first = main._models[model_id].obs_path
    before = first.read_bytes()

    obs = json.loads(LV_OBS_DATA_PATH.read_text())
    obs["data_items"][0]["value"] = 123.0
    saved = _save_obs(client, model_id, work, obs)

    edited = main._models[model_id].obs_path
    assert edited != first and edited.name == first.name
    assert STAMP.match(edited.parent.name) and edited.parent.parent == first.parent.parent
    assert json.loads(edited.read_text())["data_items"][0]["value"] == 123.0
    assert first.read_bytes() == before, "the earlier version was rewritten"
    assert working_dir.latest(work, "obs_data", first.name) == edited
    # the dated copy Save reports is a version of its own too
    assert STAMP.match(Path(saved).parent.name) and Path(saved).name == "LV_obs_261031.json"

    # saving the same document again stores nothing new
    folders = sorted(p.name for p in (work / "inputs" / "obs_data").iterdir())
    assert _save_obs(client, model_id, work, obs) == saved
    assert main._models[model_id].obs_path == edited
    assert sorted(p.name for p in (work / "inputs" / "obs_data").iterdir()) == folders


def test_a_ca_without_the_input_store_is_named_when_an_input_is_saved(client, tmp_path,
                                                                     monkeypatch):
    model_id = _study(client)
    from ca_imports import CaImportError

    def too_old(module, *names):
        raise CaImportError(f"{module!r} has no {names[0]}")

    monkeypatch.setattr(working_dir, "ca_from", too_old)
    resp = client.post("/api/obs_data/upload", params={"working_dir": str(tmp_path / "W")},
                       json={"model_id": model_id,
                             "obs_data": json.loads(LV_OBS_DATA_PATH.read_text())})
    assert resp.status_code == 422
    assert "store_input" in resp.json()["detail"] and "#619" in resp.json()["detail"]


def test_saving_an_earlier_content_again_makes_it_the_current_version(client, tmp_path):
    work = tmp_path / "W"
    model_id = _study(client, work)
    first = main._models[model_id].obs_path
    original = json.loads(first.read_text())
    edited = json.loads(json.dumps(original))
    edited["data_items"][0]["value"] = 7.0
    _save_obs(client, model_id, work, edited)
    _save_obs(client, model_id, work, original)
    assert main._models[model_id].obs_path.read_text() == first.read_text()


def test_saved_parameter_sets_go_to_saved_params(client, tmp_path):
    work = tmp_path / "W"
    resp = client.post("/api/params/save", json={
        "values": {"a/x": 1.0}, "order": ["a/x"], "filename": "manual.npy",
        "working_dir": str(work)})
    assert resp.status_code == 200, resp.text
    assert resp.json()["path"] == str(work / "saved_params" / "manual.npy")
    listed = client.get("/api/runs", params={"dir": str(work)}).json()
    assert listed["dir"] == str(work / "saved_params")


def test_saved_parameter_sets_at_the_top_of_an_older_folder_are_still_listed(client, tmp_path):
    import saved_runs

    work = tmp_path / "W"
    work.mkdir()
    np.save(work / "old.npy", np.array([1.0]))
    saved_runs.save_run(str(work / "old.npy"), {"a/x": 1.0},
                        {"t": [0.0, 1.0], "outputs": {"a/y": [0.0, 1.0]}})
    listed = client.get("/api/runs", params={"dir": str(work)}).json()
    assert [r["prefix"] for r in listed["runs"]] == ["old"]


# --- a run needs a working dir, and is given the copies in it ----------------------

@pytest.mark.parametrize("route", ["/api/calibration/run", "/api/sensitivity/run",
                                   "/api/uq/run", "/api/emulator/train"])
def test_a_run_without_a_working_dir_is_refused(client, route):
    model_id = _study(client)
    # UQ asks first for a calibration to reuse, unless it runs one itself.
    settings = {"run_calibration_first": True} if route == "/api/uq/run" else {}
    resp = client.post(route, json={"model_id": model_id, "settings": settings})
    assert resp.status_code == 422
    assert resp.json()["detail"].startswith("Set a working dir")


def test_a_run_is_given_the_inputs_copied_into_the_working_dir(client, tmp_path, monkeypatch):
    model_id = _study(client)          # uploaded before any working dir was chosen
    captured = {}
    monkeypatch.setattr(calibration_mod.calibration, "start",
                        lambda config: captured.update(config) or "job-1")
    work = tmp_path / "W"

    resp = client.post("/api/calibration/run",
                       json={"model_id": model_id, "settings": {"working_dir": str(work)}})

    assert resp.status_code == 200, resp.text
    assert captured["output_dir"] == str(work)
    assert captured["model_path"] == str(_stored(work, "model", "Lotka_Volterra_forced.cellml"))
    assert captured["cellml_path"] == captured["model_path"]
    assert captured["obs_path"] == str(
        _stored(work, "obs_data", "Lotka_Volterra_forced_obs_data.json"))
    assert captured["params_path"] == str(
        _stored(work, "params_for_id", "Lotka_Volterra_forced_params_for_id.json"))
    for key in ("model_path", "obs_path", "params_path"):
        assert os.path.isfile(captured[key]), key


def test_sensitivity_results_go_to_the_sensitivity_folder(client, tmp_path, monkeypatch):
    model_id = _study(client)
    captured = {}
    monkeypatch.setattr(sensitivity_mod.sensitivity, "start",
                        lambda config: captured.update(config) or "job-1")
    work = tmp_path / "W"
    resp = client.post("/api/sensitivity/run", json={
        "model_id": model_id,
        "settings": {"working_dir": str(work), "method": "local", "nominal": "current"}})
    assert resp.status_code == 200, resp.text
    assert captured["output_dir"] == str(work)
    assert captured["sa_output_dir"] == str(work / "sensitivity")


def test_sensitivity_results_are_read_from_either_layout(tmp_path):
    import ca_run_history

    new = tmp_path / "new"
    (new / "sensitivity").mkdir(parents=True)
    ca_run_history.write_local_sensitivity(str(new / "sensitivity"), "relative",
                                           {"y": {"p/a": 0.5}}, ["y"])
    old = tmp_path / "old"
    old.mkdir()
    ca_run_history.write_local_sensitivity(str(old), "relative", {"y": {"p/a": 0.25}}, ["y"])
    for folder in (new, old):
        found = load_outputs.load_outputs(str(folder))
        assert "sensitivity" in found["found"], folder


def test_funcs_in_an_older_folders_user_funcs_are_read_there_and_saved_as_a_version(
        tmp_path, monkeypatch):
    monkeypatch.setattr(uf, "_circulatory_autogen_src", lambda: str(tmp_path / "ca"))
    work = tmp_path / "W"
    uf.save_user_func("operation", "", VALID_OP, str(work))
    stored = _stored(work, "funcs", "operation_funcs_user.py")
    (work / "user_funcs").mkdir()
    shutil.move(str(stored), str(work / "user_funcs"))
    shutil.rmtree(work / "inputs")

    legacy = work / "user_funcs" / "operation_funcs_user.py"
    assert uf.external_path("operation", str(work)) == str(legacy)
    uf.save_user_func("operation", "", VALID_OP.replace("spread", "spread2"), str(work))
    edited = _stored(work, "funcs", "operation_funcs_user.py")
    assert "spread2" in edited.read_text() and "def spread(" in edited.read_text()
    assert "spread2" not in legacy.read_text()
    assert uf.external_path("operation", str(work)) == str(edited)


def test_load_outputs_finds_the_dated_inputs_without_a_manifest(tmp_path, requires_ca):
    """A folder libcuflynx stored inputs in (its store_input), with no manifest."""
    from ca_imports import ca_from

    store_input = ca_from("utilities.cuflynx_study", "store_input")
    work = tmp_path / "W"
    model = work / store_input(str(work), "model", str(LV_MODEL_PATH), "LV.cellml")
    store_input(str(work), "user_inputs", USER_INPUTS.encode(), "user_inputs.yaml")
    user_inputs = work / store_input(str(work), "user_inputs", (USER_INPUTS + "#\n").encode(),
                                     "user_inputs.yaml")
    found = load_outputs.load_outputs(str(work), file_prefix="LV")["study"]
    assert found["model"] == str(model)
    assert found["user_inputs"] == str(user_inputs)


# --- wire names and routes -------------------------------------------------------------

def test_working_dir_is_copied_onto_the_old_names_at_any_depth():
    body = {"working_dir": "/w", "settings": {"working_dir": "/s", "output_dir": "/kept"}}
    out = wire_aliases.with_legacy_names(body)
    assert out["config_outputs_dir"] == out["output_dir"] == "/w"
    assert out["settings"]["config_outputs_dir"] == "/s"
    assert out["settings"]["output_dir"] == "/kept"


def test_an_old_name_in_the_query_is_not_overridden():
    query = wire_aliases.query_with_legacy_names(b"working_dir=%2Fw&output_dir=%2Fo")
    assert b"output_dir=%2Fo" in query and b"config_outputs_dir=%2Fw" in query
    assert b"output_dir=%2Fw" not in query.replace(b"outputs_dir", b"")


@pytest.mark.parametrize("name", ["working_dir", "output_dir"])
def test_the_new_and_the_old_query_name_both_reach_the_route(client, tmp_path, name,
                                                              monkeypatch):
    monkeypatch.setattr(uf, "_circulatory_autogen_src", lambda: str(tmp_path / "ca"))
    uf.save_user_func("operation", "", VALID_OP, str(tmp_path))
    resp = client.get("/api/operation_funcs", params={name: str(tmp_path)})
    assert [f["name"] for f in resp.json()["functions"]] == ["spread"]


@pytest.mark.parametrize("name", ["working_dir", "config_outputs_dir"])
def test_the_new_and_the_old_settings_name_both_start_a_run(client, tmp_path, monkeypatch,
                                                             name):
    model_id = _study(client)
    captured = {}
    monkeypatch.setattr(calibration_mod.calibration, "start",
                        lambda config: captured.update(config) or "job-1")
    resp = client.post("/api/calibration/run",
                       json={"model_id": model_id, "settings": {name: str(tmp_path)}})
    assert resp.status_code == 200, resp.text
    assert captured["output_dir"] == str(tmp_path)


@pytest.mark.parametrize("route", ["load", "manifest", "study"])
def test_the_old_outputs_routes_answer_as_the_working_dir_routes(client, tmp_path, route):
    if route == "load":
        new = client.get("/api/working-dir/load", params={"dir": str(tmp_path)})
        old = client.get("/api/outputs/load", params={"dir": str(tmp_path)})
    else:
        body = ({"content": {"schema": 1}} if route == "manifest" else {"dir": str(tmp_path)})
        new = client.post(f"/api/working-dir/{route}", json=body)
        old = client.post(f"/api/outputs/{route}", json=body)
    assert new.status_code == old.status_code != 404
    assert new.json() == old.json()


# --- a working dir zipped, moved and opened again --------------------------------------
#
# Hand-built to Schematic B: only relative paths, every input in a dated folder under
# inputs/ (stored by libcuflynx's store_input, so inputs/index.json lists them), one
# calibration run with its history.

PARAMS_JSON = {"version": 1, "defaults": {}, "params": [
    {"targets": ["Lotka_Volterra_module/alpha"], "name": "Lotka_Volterra_module/alpha",
     "param_type": "const", "name_for_plotting": "alpha", "min": "0.1", "max": "3.0"}]}


def _build_working_dir(work):
    def store(kind, source, name):
        return str(working_dir.store(work, kind, source, name).relative_to(work))

    model = store("model", LV_MODEL_PATH, "LV.cellml")
    obs = store("obs_data", LV_OBS_DATA_PATH, "LV_obs_data.json")
    params = store("params_for_id", json.dumps(PARAMS_JSON).encode(), "LV_params_for_id.json")
    user_inputs = store("user_inputs", USER_INPUTS.encode(), "user_inputs.yaml")
    uf.save_user_func("operation", "", VALID_OP, str(work))
    funcs = str(_stored(work, "funcs", "operation_funcs_user.py").relative_to(work))

    run = work / "genetic_algorithm_LV_LV_obs_data"
    run.mkdir()
    np.save(run / "best_param_vals.npy", np.array([1.25]))
    np.save(run / "best_cost.npy", np.array(0.125))
    with open(run / "param_names.csv", "w", newline="") as handle:
        csv.writer(handle).writerows([["Lotka_Volterra_module/alpha"]])
    with open(run / "best_cost_history.csv", "w") as handle:
        handle.write("0.500000000\n0.125000000\n")
    with open(run / "best_param_vals_history.csv", "w") as handle:
        handle.write("Lotka_Volterra_module alpha\n1.00000e-01\n2.50000e-01\n")
    (run / "param_bounds.json").write_text(json.dumps({
        "param_labels": ["Lotka_Volterra_module/alpha"],
        "param_mins": [0.1], "param_maxs": [3.0]}))

    manifest = work / "LV_cuflynx_study.json"
    manifest.write_text(json.dumps({
        "schema": 1, "study_name": "LV", "file_prefix": "LV",
        "user_inputs": user_inputs, "model": model, "base_model": model,
        "obs_data": obs, "params_for_id": params, "user_funcs": {"operation": funcs},
        "solver_info": {"solver": "CVODE_myokit", "method": "CVODE",
                        "MaximumStep": 0.0005, "dt": 0.005},
        "runs": [{"dir": run.name, "method": "genetic_algorithm",
                  "analyses": ["genetic_algorithm"], "status": "finished"}],
    }, indent=2))
    return manifest


def _zip_and_move(work, moved):
    archive = shutil.make_archive(str(work.parent / "study"), "zip", root_dir=work.parent,
                                  base_dir=work.name)
    shutil.rmtree(work)
    moved.mkdir(parents=True)
    with zipfile.ZipFile(archive) as zf:
        zf.extractall(moved)
    return moved / work.name


def test_a_zipped_and_moved_working_dir_opens_whole(client, tmp_path, monkeypatch,
                                                    requires_ca):
    monkeypatch.setattr(uf, "_circulatory_autogen_src", lambda: str(tmp_path / "ca"))
    manifest = _build_working_dir(tmp_path / "orig" / "W")
    assert '"/' not in manifest.read_text(), "the manifest records an absolute path"
    work = _zip_and_move(tmp_path / "orig" / "W", tmp_path / "elsewhere")
    assert not (tmp_path / "orig" / "W").exists()

    # Dropping the manifest opens the folder it is in.
    dropped = work / manifest.name
    opened = client.post("/api/working-dir/manifest", json={
        "content": dropped.read_text(), "path": str(dropped)})
    assert opened.status_code == 200, opened.text
    assert opened.json()["dir"] == str(work)

    # The results: best fit and progress.
    loaded = client.get("/api/working-dir/load", params={"dir": str(work)}).json()
    assert {"calibration", "progress"} <= set(loaded["found"]), loaded.get("missing")
    assert loaded["progress"]["cost_history"] == [[0.5], [0.125]]
    assert loaded["study"]["model"] == str(_stored(work, "model", "LV.cellml"))
    best = loaded["calibration"]["best"]
    assert best["cost"] == pytest.approx(0.125)
    assert best["params"]["Lotka_Volterra_module/alpha"] == pytest.approx(1.25)

    # The study: model, obs_data, params_for_id, and the solver settings.
    study = client.post("/api/working-dir/study", json={"dir": str(work)})
    assert study.status_code == 200, study.text
    body = study.json()
    assert body["model_id"] and body["variable_count"] > 0
    assert body["obs_data"]["data_items"]
    assert [p["name"] for p in body["params_for_id"]["params"]] == [
        "Lotka_Volterra_module/alpha"]
    assert body["solver_settings"]["solver_info"]["MaximumStep"] == 0.0005
    assert engine.dt == 0.005
    # Run from the folder's own model, not a copy of it.
    assert main._models[body["model_id"]].working_model == _stored(work, "model", "LV.cellml")

    # The funcs.
    funcs = client.get("/api/operation_funcs", params={"working_dir": str(work)}).json()
    assert [f["name"] for f in funcs["functions"]] == ["spread"]
    assert funcs["path"] == str(_stored(work, "funcs", "operation_funcs_user.py"))


def test_the_layout_names_every_input_kind():
    assert set(working_dir.KINDS) >= {"model", "obs_data", "params_for_id", "user_inputs",
                                      "funcs", "workflows", "generated_models"}


# --- a dropped zip of a working dir ------------------------------------------------------

REPO_RESOURCES = Path(__file__).resolve().parents[3] / "resources"


@pytest.fixture
def staged_zips(tmp_path, monkeypatch):
    staging = tmp_path / "staged"
    monkeypatch.setattr(main, "WORKING_DIR_ZIPS", staging)
    return staging


def _zip_working_dir(work, archive, *, folder=True):
    """``work`` zipped as a folder (``W/...``) or as its contents, then removed."""
    root_dir, base_dir = (work.parent, work.name) if folder else (work, ".")
    made = shutil.make_archive(str(archive.with_suffix("")), "zip", root_dir=root_dir,
                               base_dir=base_dir)
    shutil.rmtree(work)
    return Path(made)


def _drop_zip(client, archive, **params):
    with open(archive, "rb") as fh:
        return client.post("/api/working-dir/zip", params=params,
                           files={"file": (archive.name, fh, "application/zip")})


def _zip_of(members, archive):
    with zipfile.ZipFile(archive, "w") as zf:
        for name, data in members.items():
            zf.writestr(name, data)
    return archive


MANIFEST_ONLY = json.dumps({"schema": 1, "study_name": "LV", "file_prefix": "LV"})


@pytest.mark.parametrize("folder", [True, False], ids=["folder", "contents"])
def test_a_dropped_working_dir_zip_is_extracted_and_opens_with_its_runs_and_settings(
        client, tmp_path, monkeypatch, staged_zips, requires_ca, folder):
    monkeypatch.setattr(uf, "_circulatory_autogen_src", lambda: str(tmp_path / "ca"))
    _build_working_dir(tmp_path / "orig" / "W")
    (tmp_path / "Downloads").mkdir()
    archive = _zip_working_dir(tmp_path / "orig" / "W", tmp_path / "Downloads" / "study.zip",
                               folder=folder)

    inspected = _drop_zip(client, archive, zip_path=str(archive))
    assert inspected.status_code == 200, inspected.text
    body = inspected.json()
    assert body["kind"] == "working_dir" and body["study_name"] == "LV"
    # Beside the zip, named for the study: not in the pruned uploads dir.
    assert body["default_dir"] == str(tmp_path / "Downloads" / "LV")

    extracted = client.post("/api/working-dir/zip/extract", json={
        "token": body["token"], "dest": body["default_dir"]})
    assert extracted.status_code == 200, extracted.text
    opened = extracted.json()
    work = tmp_path / "Downloads" / "LV"
    assert opened["dir"] == str(work)
    assert opened["study_name"] == "LV" and opened["warnings"] == []
    assert (work / "LV_cuflynx_study.json").is_file()
    assert not list(staged_zips.iterdir()), "the staged zip is left behind"

    # What the app does with the answer: set the working dir and reload it.
    loaded = client.get("/api/working-dir/load", params={"dir": opened["dir"]}).json()
    assert {"calibration", "progress"} <= set(loaded["found"]), loaded.get("missing")
    assert loaded["calibration"]["best"]["cost"] == pytest.approx(0.125)
    study = client.post("/api/working-dir/study", json={"dir": opened["dir"]})
    assert study.status_code == 200, study.text
    assert study.json()["solver_settings"]["solver_info"]["MaximumStep"] == 0.0005
    assert engine.dt == 0.005
    assert main._models[study.json()["model_id"]].working_model == _stored(
        work, "model", "LV.cellml")


def test_a_zip_with_no_path_is_proposed_in_the_documents_dir_under_a_free_name(
        client, tmp_path, monkeypatch, staged_zips):
    monkeypatch.setattr(main, "_documents_dir", lambda: tmp_path / "Documents")
    (tmp_path / "Documents" / "LV").mkdir(parents=True)
    (tmp_path / "Documents" / "LV" / "other.txt").write_text("someone else's")
    archive = _zip_of({"W/LV_cuflynx_study.json": MANIFEST_ONLY}, tmp_path / "W.zip")
    body = _drop_zip(client, archive).json()
    assert body["default_dir"] == str(tmp_path / "Documents" / "LV_2")


def test_an_omex_dropped_as_a_zip_is_still_an_omex(client, tmp_path, staged_zips):
    archive = tmp_path / "3compartment.zip"
    shutil.copy(REPO_RESOURCES / "3compartment.omex", archive)
    resp = _drop_zip(client, archive)
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"kind": "omex"}
    # An OMEX carries a study manifest at its root too: manifest.xml is what decides.
    archive = _zip_of({"manifest.xml": "<omexManifest/>",
                       "LV_cuflynx_study.json": MANIFEST_ONLY}, tmp_path / "x.zip")
    assert _drop_zip(client, archive).json() == {"kind": "omex"}
    assert not staged_zips.exists() or not list(staged_zips.iterdir())


@pytest.mark.parametrize("bad", ["W/../../evil.txt", "/tmp/evil.txt", "C:/evil.txt",
                                 "W\\..\\..\\evil.txt"])
def test_a_zip_with_an_entry_outside_its_folder_is_refused(client, tmp_path, staged_zips,
                                                          bad):
    archive = _zip_of({"W/LV_cuflynx_study.json": MANIFEST_ONLY, bad: "x"},
                      tmp_path / "W.zip")
    resp = _drop_zip(client, archive, zip_path=str(archive))
    assert resp.status_code == 422, resp.text
    assert "not extracted" in resp.json()["detail"]
    assert not staged_zips.exists() or not list(staged_zips.iterdir())
    assert not (tmp_path / "LV").exists() and not (tmp_path.parent / "evil.txt").exists()


def test_a_zip_with_a_symlink_is_refused(client, tmp_path, staged_zips):
    archive = tmp_path / "W.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("W/LV_cuflynx_study.json", MANIFEST_ONLY)
        link = zipfile.ZipInfo("W/inputs")
        link.external_attr = (0o120777 << 16)
        zf.writestr(link, "/etc")
    assert _drop_zip(client, archive).status_code == 422


def test_a_zip_holding_no_study_is_refused(client, tmp_path, staged_zips):
    archive = _zip_of({"a/b/LV_cuflynx_study.json": MANIFEST_ONLY, "x.txt": "x"},
                      tmp_path / "x.zip")
    resp = _drop_zip(client, archive)
    assert resp.status_code == 422 and "holds no study" in resp.json()["detail"]


def test_extracting_into_a_folder_holding_files_needs_confirmation(client, tmp_path,
                                                                    staged_zips):
    archive = _zip_of({"W/LV_cuflynx_study.json": MANIFEST_ONLY}, tmp_path / "W.zip")
    token = _drop_zip(client, archive).json()["token"]
    dest = tmp_path / "taken"
    dest.mkdir()
    (dest / "keep.txt").write_text("kept")
    resp = client.post("/api/working-dir/zip/extract", json={"token": token, "dest": str(dest)})
    assert resp.status_code == 409 and "already holds files" in resp.json()["detail"]
    assert not (dest / "LV_cuflynx_study.json").exists()
    resp = client.post("/api/working-dir/zip/extract",
                       json={"token": token, "dest": str(dest), "overwrite": True})
    assert resp.status_code == 200, resp.text
    assert resp.json()["dir"] == str(dest)
    assert (dest / "LV_cuflynx_study.json").is_file() and (dest / "keep.txt").is_file()


def test_a_zip_with_files_beside_its_study_folder_opens_that_folder(client, tmp_path,
                                                                     staged_zips):
    archive = _zip_of({"W/LV_cuflynx_study.json": MANIFEST_ONLY, "README.txt": "x"},
                      tmp_path / "W.zip")
    token = _drop_zip(client, archive).json()["token"]
    resp = client.post("/api/working-dir/zip/extract",
                       json={"token": token, "dest": str(tmp_path / "out")})
    assert resp.status_code == 200, resp.text
    assert resp.json()["dir"] == str(tmp_path / "out" / "W")
    assert (tmp_path / "out" / "README.txt").is_file()


def test_outputs_the_manifest_records_outside_the_working_dir_are_warned_about(
        client, tmp_path, staged_zips):
    manifest = json.dumps({"schema": 1, "study_name": "LV",
                           "outside_outputs": ["/data/emulators/shared"]})
    archive = _zip_of({"LV_cuflynx_study.json": manifest}, tmp_path / "W.zip")
    token = _drop_zip(client, archive).json()["token"]
    resp = client.post("/api/working-dir/zip/extract",
                       json={"token": token, "dest": str(tmp_path / "out")})
    assert resp.status_code == 200, resp.text
    [warning] = resp.json()["warnings"]
    assert "/data/emulators/shared" in warning
