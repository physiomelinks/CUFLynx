"""Recording where a value of a workflow's target comes from (the origin boxes' form): the
target's parameters file is written with libcuflynx's parameters_file, and the next describe
puts the value in its origin's box."""
import json
from pathlib import Path

import pytest

import workflow_manager as wm


def _pf():
    try:
        return wm.ca_import("utilities.parameters_file")
    except ImportError:
        pytest.skip("libcuflynx is not importable")


def _manager(monkeypatch, tmp_path, plan):
    """A manager with a workflow open, CA's plan replaced by ``plan``."""
    wf = tmp_path / "calibration_workflow.json"
    wf.write_text(json.dumps({"workflow_name": "w", "target": plan.get("target"), "steps": []}))
    m = wm.WorkflowManager()
    m.path = str(wf)
    monkeypatch.setattr(wm, "_ca_call", lambda name, *a, **k: plan)
    return m


def _plan(params, target=None, values=None):
    return {"target": target or {"cellml_model_path": "lv.cellml", "parameters": str(params)},
            "target_parameters": str(params) if params else None,
            "target_values": values or [
                {"name": "lv/alpha", "value": 5.0, "units": "dimensionless", "origin": "measured",
                 "data_reference": "smith2001", "reason": "", "calibrated_by": []},
                {"name": "lv/delta", "value": 0.2, "units": "per_s", "origin": None,
                 "data_reference": "", "reason": "", "calibrated_by": []}]}


def _write(path, doc):
    path.write_text(json.dumps(dict({"schema_version": 1}, **doc)))


def test_an_edit_changes_the_origin_in_the_parameters_file(monkeypatch, tmp_path):
    _pf()
    params = tmp_path / "lv_parameters.json"
    _write(params, {"lv": {"alpha": {"value": "5.0", "units": "dimensionless", "origin": "measured",
                                     "data_reference": "smith2001"}}})
    m = _manager(monkeypatch, tmp_path, _plan(params))
    assert m.set_value_origin("lv/alpha", "set_by_hand", "", "a round number") == str(params)
    leaf = _pf().read_parameters_file(str(params))["lv"]["alpha"]
    # the value and units are kept; the data reference is what the form says
    assert leaf == {"value": "5.0", "units": "dimensionless", "data_reference": "",
                    "origin": "set_by_hand", "reason": "a round number"}
    # no origin: "origin not recorded"; an empty reason goes too
    m.set_value_origin("lv/alpha", "", "smith2001", "")
    leaf = json.loads(params.read_text())["lv"]["alpha"]
    assert "origin" not in leaf and "reason" not in leaf and leaf["data_reference"] == "smith2001"


def test_a_derived_value_set_another_way_no_longer_names_its_derivation(monkeypatch, tmp_path):
    """libcuflynx refuses a value naming a derivation whose origin is not derived."""
    pf = _pf()
    params = tmp_path / "lv_parameters.json"
    _write(params, {"lv": {"alpha": {"value": "5.0", "units": "dimensionless", "origin": "derived",
                                     "data_reference": "", "derivation": "derive_alpha",
                                     "fixed_constants": "{}"}}})
    m = _manager(monkeypatch, tmp_path, _plan(params))
    m.set_value_origin("lv/alpha", "derived", "derive_alpha in derivations.py")
    assert pf.read_parameters_file(str(params))["lv"]["alpha"]["derivation"] == "derive_alpha"
    m.set_value_origin("lv/alpha", "measured", "smith2001")
    leaf = pf.read_parameters_file(str(params))["lv"]["alpha"]  # valid: CA reads it
    assert "derivation" not in leaf and "fixed_constants" not in leaf


def test_a_constant_the_file_does_not_set_is_added_with_the_models_value(monkeypatch, tmp_path):
    _pf()
    params = tmp_path / "lv_parameters.json"
    _write(params, {"lv": {"alpha": {"value": "5.0", "units": "dimensionless"}}})
    m = _manager(monkeypatch, tmp_path, _plan(params))
    m.set_value_origin("lv/delta", "measured", "jones2004; Fig 3")
    doc = json.loads(params.read_text())
    assert doc["lv"]["delta"] == {"value": 0.2, "units": "per_s", "data_reference": "jones2004; Fig 3",
                                  "origin": "measured"}
    assert doc["lv"]["alpha"]["value"] == 5.0


def test_a_library_targets_names_are_its_parameterisations(monkeypatch, tmp_path):
    _pf()
    params = tmp_path / "lin_a_parameters.json"
    _write(params, {"k": {"value": "2", "units": "per_s", "data_reference": ""}})
    plan = _plan(params, target={"module_type": "lin", "version": "a", "parameterisation": "lin_a"},
                 values=[{"name": "k", "value": 2.0, "units": "per_s", "origin": None,
                          "data_reference": "", "reason": "", "calibrated_by": []}])
    _manager(monkeypatch, tmp_path, plan).set_value_origin("k", "derived", "derive_k")
    assert json.loads(params.read_text())["k"]["origin"] == "derived"


