"""The six routes, and the job behind the one that does the work.

Extraction is a job because the per-sweep commentary is the product: "sweep 3
returned NaN", "no stimulus detected", "this operation does not accept
spike_min_thresh". Collapsing that into a warnings array on one response loses
both the ordering and the progress, and a few hundred recordings take minutes.

It runs on a **thread** rather than a subprocess -- there is no heavy dependency
set to escape -- so cancellation is cooperative and these tests check that a
stopped run stops and does not wedge the next one.

The corpus is CSV, for the same reason as the build tests: the routes are
under test, not the file format.
"""

from __future__ import annotations

import json
import os
import time

import numpy as np
import pytest

from obs_extract import config as C, discover
from obs_extract_fixtures import step, write_csv

# Every extraction here builds a clamp command trace, which needs scipy.
pytestmark = [pytest.mark.unit, pytest.mark.usefixtures("requires_scipy")]


def _corpus(root, n_sweeps=2, n=200):
    (root / "4AP").mkdir(exist_ok=True)
    sweeps = [[step(n, -70.0, -70.0 + 8.0 * (s + 1), lo=50, hi=150),
               step(n, 0.0, 20.0 * (s + 1), lo=50, hi=150)]
              for s in range(n_sweeps)]
    write_csv(root / "4AP" / "200926_001.1.Currentsteps.1.csv", sweeps, dt=1e-4)
    return root


def _config(root):
    cfg = C.merge_scan(C.new_config("demo", str(root)), discover(str(root)))
    cfg["subprotocols"]["4AP|Currentsteps"].update(used=True, input="current",
                                                   features=[{
        "operation": "max_in_range", "unit": "milliV", "unit_confirmed": True,
        "operation_kwargs": {}, "std": {"mode": "absolute", "value": 4.0},
        "name_suffix": "vmax"}])
    for d in cfg["datasets"]:
        d["used"] = True
    cfg["model_binding"].update(
        current_command_param="soma/I_in", voltage_command_param="soma/V_set",
        measured_voltage_variable="soma/V", measured_current_variable="soma/I")
    return cfg


def _wait(client, job_id, timeout=20.0):
    """Poll the way the frontend store does, accumulating by offset."""
    deadline = time.time() + timeout
    offset, lines = 0, []
    while time.time() < deadline:
        resp = client.get(f"/api/obs_extract/{job_id}/status", params={"offset": offset})
        assert resp.status_code == 200, resp.text
        body = resp.json()
        lines.extend(body["lines"])
        assert body["next_offset"] >= offset, "the offset must not go backwards"
        offset = body["next_offset"]
        if body["state"] != "running":
            body["lines"] = lines
            return body
        time.sleep(0.05)
    raise AssertionError("extraction did not finish")


# ---------------------------------------------------------------------------
def test_formats_lists_all_four(client):
    body = client.get("/api/obs_extract/formats").json()
    assert {f["suffix"] for f in body["formats"]} == {".wcp", ".abf", ".csv", ".npy"}


def test_scan_groups_what_it_finds(client, tmp_path):
    resp = client.post("/api/obs_extract/scan", json={"root": str(_corpus(tmp_path))})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert len(body["datasets"]) == 1
    assert body["groups"][0]["group"] == "4AP|Currentsteps"
    assert body["datasets"][0]["readable"] is True


def test_scan_of_a_missing_directory_is_the_users_error(client, tmp_path):
    resp = client.post("/api/obs_extract/scan", json={"root": str(tmp_path / "nope")})
    assert resp.status_code == 422
    assert "not a directory" in resp.json()["detail"]


def test_scan_passes_reader_options_through(client, tmp_path):
    """So a .npy whose rate the user supplied stays readable across a rescan."""
    np.save(tmp_path / "x.1.Currentsteps.1.npy", np.zeros((2, 4)))
    plain = client.post("/api/obs_extract/scan", json={"root": str(tmp_path)}).json()
    assert plain["datasets"][0]["needs"] == ["sample_rate_hz"]

    fixed = client.post("/api/obs_extract/scan", json={
        "root": str(tmp_path),
        "reader_opts": {"x.1.Currentsteps.1.npy": {"sample_rate_hz": 1000.0}},
    }).json()
    assert fixed["datasets"][0]["readable"] is True


def test_config_round_trips_through_the_routes(client, tmp_path):
    cfg = _config(_corpus(tmp_path))
    saved = client.post("/api/obs_extract/config", json={
        "config": cfg, "output_dir": str(tmp_path), "filename": "c.json"})
    assert saved.status_code == 200, saved.text
    path = saved.json()["path"]
    assert os.path.isfile(path)

    loaded = client.get("/api/obs_extract/config", params={"path": path})
    assert loaded.status_code == 200, loaded.text
    back = loaded.json()["config"]
    assert back["subprotocols"]["4AP|Currentsteps"]["used"] is True


