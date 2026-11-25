"""What passes along an arrow of the Workflow view's chart (``workflow_edges``), read from
CA's records in the shapes CA writes them (taken from the enterocyte and heat1d runs)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import workflow_edges as we

WORKFLOW = {"workflow_name": "w", "target": {"cellml_model_path": "m.cellml"}, "steps": [
    {"id": "boundary", "kind": "set", "set": [{"parameter": "heat/u_D", "value": 0.1,
                                                 "reason": "measured"}]},
    {"id": "fit-k", "fixed_from": ["boundary"], "store_distribution": True},
    {"id": "refine", "priors_from": ["fit-k"]},
    {"id": "sweep", "kind": "sweep", "on_target": True},
]}


def _write(run: Path, step: str, result: dict) -> None:
    (run / step).mkdir(parents=True, exist_ok=True)
    (run / step / "step_result.json").write_text(json.dumps({"step": step, "status": "done",
                                                            **result}))


@pytest.fixture
def run(tmp_path):
    run = tmp_path / "run"
    _write(run, "boundary", {"kind": "set", "set": [
        {"target_parameterisation_name": "heat/u_D", "value": 0.1, "units": "K",
         "reason": "measured"}]})
    _write(run, "fit-k", {"kind": "calibration",
                          "fixed": [{"host_name": "heat/u_D", "value": 0.1, "units": "K",
                                     "from_step": "boundary"}],
                          "calibrated": [{"target_parameterisation_name": "heat/k",
                                          "value": 0.25, "units": "W"}],
                          # a path from before the working dir moved: the step's own is read
                          "distribution_dir": "/gone/fit-k/distribution"})
    (run / "fit-k" / "distribution").mkdir()
    (run / "fit-k" / "distribution" / "stats.json").write_text(json.dumps({"parameters": {
        "k_heat": {"mean": 0.2508, "sd": 0.01, "quantiles": {"0.025": 0.232, "0.975": 0.27}}}}))
    _write(run, "refine", {"kind": "calibration", "priors": [
        {"step": "fit-k", "kind": "mvnormal", "inflate": 1.0, "parameters": ["k_heat"]}]})
    (run / "workflow_result.json").write_text(json.dumps({"merged_parameters": [
        {"parameterisation_name": "heat/k", "value": 0.2505, "units": "W", "from_step": "refine"}]}))
    return str(run)


def test_a_fixed_from_arrow_carries_the_values_the_receiving_step_fixed(run):
    d = we.edge_detail(run, WORKFLOW, "boundary", "fit-k", "fixed_from")
    assert d["recorded"] is True
    assert d["rows"] == [{"name": "heat/u_D", "value": 0.1, "units": "K", "note": ""}]


def test_a_fixed_from_arrow_before_the_step_ran_shows_the_sources_values(tmp_path):
    d = we.edge_detail(str(tmp_path), WORKFLOW, "boundary", "fit-k", "fixed_from")
    assert d["recorded"] is False
    assert d["rows"][0]["name"] == "heat/u_D" and d["rows"][0]["value"] == 0.1
    assert "has not run with them yet" in d["note"]


def test_a_priors_from_arrow_carries_the_stored_distribution(run):
    d = we.edge_detail(run, WORKFLOW, "fit-k", "refine", "priors_from")
    assert d["prior"] == {"kind": "mvnormal", "inflate": 1.0, "parameters": ["k_heat"]}
    assert d["distribution"] == {"k_heat": {"mean": 0.2508, "sd": 0.01, "q025": 0.232,
                                            "q975": 0.27}}


def test_the_arrow_into_the_target_carries_the_calibrated_values(run):
    """Its own calibrated values, and those it carries on from the steps whose values it
    fixed (which then need no arrow of their own to the target)."""
    d = we.edge_detail(run, WORKFLOW, "fit-k", "target", "merged")
    assert d["rows"] == [
        {"name": "heat/k", "value": 0.25, "units": "W", "note": ""},
        {"name": "heat/u_D", "value": 0.1, "units": "K", "note": "from boundary, fixed in fit-k"}]


def test_a_step_on_the_target_takes_every_merged_value(run):
    d = we.edge_detail(run, WORKFLOW, "target", "sweep", "on_target")
    assert d["rows"] == [{"name": "heat/k", "value": 0.2505, "units": "W",
                          "note": "from refine"}]


def test_the_models_arrow_names_the_model(run):
    d = we.edge_detail(run, WORKFLOW, "model", "fit-k", "model")
    assert d["model"] == {"cellml_model_path": "m.cellml"}
    assert "1 values in it are fixed" in d["note"]


def test_the_values_by_origin_are_cas_provenance_boxes(tmp_path):
    graph = tmp_path / "provenance_graph.json"
    graph.write_text(json.dumps({"schema_version": 3, "nodes": [
        {"id": "__source__", "kind": "source", "counts": {"unclassified": 73, "measured": 0}},
        {"id": "__set_by_hand__", "kind": "values", "title": "Values set by hand",
         "mark": "set_by_hand", "parameters": [
             {"name": "v_ref_Kir_Kir", "value": 775.4, "units": "fmol_per_mm2_per_s",
              "origin": "set_by_hand", "reason": "a normalisation of the Na flux signal"}]},
        {"id": "kir", "kind": "step"},
    ], "edges": [{"from": "__set_by_hand__", "to": "__target__", "style": "given"}]}))
    d = we.value_origins(str(graph))
    assert d["model_counts"] == {"unclassified": 73}
    (box,) = d["boxes"]
    assert box["mark"] == "set_by_hand" and box["to"] == ["target"]
    assert box["values"] == [{"name": "v_ref_Kir_Kir", "value": 775.4,
                              "units": "fmol_per_mm2_per_s",
                              "note": "a normalisation of the Na flux signal"}]


def test_given_values_into_the_model_source_go_to_the_charts_model_box(tmp_path):
    """CA #692: a values box's edges end at __source__ when a step is generated from the
    model source (the values are in it, so every step's model has them)."""
    graph = tmp_path / "provenance_graph.json"
    graph.write_text(json.dumps({"schema_version": 3, "nodes": [
        {"id": "__source__", "kind": "source"},
        {"id": "__set_by_hand__", "kind": "values", "mark": "set_by_hand", "parameters": []},
    ], "edges": [{"from": "__set_by_hand__", "to": "__source__", "style": "given"}]}))
    (box,) = we.value_origins(str(graph))["boxes"]
    assert box["to"] == ["model"]


def test_a_graph_of_a_schema_not_read_here_gives_no_boxes(tmp_path):
    graph = tmp_path / "provenance_graph.json"
    graph.write_text(json.dumps({"schema_version": 99, "nodes": [{"id": "x", "kind": "values"}]}))
    assert we.value_origins(str(graph)) == {"boxes": [], "model_counts": {}}
    assert we.value_origins(None) == {"boxes": [], "model_counts": {}}


def _value(name, origin=None, calibrated_by=(), **extra):
    return dict({"name": name, "value": 1.0, "units": "s", "origin": origin,
                 "data_reference": "", "reason": "", "calibrated_by": list(calibrated_by)}, **extra)


def test_every_value_of_the_target_is_in_one_box_by_its_origin():
    """Q-0053: a value whose origin is recorded stays in that origin's box when a step
    calibrates it, marked with the step; one with no origin (or inherited) that a step
    calibrates is "to be calibrated"; one with none that nothing calibrates is shown too."""
    plan = {"target_values": [
        _value("m/lit", "measured", ["fit-a"], data_reference="smith2001; Table 2"),
        _value("m/hand", "set_by_hand", reason="a round number"),
        _value("m/data", "derived", data_reference="derive_k"),
        _value("m/earlier", "calibrated"),
        _value("m/inh", "inherited"),
        _value("m/inh_fit", "inherited", ["fit-b"]),
        _value("m/free", None, ["fit-a", "fit-b"]),
        _value("m/none"),
    ]}
    boxes = {b["id"]: b for b in we.parameter_origins(plan)["boxes"]}
    assert list(boxes) == ["set_by_hand", "derived", "measured", "calibrated", "to_calibrate",
                           "inherited", "unrecorded"]
    names = {k: [v["name"] for v in b["values"]] for k, b in boxes.items()}
    assert names["measured"] == ["m/lit"] and names["unrecorded"] == ["m/none"]
    assert names["to_calibrate"] == ["m/inh_fit", "m/free"] and names["inherited"] == ["m/inh"]
    assert sum(len(n) for n in names.values()) == len(plan["target_values"])
    (lit,) = boxes["measured"]["values"]
    assert lit["calibrated_by"] == ["fit-a"] and lit["note"] == "smith2001; Table 2"
    assert boxes["set_by_hand"]["values"][0]["note"] == "a round number"
    # the values are in the model's parameters; those to be calibrated go to their steps
    assert boxes["measured"]["to"] == ["model"]
    assert boxes["to_calibrate"]["to"] == ["fit-b", "fit-a"]


def test_a_plan_without_target_values_leaves_the_boxes_to_the_provenance_graph():
    assert we.parameter_origins({"steps": []}) is None and we.parameter_origins(None) is None
    assert we.parameter_origins({"target_values": []}) == {"boxes": [], "model_counts": {},
                                                           "editable": False}


def test_each_value_carries_what_its_form_edits():
    """The box's form edits a value's origin, data reference and reason, in the target's
    parameters file: editable only when the plan names one."""
    plan = {"target_parameters": "/w/lv_parameters.json", "target_values": [
        _value("m/hand", "set_by_hand", reason="a round number", data_reference="notes.md"),
        _value("m/none")]}
    d = we.parameter_origins(plan)
    assert d["editable"] is True
    (hand,) = d["boxes"][0]["values"]
    assert (hand["origin"], hand["data_reference"], hand["reason"]) == (
        "set_by_hand", "notes.md", "a round number")
    assert d["boxes"][1]["values"][0]["origin"] is None
    assert we.parameter_origins(dict(plan, target_parameters=None))["editable"] is False


@pytest.mark.integration
def test_the_charts_boxes_hold_every_constant_of_a_cellml_target(client, requires_simulation,
                                                                 tmp_path):
    """Before any run, CA's plan boxes every constant of the target by its origin."""
    import shutil

    from ca_imports import ca_from
    from conftest import LV_MODEL_PATH, LV_OBS_DATA_PATH

    builder = ca_from("calibration_workflow", "WorkflowBuilder")
    cellml_model = ca_from("calibration_workflow", "cellml_model")
    for f in (LV_MODEL_PATH, LV_OBS_DATA_PATH):
        shutil.copy(f, tmp_path)
    (tmp_path / "values.json").write_text(json.dumps({"schema_version": 1, "Lotka_Volterra_module": {
        "alpha": {"value": "4.0", "units": "dimensionless", "origin": "measured",
                  "data_reference": "smith2001; Table 2"},
        "beta": {"value": "0.3", "units": "dimensionless", "origin": "set_by_hand",
                 "reason": "a round number"}}}))
    (tmp_path / "params.json").write_text(json.dumps({"version": 1, "params": [
        {"targets": [f"Lotka_Volterra_module/{p}"], "min": 0.1, "max": 10.0}
        for p in ("alpha", "gamma")]}))
    model = cellml_model(str(tmp_path / LV_MODEL_PATH.name), str(tmp_path / "values.json"))
    wf = builder("origins", target=model, directory=str(tmp_path / "wf"))
    wf.add_calibration("fit", obs_data=str(tmp_path / LV_OBS_DATA_PATH.name),
                       params_for_id=str(tmp_path / "params.json"))
    r = client.post("/api/workflow/load",
                    json={"path": str(wf.write()), "config_outputs_dir": str(tmp_path / "out")})
    assert r.status_code == 200, r.text
    if "target_values" not in r.json()["plan"]:
        pytest.skip("this libcuflynx's plan has no target_values")
    boxes = {b["id"]: b for b in r.json()["value_origins"]["boxes"]}
    names = {k: [v["name"].partition("/")[2] for v in b["values"]] for k, b in boxes.items()}
    assert names["set_by_hand"] == ["beta"] and names["measured"] == ["alpha"]
    assert boxes["measured"]["values"][0]["calibrated_by"] == ["fit"]
    assert names["to_calibrate"] == ["gamma"] and boxes["to_calibrate"]["to"] == ["fit"]
    assert sorted(names["unrecorded"]) == ["delta", "u_alpha", "u_gamma"]


def test_a_point_is_the_set_steps_values(run):
    d = we.edge_detail(run, WORKFLOW, "boundary", "fit-k", "start_from")
    assert d["rows"][0]["name"] == "heat/u_D" and d["note"].startswith("The values boundary sets")


COMBINED = {"workflow_name": "w", "target": {"cellml_model_path": "m.cellml"}, "steps": [
    {"id": "fit-a"}, {"id": "fit-b"},
    {"id": "best", "kind": "combine", "combine_from": ["fit-a", "fit-b"],
     "combine": {"method": "best_fit"}},
]}


def test_a_combined_arrow_says_whether_the_combine_step_chose_its_values(tmp_path):
    """CA's combine step (CA #685) records the costs it compared and the input it chose."""
    run = tmp_path / "run"
    for step, k in (("fit-a", 0.3), ("fit-b", 0.4)):
        _write(run, step, {"kind": "calibration", "best_cost": k, "calibrated": [
            {"target_parameterisation_name": "heat/k", "value": k, "units": "W"}]})
    d = we.edge_detail(run, COMBINED, "fit-a", "best", "combine_from")
    assert d["rows"][0]["value"] == 0.3 and "has not run yet" in d["note"]
    _write(run, "best", {"kind": "combine", "method": "best_fit", "chosen_from": "fit-a",
                         "best_cost": 0.3, "costs": {"fit-a": 0.3, "fit-b": 0.4}})
    assert we.edge_detail(run, COMBINED, "fit-a", "best", "combine_from")["note"] == (
        "best chose these: fit-a has the lowest cost (cost 0.3).")
    assert we.edge_detail(run, COMBINED, "fit-b", "best", "combine_from")["note"] == (
        "best chose fit-a's values; fit-b's cost (cost 0.4) is higher.")
