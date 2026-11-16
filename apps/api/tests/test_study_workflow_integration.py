"""The study workflow end to end: real runs through the analysis routes on the
Lotka-Volterra fixture, each one a step of ``<W>/workflow/calibration_workflow.json``.

Small budgets, so the whole file runs in a few minutes: a short GA, 16 Sobol samples, a
local analysis by finite differences, a Laplace approximation and a 12-step MCMC chain.
"""

from __future__ import annotations

import json
import math
import time
from pathlib import Path

import pytest

from conftest import (
    LV_MODEL_PATH,
    LV_OBS_DATA_PATH,
    LV_PARAMS_CSV_PATH,
    upload_model,
)

pytestmark = pytest.mark.integration

LV = "Lotka_Volterra_module"
QNAMES = [f"{LV}/{p}" for p in ("alpha", "beta", "delta", "gamma")]


def _setup(client, work: Path) -> str:
    model_id = upload_model(client, LV_MODEL_PATH)["model_id"]
    obs = json.loads(LV_OBS_DATA_PATH.read_text())
    r = client.post("/api/obs_data/upload", json={"model_id": model_id, "obs_data": obs})
    assert r.status_code == 200, r.text
    with open(LV_PARAMS_CSV_PATH, "rb") as fh:
        r = client.post(f"/api/params_for_id/upload?model_id={model_id}",
                        files={"file": (LV_PARAMS_CSV_PATH.name, fh, "text/csv")})
    assert r.status_code == 200, r.text
    return model_id


def _wait(client, kind: str, job_id: str, timeout: float = 300) -> tuple[dict, list]:
    offset, lines = 0, []
    deadline = time.time() + timeout
    while time.time() < deadline:
        s = client.get(f"/api/{kind}/{job_id}/status?offset={offset}").json()
        lines += s["lines"]
        offset = s["next_offset"]
        if s["state"] != "running":
            return s, lines
        time.sleep(0.2)
    raise AssertionError(f"{kind} did not finish:\n" + "\n".join(lines[-60:]))


def _run(client, kind: str, payload: dict) -> tuple[dict, list]:
    r = client.post(f"/api/{kind}/run", json=payload)
    assert r.status_code == 200, r.text
    status, lines = _wait(client, kind, r.json()["job_id"])
    assert status["state"] == "done", "\n".join(lines[-80:])
    if kind in ("calibration", "uq"):
        # the live progress reads the step's run directory
        status["progress"] = client.get(f"/api/{kind}/{status['job_id']}/progress").json()
    return status, lines


def _steps(work: Path) -> list[dict]:
    raw = json.loads((work / "workflow" / "calibration_workflow.json").read_text())
    return raw["steps"]


