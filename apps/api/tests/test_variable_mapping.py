"""The Variables panel's LaTeX symbols (symbol_mapping.py + /api/models/{id}/variable_mapping).

Everything about the mapping file is CA's (libcuflynx.reporting.variable_mapping); CUFLynx
decides where a study's file lives and passes edits through. The unit tier injects a fake CA
module; the integration test uses CA's own with a real model.
"""

import csv
import os
import types

import pytest

import main
import workflow_manager
from conftest import LV_MODEL_PATH, set_ca_module, upload_model


def _fake_ca(monkeypatch, calls, refuse=None):
    mod = types.ModuleType("variable_mapping")
    files = {}

    def variable_mapping(model_path, path):
        calls.append(("rows", model_path, path))
        if refuse:
            raise ValueError(refuse)
        kept = files.get(path, {})
        return [{"variable_name": name, "latex": kept.get(name, default),
                 "default_latex": default, "kind": "parameter", "component": "parameters",
                 "units": "1"}
                for name, default in (("parameters/a", "a"), ("parameters/b", "b"))]

    def check_variable_mapping(rows):
        return {"empty": [r["variable_name"] for r in rows if not r["latex"]], "duplicates": {}}

    def write_variable_mapping(rows, path):
        calls.append(("write", path, [(r["variable_name"], r["latex"]) for r in rows]))
        files[path] = {r["variable_name"]: r["latex"] for r in rows}
        os.makedirs(os.path.dirname(path), exist_ok=True)
        open(path, "w").write("written by the fake\n")
        return path

    for fn in (variable_mapping, check_variable_mapping, write_variable_mapping):
        setattr(mod, fn.__name__, fn)
    set_ca_module(monkeypatch, "reporting.variable_mapping", mod)
    methods = types.ModuleType("methods")
    methods.workflow_mapping_path = (
        lambda path, dirs: "/library/twin/v1/instances/split/split_variable_mapping.csv")
    set_ca_module(monkeypatch, "reporting.methods", methods)
    return files


def test_a_study_s_mapping_lives_in_the_outputs_directory(client, monkeypatch, tmp_path):
    calls = []
    _fake_ca(monkeypatch, calls)
    model_id = upload_model(client, LV_MODEL_PATH)["model_id"]
    record = main._models[model_id]
    r = client.get(f"/api/models/{model_id}/variable_mapping",
                   params={"output_dir": str(tmp_path)})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["path"] == str(tmp_path / f"{main._record_prefix(record)}_variable_mapping.csv")
    assert body["exists"] is False and [row["latex"] for row in body["rows"]] == ["a", "b"]
    assert calls[0] == ("rows", str(record.path), body["path"])
    # without an outputs directory it is kept with the upload
    body = client.get(f"/api/models/{model_id}/variable_mapping").json()
    assert body["path"].startswith(str(main.UPLOAD_DIR))


def test_edits_are_saved_through_ca_over_the_model_s_rows(client, monkeypatch, tmp_path):
    calls = []
    _fake_ca(monkeypatch, calls)
    model_id = upload_model(client, LV_MODEL_PATH)["model_id"]
    r = client.put(f"/api/models/{model_id}/variable_mapping",
                   json={"output_dir": str(tmp_path),
                         "rows": [{"variable_name": "parameters/b", "latex": r" \beta "},
                                  {"variable_name": "not/in_the_model", "latex": "x"}]})
    assert r.status_code == 200, r.text
    written = [c for c in calls if c[0] == "write"][0]
    # every row of the model, the edit applied (trimmed), nothing the model does not have
    assert written[2] == [("parameters/a", "a"), ("parameters/b", r"\beta")]
    body = r.json()
    assert body["exists"] is True
    assert {row["variable_name"]: row["latex"] for row in body["rows"]}["parameters/b"] == r"\beta"


def test_the_supermodule_tab_keeps_its_mapping_beside_the_target_instance(client, monkeypatch):
    calls = []
    _fake_ca(monkeypatch, calls)
    model_id = upload_model(client, LV_MODEL_PATH)["model_id"]
    workflow = workflow_manager.workflow
    workflow.path = "/library/twin/v1/instances/split/calibration_workflow.json"
    workflow.view_models[model_id] = workflow_manager.TARGET_VIEW
    body = client.get(f"/api/models/{model_id}/variable_mapping").json()
    assert body["path"] == "/library/twin/v1/instances/split/split_variable_mapping.csv"
    # a step tab is an ordinary study
    workflow.view_models[model_id] = "fit_a"
    assert client.get(f"/api/models/{model_id}/variable_mapping").json()["path"] != body["path"]
    # a CA that cannot say where the target's file goes: kept with the study, not a 500
    workflow.view_models[model_id] = workflow_manager.TARGET_VIEW
    set_ca_module(monkeypatch, "reporting.methods", None)
    r = client.get(f"/api/models/{model_id}/variable_mapping")
    assert r.status_code == 200 and r.json()["path"].startswith(str(main.UPLOAD_DIR))


def test_refusals_and_an_older_ca(client, monkeypatch):
    _fake_ca(monkeypatch, [], refuse="only CellML models can be mapped")
    model_id = upload_model(client, LV_MODEL_PATH)["model_id"]
    r = client.get(f"/api/models/{model_id}/variable_mapping")
    assert r.status_code == 422 and r.json()["detail"] == "only CellML models can be mapped"
    set_ca_module(monkeypatch, "reporting.variable_mapping", None)
    r = client.get(f"/api/models/{model_id}/variable_mapping")
    assert r.status_code == 501 and "libcuflynx.reporting" in r.json()["detail"]


@pytest.mark.integration
def test_a_real_model_s_symbols_are_edited_and_kept(client, requires_simulation, tmp_path):
    try:
        from ca_imports import ca_from

        ca_from("reporting.variable_mapping", "variable_mapping")
    except ImportError:
        if os.environ.get("CUFLYNX_REQUIRE_CA_RUN"):
            raise
        pytest.skip("this circulatory_autogen has no libcuflynx.reporting (CA #547)")
    model_id = upload_model(client, LV_MODEL_PATH)["model_id"]
    body = client.get(f"/api/models/{model_id}/variable_mapping",
                      params={"output_dir": str(tmp_path)}).json()
    names = {row["variable_name"]: row for row in body["rows"]}
    alpha = next(n for n in names if n.endswith("/alpha"))
    assert names[alpha]["latex"].startswith(r"\alpha")
    r = client.put(f"/api/models/{model_id}/variable_mapping",
                   json={"output_dir": str(tmp_path),
                         "rows": [{"variable_name": alpha, "latex": r"\alpha_{\mathrm{prey}}"}]})
    assert r.status_code == 200, r.text
    with open(r.json()["path"]) as f:
        saved = {row["variable_name"]: row["latex"] for row in csv.DictReader(f)}
    assert saved[alpha] == r"\alpha_{\mathrm{prey}}"
    again = client.get(f"/api/models/{model_id}/variable_mapping",
                       params={"output_dir": str(tmp_path)}).json()
    row = {x["variable_name"]: x for x in again["rows"]}[alpha]
    assert row["latex"] == r"\alpha_{\mathrm{prey}}" and row["edited"] is True
