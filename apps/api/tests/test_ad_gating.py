"""Integration tests for casadi_python automatic differentiation (AD).

Covers the config-level AD capability flag and the end-to-end AD local-sensitivity
wiring (CasADi jacobian). Skipped without libcellml / myokit / casadi / CA.
"""

import json
import math

import model_codegen
import pytest
import sensitivity_runner
from conftest import LV_MODEL_PATH, LV_OBS_DATA_PATH, LV_PARAMS_CSV_PATH, RESOURCES_DIR

C3_MODEL_PATH = RESOURCES_DIR / "3compartment_flat.cellml"
C3_OBS_DATA_PATH = RESOURCES_DIR / "3compartment_obs_data.json"
C3_PARAMS_CSV_PATH = RESOURCES_DIR / "3compartment_params_for_id.csv"


@pytest.mark.integration
def test_fd_local_sensitivity_on_casadi_python_reduces_with_numpy_ops(tmp_path, requires_casadi):
    """Regression: FD local sensitivity on a casadi_python model reduces its
    numeric forward-run results with numpy-mode operation funcs. The SA manager
    holds casadi-mode ops for casadi_python (e.g. mean -> ca.sum(x)/x.numel()),
    which crash on the numpy arrays the FD path produces. 3compartment's obs_data
    uses 'mean'/'max_minus_min'/'min', so this exercises that path."""
    config = {
        "model_path": model_codegen.resolve_model_path(str(C3_MODEL_PATH), "casadi_python"),
        "model_type": "casadi_python",
        "solver": "casadi_integrator",
        "solver_info": {"solver": "casadi_integrator", "method": "semi_implicit_euler"},
        "obs_path": str(C3_OBS_DATA_PATH),
        "params_path": str(C3_PARAMS_CSV_PATH),
        "output_dir": str(tmp_path / "sa_out"),
        "file_prefix": "3compartment",
        "settings": {"method": "local", "gradient_method": "FD", "nominal": "current", "dt": 0.01},
    }
    payload = sensitivity_runner.run(config)
    assert payload["gradient_method"] == "FD"
    coeffs = [v for row in payload["indices"]["local"].values() for v in row.values()]
    assert any(c is not None and math.isfinite(c) for c in coeffs)


@pytest.mark.integration
def test_config_reports_differentiable_ops_for_casadi_python(client, requires_casadi):
    body = client.post(
        "/api/config",
        json={"generated_model_format": "casadi_python", "solver": "casadi_integrator"},
    ).json()
    # The reduction ops the Lotka-Volterra obs_data uses are differentiable.
    assert body["differentiable_operations"].get("max") is True
    assert body["differentiable_operations"].get("min") is True
    # ad_available is exactly the casadi_python + all-differentiable gate.
    assert body["ad_available"] == (
        body["generated_model_format"] == "casadi_python" and body["all_differentiable"]
    )


def _local_sa_config(tmp_path, gradient_method):
    """A sensitivity_runner config for a casadi_python local-SA run."""
    model_path = model_codegen.resolve_model_path(str(LV_MODEL_PATH), "casadi_python")
    return {
        "model_path": model_path,
        "model_type": "casadi_python",
        "solver": "casadi_integrator",
        "solver_info": {"solver": "casadi_integrator", "method": "cvodes"},
        "obs_path": str(LV_OBS_DATA_PATH),
        "params_path": str(LV_PARAMS_CSV_PATH),
        "output_dir": str(tmp_path / "sa_out"),
        "file_prefix": "Lotka_Volterra_forced",
        "settings": {
            "method": "local",
            "gradient_method": gradient_method,
            "nominal": "current",
            "dt": 0.01,
        },
    }


@pytest.mark.integration
def test_ad_local_sensitivity_runs_on_casadi_python(tmp_path, requires_casadi):
    payload = sensitivity_runner.run(_local_sa_config(tmp_path, "AD"))
    assert payload["gradient_method"] == "AD"
    local = payload["indices"]["local"]
    assert payload["param_names"] and payload["output_names"]
    # At least one coefficient is a finite number (AD produced real gradients).
    coeffs = [v for row in local.values() for v in row.values()]
    assert any(c is not None and math.isfinite(c) for c in coeffs)


@pytest.mark.integration
def test_ad_with_non_differentiable_obs_op_fails_informatively(tmp_path, requires_casadi):
    """Using AD with an obs operation that isn't @differentiable must fail with a
    clear, actionable error (name the op + point to FD), not a cryptic crash."""
    obs = json.loads(LV_OBS_DATA_PATH.read_text())
    # max_first_half is a real CA operation that is NOT marked @differentiable.
    obs["data_items"][0]["operation"] = "max_first_half"
    obs_path = tmp_path / "non_diff_obs.json"
    obs_path.write_text(json.dumps(obs))

    config = _local_sa_config(tmp_path, "AD")
    config["obs_path"] = str(obs_path)

    with pytest.raises(ValueError) as ei:
        sensitivity_runner.run(config)
    msg = str(ei.value)
    assert "max_first_half" in msg
    assert "@differentiable" in msg
    assert "FD" in msg
