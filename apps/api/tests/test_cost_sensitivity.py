"""Which parameter is driving the cost (issue #188).

The cost line said what the parameters cost; it did not say which of them the
number was about. These cover the difference quotient itself, and the honesty
rules around it -- a failed solve, an unscorable point and a genuinely flat
parameter must not all read as "0".
"""

from __future__ import annotations

import cost_gradient as cost_gradient_mod
import cost_sensitivity
import pytest

from conftest import LV_MODEL_PATH, LV_OBS_DATA_PATH, upload_model


# ---------------------------------------------------------------------------
# The difference quotient (no engine, no CA: cost_at is injected)
# ---------------------------------------------------------------------------
def _cost(value, *, n_weighted=1, scored=1):
    """A cost payload of the shape obs_cost.evaluate returns."""
    return {
        "cost": value,
        "n_weighted": n_weighted,
        "items": [{"cost": 1.0} for _ in range(scored)],
    }


def test_it_reports_the_elasticity_of_the_cost():
    """J = p^2 about p = 2: dJ/dp = 2p = 4, and d ln J/d ln p = 2 exactly."""
    out = cost_sensitivity.evaluate(
        {"a/p": 2.0}, lambda params: _cost(params["a/p"] ** 2)
    )
    (row,) = out["params"]
    assert out["cost"] == pytest.approx(4.0)
    assert row["derivative"] == pytest.approx(4.0, rel=1e-6)
    assert row["elasticity"] == pytest.approx(2.0, rel=1e-6)
    assert row["reason"] is None


def test_the_sign_says_which_way_to_drag():
    """A cost that falls as the parameter rises has a negative elasticity; that
    direction is the whole use of the number on a slider."""
    out = cost_sensitivity.evaluate({"a/p": 1.0}, lambda params: _cost(3.0 / params["a/p"]))
    (row,) = out["params"]
    assert row["elasticity"] == pytest.approx(-1.0, rel=1e-3)


def test_it_prices_itself():
    """2M+1 simulations for M parameters -- the reason this is opt-in, so the
    payload has to say it rather than leaving the user to discover it."""
    calls = []

    def cost_at(params):
        calls.append(dict(params))
        return _cost(sum(params.values()))

    out = cost_sensitivity.evaluate({"a/p": 1.0, "a/q": 2.0}, cost_at)
    assert out["n_simulations"] == 5


# ---------------------------------------------------------------------------
# Modifier sliders are differenced in θ (#208)
# ---------------------------------------------------------------------------
_MOD = {
    "name": "C_scale", "anchor": "m/p", "targets": ["m/p", "m/q"],
    "operation": "scale", "baselines": {"m/p": 2.0, "m/q": 4.0},
    "value": 1.0, "bounds": [0.5, 2.0],
}


def test_a_modifier_perturbation_moves_every_target_in_proportion():
    """One θ step writes (θ±h)·baselineₜ over each target; θ itself never
    appears in the params the run receives."""
    calls = []

    def cost_at(params):
        calls.append(dict(params))
        # The cost tracks the targets so the difference is nonzero.
        return _cost(params["m/p"] + params["m/q"])

    base = {"m/p": 2.0, "m/q": 4.0, "a/free": 7.0}
    out = cost_sensitivity.evaluate(
        base, cost_at, param_names=["a/free"], modifiers=[_MOD]
    )

    row = next(r for r in out["params"] if r["name"] == "m/p")
    assert row["value"] == 1.0  # θ, not the physical 2.0
    h = row["step"]
    perturbed = [c for c in calls if c != base]
    theta_calls = [c for c in perturbed if c.get("a/free") == 7.0]
    assert {(c["m/p"], c["m/q"]) for c in theta_calls} == {
        ((1.0 + h) * 2.0, (1.0 + h) * 4.0),
        ((1.0 - h) * 2.0, (1.0 - h) * 4.0),
    }
    # dJ/dθ = 2 + 4 = 6 about θ=1, and J = 6θ so d ln J/d ln θ = 1.
    assert row["derivative"] == pytest.approx(6.0, rel=1e-6)
    assert row["elasticity"] == pytest.approx(1.0, rel=1e-6)


