"""Integration tests for the python / casadi_python backends.

The dropped CellML is code-generated to a runnable Python module (via CA's
PythonGenerator) and simulated through the matching solver wrapper. Skipped when
the simulation deps (libcellml / myokit / casadi / CA) aren't installed.
"""

import json

import model_codegen
import numpy as np
import pytest
from conftest import RESOURCES_DIR, LV_MODEL_PATH, upload_model

C3_MODEL_PATH = RESOURCES_DIR / "3compartment_flat.cellml"
C3_OBS_DATA_PATH = RESOURCES_DIR / "3compartment_obs_data.json"


def _set_backend(client, fmt, solver, solver_info=None):
    resp = client.post(
        "/api/config",
        json={"generated_model_format": fmt, "solver": solver, "solver_info": solver_info or {}},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def _simulate(client, model_id):
    return client.post(
        "/api/simulate",
        json={
            "model_id": model_id,
            "params": {},
            "sim_time": 5,
            "pre_time": 0,
            "outputs": ["Lotka_Volterra_module/x"],
        },
    )


@pytest.mark.integration
def test_run_python_model_generates_and_simulates(client, requires_simulation):
    model_id = upload_model(client, LV_MODEL_PATH)["model_id"]
    body = _set_backend(client, "python", "solve_ivp", {"method": "RK45"})
    assert body["generated_model_format"] == "python"

    resp = _simulate(client, model_id)
    assert resp.status_code == 200, resp.text
    out = resp.json()["outputs"]["Lotka_Volterra_module/x"]
    assert len(out) > 0
    assert np.all(np.isfinite(out))
    # The CellML was code-generated to a python module.
    assert (model_codegen.GEN_DIR / f"gen_{model_id}_py.py").is_file()


@pytest.mark.integration
def test_run_casadi_python_model_generates_and_simulates(client, requires_casadi):
    model_id = upload_model(client, LV_MODEL_PATH)["model_id"]
    body = _set_backend(client, "casadi_python", "casadi_integrator", {"method": "cvodes"})
    assert body["generated_model_format"] == "casadi_python"

    resp = _simulate(client, model_id)
    assert resp.status_code == 200, resp.text
    out = resp.json()["outputs"]["Lotka_Volterra_module/x"]
    assert len(out) > 0
    assert np.all(np.isfinite(out))
    # casadi_python uses the CasADi-compatible generated module.
    assert (model_codegen.GEN_DIR / f"gen_{model_id}_casadi.py").is_file()


@pytest.mark.integration
def test_casadi_python_protocol_run_populates_output_traces(client, requires_casadi):
    """Regression: a casadi_python protocol run must return the requested output
    traces. The python/casadi ProtocolRunner exposes var2idx as 'comp/var' (bare
    component), which the output-name resolver must match — otherwise the
    experiments come back with an empty outputs dict (no traces plotted)."""
    model_id = upload_model(client, C3_MODEL_PATH)["model_id"]
    odes = client.get(f"/api/models/{model_id}/variables").json()["odes"]
    obs = json.loads(C3_OBS_DATA_PATH.read_text())
    assert client.post("/api/obs_data/upload", json={"model_id": model_id, "obs_data": obs}).status_code == 200
    _set_backend(client, "casadi_python", "casadi_integrator", {"method": "semi_implicit_euler"})

    outputs = odes[:3]
    resp = client.post("/api/protocol/run", json={"model_id": model_id, "params": {}, "outputs": outputs})
    assert resp.status_code == 200, resp.text
    exp = resp.json()["experiments"][0]
    # Every requested variable must have a finite, non-empty trace.
    for var in outputs:
        assert var in exp["outputs"], f"{var} missing from casadi_python protocol outputs"
        trace = np.array(exp["outputs"][var])
        assert trace.size > 0 and np.all(np.isfinite(trace))


@pytest.mark.integration
def test_casadi_python_runs_with_semi_implicit_euler(client, requires_casadi):
    """The dampened semi-implicit Euler integrator is selectable + runs for
    casadi_python (it is a casadi_python-only method)."""
    model_id = upload_model(client, LV_MODEL_PATH)["model_id"]
    _set_backend(client, "casadi_python", "casadi_integrator", {"method": "semi_implicit_euler"})

    resp = _simulate(client, model_id)
    assert resp.status_code == 200, resp.text
    out = resp.json()["outputs"]["Lotka_Volterra_module/x"]
    assert len(out) > 0
    assert np.all(np.isfinite(out))
