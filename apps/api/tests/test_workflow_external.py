"""A calibration workflow of an external Python model (CA #653) in the Workflow tab.

A step or target that is an external Python model has no CellML: its tab loads the ``.py``
as an upload does, with the values CA wrote for the step (its ``external_parameters_file``)
as the model's initial values and in every simulation and run of it. Also: the results of
steps that are not calibrations, the run's provenance, and what a send or export does with
an external target. The unit tier fakes CA's calibration_workflow module; the integration
test runs CA's heat1d example for real.
"""

import json
import os
import shutil
import sys
import time
import types
from pathlib import Path

import numpy as np
import pytest

import ca_run_history
import engine as engine_mod
import load_outputs
import main
import user_funcs
import workflow_manager as workflow_mod
from conftest import set_ca_module

MODEL_SRC = '''
class Heat:
    parameters = {"heat/k": 0.4, "heat/u_D": 0.0}
    output_names = ["heat/T"]

    def init_solver(self, config):
        pass

SIM_HELPER = Heat
'''

OBS_DATA = {
    "protocol_info": {"pre_times": [0.0], "sim_times": [[0.5]], "params_to_change": {}},
    "data_items": [{"data_item_name": "T mean", "data_type": "constant", "operation": "mean",
                    "operands": ["heat/T"], "unit": "dimensionless", "weight": 1.0,
                    "value": 0.2, "std": 0.01}],
}

PARAMS_FOR_ID = {"version": 1, "params": [
    {"targets": ["heat/k"], "min": 0.05, "max": 1.0, "param_type": "const",
     "name_for_plotting": "k"}]}

WORKFLOW = {
    "workflow_name": "heat",
    "target": {"external_model_path": "heat_model.py"},
    "steps": [
        {"id": "boundary", "kind": "set",
         "set": [{"parameter": "heat/u_D", "value": 0.1, "reason": "measured"}]},
        {"id": "fit-k", "model": {"external_model_path": "heat_model.py"},
         "obs_data": "heat_obs_data.json", "params_for_id": "heat_k_params_for_id.json",
         "fixed_from": ["boundary"]},
    ],
}


@pytest.fixture
def requires_parameters_file():
    """CA's parameters-file reader, which reads a step's values file."""
    try:
        workflow_mod._ca_from_parameters_file("read_parameters_file")
    except workflow_mod.WorkflowUnavailable:
        pytest.skip("circulatory_autogen's utilities.parameters_file is not importable")


@pytest.fixture
def study(tmp_path):
    """The workflow's folder: the model, its inputs and the workflow file."""
    folder = tmp_path / "heat"
    folder.mkdir()
    (folder / "heat_model.py").write_text(MODEL_SRC)
    (folder / "heat_obs_data.json").write_text(json.dumps(OBS_DATA))
    (folder / "heat_k_params_for_id.json").write_text(json.dumps(PARAMS_FOR_ID))
    (folder / "workflow.json").write_text(json.dumps(WORKFLOW))
    return folder


def _values_file(path: Path, k: float, u_d: float) -> Path:
    """A values file as CA's generate_external writes it (nested JSON)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"schema_version": 1, "heat": {
        "k": {"value": repr(k), "units": "", "data_reference": "step fit-k"},
        "u_D": {"value": repr(u_d), "units": "", "data_reference": "step boundary"}}}))
    return path


class _ExternalModel:
    """Shaped as CA's calibration_workflow.ExternalModel (its prefix rule)."""

    def __init__(self, path):
        stem = os.path.splitext(os.path.basename(path))[0]
        self.prefix = stem[:-len("_model")] if stem.endswith("_model") else stem


