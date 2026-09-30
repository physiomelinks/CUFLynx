"""Validating a calibration against held-out data in prediction_items (CA #535).

An obs_data prediction_item may carry measured data (``value`` with ``data_type``,
``std`` and, for a series, ``obs_dt``). It is never scored in the calibration; the
best fit is compared with it afterwards and the result lands in CA's
``validation_results.json``, which the calibration status and the output loader
return as ``validation``.

**libcuflynx does all of it.** The runner calls its ``save_prediction_data``, which
scores the items (``param_id.validation``) and writes the file; CUFLynx computes
nothing, and a libcuflynx without ``param_id.validation`` gives no validation at
all. The unit tier has no simulator, so the runner's step is driven through a fake
engine that writes the file the way libcuflynx's does.
"""

from __future__ import annotations

import json
import os
import time
import types
from pathlib import Path

import numpy as np
import pytest

import ca_run_history as crh
import calibration as calibration_mod
import calibration_runner
import obs_data
from conftest import set_ca_module

PROTOCOL = {"pre_times": [0.0], "sim_times": [[2.0]]}


def _doc(prediction_items):
    return {
        "protocol_info": dict(PROTOCOL),
        "data_items": [{"data_item_name": "c0", "operands": ["main/c"], "data_type": "constant",
                        "unit": "dimensionless", "value": 1.0, "std": 0.1}],
        "prediction_items": prediction_items,
    }


SERIES = {"data_item_name": "y_validation", "operands": ["main/y"], "unit": "mV",
          "data_type": "series", "value": [0.0, 1.0, 2.0, 3.0, 9.0], "std": 0.5, "obs_dt": 0.5}
CONSTANT = {"data_item_name": "v_end", "operands": ["main/v"], "unit": "m3",
            "data_type": "constant", "value": 4.0, "std": 1.0}
PLAIN = {"data_item_name": "z", "operands": ["main/z"], "unit": "mV"}


@pytest.fixture
def old_ca(monkeypatch):
    """A circulatory_autogen predating #535: it has no ``param_id.validation``."""
    set_ca_module(monkeypatch, "param_id.validation", None)


# ---------------------------------------------------------------------------
# obs_data: the keys are accepted, preserved, and kept from an older CA
# ---------------------------------------------------------------------------
def test_held_out_keys_survive_parsing(old_ca):
    parsed = obs_data.parse_obs_data(_doc([SERIES, CONSTANT, PLAIN]))
    assert parsed.prediction_items[0] == SERIES
    assert parsed.prediction_items[1]["std"] == 1.0
    assert obs_data.has_held_out_data(_doc([SERIES]))
    assert not obs_data.has_held_out_data(_doc([PLAIN]))


def test_cuflynx_leaves_the_held_out_rules_to_libcuflynx(old_ca):
    """No second copy of CA #535's rules: a series without obs_dt is libcuflynx's to refuse."""
    obs_data.parse_obs_data(_doc([dict(SERIES, obs_dt=None)]))


def test_an_older_ca_is_given_the_document_without_held_out_keys(old_ca):
    doc = _doc([SERIES, PLAIN])
    readable = obs_data.for_ca(doc)
    assert readable["prediction_items"][0] == {
        "data_item_name": "y_validation", "operands": ["main/y"], "unit": "mV"}
    assert readable["prediction_items"][1] == PLAIN
    assert doc["prediction_items"][0] == SERIES, "the user's document is not modified"


def test_a_current_ca_is_given_the_document_as_written(monkeypatch):
    set_ca_module(monkeypatch, "param_id.validation", types.ModuleType("validation"))
    doc = _doc([SERIES])
    assert obs_data.for_ca(doc) is doc


def test_a_document_without_held_out_data_is_never_copied(old_ca):
    doc = _doc([PLAIN])
    assert obs_data.for_ca(doc) is doc


