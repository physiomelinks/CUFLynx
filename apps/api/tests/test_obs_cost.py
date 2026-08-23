"""What the current parameters cost, against the loaded obs_data (issue #159).

Manual exploration had no number attached: you moved a slider, the trace moved,
and whether it moved *towards* the data was left to the eye.
"""

from __future__ import annotations

import obs_cost
import math
import pytest


def _item(**over):
    item = {
        "data_item_name": "pressure",
        "name_for_plotting": "u_{AR}",
        "operation": "max",
        "operands": ["a/u"],
        "value": 10.0,
        "std": 1.0,
        "weight": 1.0,
        "cost_type": "MSE",
    }
    item.update(over)
    return item


# A stand-in for CA's MSE. Its parameters are named as CA names them, because
# that is now load-bearing: since CA #370 a cost func is handed ``std``/``weight``
# only when its signature declares them by those names, so a fake with
# abbreviated parameters would not be exercising the call CA makes.
def _mse(output, desired_mean, std, weight):
    return weight * (output - desired_mean) ** 2


def _funcs(monkeypatch, *, ops=None, costs=None):
    monkeypatch.setattr(obs_cost, "get_operation_funcs", lambda _d=None: ops if ops is not None else {"max": max})
    monkeypatch.setattr(
        obs_cost,
        "get_cost_funcs",
        lambda _d=None: costs if costs is not None else {"MSE": _mse},
    )


def test_it_scores_the_run_against_the_data(monkeypatch):
    _funcs(monkeypatch)
    out = obs_cost.evaluate([_item()], {0: {"a/u": [1.0, 9.0, 3.0]}})
    assert out["cost"] == pytest.approx(1.0)
    (entry,) = out["items"]
    assert entry["model"] == 9.0
    assert entry["observed"] == 10.0


def test_the_errors_match_what_a_calibration_saves(monkeypatch):
    """percent_error_vec.npy and std_error_vec.npy, so a manual perturbation and
    a best fit can be put on the same axes."""
    _funcs(monkeypatch)
    (entry,) = obs_cost.evaluate([_item(std=2.0)], {0: {"a/u": [9.0]}})["items"]
    assert entry["percent_error"] == pytest.approx(-10.0)
    assert entry["std_error"] == pytest.approx(-0.5)


def test_each_item_is_scored_in_its_own_experiment(monkeypatch):
    """A data_item names the experiment it belongs to; scoring it against
    another one's trace would be quietly wrong."""
    _funcs(monkeypatch)
    items = [_item(value=1.0), _item(value=5.0, experiment_idx=1)]
    out = obs_cost.evaluate(items, {0: {"a/u": [1.0]}, 1: {"a/u": [5.0]}})
    assert out["cost"] == pytest.approx(0.0)


def test_time_resolves_to_the_run_time_vector(monkeypatch):
    """`time` is an operand of every windowed or peak-timing operation, and it is
    returned beside the outputs rather than in them. Missed, every such
    observable goes unscored -- which looked like a better fit than it was."""
    _funcs(monkeypatch, ops={"first": lambda t, y: t[0]})
    item = _item(operation="first", operands=["time", "a/u"], value=0.0)
    (entry,) = obs_cost.evaluate([item], {0: {"a/u": [1.0], "time": [7.0]}})["items"]
    assert entry["model"] == 7.0


def test_the_weight_counts(monkeypatch):
    _funcs(monkeypatch)
    light = obs_cost.evaluate([_item(weight=1.0)], {0: {"a/u": [9.0]}})["cost"]
    heavy = obs_cost.evaluate([_item(weight=4.0)], {0: {"a/u": [9.0]}})["cost"]
    assert heavy == pytest.approx(4 * light)


def test_an_unrecorded_operand_leaves_that_item_unscored(monkeypatch):
    """Reported as unscored rather than as zero: an observable nobody measured
    must not look like a perfect one."""
    _funcs(monkeypatch)
    out = obs_cost.evaluate([_item(), _item(operands=["a/missing"])], {0: {"a/u": [9.0]}})
    assert [i["cost"] is None for i in out["items"]] == [False, True]
    # 1.0 over the two weighted observables: the unscorable one still counts in
    # the denominator, because CA scores it (#181).
    assert out["cost"] == pytest.approx(0.5)


