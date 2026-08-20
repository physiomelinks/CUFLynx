"""Unit tests for the pipeline-export assembly (yaml + scripts)."""

import ast
import json
import math
import os
from pathlib import Path

import export_pipeline as ep
import pytest
import yaml
from conftest import (
    LV_MODEL_PATH,
    LV_OBS_DATA_PATH,
    LV_PARAMS_CSV_PATH,
    upload_model,
)


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
    assert ui["do_uq"] is False and ui["do_ia"] is False


def test_do_ad_false_for_fd():
    ui = _ui(calibration={"gradient_method": "FD"})
    assert ui["do_ad"] is False


def test_pipeline_script_is_valid_python_and_gates_each_stage():
    src = ep.render_pipeline_script()
    ast.parse(src)  # valid python
    # loads the dated yaml, and gates every stage on a do_* flag
    assert "user_inputs_*.yaml" in src
    for flag in ("do_simulation", "do_sensitivity", "do_calibration"):
        assert f'cfg.get("{flag}")' in src
    # UQ is gated through a helper because it accepts both the pre-rename do_mcmc and the
    # current do_uq, so an export made by an older CUFLynx still runs.
    assert "_do_uq(cfg)" in src
    assert 'cfg.get("do_uq", cfg.get("do_mcmc", False))' in src
    # drives CA via the tutorial's init_from_dict idiom (not a custom builder)
    assert "init_from_dict" in src
    assert "build_inp_data_dict" in src
    assert "CVS0DParamID.init_from_dict" in src
    assert "SensitivityAnalysis.init_from_dict" in src
    assert "get_simulation_helper_from_inp_data_dict" in src
    # UQ actually runs MCMC / Laplace (not a stub). run_UQ is CA's name since
    # CA #392 (CUFLynx #217); run_mcmc stays as the fallback for an older CA.
    assert "run_UQ(" in src and "run_mcmc()" in src
    assert "IdentifiabilityAnalysis.init_from_dict" in src
    assert "ensure_mle_cost_type_for_bayesian_inner" in src


def test_the_simulation_stage_asks_for_its_outputs_once():
    """CA publishes a combined accessor and uses it itself; the stage used to
    call get_all_variable_names + get_results and branch on get_time (#217)."""
    src = ep.render_pipeline_script()

    assert "get_all_results_dict()" in src
    assert "get_all_variable_names" not in src
    assert 'hasattr(sim_helper, "get_time")' not in src


def test_every_stage_reports_in_circulatory_autogens_own_formats():
    """No CUFLynx-authored results format is written anywhere (#210).

    Traces go into the ``all_outputs_*.npz`` CA already writes for a best fit,
    posteriors into a plain ``.npy`` of samples, and the sensitivity stage writes
    nothing at all because CA has already written its indices CSV. A run
    directory produced by CA's own scripts then plots exactly like one produced
    here.
    """
    src = ep.render_pipeline_script()

    assert "def save_all_outputs(" in src
    assert "all_outputs_exp_%d.npz" in src
    assert "def save_uq_samples(" in src
    assert "uq_posterior_samples.npy" in src
    # Nothing writes a JSON summary any more.
    for gone in ("write_stage(", "simulation.json", "sensitivity.json",
                 "uq.json", "results.json"):
        assert gone not in src, gone


def test_the_sensitivity_stage_leaves_cas_indices_alone():
    """It used to write a second summary beside CA's own indices CSV, which is a
    format to keep in step for no gain (#210)."""
    src = ep.render_pipeline_script()

    assert "def sobol_indices(" not in src
    assert "load_sobol_indices()" not in src


def test_uq_reuses_the_calibration_engine():
    """Building a second CVS0DParamID for Laplace compiles the model again
    (#216); the calibration's engine is exactly what it needs."""
    src = ep.render_pipeline_script()

    assert "calibrated = param_id" in src
    assert "cvs = calibrated or CVS0DParamID.init_from_dict(uq_inp)" in src


def test_the_simulation_helper_is_released_before_the_next_stage():
    """It holds a compiled model and every stage below builds its own (#216)."""
    src = ep.render_pipeline_script()

    assert "close_simulation()" in src