@pytest.mark.parametrize("target, why", [
    ({"external_model_path": "m.py"}, "external Python model"),
    ({"cellml_model_path": "lv.cellml"}, '"parameters" beside "cellml_model_path"'),
])
def test_a_target_without_a_parameters_file_says_so(monkeypatch, tmp_path, target, why):
    _pf()
    m = _manager(monkeypatch, tmp_path, _plan(None, target=target))
    with pytest.raises(wm.WorkflowError, match=why):
        m.set_value_origin("lv/alpha", "measured")


def test_an_unknown_origin_or_parameter_is_refused(monkeypatch, tmp_path):
    _pf()
    params = tmp_path / "lv_parameters.json"
    _write(params, {})
    m = _manager(monkeypatch, tmp_path, _plan(params))
    with pytest.raises(wm.WorkflowError, match="not an origin"):
        m.set_value_origin("lv/alpha", "guessed")
    with pytest.raises(wm.WorkflowError, match='no parameter "lv/nope"'):
        m.set_value_origin("lv/nope", "measured")
    assert json.loads(params.read_text()) == {"schema_version": 1}


def test_no_edit_while_the_workflow_runs(monkeypatch, tmp_path):
    m = _manager(monkeypatch, tmp_path, _plan(tmp_path / "p.json"))
    monkeypatch.setattr(wm.WorkflowManager, "busy", property(lambda self: True))
    with pytest.raises(RuntimeError, match="running"):
        m.set_value_origin("lv/alpha", "measured")


@pytest.mark.integration
def test_an_edited_value_moves_to_its_origins_box(client, requires_simulation, tmp_path):
    """C-0012: recording a value's origin from its box moves it to that origin's box."""
    import shutil

    from ca_imports import ca_from
    from conftest import LV_MODEL_PATH, LV_OBS_DATA_PATH

    builder = ca_from("calibration_workflow", "WorkflowBuilder")
    cellml_model = ca_from("calibration_workflow", "cellml_model")
    for f in (LV_MODEL_PATH, LV_OBS_DATA_PATH):
        shutil.copy(f, tmp_path)
    values = tmp_path / "values.json"
    _write(values, {"Lotka_Volterra_module": {
        "alpha": {"value": "4.0", "units": "dimensionless", "origin": "measured",
                  "data_reference": "smith2001; Table 2"}}})
    (tmp_path / "params.json").write_text(json.dumps({"version": 1, "params": [
        {"targets": ["Lotka_Volterra_module/gamma"], "min": 0.1, "max": 10.0}]}))
    wf = builder("origins", target=cellml_model(str(tmp_path / LV_MODEL_PATH.name), str(values)),
                 directory=str(tmp_path / "wf"))
    wf.add_calibration("fit", obs_data=str(tmp_path / LV_OBS_DATA_PATH.name),
                       params_for_id=str(tmp_path / "params.json"))
    r = client.post("/api/workflow/load",
                    json={"path": str(wf.write()), "config_outputs_dir": str(tmp_path / "out")})
    assert r.status_code == 200, r.text
    if "target_values" not in r.json()["plan"]:
        pytest.skip("this libcuflynx's plan has no target_values")
    origins = r.json()["value_origins"]
    assert origins["editable"] and "set_by_hand" in origins["origins"]

    def box_of(body, name):
        return next(b["id"] for b in body["value_origins"]["boxes"]
                    if any(v["name"] == f"Lotka_Volterra_module/{name}" for v in b["values"]))

    assert box_of(r.json(), "delta") == "unrecorded"
    r = client.post("/api/workflow/value_origin", json={
        "name": "Lotka_Volterra_module/delta", "origin": "set_by_hand", "reason": "a round number"})
    assert r.status_code == 200, r.text
    assert box_of(r.json(), "delta") == "set_by_hand"
    (row,) = next(b for b in r.json()["value_origins"]["boxes"] if b["id"] == "set_by_hand")["values"]
    assert row["note"] == "a round number"
    # and back: a measured value with its origin removed is "origin not recorded"
    r = client.post("/api/workflow/value_origin", json={"name": "Lotka_Volterra_module/alpha"})
    assert box_of(r.json(), "alpha") == "unrecorded"
    assert "origin" not in json.loads(values.read_text())["Lotka_Volterra_module"]["alpha"]
    r = client.post("/api/workflow/value_origin",
                    json={"name": "Lotka_Volterra_module/alpha", "origin": "guessed"})
    assert r.status_code == 422 and "not an origin" in r.json()["detail"]
