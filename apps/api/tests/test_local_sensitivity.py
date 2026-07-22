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