def _fake_ca(monkeypatch, study, *, result=None):
    """A stand-in for libcuflynx.calibration_workflow, viewing ``study``'s external model."""
    mod = types.ModuleType("calibration_workflow")
    model = {"external_model_path": str(study / "heat_model.py")}

    def load_workflow(path, module_library_dirs=None):
        return types.SimpleNamespace(name=json.loads(Path(path).read_text())["workflow_name"])

    def plan_workflow(path, module_library_dirs=None):
        return {"workflow_name": "heat", "target": model, "steps": [
            {"id": "boundary", "kind": "set", "model": None},
            {"id": "fit-k", "kind": "calibration", "model": model}]}

    def workflow_status(path, output_dir, module_library_dirs=None):
        return {"steps": {"fit-k": {"status": "done", "stale": False}}, "complete": True}

    def workflow_model(path, view, output_dir, work_dir, module_library_dirs=None):
        if view == "boundary":
            raise ValueError('step "boundary" is a set step, which has no model.')
        values = _values_file(Path(work_dir) / view / "generated_models" / view
                              / f"{view}_parameters.json", 0.25, 0.1)
        return {"view": view, "kind": "target" if view == "target" else "step",
                "model": model, "step_id": "fit-k", "place": "",
                "model_path": str(study / "heat_model.py"), "flat_model_path": None,
                "parameters_path": str(values),
                "obs_data_path": str(study / "heat_obs_data.json"),
                "params_for_id_path": str(study / "heat_k_params_for_id.json"),
                "fixed": [{"model_name": "u_D_heat", "value": 0.1, "from_step": "boundary"}],
                "calibrated": [{"model_name": "k_heat", "value": 0.25, "from_step": "fit-k"}],
                "waiting_for": [], "stale": [], "result": result,
                "param_id_output_dir": None}

    for fn in (load_workflow, plan_workflow, workflow_status, workflow_model):
        setattr(mod, fn.__name__, fn)
    mod.ExternalModel = _ExternalModel
    set_ca_module(monkeypatch, "calibration_workflow", mod)


@pytest.fixture
def opened(client, monkeypatch, study, tmp_path):
    _fake_ca(monkeypatch, study)
    work = tmp_path / "work"
    work.mkdir()
    r = client.post("/api/workflow/load",
                    json={"path": str(study / "workflow.json"), "config_outputs_dir": str(work)})
    assert r.status_code == 200, r.text
    return work


# ---------------------------------------------------------------------------
# an external step's tab
# ---------------------------------------------------------------------------
def test_an_external_step_tab_loads_its_py_with_the_step_s_values(
        client, opened, study, requires_parameters_file):
    r = client.post("/api/workflow/view",
                    json={"view": "fit-k", "config_outputs_dir": str(opened)})
    assert r.status_code == 200, r.text
    body = r.json()
    # loaded as an uploaded .py is, with the step's own obs_data and params_for_id
    assert body["model_format"] == "external_python"
    assert body["model_filename"] == "heat_model.py"
    assert body["obs_data"] and not body["obs_data"].get("error")
    assert body["params_for_id"] and not body["params_for_id"].get("error")
    # the step's values (u_D from the set step, k from the fit), not the class defaults
    variables = client.get(f"/api/models/{body['model_id']}/variables").json()
    assert variables["initial_values"] == {"heat/k": 0.25, "heat/u_D": 0.1}
    # the slider of a calibrated parameter starts at the step's value too
    assert body["params_for_id"]["params"][0]["initial_value"] == pytest.approx(0.25)
    # the values file is kept with the study, and the step's .py is the study's model
    kept = Path(body["external_parameters_file"])
    assert kept.is_file() and str(kept).startswith(str(opened))
    assert json.loads(kept.read_text())["heat"]["u_D"]["value"] == "0.1"
    assert user_funcs.model_source_path(".py", str(opened)).read_text() == MODEL_SRC
    assert body["workflow_view"]["view"] == "fit-k"


def test_an_external_target_tab_loads_too(client, opened, requires_parameters_file):
    r = client.post("/api/workflow/view",
                    json={"view": "target", "config_outputs_dir": str(opened)})
    assert r.status_code == 200, r.text
    assert r.json()["model_format"] == "external_python"
    assert r.json()["workflow_view"]["kind"] == "target"


def test_the_step_s_values_reach_the_live_engine_and_every_run(
        client, opened, monkeypatch, requires_parameters_file):
    body = client.post("/api/workflow/view",
                       json={"view": "fit-k", "config_outputs_dir": str(opened)}).json()
    model_id, kept = body["model_id"], body["external_parameters_file"]
    monkeypatch.setattr(engine_mod.engine, "model_type", engine_mod.EXTERNAL_MODEL_TYPE)
    monkeypatch.setattr(engine_mod.engine, "solver", "external")
    seen = {}

    class Helper:
        def __init__(self, **kwargs):
            seen.update(kwargs)

        def reset_and_clear(self):
            pass

        def update_times(self, *args):
            pass

        def set_param_vals(self, names, vals):
            pass

        def run(self):
            return True

        def get_time(self, include_pre_time=False):
            return [0.0, 0.5]

        def get_results(self, names, flatten=True):
            return [[0.0, 0.1]]

    monkeypatch.setattr(engine_mod.engine, "helper_factory", lambda **kw: Helper(**kw))
    engine_mod.engine.simulate(model_id, body.get("model_path") or "m.py", {}, 0.5, 0.0,
                               ["heat/T"])
    assert seen["solver_info"]["external_parameters_file"] == kept
    # what a calibration, sensitivity, UQ or emulator run of this model is configured with
    assert engine_mod.engine.with_external_parameters({}, model_id) == {
        "external_parameters_file": kept}
    # and no other model's
    assert engine_mod.engine.with_external_parameters({"a": 1}, "another") == {"a": 1}