def test_plotting_script_is_valid_python_with_every_plot_kind():
    """Each figure is drawn by its own function in the file the user edits.

    Four, not five: a simulation-only run leaves the same ``all_outputs`` npz a
    calibration does, so plot_best_fit draws both and the separate
    "simulation outputs" figure went with the JSON it used to read (#210).
    """
    src = ep.render_plotting_script()
    ast.parse(src)
    assert "def plot_simulation_outputs" not in src
    assert "def plot_best_fit" in src  # every run's traces, calibrated or not
    assert "def plot_progress" in src  # cost/param vs generation
    assert "def plot_error_bars" in src  # per-observable error
    assert "def plot_analysis" in src  # sensitivity / UQ
    assert "set_yscale" in src  # log-y cost, mirrors ProgressPanel


def test_the_plotting_utilities_are_a_separate_file():
    ast.parse(ep.render_plot_utilities())
    assert "import plot_utilities as util" in ep.render_plotting_script()


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
    # plot_outputs imports plot_utilities, so the bundle needs both or the
    # plotting half of it cannot start.
    assert os.path.isfile(os.path.join(export_dir, "plot_utilities.py"))
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
    # JSON, not CSV: an uploaded CSV is converted on the way in, so the bundle
    # always carries the canonical form CA and the editors both read.
    assert os.path.isfile(os.path.join(res, "params_for_id.json"))


# ---------------------------------------------------------------------------
# Null option values (issue #133)
#
# The analysis panels populate their fields from CA's *discovered* option schema
# and copy each option's `default` into its value. CA declares `default: null`
# for `num_calls_to_function` (genetic_algorithm) and `num_walkers` (mcmc), and
# JSON preserves null (an undefined would have been dropped), so the export
# payload arrives with those keys present and null. `int(None)` then raised
# TypeError and the route answered a bare 500 with no detail.
#
# Note the CA-less fallback schema used by the unit tier declares *non-null*
# defaults, so a purely schema-driven test passes here and only fails with a real
# CA present. These cases therefore assert the null contract directly.
# ---------------------------------------------------------------------------
NULLABLE_NUMERIC_SETTINGS = [
    ("calibration", "num_calls_to_function", ("optimiser_options", "num_calls_to_function"), 100),
    ("calibration", "cost_convergence", ("optimiser_options", "cost_convergence"), 0.0001),
    ("calibration", "max_patience", ("optimiser_options", "max_patience"), 10),
    ("sensitivity", "num_samples", ("sa_options", "num_samples"), 256),
    ("uq", "num_steps", ("UQ_options", "num_steps"), 1000),
    ("uq", "num_walkers", ("UQ_options", "num_walkers"), 64),
]


@pytest.mark.parametrize("group,key,path,expected", NULLABLE_NUMERIC_SETTINGS)
def test_null_setting_falls_back_to_default(group, key, path, expected):
    ui = _ui(**{group: {key: None}})
    section, field = path
    assert ui[section][field] == expected


@pytest.mark.parametrize("group,key,path,expected", NULLABLE_NUMERIC_SETTINGS)
def test_blank_setting_falls_back_to_default(group, key, path, expected):
    # A cleared PrimeVue InputNumber can also serialise as an empty string.
    ui = _ui(**{group: {key: ""}})
    section, field = path
    assert ui[section][field] == expected


def test_null_settings_still_yaml_serialisable():
    ui = _ui(
        calibration={"num_calls_to_function": None, "param_id_method": None},
        sensitivity={"num_samples": None, "method": None},
        uq={"num_walkers": None, "cost_type": None},
    )
    # Nulls must not leak through into the yaml CA reads back.
    assert ui["param_id_method"] == "genetic_algorithm"
    assert ui["sa_options"]["method"] == "sobol"
    assert ui["UQ_options"]["cost_type"] == "gaussian_MLE"
    yaml.safe_dump(ui)


def test_malformed_number_raises_a_typed_error_not_a_bare_crash():
    with pytest.raises(ep.ExportPipelineError) as exc:
        _ui(calibration={"num_calls_to_function": "abc"})
    assert "num_calls_to_function" in str(exc.value)


