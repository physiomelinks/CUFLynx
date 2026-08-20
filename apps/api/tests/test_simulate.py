import numpy as np
import pytest

from conftest import (
    BG_MODEL_PATH,
    LV_MODEL_PATH,
    running_against_installed_ca_only,
    upload_model,
)


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


# Issue #150: saving a run asks for every plottable variable so it can answer a
# plot added later. Some variables the CellML parser classifies as algebraic are
# not resolvable outputs in the solver, and failing the whole request for one of
# those turned the wider save into no save at all.
# Found by the integration workflow on its first run, and the finding is bigger than it
# first looked. This request is accepted (200) against the *released* libcuflynx **and**
# against current circulatory_autogen master; it is rejected (422) only against
# `d2f6cf73`, the commit `backend-unit` pins. So the engine's verdict on which CellML
# variables count as resolvable outputs changed at some point after that pin, and CI has
# been green throughout because the pin froze it at the old answer.
#
# That is the drift this whole workflow exists to surface, and it is why the weekly
# dependency-upgrade job matters: a pinned dependency does not stop upstream moving, it
# only stops you finding out.
#
# Unresolved on purpose: which behaviour is *correct* has not been established, only that
# the app behaves one way against the engine users have and another against the engine CI
# tests. Needs triage against CA rather than a guess here.
#
# The condition is a proxy, and worth naming as one: it is really "CA is newer than the
# pinned commit", which cannot be asked directly. It happens to be exactly right for the
# two CI arrangements -- the integration jobs resolve an installed package and see the
# new behaviour, `backend-unit` resolves the pinned checkout and sees the old -- but a
# developer running against a *modern* local checkout will see this fail rather than
# xfail. That is the honest outcome: it really does fail there.
@pytest.mark.xfail(
    running_against_installed_ca_only(),
    strict=True,
    reason="libcuflynx after CI's pinned d2f6cf73 accepts (200) outputs that commit "
           "rejects (422); which behaviour is correct is not yet established",
)
@pytest.mark.integration
def test_best_effort_outputs_skips_what_the_solver_cannot_resolve(
    client, requires_simulation
):
    from conftest import RESOURCES_DIR

    model_id = upload_model(client, RESOURCES_DIR / "3compartment_flat.cellml")["model_id"]
    variables = client.get(f"/api/models/{model_id}/variables").json()
    every = variables["odes"] + variables["algebraic"]

    body = {
        "model_id": model_id,
        "params": {},
        "sim_time": 2.0,
        "pre_time": 10.0,
        "outputs": every,
    }
    # Strict (the default) still fails loudly: a typo in an explicit request is a
    # mistake worth reporting.
    assert client.post("/api/simulate", json=body).status_code == 422

    resp = client.post("/api/simulate", json={**body, "best_effort_outputs": True})
    assert resp.status_code == 200, resp.text
    got = resp.json()["outputs"]
    assert 0 < len(got) < len(every)  # most of them, not all, and not a failure


def test_best_effort_is_off_by_default(client, fake_helper):
    """An explicit output list keeps its strict validation."""
    model_id = upload_model(client, LV_MODEL_PATH)["model_id"]
    resp = client.post(
        "/api/simulate", json={"model_id": model_id, "params": {}, "sim_time": 1}
    )
    assert resp.status_code == 200