def test_the_values_file_is_only_given_to_an_external_python_backend():
    engine = engine_mod.SimulationEngine()
    engine.set_external_parameters("m", "/x/values.json")
    engine.model_type = "cellml"
    assert engine.with_external_parameters({}, "m") == {}
    engine.model_type = engine_mod.EXTERNAL_MODEL_TYPE
    assert engine.with_external_parameters({}, "m") == {"external_parameters_file":
                                                        "/x/values.json"}
    engine.set_external_parameters("m", None)
    assert engine.with_external_parameters({}, "m") == {}


def test_a_set_step_has_no_tab(client, opened):
    r = client.post("/api/workflow/view", json={"view": "boundary"})
    assert r.status_code == 422 and "set step" in r.json()["detail"]


# ---------------------------------------------------------------------------
# results of steps that are not calibrations
# ---------------------------------------------------------------------------
def _laplace(directory: Path, prefix: str, mean, cov):
    directory.mkdir(parents=True, exist_ok=True)
    mean_path = directory / f"{prefix}{ca_run_history.LAPLACE_MEAN_SUFFIX}"
    cov_path = directory / f"{prefix}{ca_run_history.LAPLACE_COVARIANCE_SUFFIX}"
    np.save(mean_path, np.asarray(mean, dtype=float))
    np.save(cov_path, np.asarray(cov, dtype=float))
    return str(mean_path), str(cov_path)


def test_the_laplace_reader_gives_each_marginal_as_the_uq_panel_plots_it(tmp_path):
    mean_path, cov_path = _laplace(tmp_path, "ia", [0.25, 0.1], [[4e-4, 1e-5], [1e-5, 1e-4]])
    dists = ca_run_history.laplace_distributions(mean_path, cov_path, ["heat/k"])
    assert [d["qname"] for d in dists] == ["heat/k", "parameter 2"]
    k = dists[0]
    assert k["mean"] == pytest.approx(0.25) and k["std"] == pytest.approx(0.02)
    assert k["q05"] == pytest.approx(0.25 - 1.6449 * 0.02, rel=1e-4)
    assert k["q95"] == pytest.approx(0.25 + 1.6449 * 0.02, rel=1e-4)
    assert len(k["bins"]) == len(k["counts"]) + 1 == ca_run_history.NUM_BINS + 1
    # the density over mean +- 4 std holds almost every draw, and peaks in the middle
    assert 99_900 < sum(k["counts"]) <= 100_000
    assert max(k["counts"]) in k["counts"][18:22]
    assert ca_run_history.laplace_distributions(str(tmp_path / "no.npy"), cov_path) is None


def test_a_laplace_with_mismatched_files_is_not_read(tmp_path):
    mean_path, cov_path = _laplace(tmp_path, "ia", [0.25, 0.1], [[1.0]])
    assert ca_run_history.laplace_distributions(mean_path, cov_path) is None


def test_step_outputs_read_an_identifiability_and_an_emulation_step(tmp_path):
    assert load_outputs.step_outputs(None) is None
    assert load_outputs.step_outputs({"param_id_output_dir": "/x"}) is None
    mean_path, cov_path = _laplace(tmp_path / "ia", "fit-k", [0.25], [[4e-4]])
    found = load_outputs.step_outputs({
        "parameters": ["k"],
        "laplace": {"mean_path": mean_path, "covariance_path": cov_path}})
    assert found["uq"]["method"] == "laplace"
    assert found["uq"]["params"][0]["qname"] == "k"
    assert found["found"] == ["Laplace posterior"]
    emulator = tmp_path / "emu"
    emulator.mkdir()
    (emulator / ca_run_history.EMULATOR_METADATA_FILE).write_text(json.dumps({"model_name": "gp"}))
    found = load_outputs.step_outputs({"emulator_dir": str(emulator)})
    assert found["emulator"]["dir"] == str(emulator)
    assert found["emulator"]["metadata"]["model_name"] == "gp"
    assert found["found"] == ["emulator"]