def test_export_keeps_a_json_params_suffix(client, tmp_path):
    """CA branches CSV-vs-JSON on the filename suffix, so an exported JSON
    params doc renamed to .csv would be misparsed by the exported pipeline."""
    model_id = upload_model(client, LV_MODEL_PATH)["model_id"]
    obs = json.loads(LV_OBS_DATA_PATH.read_text())
    assert client.post("/api/obs_data/upload", json={"model_id": model_id, "obs_data": obs}).status_code == 200
    doc = {"params": [{"targets": ["Lotka_Volterra_module/alpha"], "min": 0.1, "max": 2.0}]}
    r = client.post(f"/api/params_for_id/upload?model_id={model_id}",
                    content=json.dumps(doc), headers={"content-type": "application/json"})
    assert r.status_code == 200, r.text

    resp = client.post("/api/export/pipeline", json={
        "model_id": model_id,
        "file_prefix": "lotka_volterra",
        "sim_time": 2.0,
        "enabled": {"do_simulation": True},
        "config_outputs_dir": str(tmp_path),
    })
    assert resp.status_code == 200, resp.text
    export_dir = resp.json()["export_dir"]
    import os
    assert os.path.isfile(os.path.join(export_dir, "resources", "params_for_id.json"))
    yaml_files = [f for f in os.listdir(export_dir) if f.startswith("user_inputs_") and f.endswith(".yaml")]
    ui = yaml.safe_load(open(os.path.join(export_dir, yaml_files[0])))
    # build_inp_data_dict derives params_for_id_path from this at run time.
    assert ui["params_for_id_file"] == "params_for_id.json"


def test_export_route_accepts_null_option_values(client, tmp_path):
    # The end-to-end shape of #133: the payload the UI sends before the user has
    # typed into a required field whose CA default is null.
    model_id = _setup_lv(client)
    resp = client.post("/api/export/pipeline", json={
        "model_id": model_id,
        "file_prefix": "lotka_volterra",
        "sim_time": 2.0,
        "calibration": {"param_id_method": "genetic_algorithm",
                        "num_calls_to_function": None, "cost_convergence": None},
        "uq": {"num_walkers": None},
        "enabled": {"do_simulation": True, "do_calibration": True},
        "config_outputs_dir": str(tmp_path),
    })
    assert resp.status_code == 200, resp.text
    import os
    export_dir = resp.json()["export_dir"]
    name = [f for f in os.listdir(export_dir) if f.startswith("user_inputs_")][0]
    ui = yaml.safe_load(open(os.path.join(export_dir, name)))
    assert ui["optimiser_options"]["num_calls_to_function"] == 100
    assert ui["UQ_options"]["num_walkers"] == 64


def test_export_route_reports_a_malformed_number_as_422(client, tmp_path):
    # Not a 500: the client gets a `detail` it can show the user.
    model_id = _setup_lv(client)
    resp = client.post("/api/export/pipeline", json={
        "model_id": model_id,
        "calibration": {"num_calls_to_function": "abc"},
        "config_outputs_dir": str(tmp_path),
    })
    assert resp.status_code == 422, resp.text
    assert "num_calls_to_function" in resp.json()["detail"]


