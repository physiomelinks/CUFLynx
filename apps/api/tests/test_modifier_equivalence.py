"""A modifier's theta against the same combination written into the model.

The pair (`resources/affine_native.cellml`, `resources/affine_modifier.cellml`)
is one system expressed two ways:

* **native** -- one constant ``theta`` drives two rate constants through an
  affine combination in the model's own math (``k1 = theta*c1``,
  ``k2 = theta*c2``), so the solver differentiates w.r.t. theta directly and no
  modifier code runs at all;
* **modifier** -- ``k1``/``k2`` are independent constants defaulting to exactly
  ``c1``/``c2``, driven by a params_for_id **scale modifier**, so theta's
  derivative is CA's chain rule over the two members.

At theta = 1 they are the same point of the same system (their costs agree to
the last bit), and the system is solvable in closed form:

    x(T) = x0*exp(-theta*c1*T)  =>  d ln(x)/d ln(theta) = -c1*T  exactly
    y(T) = y0*exp(-theta*c2*T)  =>  d ln(y)/d ln(theta) = -c2*T  exactly

so the tests assert the *true* answer, not merely that two paths agree.

**Two tolerances, for two different claims.**

The chain rule itself is exact arithmetic, and is asserted as such (rtol 1e-8;
it holds to ~1e-16). In relative terms a scale modifier's coefficient is simply
the sum of its members' -- ``d ln Y/d ln theta = sum_i d ln Y/d ln p_i``,
because ``p_i = theta*b_i`` makes each member's own relative coefficient carry
its weight -- so the claim can be checked against CA's per-member numbers from
the same solve, with no solver error between the two sides.

Comparing the two *models* is a different claim: two different augmented
CVODES systems, whose sensitivities agree to about 1e-7 here. That floor is the
integrator's, not the chain rule's -- tightening the solver past rtol=atol=1e-12
makes it worse (1.7e-4 at 1e-14), so 1e-6 is asserted and the reason recorded
rather than a tighter number forced.

The fixture is deliberately *decoupled* (one rate per state). The obvious
pairing, ``dx/dt = k1 - k2*x``, leaves the steady state k1/k2 independent of
theta, so scaling both members makes theta's effect the small remainder of two
large opposing terms; the chain-rule sum then amplifies each member's
integration error about sixfold and no tolerance can be tightened past it.
Well-conditioned by construction is what makes 1e-8 meaningful here.
"""

from __future__ import annotations

import json
import time

import pytest

from conftest import RESOURCES_DIR, upload_model

NATIVE_PATH = RESOURCES_DIR / "affine_native.cellml"
MODIFIER_PATH = RESOURCES_DIR / "affine_modifier.cellml"
OBS_PATH = RESOURCES_DIR / "affine_obs_data.json"

# The native model's coefficients, and therefore the modifier's baselines.
C1, C2 = 0.7314, 0.1129
SIM_TIME = 4.0  # the obs_data protocol window

# d ln(Y)/d ln(theta) in closed form, independent of the initial values.
EXACT = {"x": -C1 * SIM_TIME, "y": -C2 * SIM_TIME}

THETA_MIN, THETA_MAX = 0.5, 2.0

NATIVE_PARAMS = {
    "version": 1,
    "params": [{"name": "theta", "targets": ["affine/theta"],
                "min": THETA_MIN, "max": THETA_MAX}],
}
MODIFIER_PARAMS = {
    "version": 1,
    "params": [{"name": "k_scale", "modifies": ["affine/k1", "affine/k2"],
                "operation": "scale", "min": THETA_MIN, "max": THETA_MAX}],
}
# The same two parameters, calibrated independently: the per-member numbers the
# modifier's single coefficient must reproduce.
MEMBER_PARAMS = {
    "version": 1,
    "params": [
        {"name": "k1", "targets": ["affine/k1"], "min": 0.2, "max": 2.0},
        {"name": "k2", "targets": ["affine/k2"], "min": 0.02, "max": 0.5},
    ],
}

NATIVE_ANCHOR = "affine/theta"
MODIFIER_ANCHOR = "affine/k1"  # modifies[0]: the key theta's slider carries

MODIFIER_BLOCK = {
    "name": "k_scale",
    "anchor": MODIFIER_ANCHOR,
    "targets": ["affine/k1", "affine/k2"],
    "operation": "scale",
    "baselines": {"affine/k1": C1, "affine/k2": C2},
    "value": 1.0,
    "bounds": [THETA_MIN, THETA_MAX],
}