def test_nothing_scorable_is_none_not_zero(monkeypatch):
    """"Perfect fit" and "could not tell" must not look the same."""
    _funcs(monkeypatch)
    assert obs_cost.evaluate([_item(operands=["a/missing"])], {0: {}}) is None


def test_no_data_items_is_none(monkeypatch):
    _funcs(monkeypatch)
    assert obs_cost.evaluate([], {0: {"a/u": [1.0]}}) is None


def test_without_ca_there_is_no_cost(monkeypatch):
    """The cost has to be CA's own function; inventing one here would look
    authoritative while ranking parameter sets differently."""
    monkeypatch.setattr(obs_cost, "get_cost_funcs", lambda _d=None: None)
    monkeypatch.setattr(obs_cost, "get_operation_funcs", lambda _d=None: {"max": max})
    assert obs_cost.evaluate([_item()], {0: {"a/u": [9.0]}}) is None


# ---------------------------------------------------------------------------
# cost_kwargs (CA #370 / issue #201). A cost func no longer has one fixed
# signature: it is handed std/weight only when it declares them, plus whatever the
# data_item's `cost_kwargs` says. This panel has to call it the way the
# calibration will, or it goes back to being a second, disagreeing cost (#159).
# ---------------------------------------------------------------------------
def _requires_cost_kwargs():
    if obs_cost._ca_call_cost_func() is None:
        pytest.skip("circulatory_autogen without the cost_kwargs contract (pre-#370)")


def test_a_data_items_cost_kwargs_reach_its_cost_func(monkeypatch):
    _requires_cost_kwargs()
    seen = {}

    def tolerant(output, desired_mean, std, weight, tolerance=0.0):
        seen["tolerance"] = tolerance
        return abs(output - desired_mean) - tolerance

    _funcs(monkeypatch, costs={"tolerant": tolerant})
    out = obs_cost.evaluate(
        [_item(value=10.0, cost_type="tolerant", cost_kwargs={"tolerance": 1.5})],
        {0: {"a/u": [8.0]}},
    )
    assert seen["tolerance"] == 1.5
    assert out["cost"] == pytest.approx(0.5)


def test_a_cost_func_that_takes_no_std_is_not_handed_one(monkeypatch):
    """`multimodal_gaussian` is CA's example: it has nowhere to put a std, and a
    fixed four-argument call here would fail on it while the calibration works."""
    _requires_cost_kwargs()

    def no_std(output, desired_mean, weight):
        return weight * abs(output - desired_mean)

    _funcs(monkeypatch, costs={"no_std": no_std})
    out = obs_cost.evaluate([_item(value=10.0, cost_type="no_std")], {0: {"a/u": [8.0]}})
    assert out["cost"] == pytest.approx(2.0)


def _obs_data(**over):
    item = {
        "data_item_name": "a/u", "name_for_plotting": "u", "operation": "max",
        "operands": ["a/u"], "unit": "dimensionless", "value": 10.0, "std": 1.0,
        "weight": 1.0, "experiment_idx": 0, "subexperiment_idx": 0,
        "data_type": "constant", "cost_type": "tolerant",
    }
    item.update(over)
    return {"protocol_info": {"pre_times": [0.0], "sim_times": [[1.0]]},
            "data_items": [item]}


def test_cost_kwargs_survive_cas_own_parser_and_change_the_cost(tmp_path):
    """End to end on the path the panel actually takes: a user cost func with a
    keyword argument, CA's parser, CA's registry, CA's cost. If the kwarg did not
    reach the func the editor would be offering an input that changes nothing --
    the same class of silent no-op as a dropped key on save.
    """
    _requires_cost_kwargs()
    import user_funcs

    out_dir = str(tmp_path)
    user_funcs.save_user_func(
        "cost", "tolerant",
        "def tolerant(output, desired_mean, weight, tolerance=0.0):\n"
        "    return float(np.abs(output - desired_mean) - tolerance) * weight\n",
        base_dir=out_dir,
    )
    run = {0: {"a/u": [1.0, 8.0]}}  # max -> 8, observed 10, so |err| = 2

    plain = obs_cost._ca_evaluate(_obs_data(), run, out_dir, 0.01)
    if plain is None:
        pytest.skip("circulatory_autogen could not be reached")
    assert plain["cost"] == pytest.approx(2.0)  # the kwarg's default, 0

    tuned = obs_cost._ca_evaluate(
        _obs_data(cost_kwargs={"tolerance": 1.5}), run, out_dir, 0.01)
    assert tuned["cost"] == pytest.approx(0.5)
    # The per-item column is computed from the same call, so it has to move too.
    assert tuned["items"][0]["cost"] == pytest.approx(0.5)


