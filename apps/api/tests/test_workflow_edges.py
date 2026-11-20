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
