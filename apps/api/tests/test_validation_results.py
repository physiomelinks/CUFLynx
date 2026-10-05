"""Validating a calibration against held-out data in prediction_items (CA #535).

An obs_data prediction_item may carry measured data (``value`` with ``data_type``,
``std`` and, for a series, ``obs_dt``). It is never scored in the calibration; the
best fit is compared with it afterwards and the result lands in CA's
``validation_results.json``, which the calibration status and the output loader
return as ``validation``.

The unit tier has no simulator, so the runner's step is driven through a fake
engine that writes CA's saved prediction traces the way ``save_prediction_data``
does. The local scoring copy is pinned against CA's own test cases, and against
CA's function itself when a checkout that has it is found.
"""

from __future__ import annotations

import importlib.util
import json
import os
import time
from pathlib import Path

import numpy as np
import pytest

import ca_run_history as crh
import calibration as calibration_mod
import calibration_runner
import held_out_validation as hov
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
# Scoring: CA's rules, in the local copy
# ---------------------------------------------------------------------------
def test_a_series_is_compared_at_its_observation_times(old_ca):
    info = hov.prediction_info([SERIES, PLAIN])
    t = np.linspace(0.0, 1.5, 151)  # the run ends at 1.5: the sample at t = 2 is not reached
    (item,) = hov.validation_results(info, {0: t}, [2.0 * t, np.zeros_like(t)])["items"]
    assert item["data_item_name"] == "y_validation"
    assert item["operand"] == "main/y"
    assert item["t"] == [0.0, 0.5, 1.0, 1.5]
    assert item["model"] == pytest.approx([0.0, 1.0, 2.0, 3.0])
    assert item["n_points"] == 4
    assert item["rmse"] == pytest.approx(0.0, abs=1e-12)
    assert item["within_2std"] == 1.0
    assert item["std"] == [0.5] * 4


def test_a_constant_is_compared_with_the_end_of_the_experiment(old_ca):
    info = hov.prediction_info([CONSTANT])
    t = np.linspace(0.0, 2.0, 21)
    (item,) = hov.validation_results(info, {0: t}, [t])["items"]
    assert item["model"] == [2.0]
    assert item["rmse"] == pytest.approx(2.0)
    assert item["mean_abs_z"] == pytest.approx(2.0)
    assert item["nrmse"] == pytest.approx(0.5)
    assert item["within_2std"] == 1.0


def test_no_std_means_no_z_scores(old_ca):
    item = dict(CONSTANT, std=None)
    (res,) = hov.validation_results(hov.prediction_info([item]), {0: np.array([0.0, 1.0])},
                                    [np.array([0.0, 3.0])])["items"]
    assert res["mean_abs_z"] is None and res["within_2std"] is None
    assert res["rmse"] == pytest.approx(1.0)


def test_no_held_out_data_is_no_validation(old_ca):
    info = hov.prediction_info([PLAIN])
    assert hov.validation_results(info, {0: np.linspace(0, 1, 3)}, [np.zeros(3)]) == {"items": []}


def test_ca_is_asked_first_when_it_has_the_function(monkeypatch):
    """The copy is a fallback: CA owns the comparison whenever it can answer."""
    import types

    calls = []
    fake = types.ModuleType("validation")
    fake.validation_results = lambda *a: calls.append(a) or {"items": ["from CA"]}
    set_ca_module(monkeypatch, "param_id.validation", fake)
    info = hov.prediction_info([CONSTANT])
    assert hov.validation_results(info, {0: [0.0, 1.0]}, [[0.0, 1.0]]) == {"items": ["from CA"]}
    assert len(calls) == 1


def _ca_validation_module():
    """CA's ``validation.py`` loaded by file, from a checkout that has it -- or None."""
    candidates = [os.environ.get("CIRCULATORY_AUTOGEN_SRC", "")]
    here = Path(__file__).resolve()
    candidates += [str(p / "circulatory_autogen" / "src") for p in here.parents]
    for src in filter(None, candidates):
        path = Path(src) / "libcuflynx" / "param_id" / "validation.py"
        if path.is_file():
            spec = importlib.util.spec_from_file_location("_ca_validation_probe", path)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            return mod
    return None


def test_the_local_copy_agrees_with_cas_function():
    """Pinned against the real thing, so the copy cannot drift from CA's scores."""
    ca = _ca_validation_module()
    if ca is None:
        pytest.skip("no circulatory_autogen checkout with param_id/validation.py here")
    rng = np.random.default_rng(3)
    items = [
        dict(SERIES, value=list(rng.normal(size=9)), std=list(rng.uniform(0.1, 1, 9))),
        CONSTANT, PLAIN, dict(CONSTANT, data_item_name="c2", std=None, experiment_idx=1),
    ]
    info = hov.prediction_info(items)
    t = {0: np.linspace(0.0, 3.0, 61), 1: np.linspace(0.0, 1.0, 11)}
    preds = [rng.normal(size=61), rng.normal(size=61), np.zeros(61), rng.normal(size=11)]
    assert hov.local_validation_results(info, t, preds) == ca.validation_results(info, t, preds)