def test_the_runner_copy_keeps_the_files_name_and_leaves_the_original(old_ca, tmp_path):
    """CA and CUFLynx both derive names from the obs_data file's (run dir, emulator dir)."""
    src = tmp_path / "study_obs_data.json"
    src.write_text(json.dumps(_doc([SERIES])))
    path = obs_data.ca_obs_path(str(src))
    assert path != str(src)
    assert Path(path).name == src.name
    assert "value" not in json.loads(Path(path).read_text())["prediction_items"][0]
    assert json.loads(src.read_text())["prediction_items"][0] == SERIES
    assert obs_data.with_ca_obs_path({"obs_path": str(src)})["obs_path"] == path


def test_the_ca_verdict_on_an_older_ca_ignores_held_out_keys(old_ca, monkeypatch):
    seen = []

    class Parser:
        def parse_obs_data_json(self, obs_data_dict, pre_time, sim_time):
            seen.append(obs_data_dict)

    monkeypatch.setattr(obs_data, "_ca_parser", lambda: Parser())
    assert obs_data.ca_verdict(_doc([SERIES])).error is None
    assert "value" not in seen[0]["prediction_items"][0]


# ---------------------------------------------------------------------------
# Reading CA's file
# ---------------------------------------------------------------------------
def _run_dir(tmp_path):
    run = tmp_path / "genetic_algorithm_model_obs"
    run.mkdir(parents=True)
    np.save(run / "best_param_vals.npy", np.array([1.0]))
    np.save(run / "best_cost.npy", np.array([0.1]))
    (run / "param_names.csv").write_text("a/x\n")
    return run


def test_no_file_means_no_validation(tmp_path):
    _run_dir(tmp_path)
    assert crh.validation_results(str(tmp_path)) is None


def test_an_empty_file_is_not_a_validation(tmp_path):
    run = _run_dir(tmp_path)
    (run / crh.VALIDATION_RESULTS_FILE).write_text(json.dumps({"items": []}))
    assert crh.validation_results(str(tmp_path)) is None


def test_the_validation_is_read_from_the_run_directory(tmp_path):
    run = _run_dir(tmp_path)
    (run / crh.VALIDATION_RESULTS_FILE).write_text(json.dumps({"items": [{"rmse": 0.5}]}))
    assert crh.validation_results(str(tmp_path)) == {"items": [{"rmse": 0.5}]}


def test_a_validation_older_than_the_best_fit_is_not_this_fits(tmp_path):
    run = _run_dir(tmp_path)
    stale = run / crh.VALIDATION_RESULTS_FILE
    stale.write_text(json.dumps({"items": [{"rmse": 0.5}]}))
    old = time.time() - 600
    os.utime(stale, (old, old))
    assert crh.validation_results(str(tmp_path)) is None


def test_load_outputs_reports_the_validation(tmp_path):
    import load_outputs

    run = _run_dir(tmp_path)
    (run / crh.VALIDATION_RESULTS_FILE).write_text(json.dumps({"items": [{"rmse": 0.5}]}))
    found = load_outputs.load_outputs(str(tmp_path))
    assert found["calibration"]["validation"] == {"items": [{"rmse": 0.5}]}


# ---------------------------------------------------------------------------
# The runner's step, and the status that carries it
# ---------------------------------------------------------------------------
@pytest.fixture
def new_ca(monkeypatch):
    """A libcuflynx with ``param_id.validation``: it validates, CUFLynx only reads."""
    set_ca_module(monkeypatch, "param_id.validation", types.ModuleType("validation"))


class _FakeParamID:
    """``save_prediction_data`` writes validation_results.json, as libcuflynx's does."""

    rank = 0

    def __init__(self, run_dir, items):
        self.output_dir = str(run_dir)
        self.items = items
        self.saved = 0

    def save_prediction_data(self):
        self.saved += 1
        scored = [{"data_item_name": it["data_item_name"], "rmse": 0.0}
                  for it in self.items if it.get("value") is not None]
        if scored:
            Path(self.output_dir, crh.VALIDATION_RESULTS_FILE).write_text(
                json.dumps({"items": scored}))