def test_every_run_is_a_step_of_one_study_workflow(client, requires_simulation,
                                                   study_workflow_on, tmp_path):
    work = tmp_path / "study"
    model_id = _setup(client, work)
    common = {"config_outputs_dir": str(work), "dt": 0.01}
    sliders = {q: v for q, v in zip(QNAMES, (2.0, 0.4, 0.3, 1.5))}

    # 1. a calibration from the slider values: a set step, then the calibration from it
    cal, lines = _run(client, "calibration", {
        "model_id": model_id, "current_params": sliders,
        "settings": {**common, "param_id_method": "genetic_algorithm", "start_from": "current",
                     "num_calls_to_function": 30, "DEBUG": True}})
    assert set(cal["best_params"]) == set(QNAMES)
    assert all(math.isfinite(v) for v in cal["best_params"].values())
    assert cal["study_step"]["step"] == "calibration-1"
    assert cal["percent_error"] and cal["error_labels"], "\n".join(lines[-40:])
    assert cal["calibrated_model_path"] and Path(cal["calibrated_model_path"]).is_file()
    assert cal["progress"]["cost_history"], cal["progress"]

    # 2. Sobol
    sobol, lines = _run(client, "sensitivity", {
        "model_id": model_id,
        "settings": {**common, "method": "sobol", "num_samples": 16}})
    assert sobol["indices"] and sobol["param_names"], "\n".join(lines[-40:])

    # 3. local sensitivity at the sliders
    local, lines = _run(client, "sensitivity", {
        "model_id": model_id, "current_params": sliders,
        "settings": {**common, "method": "local", "nominal": "current",
                     "gradient_method": "FD"}})
    assert local["indices"] and local["nominal"] == pytest.approx(list(sliders.values()))

    # 4. Laplace at the calibration's best fit
    laplace, lines = _run(client, "uq", {
        "model_id": model_id, "settings": {**common, "method": "laplace"}})
    assert laplace["method"] == "laplace" and laplace["params"], "\n".join(lines[-40:])

    # 5. a short MCMC chain at the same best fit
    mcmc, lines = _run(client, "uq", {
        "model_id": model_id,
        "settings": {**common, "method": "mcmc", "num_steps": 12, "num_walkers": 8}})
    assert mcmc["method"] == "mcmc" and mcmc["params"], "\n".join(lines[-40:])
    assert mcmc["progress"]["steps"] == 12, mcmc["progress"]

    steps = _steps(work)
    assert [s["id"] for s in steps] == ["sliders-1", "calibration-1", "sobol-1", "sliders-2",
                                        "local-sa-1", "laplace-1", "mcmc-1"]
    by_id = {s["id"]: s for s in steps}
    assert by_id["sliders-1"]["kind"] == "set"
    assert {e["reason"] for e in by_id["sliders-1"]["set"]} == {"slider values in CUFLynx"}
    assert by_id["calibration-1"]["start_from"] == "sliders-1"
    assert by_id["local-sa-1"]["result_from"] == "sliders-2"
    assert by_id["laplace-1"]["result_from"] == "calibration-1"
    assert by_id["mcmc-1"]["result_from"] == "calibration-1"

    run = work / "workflows" / "study"
    svg = (run / "provenance.svg").read_text()
    assert "sliders-1" in svg and "calibration-1" in svg
    # the calibrated values are in the diagram
    alpha = cal["best_params"][f"{LV}/alpha"]
    assert f"{alpha:.3g}" in svg or f"{alpha:.4g}" in svg

    # the Workflow tab has the study workflow open, with every step done
    described = client.get("/api/workflow").json()
    assert described["loaded"] and described["path"] == str(
        work / "workflow" / "calibration_workflow.json")
    assert described["provenance"].get("svg")
    state = described["status"]["steps"]
    assert {s for s, st in state.items() if st["status"] == "done" and not st["stale"]} == {
        s["id"] for s in steps}


def _heat1d_dir() -> Path | None:
    """CA's external-model example (``funcs_user/example_model_external``), or None."""
    import os

    root = os.environ.get("CIRCULATORY_AUTOGEN_SRC")
    found = Path(root, "funcs_user", "example_model_external") if root else None
    return found if found and (found / "heat1d_model.py").is_file() else None


def test_an_external_python_calibration_is_a_step_with_an_external_target(
        client, requires_simulation, study_workflow_on, tmp_path):
    heat = _heat1d_dir()
    if heat is None:
        pytest.skip("CA's heat1d example is not in the configured CA dir")
    work = tmp_path / "heat"
    model = heat / "heat1d_model.py"
    r = client.post("/api/models/upload",
                    files={"file": (model.name, model.read_bytes(), "text/x-python")})
    assert r.status_code == 200, r.text
    model_id = r.json()["model_id"]
    r = client.post("/api/config", json={"generated_model_format": "external_python",
                                         "solver": "external", "solver_info": {"dt": 0.005}})
    assert r.status_code == 200, r.text
    obs = json.loads((heat / "heat1d_obs_data.json").read_text())
    r = client.post("/api/obs_data/upload", json={"model_id": model_id, "obs_data": obs})
    assert r.status_code == 200, r.text
    params = heat / "heat1d_k_params_for_id.json"
    r = client.post(f"/api/params_for_id/upload?model_id={model_id}",
                    files={"file": (params.name, params.read_bytes(), "application/json")})
    assert r.status_code == 200, r.text

    cal, lines = _run(client, "calibration", {
        "model_id": model_id,
        "settings": {"config_outputs_dir": str(work), "dt": 0.005,
                     "param_id_method": "CMA-ES", "num_calls_to_function": 40}})
    assert 0.05 <= cal["best_params"]["heat/k"] <= 1.0
    (step,) = _steps(work)
    assert step["id"] == "calibration-1"
    raw = json.loads((work / "workflow" / "calibration_workflow.json").read_text())
    assert "external_model_path" in raw["target"] and step["model"] == raw["target"]
    assert (work / "workflows" / "study" / "provenance.svg").is_file()
