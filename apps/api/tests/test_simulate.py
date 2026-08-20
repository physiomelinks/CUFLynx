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


# Issue #150: saving a run asks for every plottable variable so it can answer a
# plot added later. `best_effort_outputs` means "everything you can give me" rather
# than a specific list, so one variable the solver cannot resolve must not turn the
# wider save into no save at all.
#
# **The premise changed under us, and that is worth recording.** This used to assert
# that asking for *every* variable of 3compartment was a 422, because some of them --
# `pvn_module/R_v` among those the engine names -- could not be read back. They can
# now: circulatory_autogen 8ac9f80 ("a constant is not in the log, so do not look for
# it there", CA #453) fixed reading constants. `R_v` is a constant; CA's `_make_log`
# deliberately omits constants because Myokit cannot log them, while `_resolve_name`
# classified them as ordinary variables, so `_extract` went looking in a log that was
# never going to contain them and raised KeyError. CUFLynx saw that KeyError and
# turned it into the 422.
#
# So all 88 of 3compartment's variables (27 ODE + 61 algebraic) now resolve, and the
# strict request is a 200. The new behaviour is the correct one -- the old 422 was
# CUFLynx faithfully reporting a bug in the engine, and this test was pinning that bug
# as expected behaviour.
#
# It went unnoticed for the usual reason: `backend-unit` pins circulatory_autogen at
# d2f6cf73, which predates the fix, so CI kept seeing the old answer. It surfaced the
# first time the integration workflow ran the tier against an installed libcuflynx.
#
# The two halves are now separate tests, because the model no longer supplies an
# unresolvable variable and the `best_effort` contract still needs one.
@pytest.mark.integration
def test_every_3compartment_variable_can_be_read_back(client, requires_simulation):
    """Asking for every variable is a 200, and returns all of them.

    A regression pin on CA #453 from CUFLynx's side: if reading constants breaks again,
    this is the test that says so, in the layer the user actually meets it.
    """
    from conftest import RESOURCES_DIR

    model_id = upload_model(client, RESOURCES_DIR / "3compartment_flat.cellml")["model_id"]
    variables = client.get(f"/api/models/{model_id}/variables").json()
    every = variables["odes"] + variables["algebraic"]
    assert len(every) > 50, f"expected the whole model, got {len(every)} variables"

    resp = client.post("/api/simulate", json={
        "model_id": model_id,
        "params": {},
        "sim_time": 2.0,
        "pre_time": 10.0,
        "outputs": every,
    })
    assert resp.status_code == 200, resp.text
    got = resp.json()["outputs"]
    missing = [v for v in every if v not in got]
    assert not missing, (
        f"{len(missing)} of {len(every)} variables did not come back: {missing[:10]}. "
        f"Constants are the usual suspects -- see CA #453."
    )


@pytest.mark.integration
def test_best_effort_skips_an_unresolvable_output_instead_of_failing(
    client, requires_simulation
):
    """One unreadable name must not cost the caller every other trace.

    The unresolvable variable is synthetic rather than borrowed from the model, and
    deliberately so: this contract used to be tested with whichever 3compartment
    variable happened to be unreadable at the time, which meant a fix in the engine
    silently deleted the coverage. A name no model will ever have cannot be fixed out
    from under the test.
    """
    from conftest import RESOURCES_DIR

    model_id = upload_model(client, RESOURCES_DIR / "3compartment_flat.cellml")["model_id"]
    variables = client.get(f"/api/models/{model_id}/variables").json()
    real = (variables["odes"] + variables["algebraic"])[:5]
    assert real, "no variables to ask for"
    requested = real + ["no_such_module/no_such_variable"]

    body = {
        "model_id": model_id,
        "params": {},
        "sim_time": 2.0,
        "pre_time": 10.0,
        "outputs": requested,
    }
    # Strict (the default) still fails loudly: a typo in an explicit request is a
    # mistake worth reporting.
    assert client.post("/api/simulate", json=body).status_code == 422

    resp = client.post("/api/simulate", json={**body, "best_effort_outputs": True})
    assert resp.status_code == 200, resp.text
    got = resp.json()["outputs"]
    assert set(got) == set(real), (
        f"best-effort should return exactly the resolvable ones; got {sorted(got)}")


def test_best_effort_is_off_by_default(client, fake_helper):
    """An explicit output list keeps its strict validation."""
    model_id = upload_model(client, LV_MODEL_PATH)["model_id"]
    resp = client.post(
        "/api/simulate", json={"model_id": model_id, "params": {}, "sim_time": 1}
    )
    assert resp.status_code == 200