def test_the_runner_has_libcuflynx_validate_the_best_fit(new_ca, tmp_path):
    run = _run_dir(tmp_path)
    items = [SERIES, PLAIN, CONSTANT]
    pid = _FakeParamID(run, items)
    path = calibration_runner._validate_held_out(pid, items, emulated=False)
    assert pid.saved == 1
    assert path == str(run / crh.VALIDATION_RESULTS_FILE)
    got = crh.validation_results(str(tmp_path))["items"]
    assert [i["data_item_name"] for i in got] == ["y_validation", "v_end"]


def test_an_older_libcuflynx_gives_no_validation(old_ca, tmp_path, capsys):
    """No local scoring to fall back on: CUFLynx computes nothing itself."""
    run = _run_dir(tmp_path)
    pid = _FakeParamID(run, [SERIES])
    assert calibration_runner._validate_held_out(pid, [SERIES], emulated=False) is None
    assert pid.saved == 0
    assert not (run / crh.VALIDATION_RESULTS_FILE).exists()
    assert "newer libcuflynx" in capsys.readouterr().out
    assert crh.validation_results(str(tmp_path)) is None


def test_there_is_no_local_copy_of_the_scoring():
    import importlib.util

    assert importlib.util.find_spec("held_out_validation") is None
    assert not hasattr(crh, "write_validation_results")
    assert not hasattr(crh, "prediction_series")


def test_a_stale_file_from_an_earlier_run_is_not_this_runs(new_ca, tmp_path):
    run = _run_dir(tmp_path)
    stale = run / crh.VALIDATION_RESULTS_FILE
    stale.write_text(json.dumps({"items": [{"rmse": 9.0}]}))
    old = time.time() - 600
    os.utime(stale, (old, old))

    class Silent(_FakeParamID):
        def save_prediction_data(self):
            self.saved += 1  # wrote nothing this time

    pid = Silent(run, [SERIES])
    assert calibration_runner._validate_held_out(pid, [SERIES], emulated=False) is None


def test_the_runner_leaves_a_study_without_held_out_data_alone(new_ca, tmp_path):
    run = _run_dir(tmp_path)
    pid = _FakeParamID(run, [PLAIN])
    assert calibration_runner._validate_held_out(pid, [PLAIN], emulated=False) is None
    assert pid.saved == 0, "no prediction traces written for a study that asked for none"
    assert not (run / crh.VALIDATION_RESULTS_FILE).exists()


def test_an_emulated_calibration_is_not_validated(new_ca, tmp_path):
    """An emulator predicts the scalar features only; there is no trace to compare."""
    run = _run_dir(tmp_path)
    pid = _FakeParamID(run, [SERIES])
    assert calibration_runner._validate_held_out(pid, [SERIES], emulated=True) is None
    assert pid.saved == 0


def test_a_validation_failure_never_fails_the_run(new_ca, tmp_path):
    class Broken(_FakeParamID):
        def save_prediction_data(self):
            raise RuntimeError("solver fell over")

    pid = Broken(_run_dir(tmp_path), [SERIES])
    assert calibration_runner._validate_held_out(pid, [SERIES], emulated=False) is None


def test_the_calibration_status_carries_the_validation(tmp_path):
    run = _run_dir(tmp_path)
    (run / crh.VALIDATION_RESULTS_FILE).write_text(json.dumps({"items": [{"rmse": 0.5}]}))
    mgr = calibration_mod.CalibrationManager()
    job = calibration_mod.CalibrationJob("j1", str(tmp_path), model_id="m")
    job.started_at = time.time() - 60
    mgr._job = job
    mgr._finalize(job, code=0)
    assert job.state == "done"
    assert mgr.status("j1")["validation"] == {"items": [{"rmse": 0.5}]}


def test_the_calibration_status_has_no_validation_without_held_out_data(tmp_path):
    _run_dir(tmp_path)
    mgr = calibration_mod.CalibrationManager()
    job = calibration_mod.CalibrationJob("j1", str(tmp_path), model_id="m")
    job.started_at = time.time() - 60
    mgr._job = job
    mgr._finalize(job, code=0)
    assert mgr.status("j1")["validation"] is None