@pytest.mark.integration
def test_export_accepts_every_default_from_the_real_ca_schema(
    client, requires_simulation, tmp_path
):
    """Drive the export with the payload the panels build from CA's own schema.

    This is the test that would have caught #133: it walks the *discovered*
    option schemas and mirrors CalibrationPanel's `buildSettings()` — each
    option's value starts as its `default`, nulls included. Schema-driven, so a
    future CA option with a null default is caught without editing this test.
    """
    model_id = _setup_lv(client)

    cal = client.get("/api/calibration/defaults").json()
    uq_d = client.get("/api/uq/defaults").json()
    sa_d = client.get("/api/sensitivity/defaults").json()

    def opts(schema):
        return {o["name"]: o.get("default") for o in schema}

    for method in cal.get("methods", []):
        calibration = {"param_id_method": method["value"], **opts(method.get("options", []))}
        resp = client.post("/api/export/pipeline", json={
            "model_id": model_id,
            "file_prefix": "lotka_volterra",
            "sim_time": 2.0,
            "calibration": calibration,
            "sensitivity": opts(sa_d.get("options", [])),
            "uq": opts(uq_d.get("uq_options", [])),
            "enabled": {"do_simulation": True, "do_calibration": True,
                        "do_sensitivity": True, "do_uq": True},
            "config_outputs_dir": str(tmp_path),
        })
        assert resp.status_code == 200, (
            f"export rejected CA's own defaults for {method['value']}: {resp.text}"
        )


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

    # Pass --ca-src only when there really is a checkout to point at. With none
    # configured the bundle now resolves an installed libcuflynx by itself, and handing
    # it a path that does not exist is -- correctly -- an error rather than something to
    # ignore. Before that fix this line passed a non-existent path and the whole test
    # died on it, which is how the gap was found.
    ca_src = engine_mod._circulatory_autogen_src()
    args = ["--ca-src", ca_src] if ca_src and os.path.isdir(ca_src) else []
    proc = subprocess.run(
        [sys.executable, "run_pipeline.py", *args],
        cwd=export_dir, capture_output=True, text=True, timeout=600,
    )
    assert proc.returncode == 0, f"pipeline failed:\n{proc.stdout}\n{proc.stderr}"

    # circulatory_autogen's own npz shape, the same file a calibrated best fit
    # leaves, so the plotting script has one reader for both (#210).
    sim_path = os.path.join(export_dir, "output", "all_outputs_exp_0.npz")
    assert os.path.isfile(sim_path), "all_outputs_exp_0.npz not written"
    import numpy as np

    data = np.load(sim_path, allow_pickle=False)
    t = [float(v) for v in data["time"]]
    outputs = {name: [float(v) for v in data[name]] for name in data.files if name != "time"}

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


# ---------------------------------------------------------------------------
# User-authored funcs travel with the export (CA #303)
#
# An obs_data data_item names its operation and cost_type *by name*. A study
# using a func the user wrote in the GUI is therefore not reproducible unless
# the func file travels with it: the exported run dies on an operation CA has
# never heard of. The bundle used to omit them entirely.
# ---------------------------------------------------------------------------
_OP_SRC = """
def my_export_op(x):
    return max(x)
"""

_COST_SRC = """
def my_export_cost(o, d, s, w):
    return w * (o - d) ** 2
"""

# A modifier is the third kind (CA #383): a params_for_id entry names it, so a
# study using one is no more reproducible without its file than one using a
# custom operation. Decorated, because CA registers only decorated functions.
_MODIFIER_SRC = """
@modifier_func(inputs={}, description="offset every target by theta")
def my_export_modifier(theta, baseline):
    return baseline + theta
"""


def _save_func(client, kind, name, source, out_dir):
    r = client.post(
        f"/api/{kind}_funcs",
        json={"name": name, "source": source, "output_dir": str(out_dir)},
    )
    assert r.status_code == 200, r.text


def test_the_export_carries_user_authored_funcs(client, tmp_path):
    model_id = _setup_lv(client)
    _save_func(client, "operation", "my_export_op", _OP_SRC, tmp_path)
    _save_func(client, "cost", "my_export_cost", _COST_SRC, tmp_path)

    resp = client.post("/api/export/pipeline", json={
        "model_id": model_id,
        "file_prefix": "lv",
        "sim_time": 2.0,
        "enabled": {"do_calibration": True},
        "config_outputs_dir": str(tmp_path),
    })
    assert resp.status_code == 200, resp.text
    body = resp.json()
    export_dir = Path(body["export_dir"])

    # Copied into the bundle under CA's own filenames...
    op_file = export_dir / "resources" / "operation_funcs_user.py"
    cost_file = export_dir / "resources" / "cost_funcs_user.py"
    assert op_file.is_file() and cost_file.is_file()
    assert "my_export_op" in op_file.read_text()
    assert "my_export_cost" in cost_file.read_text()

    # ...and pointed at by relative path in the yaml, so the bundle is portable.
    ui = yaml.safe_load(next(export_dir.glob("user_inputs_*.yaml")).read_text())
    assert ui["operation_funcs_external_path"] == "resources/operation_funcs_user.py"
    assert ui["cost_funcs_external_path"] == "resources/cost_funcs_user.py"

    # Reported back, so the user can see what shipped.
    assert "resources/operation_funcs_user.py" in body["files"]
    assert "resources/cost_funcs_user.py" in body["files"]


