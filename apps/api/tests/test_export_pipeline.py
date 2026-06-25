"""Unit tests for the pipeline-export assembly (yaml + scripts)."""

import ast
import json
import math

import export_pipeline as ep
import pytest
import yaml
from conftest import LV_MODEL_PATH, LV_OBS_DATA_PATH, LV_PARAMS_CSV_PATH, upload_model


def _ui(**over):
    base = dict(
        file_prefix="3compartment",
        model_type="casadi_python",
        solver="casadi_integrator",
        solver_info={"method": "semi_implicit_euler"},
        dt=0.01,
        pre_time=0.0,
        sim_time=2.0,
        model_file="3compartment.cellml",
        obs_file="3compartment_obs_data.json",
        params_for_id_file="3compartment_params_for_id.csv",
        calibration={"param_id_method": "sp_minimize", "gradient_method": "AD"},
        sensitivity={"method": "local"},
        uq={"num_steps": 500},
        enabled={"do_simulation": True, "do_calibration": True, "do_sensitivity": True},
    )
    base.update(over)
    return ep.build_user_inputs(**base)


def test_build_user_inputs_maps_settings_and_is_yaml_serialisable():
    ui = _ui()
    assert ui["file_prefix"] == "3compartment"
    assert ui["model_type"] == "casadi_python"
    assert ui["solver_info"]["solver"] == "casadi_integrator"  # solver injected
    assert ui["param_id_method"] == "sp_minimize"
    assert ui["do_ad"] is True  # gradient_method AD -> do_ad
    # relative resource paths (bundle is self-contained)
    assert ui["resources_dir"] == "resources"
    assert ui["param_id_obs_path"] == "resources/3compartment_obs_data.json"
    assert ui["model_file"] == "3compartment.cellml"
    yaml.safe_dump(ui)  # must round-trip through yaml


def test_enablement_flags_default_and_override():
    ui = _ui(enabled={"do_sensitivity": True})
    assert ui["do_simulation"] is True  # default on
    assert ui["do_sensitivity"] is True
    assert ui["do_calibration"] is False  # default off
    assert ui["do_mcmc"] is False and ui["do_ia"] is False


def test_do_ad_false_for_fd():
    ui = _ui(calibration={"gradient_method": "FD"})
    assert ui["do_ad"] is False


def test_pipeline_script_is_valid_python_and_gates_each_stage():
    src = ep.render_pipeline_script()
    ast.parse(src)  # valid python
    # loads the dated yaml, and gates every stage on a do_* flag
    assert "user_inputs_*.yaml" in src
    for flag in ("do_simulation", "do_sensitivity", "do_calibration", "do_mcmc"):
        assert f'cfg.get("{flag}")' in src
    # drives CA via the tutorial's init_from_dict idiom (not a custom builder)
    assert "init_from_dict" in src
    assert "build_inp_data_dict" in src
    assert "CVS0DParamID.init_from_dict" in src
    assert "SensitivityAnalysis.init_from_dict" in src
    assert "get_simulation_helper_from_inp_data_dict" in src
    # UQ actually runs MCMC / Laplace (not a stub)
    assert "run_mcmc()" in src and "IdentifiabilityAnalysis.init_from_dict" in src
    assert "ensure_mle_cost_type_for_bayesian_inner" in src


def test_plotting_script_is_valid_python_with_three_plot_kinds():
    src = ep.render_plotting_script()
    ast.parse(src)
    assert "def plot_outputs" in src  # output traces
    assert "def plot_progress" in src  # cost/param vs generation
    assert "def plot_analysis" in src  # sensitivity / UQ
    assert "set_yscale" in src  # log-y cost, mirrors ProgressPanel


def _setup_lv(client):
    model_id = upload_model(client, LV_MODEL_PATH)["model_id"]
    obs = json.loads(LV_OBS_DATA_PATH.read_text())
    assert client.post("/api/obs_data/upload", json={"model_id": model_id, "obs_data": obs}).status_code == 200
    with open(LV_PARAMS_CSV_PATH, "rb") as fh:
        r = client.post(f"/api/params_for_id/upload?model_id={model_id}",
                        files={"file": (LV_PARAMS_CSV_PATH.name, fh, "text/csv")})
    assert r.status_code == 200
    return model_id


def test_export_pipeline_writes_self_contained_folder(client, tmp_path):
    model_id = _setup_lv(client)
    resp = client.post("/api/export/pipeline", json={
        "model_id": model_id,
        "file_prefix": "lotka_volterra",  # loaded filename stem, not <model name>
        "sim_time": 2.0,
        "calibration": {"param_id_method": "genetic_algorithm"},
        "enabled": {"do_simulation": True, "do_calibration": True},
        "config_outputs_dir": str(tmp_path),
    })
    assert resp.status_code == 200, resp.text
    body = resp.json()
    export_dir = body["export_dir"]
    import os
    # The bundle is self-contained: script(s), dated yaml, and copied resources.
    assert os.path.isfile(os.path.join(export_dir, "run_pipeline.py"))
    assert os.path.isfile(os.path.join(export_dir, "plot_outputs.py"))
    yaml_files = [f for f in os.listdir(export_dir) if f.startswith("user_inputs_") and f.endswith(".yaml")]
    assert yaml_files, "dated user_inputs yaml missing"
    ui = yaml.safe_load(open(os.path.join(export_dir, yaml_files[0])))
    assert ui["do_calibration"] is True and ui["do_simulation"] is True
    assert ui["param_id_obs_path"] == "resources/obs_data.json"
    # Uses the supplied file_prefix for the model file, not the internal model name.
    assert ui["file_prefix"] == "lotka_volterra"
    assert ui["model_file"] == "lotka_volterra.cellml"
    # Model laid out where circulatory_autogen resolves model_path; obs/params in resources/.
    assert os.path.isfile(os.path.join(export_dir, "generated_models", "lotka_volterra", ui["model_file"]))
    res = os.path.join(export_dir, "resources")
    assert os.path.isfile(os.path.join(res, "obs_data.json"))
    assert os.path.isfile(os.path.join(res, "params_for_id.csv"))


