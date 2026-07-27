"""Tests for saving/loading manual parameter vectors (issue #106)."""

from __future__ import annotations

import numpy as np
import pytest

from param_io import (
    ParamIOError,
    format_for,
    load_param_values,
    save_param_values,
)

ORDER = ["heart/R", "aorta/C", "global/E"]
VALUES = {"heart/R": 1.5, "aorta/C": 2.0e-8, "global/E": 3.3e8}


def test_format_for():
    assert format_for("manual_params.npy") == "npy"
    assert format_for("x.CSV") == "csv"
    assert format_for("noext") == "npy"


def test_npy_round_trip_preserves_order(tmp_path):
    path = save_param_values(VALUES, ORDER, str(tmp_path), "manual_params.npy")
    assert path.endswith("/manual_params.npy")
    # Bare array in ORDER, matching CA's best_param_vals.npy convention.
    arr = np.load(path)
    assert list(arr) == [1.5, 2.0e-8, 3.3e8]
    # Reload with the same order -> the original mapping.
    assert load_param_values(path, ORDER) == pytest.approx(VALUES)


def test_csv_round_trip_is_self_describing(tmp_path):
    path = save_param_values(VALUES, ORDER, str(tmp_path), "manual_params.csv")
    text = (tmp_path / "manual_params.csv").read_text()
    assert text.splitlines()[0] == "vessel_name,param_name,value"
    assert "heart,R," in text
    # CSV carries names, so no order needed to reload.
    assert load_param_values(path) == pytest.approx(VALUES)


def test_npy_load_rejects_length_mismatch(tmp_path):
    path = save_param_values(VALUES, ORDER, str(tmp_path), "p.npy")
    with pytest.raises(ParamIOError, match="current parameters"):
        load_param_values(path, ["only/one"])


def test_save_rejects_path_separators_in_name(tmp_path):
    with pytest.raises(ParamIOError, match="bare file name"):
        save_param_values(VALUES, ORDER, str(tmp_path), "sub/dir/x.npy")


def test_save_rejects_missing_value(tmp_path):
    with pytest.raises(ParamIOError, match="missing values"):
        save_param_values({"heart/R": 1.0}, ORDER, str(tmp_path), "p.npy")


def test_load_missing_file(tmp_path):
    with pytest.raises(ParamIOError, match="not found"):
        load_param_values(str(tmp_path / "nope.npy"), ORDER)


def test_load_csv_missing_column(tmp_path):
    (tmp_path / "bad.csv").write_text("vessel_name,param_name\nheart,R\n")
    with pytest.raises(ParamIOError, match="missing required column"):
        load_param_values(str(tmp_path / "bad.csv"))


# ---------------------------------------------------------------------------
# routes
# ---------------------------------------------------------------------------
def test_route_save_then_load_npy(client, tmp_path):
    out = str(tmp_path / "outs")
    resp = client.post("/api/params/save", json={
        "values": VALUES, "order": ORDER, "filename": "manual_params.npy", "output_dir": out,
    })
    assert resp.status_code == 200, resp.text
    saved = resp.json()["path"]
    assert saved.endswith("manual_params.npy")

    resp = client.post("/api/params/load", json={"path": saved, "order": ORDER})
    assert resp.status_code == 200, resp.text
    assert resp.json()["values"] == pytest.approx(VALUES)


def test_route_save_rejects_relative_output_dir(client):
    resp = client.post("/api/params/save", json={
        "values": VALUES, "order": ORDER, "filename": "p.npy", "output_dir": "rel/dir",
    })
    assert resp.status_code == 422


def test_route_load_bad_file_422(client, tmp_path):
    resp = client.post("/api/params/load", json={"path": str(tmp_path / "missing.npy"), "order": ORDER})
    assert resp.status_code == 422