def test_the_export_carries_a_user_authored_modifier(client, tmp_path):
    """The third kind travels too. A params_for_id entry names its modifier by
    name, so without the file the exported run dies on a modifier CA has never
    heard of -- exactly the failure the operation/cost copies prevent."""
    model_id = _setup_lv(client)
    _save_func(client, "modifier", "my_export_modifier", _MODIFIER_SRC, tmp_path)

    resp = client.post("/api/export/pipeline", json={
        "model_id": model_id,
        "file_prefix": "lv",
        "sim_time": 2.0,
        "enabled": {"do_calibration": True},
        "config_outputs_dir": str(tmp_path),
    })
    assert resp.status_code == 200, resp.text
    body = resp.json()
    export_dir = Path(body["export_dir"])

    mod_file = export_dir / "resources" / "modifier_funcs_user.py"
    assert mod_file.is_file()
    assert "my_export_modifier" in mod_file.read_text()

    ui = yaml.safe_load(next(export_dir.glob("user_inputs_*.yaml")).read_text())
    assert ui["modifier_funcs_external_path"] == "resources/modifier_funcs_user.py"
    assert "resources/modifier_funcs_user.py" in body["files"]


def test_the_run_script_absolutises_the_modifier_func_path(tmp_path):
    """The generated script matches the external-path keys by suffix, not from a
    fixed list, so the third kind resolves without it having been named there --
    and a fourth would too."""
    script = tmp_path / "run_pipeline.py"
    script.write_text(ep.render_pipeline_script(), encoding="utf-8")
    ns = {"__name__": "exported_pipeline", "__file__": str(script)}
    exec(compile(script.read_text(), str(script), "exec"), ns)  # noqa: S102

    cfg = {
        "file_prefix": "m",
        "model_file": "m.cellml",
        "modifier_funcs_external_path": "resources/modifier_funcs_user.py",
    }
    inp = ns["build_inp_data_dict"](cfg, str(tmp_path))
    got = inp["modifier_funcs_external_path"]
    assert os.path.isabs(got), got
    assert os.path.normpath(got) == os.path.normpath(
        os.path.join(str(tmp_path), "resources", "modifier_funcs_user.py")
    )


def test_the_bundle_lists_the_plotting_module_it_ships(client, tmp_path):
    """plot_outputs.py imports plot_utilities, and the bundle writes both -- but
    only one was reported, which reads as a bundle missing a module."""
    model_id = _setup_lv(client)
    resp = client.post("/api/export/pipeline", json={
        "model_id": model_id,
        "file_prefix": "lv",
        "sim_time": 2.0,
        "config_outputs_dir": str(tmp_path),
    })
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "plot_utilities.py" in body["files"]
    for name in body["files"]:
        assert (Path(body["export_dir"]) / name).is_file(), f"{name} listed but not written"


def test_the_run_script_resolves_the_func_paths_absolutely(tmp_path):
    """Relative in the yaml, absolute in the inp_data_dict — CA is given a path
    inside this export folder, not wherever the funcs lived on the machine that
    produced it."""
    # build_inp_data_dict lives in the *generated* script, so exercise it there:
    # exec the rendered source the way the exported folder would run it.
    script = tmp_path / "run_pipeline.py"
    script.write_text(ep.render_pipeline_script(), encoding="utf-8")
    ns = {"__name__": "exported_pipeline", "__file__": str(script)}
    exec(compile(script.read_text(), str(script), "exec"), ns)  # noqa: S102

    cfg = {
        "file_prefix": "m",
        "model_file": "m.cellml",
        "operation_funcs_external_path": "resources/operation_funcs_user.py",
        "cost_funcs_external_path": "resources/cost_funcs_user.py",
    }
    inp = ns["build_inp_data_dict"](cfg, str(tmp_path))
    for key in ("operation_funcs_external_path", "cost_funcs_external_path"):
        assert os.path.isabs(inp[key]), inp[key]
        # Compared normalised, not by suffix. os.path.join only inserts the native
        # separator *between* its arguments, so on Windows the relative part keeps
        # its forward slashes and the result mixes the two -- which the filesystem
        # accepts, and which is how the neighbouring param_id_obs_path has always
        # resolved. What this asserts is the thing that matters: the path names
        # the file inside this export folder.
        assert os.path.normpath(inp[key]) == os.path.normpath(
            os.path.join(str(tmp_path), cfg[key])
        )


