"""Unit tests for the series_output (transformed) overlay computation (issue #111).

These stub CA's operation-func registry so they exercise ``compute_output_series``
(and the /api/simulate plumbing) without needing circulatory_autogen on sys.path.
"""

from __future__ import annotations

import numpy as np
import pytest

import obs_series
from conftest import LV_MODEL_PATH, upload_model


# ---------------------------------------------------------------------------
# A fake operation registry mirroring CA's shape.
# ---------------------------------------------------------------------------
def _series_to_constant(fn):
    fn.series_to_constant = True
    return fn


@_series_to_constant
def _bpm(x, series_output=False):
    # A @series_to_constant op: the plotted series is 60/x; the scalar branch
    # would return a mean (not exercised here).
    if series_output:
        return 60.0 / np.asarray(x)
    return float(np.mean(60.0 / np.asarray(x)))


@_series_to_constant
def _max(x, series_output=False):
    if series_output:
        return np.asarray(x)  # built-in style: series_output is the raw operand
    return float(np.max(x))


@_series_to_constant
def _dbl(x, series_output=False):
    if series_output:
        return 2.0 * np.asarray(x)
    return float(np.mean(x))


def _scalar_only(x):
    # No series_to_constant, returns a scalar -> no series to plot.
    return float(np.max(x))


FAKE_OPS = {"bpm": _bpm, "max": _max, "dbl": _dbl, "scalar_only": _scalar_only}


@pytest.fixture
def fake_ops(monkeypatch):
    monkeypatch.setattr(obs_series, "get_operation_funcs", lambda output_dir=None: FAKE_OPS)


# ---------------------------------------------------------------------------
# compute_output_series
# ---------------------------------------------------------------------------
def test_series_output_branch_is_applied(fake_ops):
    items = [
        {"operation": "bpm", "operands": ["heart/period"], "data_type": "constant"},
    ]
    outputs = {"heart/period": [1.0, 2.0, 3.0, 4.0]}
    result = obs_series.compute_output_series(items, outputs)
    assert 0 in result
    assert result[0] == pytest.approx([60.0, 30.0, 20.0, 15.0])


def test_operation_kwargs_are_passed_through(fake_ops):
    def _scaled(x, factor=1.0, series_output=False):
        if series_output:
            return factor * np.asarray(x)
        return float(np.mean(x))

    _scaled.series_to_constant = True
    FAKE_OPS["scaled"] = _scaled
    try:
        items = [
            {
                "operation": "scaled",
                "operands": ["v"],
                "operation_kwargs": {"factor": 3.0},
            }
        ]
        result = obs_series.compute_output_series(items, {"v": [1.0, 2.0]})
        assert result[0] == pytest.approx([3.0, 6.0])
    finally:
        FAKE_OPS.pop("scaled")


def test_no_operation_and_scalar_only_are_skipped(fake_ops):
    items = [
        {"operation": None, "operands": ["v"]},
        {"operation": "scalar_only", "operands": ["v"]},
        {"operation": "unregistered_op", "operands": ["v"]},
        {"data_type": "frequency", "operation": "bpm", "operands": ["v"]},
    ]
    result = obs_series.compute_output_series(items, {"v": [1.0, 2.0, 3.0]})
    assert result == {}


def test_builtin_style_returns_raw_operand(fake_ops):
    items = [{"operation": "max", "operands": ["v"], "data_type": "constant"}]
    result = obs_series.compute_output_series(items, {"v": [2.0, 5.0, 1.0]})
    assert result[0] == pytest.approx([2.0, 5.0, 1.0])


def test_unresolved_operand_is_skipped(fake_ops):
    items = [{"operation": "bpm", "operands": ["not/simulated"]}]
    result = obs_series.compute_output_series(items, {"v": [1.0, 2.0]})
    assert result == {}


def test_missing_ca_registry_returns_empty(monkeypatch):
    monkeypatch.setattr(obs_series, "get_operation_funcs", lambda output_dir=None: None)
    items = [{"operation": "bpm", "operands": ["v"]}]
    assert obs_series.compute_output_series(items, {"v": [1.0]}) == {}


def test_resolves_operand_across_separator_conventions(fake_ops):
    # operand uses '/', simulated output key uses '.' (Myokit dotted names).
    items = [{"operation": "bpm", "operands": ["heart/period"]}]
    result = obs_series.compute_output_series(items, {"heart.period": [1.0, 2.0]})
    assert result[0] == pytest.approx([60.0, 30.0])


# ---------------------------------------------------------------------------
# /api/simulate plumbing (fake helper, fake ops — no Myokit / CA needed)
# ---------------------------------------------------------------------------
def test_simulate_response_includes_output_series(client, fake_helper, monkeypatch):
    monkeypatch.setattr(
        obs_series, "get_operation_funcs", lambda output_dir=None: FAKE_OPS
    )
    model_id = upload_model(client, LV_MODEL_PATH)["model_id"]

    obs = {
        "protocol_info": {"pre_times": [0.0], "sim_times": [[5]], "params_to_change": {}},
        "data_items": [
            {
                "variable": "hr",
                "name_for_plotting": "HR",
                "data_type": "constant",
                "operation": "dbl",
                "operands": ["Lotka_Volterra_module/x"],
                "value": 60,
                "experiment_idx": 0,
                "plot_type": "horizontal",
            }
        ],
    }
    up = client.post("/api/obs_data/upload", json={"model_id": model_id, "obs_data": obs})
    assert up.status_code == 200, up.text

    resp = client.post(
        "/api/simulate",
        json={
            "model_id": model_id,
            "params": {},
            "sim_time": 5,
            "outputs": ["Lotka_Volterra_module/x"],
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "output_series" in body
    raw = body["outputs"]["Lotka_Volterra_module/x"]
    # dbl(series_output=True) == 2*x, elementwise over the raw operand — the
    # transformed series, not the raw operand (issue #111).
    assert body["output_series"]["0"] == pytest.approx([2.0 * v for v in raw])


def test_simulate_without_obs_data_has_no_output_series(client, fake_helper):
    model_id = upload_model(client, LV_MODEL_PATH)["model_id"]
    resp = client.post("/api/simulate", json={"model_id": model_id, "params": {}})
    assert resp.status_code == 200, resp.text
    assert "output_series" not in resp.json()