def test_the_positional_call_is_kept_for_a_ca_without_the_contract(monkeypatch):
    """Pre-#370 every cost func had the fixed (output, gt, std, weight) signature
    and there were no cost_kwargs to pass, so the panel must still score there."""
    monkeypatch.setattr(obs_cost, "_ca_call_cost_func", lambda: None)
    _funcs(monkeypatch)
    out = obs_cost.evaluate([_item(value=10.0)], {0: {"a/u": [8.0]}})
    assert out["cost"] == pytest.approx(4.0)


def test_a_cost_func_that_raises_does_not_lose_the_others(monkeypatch):
    def angry(*_a, **_k):
        raise ValueError("no")

    _funcs(monkeypatch, costs={"MSE": lambda output, desired_mean, std, weight: 1.0, "bad": angry})
    out = obs_cost.evaluate([_item(), _item(cost_type="bad")], {0: {"a/u": [9.0]}})
    assert out["cost"] == pytest.approx(0.5)  # 1.0 / 2 weighted observables (#181)
    assert out["items"][1]["cost"] is None


def test_a_series_returning_operation_is_not_scored(monkeypatch):
    """It has no single value to compare with an observation."""
    _funcs(monkeypatch, ops={"identity": lambda y: y})
    assert obs_cost.evaluate([_item(operation="identity")], {0: {"a/u": [1.0, 2.0]}}) is None


def test_a_non_finite_result_is_not_scored(monkeypatch):
    """A parameter driven somewhere absurd produces nan, and nan sorts oddly and
    formats worse; better no number than a meaningless one."""
    _funcs(monkeypatch, ops={"nan": lambda y: float("nan")})
    assert obs_cost.evaluate([_item(operation="nan")], {0: {"a/u": [1.0]}}) is None


@pytest.mark.integration
def test_the_run_routes_report_a_cost(client, requires_simulation):
    """End to end, with circulatory_autogen's real operation and cost functions."""
    import json

    from conftest import SN_MODEL_PATH, SN_OBS_DATA_PATH, upload_model

    model_id = upload_model(client, SN_MODEL_PATH)["model_id"]
    obs = json.loads(SN_OBS_DATA_PATH.read_text())
    assert client.post(
        "/api/obs_data/upload", json={"model_id": model_id, "obs_data": obs}
    ).status_code == 200

    resp = client.post(
        "/api/protocol/run",
        json={"model_id": model_id, "params": {}, "outputs": ["soma_SN/V"]},
    )
    assert resp.status_code == 200, resp.text
    cost = resp.json()["cost"]
    assert cost and cost["cost"] > 0
    # Every observable is scored, not only the ones that happened to be plotted:
    # the run asks for the obs operands too.
    assert all(item["cost"] is not None for item in cost["items"])


@pytest.mark.integration
def test_moving_a_parameter_moves_the_cost(client, requires_simulation):
    """The point of the number: it has to respond to the slider."""
    import json

    from conftest import SN_MODEL_PATH, SN_OBS_DATA_PATH, upload_model

    model_id = upload_model(client, SN_MODEL_PATH)["model_id"]
    client.post(
        "/api/obs_data/upload",
        json={"model_id": model_id, "obs_data": json.loads(SN_OBS_DATA_PATH.read_text())},
    )

    def cost_for(params):
        resp = client.post(
            "/api/protocol/run",
            json={"model_id": model_id, "params": params, "outputs": ["soma_SN/V"]},
        )
        assert resp.status_code == 200, resp.text
        return resp.json()["cost"]["cost"]

    base = cost_for({})
    # g_Na, not g_M: the protocol's params_to_change sets g_M per sub-experiment,
    # so a slider value for it is overridden by the protocol -- correctly, and
    # the first version of this test read that as "the cost does not respond".
    moved = cost_for({"soma_SN/g_Na": 6.0})
    assert moved != pytest.approx(base)


