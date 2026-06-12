import numpy as np
import pytest

from conftest import BG_MODEL_PATH, LV_MODEL_PATH, upload_model


# ---------------------------------------------------------------------------
# Unit tier (fake helper, no Myokit)
# ---------------------------------------------------------------------------
def test_simulate_endpoint_calls_set_param_vals(client, fake_helper):
    model_id = upload_model(client, LV_MODEL_PATH)["model_id"]
    resp = client.post(
        "/api/simulate",
        json={
            "model_id": model_id,
            "params": {"Lotka_Volterra_module/alpha": 3.0},
            "sim_time": 5,
        },
    )
    assert resp.status_code == 200, resp.text
    assert fake_helper.set_param_calls == [
        (["Lotka_Volterra_module/alpha"], [3.0])
    ]


def test_simulate_returns_time_and_outputs_shape(client, fake_helper):
    model_id = upload_model(client, LV_MODEL_PATH)["model_id"]
    resp = client.post(
        "/api/simulate",
        json={"model_id": model_id, "params": {}, "sim_time": 5},
    )
    body = resp.json()
    assert len(body["time"]) == fake_helper.n
    assert body["outputs"]
    for series in body["outputs"].values():
        assert len(series) == fake_helper.n


def test_simulate_unknown_model_returns_404(client, fake_helper):
    resp = client.post(
        "/api/simulate", json={"model_id": "nope", "params": {}}
    )
    assert resp.status_code == 404


def test_simulate_invalid_param_name_returns_422(client, fake_helper):
    model_id = upload_model(client, LV_MODEL_PATH)["model_id"]
    resp = client.post(
        "/api/simulate",
        json={"model_id": model_id, "params": {"alpha": 3.0}},
    )
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Integration tier (real Myokit)
# ---------------------------------------------------------------------------
@pytest.mark.integration
def test_simulate_bg_model_returns_finite_values(client, requires_simulation):
    model_id = upload_model(client, BG_MODEL_PATH)["model_id"]
    resp = client.post(
        "/api/simulate",
        json={
            "model_id": model_id,
            "params": {},
            "sim_time": 20,
            "pre_time": 0,
            "outputs": ["main/p_o2"],
        },
    )
    assert resp.status_code == 200, resp.text
    p_o2 = np.array(resp.json()["outputs"]["main/p_o2"])
    assert p_o2.size > 0
    assert np.all(np.isfinite(p_o2))
    # p_o2 rises monotonically from 0 towards equilibrium.
    assert np.all(np.diff(p_o2) >= -1e-9)


@pytest.mark.integration
def test_simulate_lotka_volterra_returns_finite_values(client, requires_simulation):
    model_id = upload_model(client, LV_MODEL_PATH)["model_id"]
    resp = client.post(
        "/api/simulate",
        json={
            "model_id": model_id,
            "params": {},
            "sim_time": 5,
            "outputs": ["Lotka_Volterra_module/x", "Lotka_Volterra_module/y"],
        },
    )
    assert resp.status_code == 200, resp.text
    outputs = resp.json()["outputs"]
    for key in ("Lotka_Volterra_module/x", "Lotka_Volterra_module/y"):
        arr = np.array(outputs[key])
        assert arr.size > 0
        assert np.all(np.isfinite(arr))


@pytest.mark.integration
def test_simulate_different_alpha_gives_different_lv_traces(client, requires_simulation):
    model_id = upload_model(client, LV_MODEL_PATH)["model_id"]

    def max_x(alpha: float) -> float:
        resp = client.post(
            "/api/simulate",
            json={
                "model_id": model_id,
                "params": {"Lotka_Volterra_module/alpha": alpha},
                "sim_time": 5,
                "outputs": ["Lotka_Volterra_module/x"],
            },
        )
        assert resp.status_code == 200, resp.text
        return max(resp.json()["outputs"]["Lotka_Volterra_module/x"])

    low, high = max_x(2.0), max_x(6.0)
    assert abs(high - low) / abs(low) > 0.01