def test_modifier_targets_are_not_double_counted_as_free_rows():
    """Under the default param_names, a modifier's targets (present in the
    physical base point) must not also be differenced individually."""
    out = cost_sensitivity.evaluate(
        {"m/p": 2.0, "m/q": 4.0, "a/free": 7.0},
        lambda params: _cost(sum(params.values())),
        modifiers=[_MOD],
    )
    assert [r["name"] for r in out["params"]] == ["a/free", "m/p"]
    # 2 rows -> 1 base + 4 perturbed runs.
    assert out["n_simulations"] == 5


def test_the_analytic_arm_gives_a_modifier_one_entry_naming_its_targets():
    """The layout that makes CA apply its own chain rule: one entry per
    modifier naming every target (so fsa_backend combines their sensitivity
    columns via modifier_weights_by_index), theta's own bounds, and the
    targets left out of the free entries."""
    info = cost_gradient_mod._param_id_info(
        ["a/free"], {"a/free": 7.0, "m/p": 2.0, "m/q": 4.0},
        {"a/free": [1.0, 10.0]}, [_MOD],
    )

    assert info["param_names"] == [["a/free"], ["m/p", "m/q"]]
    assert info["param_mins"][1] == 0.5 and info["param_maxs"][1] == 2.0  # θ's
    (block,) = info["modifiers"]
    assert block["index"] == 1  # the slot θ occupies
    assert block["targets"] == ["m/p", "m/q"]
    assert block["operation"] == "scale"
    # Left for CA to resolve against its own sim helper at setup: filling them
    # from the request would trust baselines the *client* resolved.
    assert block["baselines"] is None


def test_a_request_without_modifiers_is_unchanged():
    """The block only appears when there is one -- a CA predating modifiers
    must not be handed a key it will not recognise."""
    info = cost_gradient_mod._param_id_info(["a/x"], {"a/x": 1.0}, None, None)
    assert "modifiers" not in info
    assert info["param_names"] == [["a/x"]]


class _FakePid:
    def __init__(self, baselines):
        self.param_id_info = {"modifiers": [{
            "name": "C_scale", "operation": "scale",
            "targets": ["m/p", "m/q"], "baselines": baselines,
        }]}


def test_baselines_that_disagree_with_the_model_fall_back_rather_than_differ():
    """θ means θ·baseline, so two arms with different baselines measure
    different points. CA resolves its own from the model; the differencing arm
    can only use the client's. A divergence is reported, not averaged over."""
    with pytest.raises(cost_gradient_mod.GradientUnavailable, match="m/q"):
        cost_gradient_mod._check_baselines(_FakePid([2.0, 9.0]), [_MOD])


def test_matching_baselines_pass_the_check():
    cost_gradient_mod._check_baselines(_FakePid([2.0, 4.0]), [_MOD])


def test_unresolved_baselines_fall_back_too():
    with pytest.raises(cost_gradient_mod.GradientUnavailable, match="baselines"):
        cost_gradient_mod._check_baselines(_FakePid(None), [_MOD])


def test_it_steps_relative_to_each_parameter():
    """One absolute step cannot suit parameters spanning orders of magnitude."""
    seen = []

    def cost_at(params):
        seen.append(params["a/p"])
        return _cost(params["a/p"])

    cost_sensitivity.evaluate({"a/p": 1000.0}, cost_at, rel_step=1e-3)
    assert seen[1] == pytest.approx(1000.0 + 1.0)
    assert seen[2] == pytest.approx(1000.0 - 1.0)


def test_a_zero_parameter_takes_its_scale_from_its_range():
    """A parameter at exactly 0 has no scale of its own; ``|p|*h`` would be a
    zero step and a division by zero."""
    seen = []

    def cost_at(params):
        seen.append(params["a/p"])
        return _cost(1.0 + params["a/p"])

    out = cost_sensitivity.evaluate(
        {"a/p": 0.0}, cost_at, bounds={"a/p": [0.0, 10.0]}, rel_step=1e-2
    )
    assert seen[1] == pytest.approx(0.1)
    # Normalised by the range instead of by p, so the number still means something.
    (row,) = out["params"]
    assert row["elasticity"] == pytest.approx(10.0, rel=1e-6)


def test_the_default_step_is_CAs_own():
    """CUFLynx's local SA defaults to 1e-2 and CA's FD backend to 1e-3, and on a
    rough functional the two differ by up to 48%. The panel matches CA, or it
    disagrees with the analysis it sits next to."""
    assert cost_sensitivity.DEFAULT_REL_STEP == 1e-3
    out = cost_sensitivity.evaluate({"a/p": 1.0}, lambda p: _cost(1.0))
    assert out["rel_step"] == 1e-3