# ---------------------------------------------------------------------------
# The number must be the same number the calibration reports (issue #181)
#
# Reported as: after a calibration, "Reset to best fit" puts the calibration's
# own parameters on the sliders, and the output-plots cost still disagrees with
# the calibration panel's. Same parameters, same data, two different numbers --
# so one of them is computed differently, and it was this one.
#
# circulatory_autogen aggregates in paramID.get_cost_obs_and_pred_from_params:
#
#     cost += sub_cost                       # over experiments x subexperiments
#     ...
#     cost = cost / float(weighted_obs_denominator)
#
# i.e. the *mean* contribution per weighted observable, where the denominator
# counts every observable with a non-zero weight. This summed instead, so it was
# larger by exactly the number of observables -- which is why it looked like a
# plausible cost rather than an obviously broken one.
# ---------------------------------------------------------------------------
def test_the_cost_is_the_mean_per_observable_not_the_sum(monkeypatch):
    """The reported mismatch, at its smallest: two observables, each off by 2."""
    _funcs(monkeypatch)
    items = [_item(value=10.0), _item(value=10.0, operands=["a/v"])]
    out = obs_cost.evaluate(items, {0: {"a/u": [8.0], "a/v": [8.0]}})
    # Each contributes (8-10)^2 = 4. CA divides by the 2 weighted observables.
    assert out["cost"] == pytest.approx(4.0)
    assert [e["cost"] for e in out["items"]] == [pytest.approx(4.0)] * 2


def test_one_observable_is_unaffected_by_the_normalisation(monkeypatch):
    """Which is why the bug survived: the single-observable case is exact, and
    every hand-checked example was a single observable."""
    _funcs(monkeypatch)
    out = obs_cost.evaluate([_item(value=10.0)], {0: {"a/u": [8.0]}})
    assert out["cost"] == pytest.approx(4.0)


def test_a_zero_weighted_observable_is_excluded_entirely(monkeypatch):
    """CA skips weight == 0 rather than scoring it: `if weight_entry != 0`. It
    must leave the denominator too, or turning an observable off would change
    the cost of the ones left on."""
    _funcs(monkeypatch)
    items = [_item(value=10.0), _item(value=0.0, weight=0.0, operands=["a/v"])]
    out = obs_cost.evaluate(items, {0: {"a/u": [8.0], "a/v": [1e6]}})
    assert out["cost"] == pytest.approx(4.0)
    assert out["items"][1]["cost"] is None


def test_a_zero_weight_is_not_read_as_a_default(monkeypatch):
    """`item.get("weight") or 1.0` turns a deliberate 0 into a 1 -- the one
    coercion that silently reverses the user's intent."""
    _funcs(monkeypatch)
    out = obs_cost.evaluate([_item(value=10.0, weight=0.0)], {0: {"a/u": [8.0]}})
    assert out is None  # nothing weighted, so nothing to report


def test_the_denominator_spans_experiments(monkeypatch):
    """CA's denominator is global -- summed over every experiment and
    subexperiment -- not per experiment."""
    _funcs(monkeypatch)
    items = [_item(value=10.0), _item(value=10.0, experiment_idx=1)]
    out = obs_cost.evaluate(items, {0: {"a/u": [8.0]}, 1: {"a/u": [8.0]}})
    assert out["cost"] == pytest.approx(4.0)


def test_an_unscorable_observable_still_counts_against_the_mean(monkeypatch):
    """It counts in CA's denominator, because CA scores it. Dropping it from
    ours as well would report a *better* cost than the calibration for the same
    parameters -- the same class of disagreement, in the flattering direction."""
    _funcs(monkeypatch)
    items = [_item(value=10.0), _item(value=10.0, operands=["not/recorded"])]
    out = obs_cost.evaluate(items, {0: {"a/u": [8.0]}})
    assert out["cost"] == pytest.approx(2.0)  # 4 / 2, not 4 / 1
    assert out["incomplete"] is True


def test_a_complete_score_is_not_flagged_incomplete(monkeypatch):
    _funcs(monkeypatch)
    out = obs_cost.evaluate([_item(value=10.0)], {0: {"a/u": [8.0]}})
    assert out["incomplete"] is False



