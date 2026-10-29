"""Prediction features and held-out validation against a real libcuflynx.

Runs CUFLynx's runners on the Lotka-Volterra fixture with prediction_items added --
two with an operation (features), one plain trace (skipped by libcuflynx with a
warning) -- and reads back what libcuflynx wrote. Skipped unless the libcuflynx in
reach supports prediction features (``obs_data.PREDICTION_FEATURES_FLAG``); point
``CIRCULATORY_AUTOGEN_SRC`` at a checkout that has them to run it.
"""

from __future__ import annotations

import json
import math

import pytest

import ca_run_history as crh
import model_codegen
import obs_data
import sensitivity_runner
from conftest import LV_MODEL_PATH, LV_OBS_DATA_PATH, LV_PARAMS_CSV_PATH

pytestmark = pytest.mark.integration

KEY = obs_data.INCLUDE_PREDICTION_ITEMS
FEATURES = ["x_mean_pred", "y_min_pred"]


@pytest.fixture
def requires_prediction_features(requires_simulation):
    if not obs_data.ca_supports_prediction_features():
        pytest.skip("this libcuflynx has no prediction features")


def _obs_path(tmp_path):
    doc = json.loads(LV_OBS_DATA_PATH.read_text())
    doc["prediction_items"] = [
        {"data_item_name": "x_mean_pred", "operands": ["Lotka_Volterra_module/x"],
         "operation": "mean", "unit": "dimensionless", "experiment_idx": 0,
         # held-out data for the feature: validated as mean(x) against it
         "data_type": "constant", "value": 10.0, "std": 2.0},
        {"data_item_name": "y_min_pred", "operands": ["Lotka_Volterra_module/y"],
         "operation": "min", "unit": "dimensionless", "experiment_idx": 0},
        {"data_item_name": "y_trace", "operands": ["Lotka_Volterra_module/y"],
         "unit": "dimensionless", "experiment_idx": 0},
    ]
    path = tmp_path / "lv_pred_obs_data.json"
    path.write_text(json.dumps(doc))
    return str(path)


def _config(tmp_path, out, settings):
    return {
        "model_path": model_codegen.resolve_model_path(str(LV_MODEL_PATH), "cellml"),
        "model_type": "cellml",
        "solver": "CVODE_myokit",
        "solver_info": {"solver": "CVODE_myokit", "method": "CVODE"},
        "obs_path": _obs_path(tmp_path),
        "params_path": str(LV_PARAMS_CSV_PATH),
        "output_dir": str(tmp_path / out),
        "file_prefix": "lv",
        "settings": {"dt": 0.01, "sim_time": 5.0, "pre_time": 0.0, **settings},
    }


def test_local_sa_rows_for_the_prediction_features(tmp_path, requires_prediction_features):
    """libcuflynx supplies the rows, at CUFLynx's nominal point, after the data_items'."""
    config = _config(tmp_path, "local", {
        "method": "local", "gradient_method": "FD", "rel_step": 0.01,
        "nominal": "midpoint", KEY: True})
    payload = sensitivity_runner.run(config)
    assert payload["prediction_outputs"] == FEATURES
    assert payload["output_names"][-2:] == FEATURES
    assert len(payload["output_names"]) == 4, "the two data_items keep their rows"
    for name in FEATURES:
        row = payload["indices"]["local"][name]
        assert any(v is not None and math.isfinite(v) for v in row.values())
    # and in CA's CSV the manager reads
    assert crh.local_sensitivity(config["output_dir"])["output_names"][-2:] == FEATURES


def test_local_sa_without_the_option_is_unchanged(tmp_path, requires_prediction_features):
    config = _config(tmp_path, "local_off", {
        "method": "local", "gradient_method": "FD", "nominal": "midpoint"})
    payload = sensitivity_runner.run(config)
    assert payload["prediction_outputs"] == []
    assert len(payload["output_names"]) == 2


def test_sobol_names_its_prediction_columns(tmp_path, requires_prediction_features):
    """Tagged from libcuflynx's sobol_output_features.json, not from the labels."""
    config = _config(tmp_path, "sobol", {
        "method": "sobol", "sample_type": "saltelli", "num_samples": 8, KEY: True})
    sensitivity_runner.run(config)
    found = crh.sobol_indices(config["output_dir"])
    assert [o.split(" (")[0] for o in found["prediction_outputs"]] == FEATURES
    assert set(found["prediction_outputs"]) <= set(found["output_names"])
    assert len(found["output_names"]) > len(found["prediction_outputs"])


def test_libcuflynx_validates_a_prediction_feature(tmp_path, requires_prediction_features):
    """save_prediction_data scores operation(model operands) against the held-out value."""
    import calibration_runner

    config = _config(tmp_path, "calib", {
        "param_id_method": "sp_minimize", "gradient_method": "FSA",
        "cost_convergence": 1e12, "num_calls_to_function": 3})
    calibration_runner.run(config)
    found = crh.validation_results(config["output_dir"])
    assert found is not None
    (item,) = found["items"]
    assert item["data_item_name"] == "x_mean_pred"
    assert item["operation"] == "mean"
    assert item["n_points"] == 1 and len(item["model"]) == 1
    assert item["t"] == [pytest.approx(5.0)]
