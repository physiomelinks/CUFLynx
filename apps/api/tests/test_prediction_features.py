"""Prediction items as sensitivity / emulator features ("Include prediction items").

libcuflynx takes ``include_prediction_items`` in ``sa_options`` and in
``emulator_settings``: when true it also treats the obs_data's prediction_items
that have an ``operation`` as features. CUFLynx only passes the option through --
and only to a libcuflynx that says it supports it (``obs_data.
PREDICTION_FEATURES_FLAG``), since an older one rejects the key, as it rejects an
``operation`` on a prediction_item. Which items become features is never decided
here.
"""

from __future__ import annotations

import json
import types
from pathlib import Path

import pytest

import emulator_runner
import export_pipeline
import obs_data
import sensitivity_runner
from conftest import set_ca_module

FLAG_MODULE, FLAG_NAME = obs_data.PREDICTION_FEATURES_FLAG
KEY = obs_data.INCLUDE_PREDICTION_ITEMS

PRED_FEATURE = {"data_item_name": "v_max", "operands": ["main/v"], "unit": "m3",
                "operation": "max", "operation_kwargs": {"axis": 0}}
PRED_TRACE = {"data_item_name": "z", "operands": ["main/z"], "unit": "mV"}


def _doc(prediction_items):
    return {
        "protocol_info": {"pre_times": [0.0], "sim_times": [[2.0]]},
        "data_items": [{"data_item_name": "c0", "operands": ["main/c"], "data_type": "constant",
                        "unit": "dimensionless", "value": 1.0, "std": 0.1}],
        "prediction_items": prediction_items,
    }


@pytest.fixture
def supported(monkeypatch):
    """A libcuflynx whose sensitivity_analysis sets the feature flag."""
    mod = types.ModuleType(FLAG_MODULE)
    setattr(mod, FLAG_NAME, True)
    set_ca_module(monkeypatch, FLAG_MODULE, mod)


@pytest.fixture
def unsupported(monkeypatch):
    """A libcuflynx predating the feature: the module is there, the flag is not."""
    set_ca_module(monkeypatch, FLAG_MODULE, types.ModuleType(FLAG_MODULE))


# ---------------------------------------------------------------------------
# The feature detect
# ---------------------------------------------------------------------------
def test_the_flag_is_read_with_getattr(supported):
    assert obs_data.ca_supports_prediction_features() is True


def test_no_flag_means_unsupported(unsupported):
    assert obs_data.ca_supports_prediction_features() is False


def test_a_false_flag_means_unsupported(monkeypatch):
    mod = types.ModuleType(FLAG_MODULE)
    setattr(mod, FLAG_NAME, False)
    set_ca_module(monkeypatch, FLAG_MODULE, mod)
    assert obs_data.ca_supports_prediction_features() is False


def test_no_module_means_unsupported(monkeypatch):
    set_ca_module(monkeypatch, FLAG_MODULE, None)
    assert obs_data.ca_supports_prediction_features() is False


# ---------------------------------------------------------------------------
# The option, through both runners
# ---------------------------------------------------------------------------
def test_the_option_is_forwarded_when_asked_and_supported(supported):
    assert obs_data.prediction_features_option({KEY: True}) == {KEY: True}
    sa = sensitivity_runner._sa_options({"method": "sobol", KEY: True}, "/out")
    assert sa[KEY] is True
    emu = emulator_runner._emulator_settings({KEY: True}, "/tmp/emu")
    assert emu[KEY] is True


def test_the_option_is_never_sent_when_off(supported):
    """False is libcuflynx's default: sending it would only risk an older one's refusal."""
    assert obs_data.prediction_features_option({KEY: False}) == {}
    assert KEY not in sensitivity_runner._sa_options({"method": "sobol", KEY: False}, "/out")
    assert KEY not in emulator_runner._emulator_settings({KEY: False}, "/tmp/emu")


def test_an_older_libcuflynx_is_not_sent_the_key(unsupported, capsys):
    sa = sensitivity_runner._sa_options({"method": "sobol", KEY: True}, "/out")
    emu = emulator_runner._emulator_settings({KEY: True}, "/tmp/emu")
    assert KEY not in sa and KEY not in emu
    assert "include_prediction_items ignored" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# obs_data: operation / operation_kwargs on a prediction_item