def test_an_export_without_user_funcs_omits_the_keys(client, tmp_path):
    """A study using only built-in operations must not gain paths to files that
    do not exist — CA would fail loading them."""
    model_id = _setup_lv(client)
    resp = client.post("/api/export/pipeline", json={
        "model_id": model_id,
        "file_prefix": "lv",
        "sim_time": 2.0,
        "config_outputs_dir": str(tmp_path),
    })
    assert resp.status_code == 200, resp.text
    ui = yaml.safe_load(next(Path(resp.json()["export_dir"]).glob("user_inputs_*.yaml")).read_text())
    assert "operation_funcs_external_path" not in ui
    assert "cost_funcs_external_path" not in ui


# ---------------------------------------------------------------------------
# The bundle finds circulatory_autogen the same three ways the app does
#
# An exported bundle used to insist on a checkout: `resolve_ca_src` accepted only
# --ca-src / CIRCULATORY_AUTOGEN_SRC and exited 1 otherwise. Since CA #452 put the
# engine on PyPI as `libcuflynx`, a user who ran `pip install libcuflynx`, configured
# no CA directory in the GUI and exported a pipeline got a bundle that could not run,
# asking them for a checkout they had no reason to have.
#
# The app's own resolver (`ca_imports`) already accepted an installed package; this
# script carries a deliberate duplicate of that rule and the duplicate was not updated
# with it. These pin all three arrangements so it cannot drift back.
#
# Driven as a subprocess against the *rendered* script rather than by importing
# `resolve_ca_src`: what ships is the rendered text, and exercising the generator's own
# source would not notice a rendering bug. Each run stops at `load_config` (no yaml
# beside it), which is far enough to prove CA resolution happened and cheap enough for
# the unit tier.
# ---------------------------------------------------------------------------
def _render_bundle_script(tmp_path):
    """Write the rendered run_pipeline.py into an otherwise empty directory."""
    path = tmp_path / "run_pipeline.py"
    path.write_text(ep.render_pipeline_script())
    return path


def _run_bundle(script_path, args=(), env_extra=None, timeout=600):
    """Run the rendered script; return (returncode, combined output).

    CIRCULATORY_AUTOGEN_SRC is stripped unless a case sets it, so a developer's own
    environment cannot silently decide which arrangement is under test.
    """
    import subprocess
    import sys

    env = dict(os.environ)
    env.pop("CIRCULATORY_AUTOGEN_SRC", None)
    env.update(env_extra or {})
    proc = subprocess.run(
        [sys.executable, str(script_path), *args],
        cwd=str(script_path.parent), capture_output=True, text=True, timeout=timeout,
        env=env,
    )
    return proc.returncode, (proc.stdout + proc.stderr)


#: What the old code exited with. Named once so the assertions read as "it no longer
#: says this" rather than repeating a string.
_OLD_REFUSAL = "Pass --ca-src <circulatory_autogen/src> or set CIRCULATORY_AUTOGEN_SRC."


def _libcuflynx_is_installed():
    from ca_imports import installed_package_available

    return installed_package_available()


def test_the_bundle_runs_against_an_installed_libcuflynx_with_no_checkout(tmp_path):
    """No --ca-src, no env var: an installed libcuflynx must be enough.

    The regression test for the reported failure. "It got past CA resolution" is
    asserted by what it complains about *instead* -- the missing yaml, which is the next
    thing main() does. Asserting merely a non-zero exit would pass against the old code
    too, which also exited non-zero.
    """
    if not _libcuflynx_is_installed():
        pytest.skip("libcuflynx is not installed as a package in this environment")
    script = _render_bundle_script(tmp_path)
    rc, out = _run_bundle(script)
    assert _OLD_REFUSAL not in out, (
        "the bundle still demands a checkout though libcuflynx is installed:\n" + out)
    assert "circulatory_autogen was not found" not in out, out
    assert "No user_inputs_*.yaml found" in out, (
        "expected it to get as far as looking for its config; got:\n" + out)


