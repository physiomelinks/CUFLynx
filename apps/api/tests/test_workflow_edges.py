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
    d = we.edge_detail(run, WORKFLOW, "fit-k", "target", "merged")
    assert d["rows"] == [{"name": "heat/k", "value": 0.25, "units": "W", "note": ""}]


def test_a_step_on_the_target_takes_every_merged_value(run):
    d = we.edge_detail(run, WORKFLOW, "target", "sweep", "on_target")
    assert d["rows"] == [{"name": "heat/k", "value": 0.2505, "units": "W",
                          "note": "from refine"}]


def test_the_models_arrow_names_the_model(run):
    d = we.edge_detail(run, WORKFLOW, "model", "fit-k", "model")
    assert d["model"] == {"cellml_model_path": "m.cellml"}
    assert "1 values in it are fixed" in d["note"]


def test_a_point_is_the_set_steps_values(run):
    d = we.edge_detail(run, WORKFLOW, "boundary", "fit-k", "start_from")
    assert d["rows"][0]["name"] == "heat/u_D" and d["note"].startswith("The values boundary sets")