def test_saving_an_invalid_config_is_refused_with_the_key(client, tmp_path):
    cfg = _config(_corpus(tmp_path))
    cfg["datasets"][0]["uzed"] = True
    resp = client.post("/api/obs_extract/config",
                       json={"config": cfg, "output_dir": str(tmp_path)})
    assert resp.status_code == 422
    assert "uzed" in resp.json()["detail"]


def test_loading_a_non_json_path_is_refused(client, tmp_path):
    resp = client.get("/api/obs_extract/config", params={"path": str(tmp_path / "x.wcp")})
    assert resp.status_code == 422
    assert ".json" in resp.json()["detail"]


# ---------------------------------------------------------------------------
def test_a_run_produces_obs_data_a_config_and_a_report(client, tmp_path, requires_ca):
    root = _corpus(tmp_path)
    out = tmp_path / "outputs"
    resp = client.post("/api/obs_extract/run",
                       json={"config": _config(root), "output_dir": str(out)})
    assert resp.status_code == 200, resp.text
    body = _wait(client, resp.json()["job_id"])

    assert body["state"] == "done", body.get("error")
    result = body["result"]
    assert result["n_experiments"] == 2
    assert result["n_data_items"] == 2
    assert result["obs_data"]["data_items"]

    assert os.path.isfile(result["config_path"])
    assert os.path.isfile(result["tex_path"])
    # The saved config reloads, which is the whole point of saving it.
    assert json.loads(open(result["config_path"]).read())["name"] == "demo"


def test_the_log_is_streamed_by_offset(client, tmp_path, requires_ca):
    resp = client.post("/api/obs_extract/run",
                       json={"config": _config(_corpus(tmp_path)),
                             "output_dir": str(tmp_path / "o")})
    body = _wait(client, resp.json()["job_id"])
    assert any("[info]" in line for line in body["lines"])
    assert body["next_offset"] == len(body["lines"])


def test_a_second_run_while_one_is_going_is_refused(client, tmp_path, monkeypatch):
    """One extraction at a time, like every other analysis here."""
    from obs_extract import job as job_mod

    started = {"go": False}

    def slow_build(*_a, **_k):
        while not started["go"]:
            time.sleep(0.01)
        raise RuntimeError("stopped")

    monkeypatch.setattr(job_mod, "build_obs_data", slow_build)
    cfg = _config(_corpus(tmp_path))
    first = client.post("/api/obs_extract/run",
                        json={"config": cfg, "output_dir": str(tmp_path / "o")})
    assert first.status_code == 200
    second = client.post("/api/obs_extract/run",
                         json={"config": cfg, "output_dir": str(tmp_path / "o")})
    assert second.status_code == 409
    started["go"] = True


def test_cancelling_stops_the_run(client, tmp_path, monkeypatch):
    from obs_extract import job as job_mod

    seen = {}

    def watching_build(*_a, **kwargs):
        seen["cancelled"] = kwargs["cancelled"]
        for _ in range(200):
            if kwargs["cancelled"]():
                raise job_mod.ObsExtractError("extraction was cancelled")
            time.sleep(0.01)
        raise AssertionError("cancellation never arrived")

    monkeypatch.setattr(job_mod, "build_obs_data", watching_build)
    job_id = client.post("/api/obs_extract/run",
                         json={"config": _config(_corpus(tmp_path)),
                               "output_dir": str(tmp_path / "o")}).json()["job_id"]
    time.sleep(0.05)
    assert client.post(f"/api/obs_extract/{job_id}/cancel").json()["cancelled"] is True
    body = _wait(client, job_id)
    assert body["state"] == "cancelled"


def test_status_and_cancel_404_on_an_unknown_job(client):
    assert client.get("/api/obs_extract/nope/status").status_code == 404
    assert client.post("/api/obs_extract/nope/cancel").status_code == 404


def test_a_failing_run_reports_the_reason_without_a_traceback(client, tmp_path):
    """An ObsExtractError is the user's config or their files, so it is a
    message; anything else is a fault and keeps its traceback."""
    cfg = _config(_corpus(tmp_path))
    for d in cfg["datasets"]:
        d["used"] = False
    resp = client.post("/api/obs_extract/run",
                       json={"config": cfg, "output_dir": str(tmp_path / "o")})
    body = _wait(client, resp.json()["job_id"])
    assert body["state"] == "error"
    assert "nothing to extract" in body["error"]
    assert "Traceback" not in "\n".join(body["lines"])


def test_a_run_recovers_for_the_next_one(client, tmp_path, requires_ca):
    """A finished job must not leave the manager busy."""
    cfg = _config(_corpus(tmp_path))
    first = client.post("/api/obs_extract/run",
                        json={"config": cfg, "output_dir": str(tmp_path / "o")})
    _wait(client, first.json()["job_id"])
    second = client.post("/api/obs_extract/run",
                         json={"config": cfg, "output_dir": str(tmp_path / "o2")})
    assert second.status_code == 200
    _wait(client, second.json()["job_id"])
