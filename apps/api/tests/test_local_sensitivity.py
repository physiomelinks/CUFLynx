"""Unit tests for the local-sensitivity AD guard (no CA / casadi needed)."""

import local_sensitivity as ls
import numpy as np
import pytest


def _op(differentiable: bool):
    def fn(*_a, **_k):
        return 0.0

    fn._diff = differentiable
    return fn


def _is_diff(fn):
    return getattr(fn, "_diff", False)


def test_assert_ad_operations_passes_when_all_differentiable():
    funcs = {"max": _op(True), "min": _op(True)}
    # Should not raise (duplicates collapsed, all differentiable).
    ls.assert_ad_operations(["max", "min", "max"], funcs, _is_diff)


def test_assert_ad_operations_raises_informative_error_for_non_differentiable():
    funcs = {"max": _op(True), "calc_spike_period": _op(False)}
    with pytest.raises(ValueError) as ei:
        ls.assert_ad_operations(["max", "calc_spike_period"], funcs, _is_diff)
    msg = str(ei.value)
    assert "calc_spike_period" in msg  # names the offending op
    assert "@differentiable" in msg  # explains why
    assert "FD" in msg  # tells the user how to proceed


def test_assert_ad_operations_flags_unknown_operation():
    with pytest.raises(ValueError) as ei:
        ls.assert_ad_operations(["mystery_op"], {}, _is_diff)
    assert "mystery_op" in str(ei.value)


def test_assert_ad_operations_ignores_empty_and_none():
    ls.assert_ad_operations(["", None, "max"], {"max": _op(True)}, _is_diff)


def test_assert_ad_operations_names_non_differentiable_cost_function():
    op_funcs = {"max": _op(True)}
    cost_funcs = {"MSE": _op(True), "weird_cost": _op(False)}
    with pytest.raises(ValueError) as ei:
        ls.assert_ad_operations(
            ["max"], op_funcs, _is_diff,
            cost_types=["MSE", "weird_cost"], cost_funcs_dict=cost_funcs,
        )
    msg = str(ei.value)
    assert "cost function(s)" in msg
    assert "weird_cost" in msg
    # The (differentiable) operation isn't blamed.
    assert "operation(s)" not in msg


def test_assert_ad_operations_names_both_operations_and_cost_functions():
    with pytest.raises(ValueError) as ei:
        ls.assert_ad_operations(
            ["bad_op"], {"bad_op": _op(False)}, _is_diff,
            cost_types=["bad_cost"], cost_funcs_dict={"bad_cost": _op(False)},
        )
    msg = str(ei.value)
    assert "operation(s)" in msg and "bad_op" in msg
    assert "cost function(s)" in msg and "bad_cost" in msg


# --- nominal point resolution: local SA must honour the current slider values ---
class _FakeHelper:
    def get_init_param_vals(self, _names):
        # model built-in initial values for two params
        return [[1.0], [2.0]]


class _FakeSM:
    sim_helper = _FakeHelper()
    SA_info = {"param_names": [["a/x"], ["b/y"]]}


def _resolve(current_params, mode="current"):
    return ls._resolve_nominal(
        _FakeSM(), ["a/x", "b/y"], np.array([0.0, 0.0]), np.array([10.0, 10.0]),
        {"nominal": mode}, None, None, current_params=current_params,
    )


def test_resolve_nominal_current_uses_slider_values(monkeypatch):
    """Regression (#65): nominal='current' local SA must linearise about the user's
    current slider values, not the model's built-in initial values."""
    nominal, source = _resolve({"a/x": 5.0, "b/y": 7.0})
    assert list(nominal) == [5.0, 7.0]
    assert "sliders" in source


def test_resolve_nominal_current_falls_back_to_model_defaults():
    """With no slider values supplied, fall back to the model's initial values."""
    nominal, source = _resolve(None)
    assert list(nominal) == [1.0, 2.0]
    assert "model defaults" in source


def test_resolve_nominal_current_partial_override():
    """A param missing from the slider map keeps its model-default init value."""
    nominal, source = _resolve({"a/x": 5.0})  # only a/x provided
    assert list(nominal) == [5.0, 2.0]
    assert "sliders" in source