def test_export_pipeline_rejects_relative_outputs_dir(client):
    model_id = upload_model(client, LV_MODEL_PATH)["model_id"]
    resp = client.post("/api/export/pipeline", json={
        "model_id": model_id, "config_outputs_dir": "relative/dir",
    })
    assert resp.status_code == 422


def test_export_plotting_writes_script(client, tmp_path):
    resp = client.post("/api/export/plotting", json={"config_outputs_dir": str(tmp_path)})
    assert resp.status_code == 200, resp.text
    import os
    assert os.path.isfile(resp.json()["path"])
    assert resp.json()["path"].endswith("plot_outputs.py")


# ---------------------------------------------------------------------------
# End-to-end: full CUFLynx pipeline (load 3 files -> export -> run -> check)
# ---------------------------------------------------------------------------
from conftest import RESOURCES_DIR  # noqa: E402

C3_MODEL_PATH = RESOURCES_DIR / "3compartment_flat.cellml"
C3_OBS_DATA_PATH = RESOURCES_DIR / "3compartment_obs_data.json"
C3_PARAMS_CSV_PATH = RESOURCES_DIR / "3compartment_params_for_id.csv"


def _setup_3compartment(client):
    """Load the model + obs_data + params_for_id (the three files) into the app."""
    model_id = upload_model(client, C3_MODEL_PATH)["model_id"]
    obs = json.loads(C3_OBS_DATA_PATH.read_text())
    assert client.post(
        "/api/obs_data/upload", json={"model_id": model_id, "obs_data": obs}
    ).status_code == 200
    with open(C3_PARAMS_CSV_PATH, "rb") as fh:
        r = client.post(
            f"/api/params_for_id/upload?model_id={model_id}",
            files={"file": (C3_PARAMS_CSV_PATH.name, fh, "text/csv")},
        )
    assert r.status_code == 200
    return model_id


@pytest.mark.integration
def test_export_pipeline_simulation_runs_and_honors_obs_protocol(client, requires_casadi, tmp_path):
    """Full pipeline: load the three files, set a casadi_python backend, export the
    reproducible python bundle, run its simulation stage, and check the outputs.

    Asserts, end to end:
      * the generated script runs to completion and writes simulation.json with the
        obs_data operand traces, all finite (the casadi helper has no get_time, so the
        export must fall back to the 'time' variable — regression for that crash);
      * the simulation window is driven by the obs_data protocol_info (sim_time=2),
        *not* the yaml times we deliberately export wrong (sim_time=10) — regression
        for the simulation stage ignoring protocol_info.
    """
    import os
    import subprocess
    import sys

    import engine as engine_mod

    model_id = _setup_3compartment(client)
    body = client.post(
        "/api/config",
        json={
            "generated_model_format": "casadi_python",
            "solver": "casadi_integrator",
            "solver_info": {"method": "cvodes"},
        },
    ).json()
    assert body["generated_model_format"] == "casadi_python"

    # Export with deliberately wrong sim/pre times; obs_data protocol_info (pre=10,
    # sim=2) must take precedence in the simulation stage.
    resp = client.post("/api/export/pipeline", json={
        "model_id": model_id,
        "file_prefix": "3compartment_flat",
        "pre_time": 0.0,
        "sim_time": 10.0,
        "enabled": {"do_simulation": True},
        "config_outputs_dir": str(tmp_path),
    })
    assert resp.status_code == 200, resp.text
    export_dir = resp.json()["export_dir"]

    ca_src = engine_mod._circulatory_autogen_src()
    proc = subprocess.run(
        [sys.executable, "run_pipeline.py", "--ca-src", ca_src],
        cwd=export_dir, capture_output=True, text=True, timeout=600,
    )
    assert proc.returncode == 0, f"pipeline failed:\n{proc.stdout}\n{proc.stderr}"

    sim_path = os.path.join(export_dir, "output", "simulation.json")
    assert os.path.isfile(sim_path), "simulation.json not written"
    sim = json.loads(open(sim_path).read())
    t, outputs = sim["time"], sim["outputs"]

    # Window comes from obs_data protocol_info (sim_time=2), not the yaml's 10.
    assert abs((t[-1] - t[0]) - 2.0) < 0.2, f"sim window {t[-1]-t[0]:.2f}s != obs sim_time 2s"

    # The obs_data operand traces are present and finite.
    obs = json.loads(C3_OBS_DATA_PATH.read_text())
    operands = {op for item in obs["data_items"] for op in item["operands"]}
    for op in operands:
        assert op in outputs, f"obs operand {op!r} missing from simulation outputs"
        series = outputs[op]
        assert len(series) > 0 and all(math.isfinite(v) for v in series), f"{op} not finite"

    # Aortic pressure is a sensible pulsatile trace (max above min).
    u = outputs["aortic_root/u"]
    assert max(u) > min(u), "aortic pressure is flat — simulation did not run a pulse"