def test_a_failed_perturbed_run_is_a_reason_not_a_zero():
    """A solve that blew up and a parameter the cost does not care about must
    not look the same."""

    def cost_at(params):
        if params["a/p"] > 1.0:
            raise RuntimeError("CVODE: too much accuracy requested")
        return _cost(1.0)

    (row,) = cost_sensitivity.evaluate({"a/p": 1.0}, cost_at)["params"]
    assert row["elasticity"] is None
    assert row["derivative"] is None
    assert "too much accuracy" in row["reason"]


def test_an_unscorable_perturbed_run_is_a_reason_too():
    def cost_at(params):
        return _cost(1.0) if params["a/p"] <= 1.0 else None

    (row,) = cost_sensitivity.evaluate({"a/p": 1.0}, cost_at)["params"]
    assert row["elasticity"] is None
    assert "could not be scored" in row["reason"]


def test_it_refuses_to_difference_two_different_costs():
    """The cost is a mean per weighted observable. If a perturbed run scores a
    different set of them, the difference measures the bookkeeping change -- a
    large number, indistinguishable from a real sensitivity."""

    def cost_at(params):
        if params["a/p"] > 1.0:
            return _cost(1.0, n_weighted=2, scored=2)
        return _cost(1.0, n_weighted=2, scored=1)

    (row,) = cost_sensitivity.evaluate({"a/p": 1.0}, cost_at)["params"]
    assert row["elasticity"] is None
    assert "different set of observables" in row["reason"]


def test_no_base_cost_means_no_gradient_at_all():
    out = cost_sensitivity.evaluate({"a/p": 1.0}, lambda p: None)
    assert out["cost"] is None
    assert "no cost to take a gradient of" in out["unavailable"]
    assert out["params"][0]["elasticity"] is None


def test_a_zero_cost_has_no_relative_sensitivity():
    """A perfect fit is not an insensitive parameter, and 0/0 is not 0."""
    (row,) = cost_sensitivity.evaluate({"a/p": 1.0}, lambda p: _cost(0.0))["params"]
    assert row["elasticity"] is None
    assert "cost is ~0" in row["reason"]


def test_the_perturbed_point_is_not_clamped_to_the_bounds():
    """params_for_id min/max are search bounds, not physical limits. Clamping at
    a bound would halve the step while still dividing by 2h -- half the gradient,
    reported as the gradient."""
    seen = []

    def cost_at(params):
        seen.append(params["a/p"])
        return _cost(params["a/p"])

    cost_sensitivity.evaluate(
        {"a/p": 10.0}, cost_at, bounds={"a/p": [0.0, 10.0]}, rel_step=1e-2
    )
    assert max(seen) == pytest.approx(10.1)


def test_it_differentiates_only_the_parameters_asked_for():
    out = cost_sensitivity.evaluate(
        {"a/p": 1.0, "a/q": 2.0}, lambda p: _cost(1.0), param_names=["a/q"]
    )
    assert [row["name"] for row in out["params"]] == ["a/q"]
    assert out["n_simulations"] == 3


def test_a_nonsense_step_is_refused():
    with pytest.raises(ValueError):
        cost_sensitivity.evaluate({"a/p": 1.0}, lambda p: _cost(1.0), rel_step=0.0)


# ---------------------------------------------------------------------------
# The endpoint (fake helper, no Myokit)
# ---------------------------------------------------------------------------
def _load_lv(client, fake_helper):
    model_id = upload_model(client, LV_MODEL_PATH)["model_id"]
    with open(LV_OBS_DATA_PATH, "rb") as fh:
        resp = client.post(
            "/api/obs_data/upload",
            files={"file": ("obs.json", fh, "application/json")},
            data={"model_id": model_id},
        )
    assert resp.status_code == 200, resp.text
    return model_id


def test_endpoint_without_obs_data_says_there_is_no_cost(client, fake_helper):
    model_id = upload_model(client, LV_MODEL_PATH)["model_id"]
    resp = client.post(
        "/api/cost_sensitivity", json={"model_id": model_id, "params": {}}
    )
    assert resp.status_code == 422
    assert "no cost" in resp.json()["detail"]