# ---------------------------------------------------------------------------
def test_operation_keys_survive_parsing(unsupported):
    parsed = obs_data.parse_obs_data(_doc([PRED_FEATURE, PRED_TRACE]))
    assert parsed.prediction_items[0] == PRED_FEATURE


def test_a_supporting_libcuflynx_is_given_the_document_as_written(supported):
    doc = _doc([PRED_FEATURE, PRED_TRACE])
    assert obs_data.for_ca(doc) is doc


def test_an_older_libcuflynx_is_given_the_document_without_them(unsupported):
    doc = _doc([PRED_FEATURE, PRED_TRACE])
    readable = obs_data.for_ca(doc)
    assert readable["prediction_items"][0] == {
        "data_item_name": "v_max", "operands": ["main/v"], "unit": "m3"}
    assert readable["prediction_items"][1] == PRED_TRACE
    assert doc["prediction_items"][0] == PRED_FEATURE, "the user's document is not modified"


def test_held_out_and_operation_keys_are_judged_separately(supported, monkeypatch):
    """A libcuflynx with prediction features but no validation keeps the operation."""
    set_ca_module(monkeypatch, "param_id.validation", None)
    item = dict(PRED_FEATURE, data_type="constant", value=3.0, std=0.5)
    readable = obs_data.for_ca(_doc([item]))
    assert readable["prediction_items"][0] == PRED_FEATURE


def test_the_runner_copy_drops_them_for_an_older_libcuflynx(unsupported, tmp_path):
    src = tmp_path / "study_obs_data.json"
    src.write_text(json.dumps(_doc([PRED_FEATURE])))
    path = obs_data.ca_obs_path(str(src))
    assert Path(path).name == src.name and path != str(src)
    item = json.loads(Path(path).read_text())["prediction_items"][0]
    assert "operation" not in item and "operation_kwargs" not in item
    assert json.loads(src.read_text())["prediction_items"][0] == PRED_FEATURE


# ---------------------------------------------------------------------------
# The exported user_inputs.yaml
# ---------------------------------------------------------------------------
def _user_inputs(sensitivity, prediction_features):
    return export_pipeline.build_user_inputs(
        file_prefix="m", model_type="cellml", solver="CVODE_myokit", solver_info={},
        dt=0.01, pre_time=0.0, sim_time=1.0, model_file="m.cellml",
        obs_file="obs_data.json", params_for_id_file="params_for_id.csv",
        calibration={}, sensitivity=sensitivity, uq={}, enabled={},
        prediction_features=prediction_features,
    )


def test_the_export_writes_the_option_for_a_supporting_libcuflynx():
    ui = _user_inputs({"method": "sobol", KEY: True}, prediction_features=True)
    assert ui["sa_options"][KEY] is True


@pytest.mark.parametrize(
    "sensitivity, prediction_features",
    [({"method": "sobol", KEY: True}, False), ({"method": "sobol", KEY: False}, True)],
)
def test_the_export_leaves_it_out_otherwise(sensitivity, prediction_features):
    assert KEY not in _user_inputs(sensitivity, prediction_features)["sa_options"]


# ---------------------------------------------------------------------------
# The API: the feature detect, where the checkboxes read it
# ---------------------------------------------------------------------------
def test_the_sensitivity_defaults_carry_the_feature_detect(client, supported):
    body = client.get("/api/sensitivity/defaults").json()
    assert body["prediction_features_supported"] is True
    assert body[KEY] is False


def test_the_emulator_defaults_carry_the_feature_detect(client, unsupported):
    assert client.get("/api/emulator/defaults").json()["prediction_features_supported"] is False


@pytest.mark.parametrize("present", [True, False])
def test_the_config_says_whether_libcuflynx_validates(client, monkeypatch, present):
    set_ca_module(monkeypatch, "param_id.validation",
                  types.ModuleType("validation") if present else None)
    assert client.get("/api/config").json()["held_out_validation_supported"] is present