def test_a_sensitivity_step_s_indices_are_read_from_its_dir(tmp_path):
    directory = tmp_path / "sensitivity"
    directory.mkdir()
    (directory / "all_outputs_n8_Sobol_indices.csv").write_text(
        "Parameter,S1_T,ST_T\nk,0.6,0.7\n")
    found = load_outputs.step_outputs({"sensitivity_dir": str(directory)})
    assert found["sensitivity"]["sobol"]["indices"]["ST"]["T"]["k"] == pytest.approx(0.7)
    assert found["sensitivity"]["local"] is None
    assert found["found"] == ["sensitivity"]


def test_a_tab_carries_its_step_s_other_results(
        client, monkeypatch, study, tmp_path, requires_parameters_file):
    mean_path, cov_path = _laplace(tmp_path / "ia", "fit-k", [0.25], [[4e-4]])
    _fake_ca(monkeypatch, study, result={
        "parameters": ["k"], "laplace": {"mean_path": mean_path, "covariance_path": cov_path}})
    client.post("/api/workflow/load", json={"path": str(study / "workflow.json"),
                                             "config_outputs_dir": str(tmp_path / "w")})
    body = client.post("/api/workflow/view", json={"view": "fit-k"}).json()
    outputs = body["workflow_view"]["step_outputs"]
    assert outputs["uq"]["params"][0]["mean"] == pytest.approx(0.25)


# ---------------------------------------------------------------------------
# provenance
# ---------------------------------------------------------------------------
def test_the_run_s_provenance_is_served_once_ca_has_written_it(client, opened):
    run_dir = opened / "workflows" / "heat"
    assert client.get("/api/workflow/provenance/provenance.svg").status_code == 404
    run_dir.mkdir(parents=True)
    (run_dir / "provenance.svg").write_text("<svg xmlns='http://www.w3.org/2000/svg'/>")
    (run_dir / "provenance_graph.json").write_text('{"nodes": []}')
    described = client.get("/api/workflow").json()
    assert set(described["provenance"]) == {"svg", "graph"}
    svg = client.get("/api/workflow/provenance/provenance.svg")
    assert svg.status_code == 200 and svg.headers["content-type"].startswith("image/svg+xml")
    assert svg.text.startswith("<svg")
    graph = client.get("/api/workflow/provenance/provenance_graph.json")
    assert graph.status_code == 200 and graph.json() == {"nodes": []}
    # only those two files, never another path in the run directory
    assert client.get("/api/workflow/provenance/workflow_result.json").status_code == 404


# ---------------------------------------------------------------------------
# sending and exporting an external target
# ---------------------------------------------------------------------------
def test_an_external_target_is_not_sent_back_and_says_where_its_values_go(opened):
    files, said = main._calibrated_target()
    assert files is None and said["sent"] == []
    assert "external Python model" in said["reason"]
    assert "heat_calibrated_parameters.json" in said["reason"]


def test_an_external_target_is_not_exported_as_an_archive(client, opened, monkeypatch):
    monkeypatch.setattr(workflow_mod.workflow, "target_model", lambda work_dir: {
        "model": {"external_model_path": "/x/heat_model.py"}, "model_path": "/x/heat_model.py",
        "flat_model_path": None})
    r = client.post("/api/workflow/export", json={"output_dir": str(opened)})
    assert r.status_code == 422 and "external Python model" in r.json()["detail"]


# ---------------------------------------------------------------------------
# write_calibrated
# ---------------------------------------------------------------------------
def test_the_runner_asks_ca_for_the_calibrated_file_only_when_told(monkeypatch):
    import workflow_runner

    calls = []
    fake = types.ModuleType("calibration_workflow")
    fake.run_calibration_workflow = lambda source, **kwargs: calls.append(kwargs) or {}
    set_ca_module(monkeypatch, "calibration_workflow", fake)
    base = {"workflow": "w.json", "output_dir": "/out"}
    workflow_runner.run(base)
    workflow_runner.run({**base, "write_calibrated": True})
    assert "write_calibrated" not in calls[0]
    assert calls[1]["write_calibrated"] is True


RECORDING_RUNNER = r"""
import json, sys
from pathlib import Path
cfg = json.loads(Path(sys.argv[1]).read_text())
Path(cfg["output_dir"], "seen_config.json").write_text(json.dumps(cfg))
print("__WORKFLOW_EVENT__" + json.dumps({"event": "workflow_finished"}), flush=True)
print("__WORKFLOW_DONE__", flush=True)
"""