def _setup(client, model_path, params_doc) -> str:
    """One model + the shared obs_data + its params_for_id, on a tightly
    toleranced Myokit CVODES backend.

    rtol == atol, which also makes every comparison here immune to CA's
    rtol/atol swap in myokit_helper.
    """
    resp = client.post("/api/config", json={
        "generated_model_format": "cellml_only",
        "solver": "CVODE_myokit",
        "solver_info": {"rtol": 1e-12, "atol": 1e-12},
    })
    assert resp.status_code == 200, resp.text

    model_id = upload_model(client, model_path)["model_id"]
    obs = json.loads(OBS_PATH.read_text())
    r = client.post("/api/obs_data/upload", json={"model_id": model_id, "obs_data": obs})
    assert r.status_code == 200, r.text
    r = client.post(
        f"/api/params_for_id/upload?model_id={model_id}",
        content=json.dumps(params_doc),
        headers={"content-type": "application/json"},
    )
    assert r.status_code == 200, r.text
    return model_id


def _wait(client, job_id, timeout=600):
    offset = 0
    lines: list[str] = []
    deadline = time.time() + timeout
    while time.time() < deadline:
        s = client.get(f"/api/sensitivity/{job_id}/status?offset={offset}").json()
        lines += s["lines"]
        offset = s["next_offset"]
        if s["state"] != "running":
            return s, lines
        time.sleep(0.05)
    raise AssertionError("sensitivity did not finish; lines:\n" + "\n".join(lines))


def _local_sa(client, model_id, current_params) -> dict:
    """FSA local sensitivities about theta = 1: {output: {param: coeff}}."""
    resp = client.post("/api/sensitivity/run", json={
        "model_id": model_id,
        "settings": {
            "method": "local",
            # Analytic, not differenced: these are exactness comparisons.
            "gradient_method": "FSA",
            "nominal": "current",
            "dt": 0.01,
            "num_cores": 1,
        },
        # What the frontend's analysisDict sends: theta at the anchor.
        "current_params": current_params,
    })
    assert resp.status_code == 200, resp.text
    status, lines = _wait(client, resp.json()["job_id"])
    assert status["state"] == "done", "\n".join(lines)
    return status["indices"]["local"]


def _one_column(local: dict) -> dict:
    """The single calibrated variable's column, keyed by output."""
    return {out: next(iter(row.values())) for out, row in local.items()}


def _one_column_sum(local: dict) -> dict:
    """Each output's members summed: what a scale modifier's single relative
    coefficient must equal."""
    return {out: sum(row.values()) for out, row in local.items()}


def _exact_for(output_name: str) -> float:
    return EXACT["x"] if "x_" in output_name else EXACT["y"]


@pytest.mark.integration
def test_the_two_models_are_the_same_system_at_theta_one(client, requires_simulation):
    """The premise every comparison rests on. If the costs differ, the models
    are not the same point and a sensitivity disagreement would say nothing
    about the chain rule."""
    native = _setup(client, NATIVE_PATH, NATIVE_PARAMS)
    native_cost = client.post(
        "/api/protocol/run", json={"model_id": native, "params": {"affine/theta": 1.0}}
    ).json()["cost"]["cost"]

    modifier = _setup(client, MODIFIER_PATH, MODIFIER_PARAMS)
    modifier_cost = client.post(
        "/api/protocol/run",
        json={"model_id": modifier, "params": {"affine/k1": C1, "affine/k2": C2}},
    ).json()["cost"]["cost"]

    assert native_cost == pytest.approx(modifier_cost, rel=1e-12)


# ---------------------------------------------------------------------------
# The chain rule, exactly (rtol 1e-8; holds to ~1e-16)
# ---------------------------------------------------------------------------
@pytest.mark.integration
def test_a_modifier_output_column_is_exactly_its_members_summed(
    client, requires_simulation
):
    """``d ln Y/d ln theta = sum_i d ln Y/d ln p_i`` for every observable.

    Both sides come from the same model and the same sensitivity columns, so
    nothing but the combination arithmetic is under test -- and it is exact,
    not merely close. This is the assertion that a dropped weight, a weight
    used twice, or a swapped pair would break outright.
    """
    members = _one_column_sum(_local_sa(
        client, _setup(client, MODIFIER_PATH, MEMBER_PARAMS),
        {"affine/k1": C1, "affine/k2": C2},
    ))
    modifier = _one_column(_local_sa(
        client, _setup(client, MODIFIER_PATH, MODIFIER_PARAMS),
        {MODIFIER_ANCHOR: 1.0},
    ))

    assert set(members) == set(modifier), (members, modifier)
    assert members, "no outputs were scored"
    for out, summed in members.items():
        assert modifier[out] == pytest.approx(summed, rel=1e-8), out