def _lv(client):
    """The Lotka-Volterra study loaded, returning (model_id, obs_data dict)."""
    import json

    from conftest import LV_MODEL_PATH, LV_OBS_DATA_PATH, upload_model

    model_id = upload_model(client, LV_MODEL_PATH)["model_id"]
    obs = json.loads(LV_OBS_DATA_PATH.read_text())
    assert client.post(
        "/api/obs_data/upload", json={"model_id": model_id, "obs_data": obs}
    ).status_code == 200
    return model_id, obs


# ---------------------------------------------------------------------------
# The number is circulatory_autogen's, not a reproduction of it
# ---------------------------------------------------------------------------
@pytest.mark.integration
def test_the_cost_is_computed_by_ca(client, requires_simulation):
    """The panel used to reproduce CA's cost by walking the data_items here, which
    is how the two came to disagree (#181, #182). It now asks CA."""
    import engine as engine_mod

    engine_mod.engine.reset()
    engine_mod.engine.model_type, engine_mod.engine.solver = "cellml", "CVODE_myokit"
    model_id, _ = _lv(client)

    r = client.post("/api/protocol/run", json={"model_id": model_id, "params": {}})
    assert r.status_code == 200, r.text
    cost = r.json()["cost"]
    assert cost["computed_by"] == "circulatory_autogen"


@pytest.mark.integration
def test_it_matches_cas_own_cost_path(client, requires_simulation):
    """Asserted against CA directly, so a divergence shows up here rather than as
    a calibration panel and an output panel quietly disagreeing."""
    import numpy as np
    import tempfile
    import engine as engine_mod
    from ca_imports import ca_from

    ObsAndParamDataParser, scriptFunctionParser = ca_from(
        "parsers.PrimitiveParsers", "ObsAndParamDataParser", "scriptFunctionParser")
    OpencorParamID = ca_from("param_id.paramID", "OpencorParamID")

    engine_mod.engine.reset()
    engine_mod.engine.model_type, engine_mod.engine.solver = "cellml", "CVODE_myokit"
    model_id, obs = _lv(client)

    r = client.post("/api/protocol/run", json={"model_id": model_id, "params": {}})
    body = r.json()
    ours = body["cost"]["cost"]

    scored_by = {e: {**x.get("outputs", {}), "time": x.get("time", [])}
                 for e, x in enumerate(body.get("experiments", []))}
    for s in body.get("subexperiments") or []:
        scored_by[(s["experiment_idx"], s["subexperiment_idx"])] = s.get("outputs", {})

    parser = ObsAndParamDataParser()
    parsed = parser.parse_obs_data_json(obs_data_dict=obs, pre_time=0.0, sim_time=1.0)
    with tempfile.TemporaryDirectory() as d:
        obs_info = parser.process_obs_info(gt_df=parsed["gt_df"], output_dir=d,
                                           dt=engine_mod.engine.dt)
    proto = parser.process_protocol_and_weights(
        gt_df=parsed["gt_df"], protocol_info=parsed["protocol_info"],
        dt=engine_mod.engine.dt)

    pid = OpencorParamID.__new__(OpencorParamID)
    pid.obs_info, pid.protocol_info = obs_info, proto
    pid.cost_type = obs_info["cost_type"]
    pid.dt = engine_mod.engine.dt
    pid.model_type = "cellml"
    sfp = scriptFunctionParser()
    pid.cost_funcs_dict = pid.cost_funcs_dict_symbolic = sfp.get_cost_funcs_dict("numpy")
    pid.operation_funcs_dict = pid.operation_funcs_dict_symbolic = \
        sfp.get_operation_funcs_dict("numpy")
    pid._num_weighted_obs_by_exp_sub = None
    pid._refresh_num_weighted_obs_tables()

    total, denom = 0.0, 0
    for exp in range(len(proto["sim_times"])):
        for sub in range(proto["num_sub_per_exp"][exp]):
            seg = scored_by.get((exp, sub)) or scored_by.get(exp) or {}
            operands = [[np.asarray(seg.get(n, []), dtype=float)
                         for n in obs_info["operands"][JJ]]
                        for JJ in range(obs_info["num_obs"])]
            total += pid.get_cost_from_operands(operands, exp_idx=exp, sub_idx=sub)
            denom += pid._num_weighted_obs_by_exp_sub[exp][sub]

    assert ours == pytest.approx(total / float(denom))