# ---------------------------------------------------------------------------
# operation_kwargs actually reach the operation call (#112/#113)
# ---------------------------------------------------------------------------
class _FakeExecutor:
    """Stands in for the protocol executor: returns canned operand outputs."""

    def __init__(self, outputs):
        self.outputs = outputs

    def run_protocol(self, _protocol_info, **_kwargs):
        return True, self.outputs, None, None


class _FakeFeatureSM:
    def __init__(self, obs_info, outputs):
        self.obs_info = obs_info
        self.protocol_info = {}
        self.param_id_info = {"param_names": ["p"]}
        self._protocol_executor = _FakeExecutor(outputs)


def _feature_sm(kwargs_list, n=1, names=None):
    obs = {
        "operations": ["scaled_max"] * n,
        "operands": [["m/x"]] * n,
        "experiment_idxs": [0] * n,
        "subexperiment_idxs": [0] * n,
        "operation_kwargs": kwargs_list,
        "names_for_plotting": names or [f"f{i}" for i in range(n)],
    }
    # operands_outputs[j] is the operand tuple for observable j.
    outputs = {(0, 0): [(np.array([1.0, 3.0, 2.0]),)] * n}
    return _FakeFeatureSM(obs, outputs)


def _scaled_max(x, factor=1.0):
    return float(np.max(x) * factor)


@pytest.mark.parametrize("factor,expected", [(1.0, 3.0), (2.0, 6.0), (4.0, 12.0)])
def test_evaluate_features_passes_operation_kwargs_to_the_op(factor, expected):
    """The per-data_item kwarg changes the computed feature — i.e. the inputs the
    obs_data editor collects actually do something."""
    sm = _feature_sm([{"factor": factor}])
    out = ls._evaluate_features(sm, np.array([1.0]), {"scaled_max": _scaled_max})
    assert out[0] == pytest.approx(expected)


def test_evaluate_features_uses_the_op_default_without_kwargs():
    """No kwargs -> the func's own default applies (max * 1.0)."""
    for empty in ({}, None):
        sm = _feature_sm([empty])
        out = ls._evaluate_features(sm, np.array([1.0]), {"scaled_max": _scaled_max})
        assert out[0] == pytest.approx(3.0)


def test_evaluate_features_substitutes_a_kwarg_naming_an_earlier_feature():
    """A string kwarg matching an earlier observable's name_for_plotting is
    replaced by that feature's value before the call."""
    sm = _feature_sm(
        [{}, {"factor": "base"}], n=2, names=["base", "derived"]
    )
    out = ls._evaluate_features(sm, np.array([1.0]), {"scaled_max": _scaled_max})
    assert out[0] == pytest.approx(3.0)        # base = max = 3
    assert out[1] == pytest.approx(9.0)        # factor <- 3  => 3 * 3


# Reported running local SA with AADC + AD: the option was offered, the run
# started, and then refused. CA does offer AD for aadc_python -- but that is a
# tape, and this module's AD builds a CasADi SX jacobian, so only casadi_python
# works here.
def test_local_ad_is_offered_only_where_this_path_implements_it():
    from local_sensitivity import local_gradient_sources

    sources = [
        {"value": "FD", "label": "Finite difference"},
        {"value": "AD", "label": "Automatic differentiation (AADC)"},
    ]
    offered = {s["value"]: s for s in local_gradient_sources(sources, "aadc_python")}
    assert offered["FD"]["disabled_here"] is False
    assert offered["AD"]["disabled_here"] is True
    # Says why, and names the format that would work.
    assert "casadi_python" in offered["AD"]["reason"]


def test_local_ad_is_offered_for_casadi():
    from local_sensitivity import local_gradient_sources

    sources = [{"value": "AD", "label": "Automatic differentiation (CasADi)"}]
    assert local_gradient_sources(sources, "casadi_python")[0]["disabled_here"] is False