@pytest.mark.integration
def test_a_modifier_cost_bar_is_exactly_its_members_summed(client, requires_simulation):
    """The same identity for the cost-sensitivity panel's bar."""
    member_id = _setup(client, MODIFIER_PATH, MEMBER_PARAMS)
    member_body = client.post("/api/cost_sensitivity", json={
        "model_id": member_id,
        "params": {"affine/k1": C1, "affine/k2": C2},
        "param_names": ["affine/k1", "affine/k2"],
        "bounds": {"affine/k1": [0.2, 2.0], "affine/k2": [0.02, 0.5]},
    }).json()
    assert member_body["analytic"] is True, member_body.get("fallback_reason")
    summed = sum(r["elasticity"] for r in member_body["params"])

    modifier_id = _setup(client, MODIFIER_PATH, MODIFIER_PARAMS)
    modifier_body = client.post("/api/cost_sensitivity", json={
        "model_id": modifier_id,
        "params": {"affine/k1": C1, "affine/k2": C2},
        "param_names": [],
        "modifiers": [MODIFIER_BLOCK],
    }).json()
    assert modifier_body["analytic"] is True, modifier_body.get("fallback_reason")

    # The modifier is the panel's first row, where its slider sits in the
    # parameter column -- not appended after the free parameters.
    row = modifier_body["params"][0]
    assert row["name"] == MODIFIER_ANCHOR
    assert row["value"] == 1.0  # theta, not a physical rate
    assert abs(summed) > 1e-3, "the members do not move this cost; nothing is proven"
    assert row["elasticity"] == pytest.approx(summed, rel=1e-8)


# ---------------------------------------------------------------------------
# The two models, and the closed form (solver-limited, ~1e-7)
# ---------------------------------------------------------------------------
@pytest.mark.integration
def test_output_sensitivities_match_the_native_model_and_the_closed_form(
    client, requires_simulation
):
    """theta measured three ways: through the modifier, through the same
    combination written into the model's math, and analytically.

    1e-6 rather than 1e-8: the two models are different augmented CVODES
    systems and their sensitivities agree to ~1e-7 at rtol=atol=1e-12, which is
    the integrator's floor and not the chain rule's -- 1e-14 makes it *worse*
    (1.7e-4). The exact claim about the combination is asserted above.
    """
    native = _one_column(_local_sa(
        client, _setup(client, NATIVE_PATH, NATIVE_PARAMS), {NATIVE_ANCHOR: 1.0},
    ))
    modifier = _one_column(_local_sa(
        client, _setup(client, MODIFIER_PATH, MODIFIER_PARAMS), {MODIFIER_ANCHOR: 1.0},
    ))

    assert set(native) == set(modifier), (native, modifier)
    assert len(native) == 2, native
    for out, native_value in native.items():
        exact = _exact_for(out)
        # Each path against the truth, then against each other.
        assert native_value == pytest.approx(exact, rel=1e-6), out
        assert modifier[out] == pytest.approx(exact, rel=1e-6), out
        assert modifier[out] == pytest.approx(native_value, rel=1e-6), out


@pytest.mark.integration
def test_the_modifier_cost_bar_matches_the_native_models(client, requires_simulation):
    """The cost-sensitivity bar for theta, through the modifier and through the
    model that does the same combination itself.

    3e-3, and the loose figure is upstream's, not the chain rule's. Measured on
    this fixture against the closed form (elasticity 5.806415767):

        differenced cost      5.806415751   2.8e-09
        native FSA cost       5.804107645   4.0e-04
        modifier FSA cost     5.815423107   1.6e-03

    i.e. CA's FSA *cost* gradient (get_cost_and_jac_fsa) is orders of magnitude
    less accurate than its *output* sensitivities, which the test above pins at
    1e-6 against the same closed form. The native model carries no modifier at
    all and is still 4e-4 out, so this is not about theta. The exact claim about
    the combination is asserted in the member-sum tests.
    """
    native_id = _setup(client, NATIVE_PATH, NATIVE_PARAMS)
    native_body = client.post("/api/cost_sensitivity", json={
        "model_id": native_id,
        "params": {"affine/theta": 1.0},
        "param_names": [NATIVE_ANCHOR],
        "bounds": {NATIVE_ANCHOR: [THETA_MIN, THETA_MAX]},
    }).json()
    assert native_body["analytic"] is True, native_body.get("fallback_reason")

    modifier_id = _setup(client, MODIFIER_PATH, MODIFIER_PARAMS)
    modifier_body = client.post("/api/cost_sensitivity", json={
        "model_id": modifier_id,
        "params": {"affine/k1": C1, "affine/k2": C2},
        "param_names": [],
        "modifiers": [MODIFIER_BLOCK],
    }).json()
    assert modifier_body["analytic"] is True, modifier_body.get("fallback_reason")

    native_row = next(r for r in native_body["params"] if r["name"] == NATIVE_ANCHOR)
    modifier_row = modifier_body["params"][0]
    assert abs(native_row["elasticity"]) > 1e-3
    # The cost itself is the same number to the last bits; only its FSA
    # *gradient* is the imprecise part.
    assert modifier_body["cost"] == pytest.approx(native_body["cost"], rel=1e-8)
    assert modifier_row["elasticity"] == pytest.approx(
        native_row["elasticity"], rel=3e-3)
