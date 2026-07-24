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