def test_without_ca_the_panel_still_reports(monkeypatch):
    """A CA that cannot be reached must not lose the panel -- it degrades to the
    local walk, and says so, rather than showing nothing."""
    import obs_cost

    monkeypatch.setattr(obs_cost, "_ca_engine", lambda *a, **k: None)
    monkeypatch.setattr(obs_cost, "get_operation_funcs", lambda _d=None: {"max": max})
    monkeypatch.setattr(
        obs_cost, "get_cost_funcs", lambda _d=None: {"MSE": _mse})

    item = {"data_item_name": "x", "operation": "max", "operands": ["a/u"], "value": 1.0,
            "std": 1.0, "weight": 1.0, "cost_type": "MSE"}
    out = obs_cost.evaluate([item], {0: {"a/u": [3.0]}},
                            obs_data={"protocol_info": {}, "data_items": [item]})
    assert out["computed_by"] == "cuflynx"
    assert out["cost"] == pytest.approx(4.0)


# ---------------------------------------------------------------------------
# The emulator's cost, beside the solver's (#333)
#
# A calibration with "use the emulator" on minimises the *emulator's* cost while
# the Output plots show the solver's, and the two were never on screen together
# -- so a user comparing the reported best cost with the number above the plots
# was comparing two different functions with nothing to say so. Showing both is
# only defensible if they are the same arithmetic over different features, which
# is what these pin.
# ---------------------------------------------------------------------------
def _ca_labels(obs):
    """CA's own feature labels for an obs_data, or a skip when CA is unreachable.

    Taken from CA rather than written out here on purpose: the emulator records
    these strings when it is trained, and the match has to be against *those*.
    """
    pid = obs_cost._ca_engine(obs, None, 0.01)
    if pid is None:
        pytest.skip("circulatory_autogen could not be reached")
    labels = obs_cost._ca_feature_labels(pid.obs_info)
    if labels is None:
        pytest.skip("this circulatory_autogen has no emulator feature labels")
    return labels


def test_the_emulator_cost_is_the_solver_cost_when_the_features_agree():
    """The strong form of "same arithmetic": feed the emulated path the very
    values the run produced and every number must come back identical -- cost,
    denominator, per-observable rows and all. Anything less and the gap between
    the two figures would be partly the implementation rather than wholly the
    surrogate's error."""
    obs = _obs_data(cost_type="MSE")
    solver = obs_cost._ca_evaluate(obs, {0: {"a/u": [1.0, 8.0]}}, None, 0.01)
    if solver is None:
        pytest.skip("circulatory_autogen could not be reached")
    (label,) = _ca_labels(obs)

    emulated = obs_cost.evaluate_features({label: solver["items"][0]["model"]}, obs)
    assert emulated == solver


def test_a_prediction_that_differs_scores_differently():
    """...and by exactly the cost func's own amount: MSE of 6 against 10 is 16,
    where the run's 8 was 4. A number that did not move with the prediction would
    be describing something other than the emulator."""
    obs = _obs_data(cost_type="MSE")
    (label,) = _ca_labels(obs)
    out = obs_cost.evaluate_features({label: 6.0}, obs)
    assert out["cost"] == pytest.approx(16.0)
    assert out["items"][0]["model"] == pytest.approx(6.0)
    assert out["items"][0]["percent_error"] == pytest.approx(-40.0)


def test_predictions_are_matched_by_cas_own_disambiguated_labels():
    """A plotting name can repeat across experiments, and CA then appends
    "[exp e, sub s]". Matching on the bare name would score one experiment's
    prediction against another's data -- silently, and only for the studies where
    it matters most."""
    item = _obs_data(cost_type="MSE")["data_items"][0]
    obs = {
        "protocol_info": {"pre_times": [0.0, 0.0], "sim_times": [[1.0], [1.0]]},
        "data_items": [dict(item), dict(item, experiment_idx=1, value=4.0)],
    }
    labels = _ca_labels(obs)
    assert labels == ["u (max a/u) [exp 0, sub 0]", "u (max a/u) [exp 1, sub 0]"]

    both = obs_cost.evaluate_features({labels[0]: 8.0, labels[1]: 3.0}, obs)
    # CA's mean per weighted observable over both experiments: (16 + 1) / 2 is
    # not it -- 8 against 10 is 4, 3 against 4 is 1, so 2.5.
    assert both["cost"] == pytest.approx(2.5)
    # The undisambiguated name matches nothing, and nothing is scored at all.
    assert obs_cost.evaluate_features({"u": 8.0}, obs) is None