def test_a_run_passes_write_calibrated_to_the_runner(client, opened, tmp_path):
    runner = tmp_path / "runner.py"
    runner.write_text(RECORDING_RUNNER)
    workflow_mod.workflow.runner_path = str(runner)
    job = client.post("/api/workflow/run", json={"write_calibrated": True}).json()["job_id"]
    deadline = time.time() + 15
    while client.get(f"/api/workflow/{job}/status").json()["state"] == "running":
        assert time.time() < deadline
        time.sleep(0.05)
    seen = json.loads((opened / "workflows" / "heat" / "seen_config.json").read_text())
    assert seen["write_calibrated"] is True


# ---------------------------------------------------------------------------
# CA's heat1d example, for real
# ---------------------------------------------------------------------------
def _heat1d_example() -> Path | None:
    try:
        from ca_imports import ensure_ca_path  # noqa: PLC0415

        ensure_ca_path()
        import libcuflynx  # noqa: PLC0415
    except ImportError:
        return None
    root = Path(libcuflynx.__file__).resolve().parents[2]
    example = root / "funcs_user" / "example_model_external"
    return example if (example / "heat1d_calibration_workflow.json").is_file() else None


@pytest.mark.integration
@pytest.mark.slow
def test_the_heat1d_workflow_runs_and_every_tab_opens(client, requires_simulation, tmp_path):
    example = _heat1d_example()
    if example is None:
        pytest.skip("this circulatory_autogen has no external-model workflow example (CA #653)")
    folder = tmp_path / "heat1d"
    shutil.copytree(example, folder, ignore=shutil.ignore_patterns("__pycache__"))
    path = folder / "heat1d_calibration_workflow.json"
    raw = json.loads(path.read_text())
    # tiny budgets: this checks the plumbing, not the fit
    raw["settings"]["optimiser_options"].update(num_calls_to_function=30, max_patience=15)
    raw["settings"]["UQ_options"].update(num_steps=20, num_walkers=4)
    path.write_text(json.dumps(raw))
    work = tmp_path / "work"
    assert client.post("/api/config", json={"python_path": sys.executable}).status_code == 200
    body = client.post("/api/workflow/load",
                       json={"path": str(path), "config_outputs_dir": str(work)}).json()
    assert [s["id"] for s in body["plan"]["steps"]] == ["boundary", "fit-k", "refine"]

    job = client.post("/api/workflow/run", json={"write_calibrated": True}).json()["job_id"]
    deadline = time.time() + 300
    while (status := client.get(f"/api/workflow/{job}/status").json())["state"] == "running":
        assert time.time() < deadline, "the heat1d workflow did not finish in 5 minutes"
        time.sleep(0.5)
    assert status["state"] == "done", "\n".join(status["lines"][-40:])
    described = client.get("/api/workflow").json()
    assert described["status"]["complete"]
    assert set(described["provenance"]) == {"svg", "graph"}
    assert client.get("/api/workflow/provenance/provenance.svg").status_code == 200
    calibrated = json.loads((folder / "heat1d_calibrated_parameters.json").read_text())
    assert float(calibrated["heat"]["u_D"]["value"]) != 0.0

    for view in ("fit-k", "refine", "target"):
        r = client.post("/api/workflow/view",
                        json={"view": view, "config_outputs_dir": str(work)})
        assert r.status_code == 200, (view, r.text)
        tab = r.json()
        assert tab["model_format"] == "external_python", view
        values = client.get(f"/api/models/{tab['model_id']}/variables").json()["initial_values"]
        merged = {c["model_name"]: c["value"] for c in tab["workflow_view"]["calibrated"]}
        assert values["heat/k"] == pytest.approx(merged["k_heat"]), view
        if view == "fit-k":
            # the set step's boundary value, fixed in the step
            assert values["heat/u_D"] == pytest.approx(0.1)
        # as the tab loads its results: the calibration, and the MCMC run beside it
        wv = tab["workflow_view"]
        found = client.get("/api/working-dir/load", params={
            "dir": wv["results_dir"], "file_prefix": wv["step_id"],
            "run_dir": wv["param_id_output_dir"]}).json()
        assert {"calibration", "uq"} <= set(found["found"]), (view, found["missing"])
        assert found["run_dir"] == wv["param_id_output_dir"]
    assert client.post("/api/workflow/view", json={"view": "boundary"}).status_code == 422