def test_a_named_ca_src_that_does_not_exist_is_an_error_not_a_fallback(tmp_path):
    """--ca-src is an instruction, so a wrong path is reported rather than ignored.

    Quietly running a different engine than the one named would make a typo look like
    success, with results from an engine the user did not choose.
    """
    script = _render_bundle_script(tmp_path)
    rc, out = _run_bundle(script, ["--ca-src", str(tmp_path / "nope")])
    assert rc != 0
    assert "is not a directory" in out, out


def test_a_stale_environment_variable_falls_back_with_a_warning(tmp_path):
    """A bundle outlives the machine it was made on, so a stale env var is ordinary.

    Unlike --ca-src this is ambient rather than an instruction, so it degrades to the
    installed package -- but it has to say so, or the run silently uses an engine other
    than the one the environment names.
    """
    if not _libcuflynx_is_installed():
        pytest.skip("libcuflynx is not installed as a package in this environment")
    script = _render_bundle_script(tmp_path)
    rc, out = _run_bundle(
        script, env_extra={"CIRCULATORY_AUTOGEN_SRC": str(tmp_path / "gone")})
    assert "looking for an installed libcuflynx instead" in out, out
    assert "No user_inputs_*.yaml found" in out, out


def test_with_neither_it_names_both_ways_out(tmp_path):
    """The failure message must offer the install, not only the checkout.

    Simulated by making the package unfindable in the child, since this environment has
    it installed: without that the case is unreachable and the message goes untested --
    and it is the message a user in exactly this situation reads.
    """
    script = _render_bundle_script(tmp_path)
    (tmp_path / "sitecustomize.py").write_text(
        "import importlib.util\n"
        "_real = importlib.util.find_spec\n"
        "def _blocked(name, *a, **k):\n"
        "    if name == 'libcuflynx':\n"
        "        return None\n"
        "    return _real(name, *a, **k)\n"
        "importlib.util.find_spec = _blocked\n"
    )
    rc, out = _run_bundle(script, env_extra={"PYTHONPATH": str(tmp_path)})
    assert rc != 0
    assert "pip install libcuflynx" in out, out
    assert "--ca-src" in out, out