def test_a_feature_the_emulator_has_no_value_for_scores_nothing():
    """Not a partial cost: an unscored observable in a mean would read as a
    better fit than the solver's over the same data."""
    obs = _obs_data(cost_type="MSE")
    _ca_labels(obs)
    assert obs_cost.evaluate_features({"not a feature of this study": 8.0}, obs) is None
    assert obs_cost.evaluate_features({}, obs) is None
    assert obs_cost.evaluate_features({"x": 1.0}, None) is None


def test_without_ca_there_is_no_emulator_cost(monkeypatch):
    """`evaluate` degrades to the local walk when CA cannot be reached; this must
    not. The two figures sit side by side, so one computed by CA and the other by
    CUFLynx's approximation would be a comparison of engines dressed as a
    comparison of the model with its surrogate."""
    monkeypatch.setattr(obs_cost, "_ca_engine", lambda *a, **k: None)
    monkeypatch.setattr(obs_cost, "get_operation_funcs", lambda _d=None: {"max": max})
    monkeypatch.setattr(obs_cost, "get_cost_funcs", lambda _d=None: {"MSE": _mse})
    assert obs_cost.evaluate_features({"u (max a/u)": 8.0}, _obs_data(cost_type="MSE")) is None


def test_the_solver_cost_is_untouched_by_the_emulated_path():
    """The default call is byte-for-byte the call it always was: no features, no
    change. `evaluate` is what every run route uses, and the emulator being off
    must leave it exactly as it was."""
    obs = _obs_data(cost_type="MSE")
    run = {0: {"a/u": [1.0, 8.0]}}
    direct = obs_cost._ca_evaluate(obs, run, None, 0.01)
    if direct is None:
        pytest.skip("circulatory_autogen could not be reached")
    assert obs_cost.evaluate(obs["data_items"], run, None, obs_data=obs) == direct


# ---------------------------------------------------------------------------
# Why there is no em cost, when there is none
# ---------------------------------------------------------------------------
# The predicted features still draw their dotted overlay, so returning a bare
# None left the user with lines on the plot, no number beside them, and no way
# to tell a stale bundle from an edited obs_data from a series observable.
def _six_labels():
    """CA's feature labels for :func:`_six_item_obs`, in the emulator's output order.

    Since CA #466 a feature is labelled by the item's **identity** -- its
    `data_item_name` -- not by a label composed from `name_for_plotting` and the
    operation (`'mean(T_{p1}) (mean heat/T_p1)'`). The composed form could not be an
    identity: two items on one trace shared it, which is the collision #466 was opened
    for. Written out rather than derived from the fixture so that a change in CA's
    labelling fails here, which is the whole point of matching by label.
    """
    return [f'probe {i} {op}' for i in (1, 2, 3) for op in ('mean', 'min')]


def _six_item_obs():
    return [
        {"data_item_name": f"probe {i} {op}",
         "trace_name_for_plotting": f"{op}(T_{{p{i}}})",
         "data_type": "constant", "operation": op, "operands": [f"heat/T_p{i}"],
         "unit": "dimensionless", "weight": 1.0, "value": 0.4, "std": 0.05,
         "cost_type": "gaussian_MLE"}
        for i in (1, 2, 3) for op in ("mean", "min")
    ]


@pytest.mark.integration
def test_a_missing_feature_names_the_observable_and_says_to_retrain(requires_ca, tmp_path):
    """The reported case: an obs_data item the emulator was not trained on."""
    labels = _six_labels()
    why = []
    result = obs_cost.evaluate_features(
        {lab: 0.4 for lab in labels[:-1]},   # one observable added since training
        _six_item_obs(), str(tmp_path), dt=0.02, why=why)
    assert result is None
    assert why, 'the failure must say why'
    assert labels[-1] in why[0], 'it must name the observable that has no prediction'
    assert 'retrain' in why[0].lower()