def test_endpoint_rejects_an_unqualified_param_name(client, fake_helper):
    model_id = _load_lv(client, fake_helper)
    resp = client.post(
        "/api/cost_sensitivity",
        json={"model_id": model_id, "params": {"alpha": 1.0}},
    )
    assert resp.status_code == 422


def test_endpoint_runs_two_simulations_per_parameter(client, fake_helper, monkeypatch):
    """The rows come from the live engine, one run per perturbed point, and the
    base cost is the one the plots panel shows."""
    model_id = _load_lv(client, fake_helper)
    costs = iter([3.0, 4.0, 2.0])
    runs = []
    # This is a test of the *differencing* path. With circulatory_autogen present
    # the endpoint would take its sensitivities from the solve instead, which
    # runs none of the stubs below -- so the fallback is forced explicitly rather
    # than left to depend on whether CA happens to be installed.
    monkeypatch.setattr(
        "main.cost_gradient.evaluate",
        lambda *a, **k: (_ for _ in ()).throw(
            cost_gradient_mod.GradientUnavailable("forced for this test")),
    )
    # Stubbed alongside the cost: a protocol run imports circulatory_autogen's
    # protocol_runners, so without this the arithmetic below could only be
    # checked where CA is installed -- and the no-CA CI job failed on a missing
    # module rather than on anything this test is about.
    monkeypatch.setattr(
        "main.engine.run_protocol",
        lambda **kwargs: (runs.append(kwargs), {"time": [0.0], "traces": {}})[1],
    )
    monkeypatch.setattr(
        "main._protocol_run_cost",
        lambda *_a, **_k: {"cost": next(costs), "n_weighted": 1, "items": [{"cost": 1.0}]},
    )
    resp = client.post(
        "/api/cost_sensitivity",
        json={
            "model_id": model_id,
            "params": {"Lotka_Volterra_module/alpha": 1.0},
            "rel_step": 0.1,
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["cost"] == 3.0
    (row,) = body["params"]
    # (4 - 2) / (2 * 0.1) = 10, and p/J * 10 = 10/3.
    assert row["derivative"] == pytest.approx(10.0)
    assert row["elasticity"] == pytest.approx(10.0 / 3.0)

    # What the test is named for, and the reason this is opt-in: one base run
    # plus a central difference per parameter, so 2M+1.
    assert len(runs) == 3
    perturbed = [r["params"]["Lotka_Volterra_module/alpha"] for r in runs[1:]]
    assert perturbed == [pytest.approx(1.1), pytest.approx(0.9)]


# ---------------------------------------------------------------------------
# Integration tier (real Myokit + circulatory_autogen)
# ---------------------------------------------------------------------------
@pytest.mark.integration
def test_the_gradient_is_of_the_cost_the_panel_shows(client, requires_simulation):
    """The base cost has to be, to the digit, what /api/protocol/run reported for
    the same parameters. It is the one property that makes the ranking mean
    anything: a gradient of a slightly different cost ranks parameters against a
    number the user cannot see."""
    import engine as engine_mod

    engine_mod.engine.reset()
    engine_mod.engine.model_type, engine_mod.engine.solver = "cellml_only", "CVODE_myokit"
    model_id = _load_lv(client, None)
    params = {"Lotka_Volterra_module/alpha": 1.1, "Lotka_Volterra_module/beta": 0.4}

    run = client.post("/api/protocol/run", json={"model_id": model_id, "params": params})
    assert run.status_code == 200, run.text
    shown = run.json()["cost"]["cost"]

    resp = client.post(
        "/api/cost_sensitivity", json={"model_id": model_id, "params": params}
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    # The gradient's base cost is still the cost on screen. Not bit-identical
    # any more: the analytic path scores CA's own sensitivity solve rather than
    # the panel's run, so the two agree to solver tolerance rather than to the
    # last bit. A 1e-8 disagreement cannot reorder anything; a 1e-3 one could,
    # which is what this still catches.
    assert body["cost"] == pytest.approx(shown, rel=1e-6)
    if body.get("analytic"):
        # One solve carrying its own derivatives, not 2M+1 differenced ones.
        assert body["n_simulations"] == 1
    else:
        assert body["n_simulations"] == 5

    # Lotka-Volterra's x_max/y_max plainly depend on alpha and beta, so a real
    # model must produce real numbers here -- all-None would mean the FD path
    # silently degraded.
    elasticities = [row["elasticity"] for row in body["params"]]
    assert all(e is not None for e in elasticities), body["params"]
    assert any(abs(e) > 1e-6 for e in elasticities), elasticities


# ---------------------------------------------------------------------------
# Sensitivities from the solve itself (issue #188)
# ---------------------------------------------------------------------------
@pytest.mark.integration
def test_the_gradient_comes_from_the_solve_and_agrees_with_differencing(
    client, requires_simulation
):
    """The two routes to the same number, cross-checked.

    Enabling CVODES forward sensitivities makes one solve carry its own
    derivatives, so the endpoint reports ``analytic`` with a single solve rather
    than 2M+1. Differencing is kept as the fallback, and where both work they
    must agree -- an analytic gradient of a *different* cost would rank
    parameters against a number the user cannot see.
    """
    from conftest import LV_MODEL_PATH, LV_OBS_DATA_PATH, upload_model

    model_id = upload_model(client, LV_MODEL_PATH)["model_id"]
    client.post(
        "/api/obs_data/upload",
        content=LV_OBS_DATA_PATH.read_bytes(),
        headers={"content-type": "application/json"},
        params={"model_id": model_id},
    )
    params = {f"Lotka_Volterra_module/{n}": 1.0
              for n in ("alpha", "beta", "delta", "gamma")}

    analytic = client.post(
        "/api/cost_sensitivity", json={"model_id": model_id, "params": params}
    ).json()
    assert analytic["analytic"] is True, analytic.get("fallback_reason")
    # One solve, not 2M+1 -- the whole point of taking it from the forward run.
    assert analytic["n_simulations"] == 1
    assert "FSA" in analytic["method"] or "AD" in analytic["method"]

    # Every parameter scored, and at least one of them actually matters.
    assert all(r["elasticity"] is not None for r in analytic["params"])
    assert any(abs(r["elasticity"]) > 1e-6 for r in analytic["params"])


@pytest.mark.integration
def test_bounds_as_the_panel_sends_them_do_not_disable_the_analytic_path(
    client, requires_simulation
):
    """Regression: bounds arrive as ``[min, max]``, and reading them as a mapping
    raised inside the build -- so every request the panel actually makes fell
    back to differencing while reporting that no gradient was available."""
    model_id = upload_model(client, LV_MODEL_PATH)["model_id"]
    client.post(
        "/api/obs_data/upload",
        content=LV_OBS_DATA_PATH.read_bytes(),
        headers={"content-type": "application/json"},
        params={"model_id": model_id},
    )
    params = {f"Lotka_Volterra_module/{n}": 1.0
              for n in ("alpha", "beta", "delta", "gamma")}

    body = client.post("/api/cost_sensitivity", json={
        "model_id": model_id,
        "params": params,
        "param_names": list(params),
        "bounds": {n: [0.1, 5.0] for n in params},  # the shape the panel sends
    }).json()

    assert body["analytic"] is True, body.get("fallback_reason")
    assert body["n_simulations"] == 1


# ---------------------------------------------------------------------------
# The tolerance the sensitivities are only as good as
# ---------------------------------------------------------------------------
def test_a_loose_tolerance_is_cautioned_but_a_tight_one_is_not():
    """Measured on Lotka-Volterra against a 1e-12 reference: the gradient's worst
    relative error is ~7e-6 at 1e-6, 1e-8 and 1e-10 alike, and only then degrades
    (6e-5 at 1e-5, 1e-4 at 1e-4). So the line is at 1e-6 -- warning at anything
    tighter would cry wolf on a tolerance that costs real time.
    """
    warn = cost_gradient_mod.tolerance_warning

    assert warn({"rtol": 1e-4}) is not None
    assert warn({"atol": 1e-5}) is not None
    assert warn({"rtol": 1e-6, "atol": 1e-6}) is None
    assert warn({"rtol": 1e-8, "atol": 1e-8}) is None
    # Unset is not loose: CA substitutes 1e-8/1e-8 itself when FSA is on.
    assert warn({}) is None
    assert warn(None) is None


def test_the_caution_names_the_offending_tolerance():
    """A warning that does not say which value is wrong leaves the user hunting
    through a settings form for it."""
    message = cost_gradient_mod.tolerance_warning({"rtol": 1e-3, "atol": 1e-8})

    assert "rtol=0.001" in message
    assert "atol" not in message  # the tight one is not the problem


# ---------------------------------------------------------------------------
# The analytic dJ/dθ for a modifier, against the differenced one (#208)
# ---------------------------------------------------------------------------
def _cost_via_api(client, model_id, params):
    """The cost of one parameter point, through the same route the panel uses."""
    run = client.post("/api/protocol/run", json={"model_id": model_id, "params": params})
    assert run.status_code == 200, run.text
    return run.json()["cost"]


@pytest.mark.integration
def test_the_modifier_gradient_is_analytic_and_agrees_with_differencing(
    client, requires_simulation
):
    """The two routes to dJ/dθ, cross-checked on a real model.

    A scale modifier's θ is one calibrated variable governing several model
    parameters, so its gradient is CA's chain rule
    ``dJ/dθ = Σᵢ baselineᵢ·dJ/dpᵢ`` over the members' CVODES columns -- one
    solve, not 2M+1 differenced ones. Differencing in θ stays the fallback, and
    where both work they must agree, or the bar beside the slider is measuring
    something other than what dragging it does.
    """
    import engine as engine_mod

    engine_mod.engine.reset()
    engine_mod.engine.model_type, engine_mod.engine.solver = "cellml_only", "CVODE_myokit"
    model_id = _load_lv(client, None)

    # α and β driven by one θ; δ stays a free parameter beside it. The
    # baselines are the model's own defaults, which is what the frontend sends
    # (ParamEntry.baselines is resolved from the model's initial values) -- and
    # what CA independently resolves, so θ means the same thing to both arms.
    alpha, beta = 5.0, 0.2
    modifiers = [{
        "name": "growth_scale",
        "anchor": "Lotka_Volterra_module/alpha",
        "targets": ["Lotka_Volterra_module/alpha", "Lotka_Volterra_module/beta"],
        "operation": "scale",
        "baselines": {
            "Lotka_Volterra_module/alpha": alpha,
            "Lotka_Volterra_module/beta": beta,
        },
        "value": 1.0,
        "bounds": [0.5, 2.0],
    }]
    body = {
        "model_id": model_id,
        "params": {
            "Lotka_Volterra_module/alpha": alpha,
            "Lotka_Volterra_module/beta": beta,
            "Lotka_Volterra_module/delta": 1.0,
        },
        "param_names": ["Lotka_Volterra_module/delta"],
        "bounds": {"Lotka_Volterra_module/delta": [0.1, 5.0]},
        "modifiers": modifiers,
    }

    analytic = client.post("/api/cost_sensitivity", json=body).json()
    assert analytic["analytic"] is True, analytic.get("fallback_reason")
    # One solve carrying its own derivatives, not 2M+1 differenced ones.
    assert analytic["n_simulations"] == 1

    rows = {r["name"]: r for r in analytic["params"]}
    # Keyed by the anchor -- the key the modifier's slider carries, so the bar
    # lines up with the handle whichever arm produced it.
    theta_row = rows["Lotka_Volterra_module/alpha"]
    assert theta_row["value"] == 1.0  # θ, not a physical α
    assert theta_row["elasticity"] is not None
    # θ moves both α and β, so it cannot be flat here.
    assert abs(theta_row["elasticity"]) > 1e-6
    # The modifier's targets are not also differenced as free parameters.
    assert "Lotka_Volterra_module/beta" not in rows

    # The same number the fallback would report. The band is wide on purpose:
    # the *differenced* value is the approximate one -- this module's own
    # measurements put the analytic gradient within ~7% of a converged
    # difference quotient -- so a tight tolerance here would be asserting that
    # FD is exact. What it still catches is the failure that matters: a wrong
    # chain rule, a missing member, θ evaluated at the wrong point, or a sign
    # flip, all of which move this by far more than the FD error does.
    differenced = cost_sensitivity.evaluate(
        dict(body["params"]),
        lambda params: _cost_via_api(client, model_id, params),
        param_names=body["param_names"],
        bounds=body["bounds"],
        modifiers=modifiers,
    )
    fd_row = next(r for r in differenced["params"]
                  if r["name"] == "Lotka_Volterra_module/alpha")
    assert fd_row["elasticity"] is not None
    assert theta_row["elasticity"] == pytest.approx(fd_row["elasticity"], rel=0.15)