def test_fd_works_on_every_backend():
    """It only runs forward simulations, so nothing gates it."""
    from local_sensitivity import local_gradient_sources

    for fmt in ("aadc_python", "cellml_only", "python", "casadi_python"):
        got = local_gradient_sources([{"value": "FD", "label": "FD"}], fmt)
        assert got[0]["disabled_here"] is False


def test_an_unsupported_source_is_marked_not_dropped():
    """Dropping it would read as "this backend has no AD at all", which is wrong
    -- calibration can use AADC's."""
    from local_sensitivity import local_gradient_sources

    got = local_gradient_sources([{"value": "AD", "label": "AD"}], "aadc_python")
    assert len(got) == 1 and got[0]["value"] == "AD"


def test_the_refusal_message_explains_the_distinction():
    """The old message said AD "requires casadi_python", implying AADC has no AD."""
    import local_sensitivity as ls

    assert ls.LOCAL_GRADIENT_SUPPORT["AD"] == ("casadi_python",)
    assert ls.LOCAL_GRADIENT_SUPPORT["FD"] is None


@pytest.mark.parametrize("spelling", ["AUTO", "auto", "ANALYTIC", ""])
@pytest.mark.parametrize(
    "model_type,expected", [("casadi_python", "AD"), ("cellml_only", "FSA")]
)
def test_cas_own_spelling_resolves_to_this_backends_arm(spelling, model_type, expected):
    """CA's schema defaults gradient_method to 'AUTO', and its own API takes
    ''/'ANALYTIC'/'AUTO' to mean "this backend's analytic arm". CUFLynx names the
    arm so the menu can offer and disable it -- but rejecting CA's word for the
    same choice made a defaulted run fail with "gradient_method 'AUTO' is not
    available", which was true of the name and wrong about the capability.
    """
    import local_sensitivity as ls

    settings = {"gradient_method": spelling, "nominal": "current"}
    resolved = ls.resolve_gradient_method(settings, model_type)

    assert resolved == expected


def test_a_genuinely_unknown_gradient_method_is_still_rejected():
    """Accepting CA's spellings must not turn the check into a rubber stamp."""
    import local_sensitivity as ls

    with pytest.raises(NotImplementedError, match="not available"):
        ls.resolve_gradient_method({"gradient_method": "MAGIC"}, "cellml_only")


# ---------------------------------------------------------------------------
# The theta / anchor contract for modifier parameters (#208)
# ---------------------------------------------------------------------------
class _ModifierHelper:
    def get_init_param_vals(self, _names):
        # The anchor's *physical* model default -- wrong as a theta, which is
        # the point of the identity overwrite.
        return [[2e-8]]


class _ModifierSM:
    sim_helper = _ModifierHelper()
    SA_info = {"param_names": [["m/p", "m/q"]]}
    # A modifier's param_names entry IS its modifies list; the runner's SA
    # manager carries the matching modifiers metadata.
    param_id_info = {"modifiers": [
        {"index": 0, "name": "s", "operation": "scale",
         "targets": ["m/p", "m/q"], "baselines": None},
    ]}


def _resolve_modifier(current_params, requires_ca=True):
    return ls._resolve_nominal(
        _ModifierSM(), ["m/p"], np.array([0.5]), np.array([2.0]),
        {"nominal": "current"}, None, None, current_params=current_params,
    )


def test_a_modifier_slot_takes_theta_from_its_anchor(requires_params_csv):
    """The contract: analysisDict puts theta at modifies[0], and the nominal
    resolver matches current_params by param_names[i][0] -- which for a modifier
    IS modifies[0]. Theta lands in the modifier's slot with no name mapping."""
    nominal, source = _resolve_modifier({"m/p": 1.4})
    assert list(nominal) == [1.4]
    assert "sliders" in source


def test_a_modifier_slot_defaults_to_identity_not_a_model_value(requires_params_csv):
    """Without a slider override the slot must be the operation's identity
    (theta=1: every target at its baseline), never the anchor's physical
    default -- 2e-8 as a theta would collapse every target toward zero."""
    nominal, source = _resolve_modifier(None)
    assert list(nominal) == [1.0]
    assert "model defaults" in source