# ---------------------------------------------------------------------------
# obs_data: the keys are accepted, preserved, and kept from an older CA
# ---------------------------------------------------------------------------
def test_held_out_keys_survive_parsing(old_ca):
    parsed = obs_data.parse_obs_data(_doc([SERIES, CONSTANT, PLAIN]))
    assert parsed.prediction_items[0] == SERIES
    assert parsed.prediction_items[1]["std"] == 1.0
    assert obs_data.has_held_out_data(_doc([SERIES]))
    assert not obs_data.has_held_out_data(_doc([PLAIN]))


@pytest.mark.parametrize(
    "item, message",
    [
        (dict(CONSTANT, data_type=None), "needs data_type"),
        (dict(SERIES, obs_dt=None), "needs obs_dt"),
    ],
)
def test_held_out_data_needs_what_ca_needs(old_ca, item, message):
    """Checked here too, because an older CA never sees these keys to check them."""
    with pytest.raises(obs_data.ObsDataError, match=message):
        obs_data.parse_obs_data(_doc([item]))


def test_an_older_ca_is_given_the_document_without_held_out_keys(old_ca):
    doc = _doc([SERIES, PLAIN])
    readable = obs_data.for_ca(doc)
    assert readable["prediction_items"][0] == {
        "data_item_name": "y_validation", "operands": ["main/y"], "unit": "mV"}
    assert readable["prediction_items"][1] == PLAIN
    assert doc["prediction_items"][0] == SERIES, "the user's document is not modified"


def test_a_current_ca_is_given_the_document_as_written(monkeypatch):
    import types

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


def test_the_prediction_traces_map_onto_items_as_ca_saves_them(tmp_path):
    """Entry k is item k's experiment: rows [time, that experiment's items in order]."""
    t0, t1 = np.array([0.0, 1.0]), np.array([0.0, 2.0])
    rows0 = np.vstack([t0, [1, 2], [3, 4]])  # exp 0 holds items 0 and 2
    rows1 = np.vstack([t1, [5, 6]])          # exp 1 holds item 1
    for k, rows in enumerate([rows0, rows1, rows0]):
        np.save(tmp_path / crh.PREDICTION_DATA_FILE.format(k), rows)
    times, per_item = crh.prediction_series(str(tmp_path), [0, 1, 0])
    assert list(times[1]) == [0.0, 2.0]
    assert [list(p) for p in per_item] == [[1, 2], [5, 6], [3, 4]]
    assert crh.prediction_series(str(tmp_path), [0, 1, 0, 0]) is None, "a missing trace"


def test_load_outputs_reports_the_validation(tmp_path):
    import load_outputs

    run = _run_dir(tmp_path)
    (run / crh.VALIDATION_RESULTS_FILE).write_text(json.dumps({"items": [{"rmse": 0.5}]}))
    found = load_outputs.load_outputs(str(tmp_path))
    assert found["calibration"]["validation"] == {"items": [{"rmse": 0.5}]}


# ---------------------------------------------------------------------------
# The runner's step, and the status that carries it
# ---------------------------------------------------------------------------
class _FakeParamID:
    """Writes CA's prediction traces on ``save_prediction_data``, as CA does."""

    rank = 0

    def __init__(self, run_dir, items):
        self.output_dir = str(run_dir)
        self.items = items
        self.saved = 0

    def save_prediction_data(self):
        self.saved += 1
        t = np.linspace(0.0, 2.0, 201)
        exp_idxs = [int(it.get("experiment_idx", 0)) for it in self.items]
        for k, exp_idx in enumerate(exp_idxs):
            same = [j for j, e in enumerate(exp_idxs) if e == exp_idx]
            rows = [t] + [2.0 * t for _ in same]  # the model predicts y = 2t everywhere
            np.save(os.path.join(self.output_dir, crh.PREDICTION_DATA_FILE.format(k)),
                    np.vstack(rows))


def test_the_runner_validates_the_best_fit_on_an_older_ca(old_ca, tmp_path):
    run = _run_dir(tmp_path)
    items = [SERIES, PLAIN, CONSTANT]
    pid = _FakeParamID(run, items)
    path = calibration_runner._validate_held_out(pid, items, emulated=False)
    assert path == str(run / crh.VALIDATION_RESULTS_FILE)
    got = crh.validation_results(str(tmp_path))["items"]
    assert [i["data_item_name"] for i in got] == ["y_validation", "v_end"]
    assert got[0]["t"] == [0.0, 0.5, 1.0, 1.5, 2.0]
    assert got[0]["model"] == pytest.approx([0.0, 1.0, 2.0, 3.0, 4.0])
    assert got[1]["model"] == pytest.approx([4.0])
    assert got[1]["rmse"] == pytest.approx(0.0, abs=1e-12)


def test_the_runner_leaves_a_study_without_held_out_data_alone(old_ca, tmp_path):
    run = _run_dir(tmp_path)
    pid = _FakeParamID(run, [PLAIN])
    assert calibration_runner._validate_held_out(pid, [PLAIN], emulated=False) is None
    assert pid.saved == 0, "no prediction traces written for a study that asked for none"
    assert not (run / crh.VALIDATION_RESULTS_FILE).exists()


def test_an_emulated_calibration_is_not_validated(old_ca, tmp_path):
    """An emulator predicts the scalar features only; there is no trace to compare."""
    run = _run_dir(tmp_path)
    pid = _FakeParamID(run, [SERIES])
    assert calibration_runner._validate_held_out(pid, [SERIES], emulated=True) is None
    assert pid.saved == 0


def test_a_validation_failure_never_fails_the_run(old_ca, tmp_path):
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