@pytest.mark.integration
def test_a_complete_prediction_scores_and_says_nothing(requires_ca, tmp_path):
    why = []
    result = obs_cost.evaluate_features(
        {lab: 0.4 for lab in _six_labels()}, _six_item_obs(), str(tmp_path), dt=0.02, why=why)
    # 0.0 here is a legitimate cost: the predictions equal the ground truth, so this
    # is a perfect fit rather than a failure -- which is exactly why "could not tell"
    # must never be reported as a number.
    assert result is not None and math.isfinite(result["cost"])
    assert len(result["items"]) == 6
    assert why == [], 'a cost that could be computed has nothing to explain'


@pytest.mark.unit
def test_no_obs_data_says_so_rather_than_naming_a_label():
    why = []
    assert obs_cost.evaluate_features({'a': 1.0}, None, None, why=why) is None
    assert 'obs_data' in why[0]


# ---------------------------------------------------------------------------
# The emulator's feature labels come from CA, through the resolver
# ---------------------------------------------------------------------------
# ``_ca_feature_labels`` used to spell the flat name outright:
#
#     from param_id.paramID import emulated_feature_labels
#
# which resolves on a 0.4.x CA only through the deprecation shim -- it is the line
# that emits ``DeprecationWarning: param_id is now libcuflynx.param_id`` in the
# unit run -- and stops resolving in 0.5.0, when the shim is deleted.
#
# The import sits inside ``except Exception: return None`` by design (a CA
# predating emulators has no such function). So the breakage would have been
# silent and total: None for every study, ``_emulated_operands`` gives up, and the
# emulator's "EM COST" disappears from the panel with a generic reason, no error,
# no log line and no failing test.
def _namespaced_only(monkeypatch, flat_name, module):
    """Register ``module`` under its ``libcuflynx.`` name and *only* that one.

    The post-shim CA of 0.5.0: the flat spelling is set to None, which is the
    idiom for "importing this raises ImportError".
    """
    import sys
    import types

    import ca_imports

    ca_imports.reset_cache()
    monkeypatch.setitem(sys.modules, ca_imports.NAMESPACE,
                        types.ModuleType(ca_imports.NAMESPACE))
    monkeypatch.setitem(sys.modules, f"{ca_imports.NAMESPACE}.{flat_name}", module)
    monkeypatch.setitem(sys.modules, flat_name, None)


def test_feature_labels_resolve_on_a_ca_with_no_flat_spelling_left(monkeypatch):
    import types

    fake = types.SimpleNamespace(
        emulated_feature_labels=lambda obs_info: ["u_{AR} [max]", "q_{LV} [min]"]
    )
    _namespaced_only(monkeypatch, "param_id.paramID", fake)

    assert obs_cost._ca_feature_labels({"num_obs": 2}) == ["u_{AR} [max]", "q_{LV} [min]"]


def test_feature_labels_are_none_on_a_ca_that_has_no_such_function(monkeypatch):
    """The case the swallowing ``except`` is *for*: a CA predating emulators. It
    must still degrade rather than raise -- the caller reports "cannot label"."""
    import types

    _namespaced_only(monkeypatch, "param_id.paramID", types.SimpleNamespace())

    assert obs_cost._ca_feature_labels({"num_obs": 2}) is None


def test_no_module_spells_a_ca_import_flat_outside_the_resolver():
    """A grep, deliberately: this is the rule CLAUDE.md states and the one that
    keeps being broken by a lazy import written inside a ``try``.

    ``param_id.paramID`` is the module that got it wrong; the sweep covers every
    flat CA top-level so the next one is caught in the same place.
    """
    import ast
    from pathlib import Path

    import ca_imports

    api_dir = Path(__file__).resolve().parents[1]
    # sim_worker_runner and export_pipeline carry the deliberate duplicates
    # (tests/test_ca_import_parity.py pins those); ca_imports is the resolver.
    exempt = {"ca_imports.py", "sim_worker_runner.py", "export_pipeline.py"}
    offenders = []
    for path in sorted(api_dir.glob("*.py")):
        if path.name in exempt:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""] if node.level == 0 else []
            else:
                continue
            for name in names:
                top = name.split(".", 1)[0]
                if top in ca_imports.CA_PACKAGES or top in ca_imports.RELOCATED_MODULES:
                    offenders.append(f"{path.name}:{node.lineno} imports {name!r}")

    assert offenders == [], (
        "circulatory_autogen must be reached through ca_imports.ca_import / "
        "ca_from, never by a literal import: " + "; ".join(offenders)
    )
