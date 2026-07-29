"""Saved runs: outputs stored beside the saved parameters (issue #126).

"Save current" used to write only the slider values, so comparing against a
saved parameter set meant re-running it. The traces are now saved under the same
prefix, and the pairing is the file naming: manual_params.npy is accompanied by
manual_params_outputs.json.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import saved_runs
from conftest import LV_MODEL_PATH, upload_model

RESULT = {"time": [0.0, 1.0, 2.0], "outputs": {"m/x": [1.0, 2.0, 3.0]}}
PROTOCOL_RESULT = {
    "experiments": [
        {"time": [0.0, 1.0], "outputs": {"m/x": [1.0, 2.0]}},
        {"time": [0.0, 1.0], "outputs": {"m/x": [3.0, 4.0]}},
    ]
}
PARAMS = {"m/alpha": 1.5, "m/beta": 0.25}


# ---------------------------------------------------------------------------
# Pairing by prefix
# ---------------------------------------------------------------------------
def test_the_outputs_file_sits_beside_the_params_under_the_same_prefix(tmp_path):
    params = tmp_path / "manual_params.npy"
    path = Path(saved_runs.save_run(params, PARAMS, RESULT))
    assert path == tmp_path / "manual_params_outputs.json"
    assert path.is_file()


def test_a_csv_params_file_pairs_the_same_way(tmp_path):
    path = saved_runs.outputs_path_for(tmp_path / "run3.csv")
    assert path.name == "run3_outputs.json"


def test_the_prefix_is_the_stem_of_the_params_name():
    assert saved_runs.prefix_for("manual_params.npy") == "manual_params"
    assert saved_runs.prefix_for("a.b.csv") == "a.b"


# ---------------------------------------------------------------------------
# What is stored
# ---------------------------------------------------------------------------
def test_a_single_run_stores_its_time_and_outputs(tmp_path):
    saved_runs.save_run(tmp_path / "r.npy", PARAMS, RESULT)
    rec = json.loads((tmp_path / "r_outputs.json").read_text())
    assert rec["time"] == [0.0, 1.0, 2.0]
    assert rec["outputs"]["m/x"] == [1.0, 2.0, 3.0]
    assert rec["prefix"] == "r"
    assert rec["saved_at"]


def test_a_protocol_run_keeps_its_experiments_separate(tmp_path):
    """Flattening them would draw experiment 1's trace on experiment 0's axes."""
    saved_runs.save_run(tmp_path / "r.npy", PARAMS, PROTOCOL_RESULT)
    rec = json.loads((tmp_path / "r_outputs.json").read_text())
    assert len(rec["experiments"]) == 2
    assert rec["experiments"][1]["outputs"]["m/x"] == [3.0, 4.0]
    assert "outputs" not in rec  # not also flattened


def test_the_parameters_are_stored_in_the_run_too(tmp_path):
    """The npy is a bare array that only means anything against the current qname
    order; the slider markers need named values regardless."""
    saved_runs.save_run(tmp_path / "r.npy", PARAMS, RESULT)
    rec = json.loads((tmp_path / "r_outputs.json").read_text())
    assert rec["params"] == PARAMS


def test_series_are_coerced_to_plain_floats(tmp_path):
    """numpy scalars would not survive json.dumps."""
    import numpy as np

    result = {"time": np.array([0.0, 1.0]), "outputs": {"m/x": np.array([1.0, 2.0])}}
    saved_runs.save_run(tmp_path / "r.npy", PARAMS, result)
    rec = json.loads((tmp_path / "r_outputs.json").read_text())
    assert rec["outputs"]["m/x"] == [1.0, 2.0]


def test_variables_in_covers_both_shapes():
    assert saved_runs.variables_in({"outputs": {"m/x": [], "m/y": []}}) == ["m/x", "m/y"]
    assert saved_runs.variables_in(PROTOCOL_RESULT | {"outputs": {}}) == ["m/x"]
    assert saved_runs.variables_in({}) == []


# ---------------------------------------------------------------------------
# Listing
# ---------------------------------------------------------------------------
def test_list_runs_reports_metadata_without_the_series(tmp_path):
    """A run holds every plotted trace; listing ten in full to draw ten
    checkboxes would ship megabytes."""
    saved_runs.save_run(tmp_path / "a.npy", PARAMS, RESULT)
    runs = saved_runs.list_runs(tmp_path)
    assert len(runs) == 1
    run = runs[0]
    assert run["prefix"] == "a"
    assert run["params"] == PARAMS
    assert run["variables"] == ["m/x"]
    assert "outputs" not in run and "time" not in run


def test_list_runs_is_newest_first(tmp_path):
    saved_runs.save_run(tmp_path / "old.npy", PARAMS, RESULT)
    rec = json.loads((tmp_path / "old_outputs.json").read_text())
    rec["saved_at"] = "2000-01-01T00:00:00+00:00"
    (tmp_path / "old_outputs.json").write_text(json.dumps(rec))
    saved_runs.save_run(tmp_path / "new.npy", PARAMS, RESULT)

    assert [r["prefix"] for r in saved_runs.list_runs(tmp_path)] == ["new", "old"]


def test_a_missing_directory_lists_nothing_rather_than_erroring(tmp_path):
    assert saved_runs.list_runs(tmp_path / "nope") == []


def test_a_corrupt_run_is_skipped_not_fatal(tmp_path):
    """One bad file must not hide every other saved run."""
    saved_runs.save_run(tmp_path / "good.npy", PARAMS, RESULT)
    (tmp_path / "bad_outputs.json").write_text("{not json")
    assert [r["prefix"] for r in saved_runs.list_runs(tmp_path)] == ["good"]


def test_unrelated_json_in_the_directory_is_ignored(tmp_path):
    saved_runs.save_run(tmp_path / "good.npy", PARAMS, RESULT)
    (tmp_path / "obs_data.json").write_text("{}")
    assert [r["prefix"] for r in saved_runs.list_runs(tmp_path)] == ["good"]


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------
def test_load_run_returns_the_series(tmp_path):
    path = saved_runs.save_run(tmp_path / "r.npy", PARAMS, RESULT)
    rec = saved_runs.load_run(path)
    assert rec["outputs"]["m/x"] == [1.0, 2.0, 3.0]
    assert rec["params"] == PARAMS


def test_load_run_rejects_a_missing_file(tmp_path):
    with pytest.raises(saved_runs.SavedRunError, match="not found"):
        saved_runs.load_run(tmp_path / "nope.json")


def test_load_run_rejects_a_non_record(tmp_path):
    (tmp_path / "x.json").write_text("[1, 2, 3]")
    with pytest.raises(saved_runs.SavedRunError, match="not a saved run"):
        saved_runs.load_run(tmp_path / "x.json")


# ---------------------------------------------------------------------------
# Through the API
# ---------------------------------------------------------------------------
def test_save_params_also_saves_the_outputs(client, tmp_path):
    upload_model(client, LV_MODEL_PATH)
    resp = client.post(
        "/api/params/save",
        json={
            "values": PARAMS,
            "order": list(PARAMS),
            "filename": "run_a.csv",
            "output_dir": str(tmp_path),
            "result": RESULT,
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["outputs_path"].endswith("run_a_outputs.json")
    assert body["outputs_error"] is None
    assert Path(body["path"]).is_file()
    assert Path(body["outputs_path"]).is_file()


def test_saving_without_a_result_writes_params_only(client, tmp_path):
    """Nothing has been run yet — the params are still worth saving."""
    resp = client.post(
        "/api/params/save",
        json={
            "values": PARAMS,
            "order": list(PARAMS),
            "filename": "run_b.csv",
            "output_dir": str(tmp_path),
        },
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["outputs_path"] is None
    assert not (tmp_path / "run_b_outputs.json").exists()


def test_a_failed_outputs_write_does_not_lose_the_parameters(client, tmp_path, monkeypatch):
    """Saving the parameters is the user's action; the outputs ride along."""
    def boom(*_a, **_kw):
        raise OSError(28, "No space left on device", str(tmp_path / "x_outputs.json"))

    monkeypatch.setattr(saved_runs, "save_run", boom)
    resp = client.post(
        "/api/params/save",
        json={
            "values": PARAMS,
            "order": list(PARAMS),
            "filename": "run_c.csv",
            "output_dir": str(tmp_path),
            "result": RESULT,
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert Path(body["path"]).is_file()  # the params still landed
    assert body["outputs_path"] is None
    assert "disk is full" in body["outputs_error"]


def test_runs_endpoint_lists_what_was_saved(client, tmp_path):
    saved_runs.save_run(tmp_path / "a.npy", PARAMS, RESULT)
    resp = client.get("/api/runs", params={"dir": str(tmp_path)})
    assert resp.status_code == 200, resp.text
    runs = resp.json()["runs"]
    assert [r["prefix"] for r in runs] == ["a"]
    assert runs[0]["params"] == PARAMS


def test_runs_load_endpoint_returns_the_series(client, tmp_path):
    path = saved_runs.save_run(tmp_path / "a.npy", PARAMS, RESULT)
    resp = client.get("/api/runs/load", params={"path": path})
    assert resp.status_code == 200, resp.text
    assert resp.json()["outputs"]["m/x"] == [1.0, 2.0, 3.0]


def test_runs_load_endpoint_422s_on_a_missing_file(client, tmp_path):
    resp = client.get("/api/runs/load", params={"path": str(tmp_path / "nope.json")})
    assert resp.status_code == 422
    assert "not found" in resp.json()["detail"]