# ---------------------------------------------------------------------------
# The bundle and the GUI agree
#
# The export exists so a study can be reproduced outside CUFLynx. That promise is only
# worth anything if the bundle computes the *same thing* the app did -- and nothing
# checked it: the existing end-to-end test asserts the bundle runs and that its traces
# are finite and pulsatile, which a subtly different simulation would also satisfy.
#
# So this runs the same model twice, once through /api/simulate and once through the
# exported bundle driven by an installed libcuflynx, and compares the traces.
# ---------------------------------------------------------------------------
@pytest.mark.integration
def test_the_bundle_reproduces_the_gui_simulation(client, requires_simulation, tmp_path):
    """Same model, same window: the exported bundle's traces match the app's.

    Compared by interpolating the app's series onto the bundle's time grid -- the two
    choose their own sample points, so comparing element-wise would fail on grid shape
    rather than on physics. The tolerance is relative to each trace's own range, so a
    pressure in Pa and a flow in m^3/s are held to the same standard.

    Run with **no --ca-src**, so this is also the end-to-end proof that a bundle works
    against an installed libcuflynx: before the fix it could not get this far at all.
    """
    import subprocess
    import sys

    import numpy as np

    model_id = _setup_3compartment(client)

    # The window the bundle will use comes from the obs_data protocol_info
    # (pre_time 10, sim_time 2), so ask the app for exactly that or the two are not
    # comparable in the first place.
    obs = json.loads(C3_OBS_DATA_PATH.read_text())
    operands = sorted({op for item in obs["data_items"] for op in item["operands"]})
    gui = client.post("/api/simulate", json={
        "model_id": model_id,
        "params": {},
        "pre_time": 10.0,
        "sim_time": 2.0,
        "outputs": operands,
    })
    assert gui.status_code == 200, gui.text
    gui_body = gui.json()
    gui_time = np.asarray(gui_body["time"], dtype=float)
    gui_outputs = {k: np.asarray(v, dtype=float) for k, v in gui_body["outputs"].items()}

    resp = client.post("/api/export/pipeline", json={
        "model_id": model_id,
        "file_prefix": "3compartment_flat",
        "pre_time": 10.0,
        "sim_time": 2.0,
        "enabled": {"do_simulation": True},
        "config_outputs_dir": str(tmp_path),
    })
    assert resp.status_code == 200, resp.text
    export_dir = resp.json()["export_dir"]

    env = dict(os.environ)
    env.pop("CIRCULATORY_AUTOGEN_SRC", None)
    proc = subprocess.run(
        [sys.executable, "run_pipeline.py"],
        cwd=export_dir, capture_output=True, text=True, timeout=900, env=env,
    )
    assert proc.returncode == 0, (
        "the bundle failed to run against an installed libcuflynx:\n"
        + proc.stdout + proc.stderr
    )

    npz = os.path.join(export_dir, "output", "all_outputs_exp_0.npz")
    assert os.path.isfile(npz), "all_outputs_exp_0.npz not written"
    data = np.load(npz, allow_pickle=False)
    bundle_time = np.asarray(data["time"], dtype=float)

    # The two layers name the same variable differently, which is worth stating rather
    # than quietly papering over: the app answers in CellML component/variable form
    # ("aortic_root/u"), while circulatory_autogen's own npz uses the flattened Myokit
    # spelling with the module suffix intact ("aortic_root_module.u"). Neither is wrong
    # -- they are different layers' conventions -- but a comparison has to bridge them,
    # and doing it by rule rather than by a hand-written table means a model with other
    # components is still covered.
    def _bundle_key(gui_name):
        component, _, variable = gui_name.partition("/")
        for candidate in (f"{component}.{variable}", f"{component}_module.{variable}"):
            if candidate in data.files:
                return candidate
        return None

    pairs = [(op, _bundle_key(op)) for op in operands if op in gui_outputs]
    pairs = [(gui_name, key) for gui_name, key in pairs if key]
    # Every operand must be paired, not merely one of them. Falling to a subset is how
    # this would keep passing while comparing almost nothing -- and a renamed component
    # would do exactly that.
    assert len(pairs) == len(operands), (
        f"only paired {len(pairs)} of {len(operands)} obs operands. app gave "
        f"{sorted(gui_outputs)}; bundle gave {sorted(data.files)[:20]}...")

    # Both windows start wherever their own pre_time ended; compare shape, not offset.
    gui_t = gui_time - gui_time[0]
    bundle_t = bundle_time - bundle_time[0]
    assert abs(gui_t[-1] - bundle_t[-1]) < 0.2, (
        f"the two ran different windows: app {gui_t[-1]:.3f}s, bundle {bundle_t[-1]:.3f}s")

    for gui_name, key in pairs:
        want = np.interp(bundle_t, gui_t, gui_outputs[gui_name])
        got = np.asarray(data[key], dtype=float)
        assert np.all(np.isfinite(got)), f"{key}: bundle produced non-finite values"
        scale = float(np.ptp(want)) or float(np.max(np.abs(want))) or 1.0
        worst = float(np.max(np.abs(got - want))) / scale
        # Measured at exactly 0.0 on all three traces -- same 201 samples, same 2.000 s
        # window, identical values -- because both routes drive the same libcuflynx
        # solver with the same config. So this is a tight bound deliberately: a loose
        # one (2% was the first draft) would let a real divergence through while still
        # looking like an equivalence test. The 1e-6 is headroom for platform floating
        # point, not for a difference in what was computed.
        assert worst < 1e-6, (
            f"{gui_name} (bundle: {key}): the two disagree by {worst:.2e} of the "
            f"trace's range. They agreed exactly when this was written, so any "
            f"visible difference means the bundle now reproduces something other than "
            f"the study the app ran."
        )
