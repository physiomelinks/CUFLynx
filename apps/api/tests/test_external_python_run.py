"""Integration tier: actually running an external_python model through CA.

Needs circulatory_autogen's ``external_simulation_helper`` (``model_type
external_python`` / ``solver external``) on the sibling checkout. The unit tier
covers the metadata, the routes and the plot store without it; what only these
can show is that the contract CUFLynx parses by AST is the contract CA loads by
import, and that a figure the user's class draws reaches an HTTP response.

The model is ``tests/data/heat1d_external_model.py`` — this repo's own fixture,
not CA's ``funcs_user`` example, so the two can be changed independently.
"""

from pathlib import Path

import numpy as np
import pytest

FIXTURE = Path(__file__).resolve().parent / "data" / "heat1d_external_model.py"

OUTPUTS = ["heat/T_p1", "heat/T_p2", "heat/T_p3"]


def _upload(client):
    resp = client.post(
        "/api/models/upload",
        files={"file": ("heat1d_external_model.py", FIXTURE.read_bytes(), "text/x-python")},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["model_format"] == "external_python"
    return resp.json()["model_id"]


def _select_external_backend(client, user_config=None):
    payload = {
        "generated_model_format": "external_python",
        "solver": "external",
        "solver_info": {"dt": 0.01, **({"user_config": user_config} if user_config else {})},
    }
    resp = client.post("/api/config", json=payload)
    assert resp.status_code == 200, resp.text
    return resp.json()


@pytest.mark.integration
def test_the_backend_is_offered_by_the_live_config(client, requires_simulation):
    """CA advertises external_python in SOLVER_SCHEMA; CUFLynx must pass it
    through rather than filter it out with cpp/OpenCOR."""
    opts = client.get("/api/config").json()
    assert "external_python" in opts["model_formats"]
    assert opts["solvers_by_format"]["external_python"] == ["external"]
    assert any(f["key"] == "user_config" for f in opts["solver_info_schema"]["external"])


@pytest.mark.integration
def test_simulate_runs_the_users_solver(client, requires_simulation):
    model_id = _upload(client)
    _select_external_backend(client)
    resp = client.post(
        "/api/simulate",
        json={"model_id": model_id, "params": {}, "sim_time": 0.2, "pre_time": 0.0,
              "outputs": OUTPUTS},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    time = np.array(body["time"])
    assert len(time) > 1 and np.all(np.diff(time) > 0)
    for name in OUTPUTS:
        series = np.array(body["outputs"][name])
        assert series.shape == time.shape
        assert np.all(np.isfinite(series))
    # The mid-rod node starts at the peak and diffuses away, so it must be the
    # hottest of the three -- i.e. the model really ran, rather than returning
    # a same-shaped array of nothing.
    assert np.max(body["outputs"]["heat/T_p2"]) > np.max(body["outputs"]["heat/T_p1"])


@pytest.mark.integration
def test_a_parameter_change_changes_the_outputs(client, requires_simulation):
    """set_param_vals reaches the user's class: a higher conductivity flattens
    the peak faster."""
    model_id = _upload(client)
    _select_external_backend(client)

    def peak(k):
        resp = client.post(
            "/api/simulate",
            json={"model_id": model_id, "params": {"heat/k": k}, "sim_time": 0.5,
                  "pre_time": 0.0, "outputs": ["heat/T_p2"]},
        )
        assert resp.status_code == 200, resp.text
        return float(np.array(resp.json()["outputs"]["heat/T_p2"])[-1])

    assert peak(2.0) < peak(0.2)


@pytest.mark.integration
def test_simulate_returns_the_models_own_figures(client, requires_simulation):
    """The whole extra-figure pipeline: extra_plots -> CA's get_extra_figures ->
    PNG under the uploads dir -> a URL in the response -> the route serving it."""
    model_id = _upload(client)
    _select_external_backend(client)
    resp = client.post(
        "/api/simulate",
        json={"model_id": model_id, "params": {}, "sim_time": 0.2, "pre_time": 0.0,
              "outputs": OUTPUTS},
    )
    assert resp.status_code == 200, resp.text
    plots = resp.json()["solver_plots"]
    assert len(plots) == 1
    assert plots[0]["index"] == 0
    assert plots[0]["title"] == "Rod temperature over time"
    assert plots[0]["url"].startswith(f"/api/models/{model_id}/solver_plots/")

    image = client.get(plots[0]["url"])
    assert image.status_code == 200
    assert image.headers["content-type"] == "image/png"
    assert image.content[:8] == b"\x89PNG\r\n\x1a\n"


@pytest.mark.integration
def test_the_plot_token_changes_between_runs(client, requires_simulation):
    """Each run's figures are a distinct resource, so the browser can cache one
    URL forever and still see the new picture after a slider move."""
    model_id = _upload(client)
    _select_external_backend(client)
    urls = []
    for _ in range(2):
        resp = client.post(
            "/api/simulate",
            json={"model_id": model_id, "params": {}, "sim_time": 0.2, "pre_time": 0.0,
                  "outputs": OUTPUTS},
        )
        assert resp.status_code == 200, resp.text
        urls.append(resp.json()["solver_plots"][0]["url"])
    assert urls[0] != urls[1]
    # Two tokens are kept, so both are still served.
    assert client.get(urls[0]).status_code == 200
    assert client.get(urls[1]).status_code == 200


@pytest.mark.integration
def test_protocol_run_drives_the_external_helper(client, requires_simulation):
    """CA's ProtocolRunner is backend-agnostic; an external model has to work
    through it unchanged, extra figures included."""
    model_id = _upload(client)
    _select_external_backend(client)
    resp = client.post(
        "/api/protocol/run",
        json={
            "model_id": model_id,
            "protocol_info": {"pre_times": [0.0], "sim_times": [[0.2]], "params_to_change": {}},
            "params": {},
            "outputs": OUTPUTS,
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    experiments = body["experiments"]
    assert len(experiments) == 1
    time = np.array(experiments[0]["time"])
    assert len(time) > 1
    for name in OUTPUTS:
        assert len(experiments[0]["outputs"][name]) == len(time)
    assert body["solver_plots"][0]["title"] == "Rod temperature over time"


@pytest.mark.integration
def test_a_real_worker_returns_the_same_run_and_the_same_figures(
    client, requires_simulation
):
    """The two tiers must agree, figures included. The worker writes the PNGs
    into a directory the parent named and returns only titles, so this is the
    one test that proves the wire carries them at all."""
    import sys

    import engine as engine_mod

    model_id = _upload(client)
    _select_external_backend(client)
    request = {"model_id": model_id, "params": {}, "sim_time": 0.2, "pre_time": 0.0,
               "outputs": OUTPUTS}

    engine_mod.engine.worker_python = None
    in_process = client.post("/api/simulate", json=request)
    assert in_process.status_code == 200, in_process.text

    # sys.executable is this environment, so force the worker path explicitly
    # rather than relying on a second interpreter existing on the machine.
    engine_mod.engine.reset()
    engine_mod.engine.worker_python = sys.executable
    original = engine_mod._is_this_interpreter
    engine_mod._is_this_interpreter = lambda _p: False
    try:
        via_worker = client.post("/api/simulate", json=request)
    finally:
        engine_mod._is_this_interpreter = original
        engine_mod.engine.worker_python = None

    assert via_worker.status_code == 200, via_worker.text
    a = in_process.json()["outputs"]["heat/T_p2"]
    b = via_worker.json()["outputs"]["heat/T_p2"]
    assert len(a) == len(b)
    assert max(abs(x - y) for x, y in zip(a, b)) < 1e-9

    remote_plots = via_worker.json()["solver_plots"]
    assert [p["title"] for p in remote_plots] == [
        p["title"] for p in in_process.json()["solver_plots"]
    ]
    image = client.get(remote_plots[0]["url"])
    assert image.status_code == 200
    assert image.content[:8] == b"\x89PNG\r\n\x1a\n"


@pytest.mark.integration
def test_user_config_reaches_the_users_class(client, requires_simulation):
    """The one solver_info field this backend declares, free-form and handed over
    untouched -- the fixture reads ``initial_peak`` from it."""
    model_id = _upload(client)

    def final_peak(user_config):
        _select_external_backend(client, user_config=user_config)
        resp = client.post(
            "/api/simulate",
            json={"model_id": model_id, "params": {}, "sim_time": 0.2, "pre_time": 0.0,
                  "outputs": ["heat/T_p2"]},
        )
        assert resp.status_code == 200, resp.text
        return float(np.array(resp.json()["outputs"]["heat/T_p2"]).max())

    assert final_peak({"initial_peak": 3.0}) > final_peak({"initial_peak": 1.0})
