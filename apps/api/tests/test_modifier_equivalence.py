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

Comparing the two *models* is a different claim: two different augmented CVODES
systems. That comparison, and every closed-form assertion, now lives **upstream
in circulatory_autogen** (`tests/test_fsa_analytic_accuracy.py`, with the
fixture pair), because it pins CA's arithmetic rather than a CUFLynx seam -- and
because CUFLynx CI has no Myokit, so a numerical claim here never actually runs.
What stays below is the seam: that CUFLynx's own member-summing and label lookup
reproduce CA's per-member numbers from a single solve.

**On solver tolerance.** These tests used rtol=atol=1e-12, originally to be
immune to CA's rtol/atol swap in myokit_helper. That swap is fixed (CA #379),
and 1e-12 turned out to be actively harmful: Myokit excludes the CVODES
sensitivity variables from the local error test and sizes its finite-difference
sensitivity RHS by sqrt(rtol), so tightening rtol degrades the *gradients* while
the states stay exact (CA #387 -- measured 9e-8 at rtol 1e-8 against 3e-3 at
1e-12). CA now warns below 1e-9. These tests run at 1e-8.

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
    """One model + the shared obs_data + its params_for_id, on a Myokit CVODES
    backend at the tolerance CA's FSA gradients are actually accurate at.

    NOT tighter: below rtol 1e-9 the CVODES sensitivities get *worse*, because
    Myokit leaves them out of the local error test and sizes its
    finite-difference sensitivity RHS by sqrt(rtol) (CA #387). CA warns there.
    """
    resp = client.post("/api/config", json={
        "generated_model_format": "cellml_only",
        "solver": "CVODE_myokit",
        "solver_info": {"rtol": 1e-8, "atol": 1e-8},
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


# NOTE: test_the_two_models_are_the_same_system_at_theta_one moved to
# circulatory_autogen tests/test_fsa_analytic_accuracy.py -- it validates the
# fixture pair, so it travels with the fixture.

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
# NOTE: test_output_sensitivities_match_the_native_model_and_the_closed_form
# moved to circulatory_autogen tests/test_fsa_analytic_accuracy.py -- the
# closed-form claim (d ln Y/d ln theta = -c*T) is about CA's sensitivities,
# not about any CUFLynx seam.

@pytest.mark.integration
def test_both_cost_bars_are_analytic_and_broadly_agree(client, requires_simulation):
    """Smoke check: the cost-sensitivity bar for theta comes back *analytic* on both
    arms and lands in the same place.

    Deliberately loose (1%). The accuracy of CA's FSA cost gradient is CA's claim and
    is asserted upstream against the closed form
    (circulatory_autogen tests/test_fsa_analytic_accuracy.py); encoding a tighter
    number here would record upstream's accuracy in our suite, where it neither
    belongs nor tightens when CA improves. What this pins is ours: that the modifier
    path produces an analytic bar at all, for the right quantity, of the right size.
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
    # The cost itself is the same number to the last bits -- that much is exact.
    assert modifier_body["cost"] == pytest.approx(native_body["cost"], rel=1e-8)
    # The gradient only has to be recognisably the same bar.
    assert modifier_row["elasticity"] == pytest.approx(
        native_row["elasticity"], rel=1e-2)
