"""The cost calibration reports must be the cost the Output plots show (heat1d bug).

A user ran a GA calibration on the heat1d external model (protocol-less obs_data),
read the final cost, clicked "reset to best fit", and the cost above the Output
plots was a different number for the *same* parameters. The cause was two silent,
different timeline fallbacks: the calibration runner simulated over its own
``sim_time`` default (2.0 s) while the live cost ran over the top bar's t₁ —
nothing ever sent the top bar's times with the run payload, and a mean-over-time
feature depends on the window it is averaged over.

The frontend fix sends ``sim_time`` / ``pre_time`` with every analysis payload
(App.test.js pins that). This test guards the whole seam end to end: a real
calibration run through the runner subprocess, with the times the fixed frontend
sends, must report a cost that matches the live ``/api/simulate`` cost at the
best-fit parameters to float precision. It fails both if the runner stops
honouring ``settings['sim_time']`` and if the two cost computations ever diverge
(aggregation, std/weight handling, operation dispatch).

``sim_time`` here is deliberately NOT the runner's 2.0 fallback, so a regression
to the fallback shows up as a cost mismatch rather than a coincidental pass.
"""

import json
import math
import sys
import time
from pathlib import Path

import pytest

FIXTURE = Path(__file__).resolve().parent / "data" / "heat1d_external_model.py"

SIM_TIME = 0.6  # != the runner's historical 2.0 fallback, by design
PRE_TIME = 0.0
DT = 0.01

# Protocol-less on purpose: with a protocol_info, CA's timing wins on both sides
# and the bug cannot occur. Bare data_items are exactly the heat1d example's shape.
OBS_DATA = [
    {
        "data_item_name": f"probe {i} mean temperature",
        "trace_name_for_plotting": f"mean(T_p{i})",
        "data_type": "constant",
        "operation": "mean",
        "operands": [f"heat/T_p{i}"],
        "unit": "dimensionless",
        "weight": 1.0,
        "value": value,
        "std": 0.05,
        "cost_type": "gaussian_MLE",
    }
    for i, value in ((1, 0.30), (2, 0.28), (3, 0.20))
]

PARAMS_CSV = (
    "vessel_name,param_name,param_type,min,max,name_for_plotting\n"
    "heat,k,const,0.05,1.0,k\n"
    "heat,u_D,const,-0.5,0.5,u_D\n"
)


def _upload_study(client):
    resp = client.post(
        "/api/models/upload",
        files={"file": ("heat1d_external_model.py", FIXTURE.read_bytes(), "text/x-python")},
    )
    assert resp.status_code == 200, resp.text
    model_id = resp.json()["model_id"]

    resp = client.post(f"/api/obs_data/upload?model_id={model_id}", json=OBS_DATA)
    assert resp.status_code == 200, resp.text

    resp = client.post(
        f"/api/params_for_id/upload?model_id={model_id}",
        files={"file": ("heat1d_params_for_id.csv", PARAMS_CSV.encode(), "text/csv")},
    )
    assert resp.status_code == 200, resp.text
    return model_id


def _wait_for(client, job_id, timeout_s=240):
    deadline = time.monotonic() + timeout_s
    offset = 0
    lines = []
    while time.monotonic() < deadline:
        resp = client.get(f"/api/calibration/{job_id}/status?offset={offset}")
        assert resp.status_code == 200, resp.text
        status = resp.json()
        offset = status["next_offset"]
        lines += status["lines"]
        if status["state"] in ("done", "error", "cancelled"):
            return status, lines
        time.sleep(1.0)
    pytest.fail(f"calibration did not finish in {timeout_s}s; log tail: {lines[-15:]}")


def test_reported_calibration_cost_matches_the_live_cost_at_best_fit(
    client, requires_simulation, tmp_path
):
    model_id = _upload_study(client)

    config = client.post(
        "/api/config",
        json={
            "generated_model_format": "external_python",
            "solver": "external",
            "solver_info": {"dt": DT},
        },
    )
    assert config.status_code == 200, config.text

    # The settings the FIXED frontend sends: the top bar's t₁/pre travel with the
    # run payload (App.vue runTimes()).
    resp = client.post(
        "/api/calibration/run",
        json={
            "model_id": model_id,
            "settings": {
                "param_id_method": "genetic_algorithm",
                # The user's exact run: 100 calls, DEBUG on. DEBUG also matters
                # mechanically -- CA's default GA population (744) exceeds any
                # small call budget; the DEBUG optimiser options shrink it.
                "num_calls_to_function": 100,
                "DEBUG": True,
                "num_cores": 1,
                "dt": DT,
                "sim_time": SIM_TIME,
                "pre_time": PRE_TIME,
                "python_path": sys.executable,
                "config_outputs_dir": str(tmp_path / "outputs"),
            },
        },
    )
    assert resp.status_code == 200, resp.text
    job_id = resp.json()["job_id"]

    status, lines = _wait_for(client, job_id)
    assert status["state"] == "done", f"error={status.get('error')}; log tail: {lines[-15:]}"
    reported_cost = status["cost"]
    best_params = status["best_params"]
    assert isinstance(reported_cost, (int, float)), status
    assert best_params, status

    # "Reset to best fit" then a run at the top bar's times: the number above the
    # Output plots.
    resp = client.post(
        "/api/simulate",
        json={
            "model_id": model_id,
            "params": best_params,
            "outputs": ["heat/T_p1", "heat/T_p2", "heat/T_p3"],
            "sim_time": SIM_TIME,
            "pre_time": PRE_TIME,
        },
    )
    assert resp.status_code == 200, resp.text
    live = resp.json().get("cost")
    assert live is not None, "live cost missing from the simulate response"
    live_cost = live["cost"]

    assert math.isclose(live_cost, reported_cost, rel_tol=1e-6, abs_tol=1e-9), (
        f"calibration reported {reported_cost} but the same parameters cost "
        f"{live_cost} on the Output plots — the two tiers are simulating "
        f"different timelines or scoring differently"
    )
