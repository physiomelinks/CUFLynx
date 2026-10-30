"""The adapter between this app and circulatory_autogen's parsed dictionaries.

Two things are worth testing here and neither is "does it read the right key".

**The cache is invalidated by the right things.** ``ca_obs`` resolves CA symbols lazily and
remembers them, because some of its callers run per observable inside a loop that runs on
every simulation. A stale cache would keep the *old* CA's function alive across a change of
CA directory, which is the exact bug ``ca_imports.reset_cache()`` exists to prevent.

**It degrades rather than raises.** A CA predating one of these accessors must leave the app
working, reading the underlying key directly, not raise ``KeyError`` or ``NameError`` at a
user -- which is what the code this replaces did.
"""
import sys

import pytest

import ca_imports
import ca_obs
from conftest import set_ca_module


@pytest.fixture(autouse=True)
def _clear_adapter_cache():
    ca_obs._cache.clear()
    yield
    ca_obs._cache.clear()


OBS_INFO = {
    "data_item_names": ["a/x", "a/y"],
    "item_names_for_plotting": ["x (mean)", "y (max)"],
    "trace_names_for_plotting": ["x", "y"],
    "operands": [["a/x"], ["a/y"]],
    "operations": ["mean", "max"],
    "cost_type": ["gaussian_MLE", "gaussian_MLE"],
    "experiment_idxs": [0, 1],
    "subexperiment_idxs": [0, 0],
    "num_obs": 2,
    "const_idx_to_obs_idx": [0, 1],
    "ground_truth_const": [1.0, 2.0],
    "std_const_vec": [0.1, 0.2],
}


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------
def test_every_column_reads_through():
    assert ca_obs.item_names(OBS_INFO) == ["a/x", "a/y"]
    assert ca_obs.item_labels(OBS_INFO) == ["x (mean)", "y (max)"]
    assert ca_obs.trace_labels(OBS_INFO) == ["x", "y"]
    assert ca_obs.operand_lists(OBS_INFO) == [["a/x"], ["a/y"]]
    assert ca_obs.operations(OBS_INFO) == ["mean", "max"]
    assert ca_obs.experiment_indices(OBS_INFO) == [0, 1]
    assert ca_obs.subexperiment_indices(OBS_INFO) == [0, 0]
    assert ca_obs.count(OBS_INFO) == 2
    assert ca_obs.scalar_rows(OBS_INFO) == [0, 1]
    assert ca_obs.scalar_ground_truth(OBS_INFO) == [1.0, 2.0]
    assert ca_obs.scalar_std(OBS_INFO) == [0.1, 0.2]


def test_an_absent_dict_is_empty_not_an_error():
    """CUFLynx hands ParamID `prediction_info=None`, and a partially built obs_info is
    ordinary during startup. Neither should be an exception."""
    for reader in (ca_obs.item_names, ca_obs.item_labels, ca_obs.trace_labels,
                   ca_obs.operand_lists, ca_obs.scalar_rows, ca_obs.operations):
        assert reader(None) == []
        assert reader({}) == []
    assert ca_obs.count(None) == 0


def test_param_rows_collapse_grouped_entries_to_one_key_each():
    """A grouped or modifier row names several members; the row is addressed by the first.
    This comprehension appeared verbatim in three modules before the adapter."""
    info = {"param_names": [["a/E", "b/E"], "c/R"], "param_mins": [1, 2], "param_maxs": [3, 4]}
    assert ca_obs.param_row_members(info) == [["a/E", "b/E"], "c/R"]
    assert ca_obs.param_row_keys(info) == ["a/E", "c/R"]
    assert ca_obs.param_bounds(info) == ([1, 2], [3, 4])


# ---------------------------------------------------------------------------
# Degradation -- a CA that has not got the accessor
# ---------------------------------------------------------------------------
def test_a_ca_without_the_accessor_still_answers(monkeypatch):
    """The underlying key is read directly. The code this replaces raised instead: one
    branch left a name unbound when a *different* symbol failed to resolve, so an old CA
    produced NameError rather than the intended fallback."""
    set_ca_module(monkeypatch, "utilities.obs_data_helpers", None)
    ca_obs._cache.clear()

    assert ca_obs.item_labels(OBS_INFO) == ["x (mean)", "y (max)"]
    assert ca_obs.item_names(OBS_INFO) == ["a/x", "a/y"]
    assert ca_obs.scalar_rows(OBS_INFO) == [0, 1]


def test_param_labels_fall_back_to_the_row_keys(monkeypatch):
    set_ca_module(monkeypatch, "parsers.PrimitiveParsers", None)
    ca_obs._cache.clear()
    info = {"param_names": [["a/E", "b/E"], "c/R"]}
    assert ca_obs.param_row_labels(info) == ["a/E", "c/R"]


# ---------------------------------------------------------------------------
# The cache, and what invalidates it
# ---------------------------------------------------------------------------
def test_a_resolved_symbol_is_remembered():
    ca_obs.item_labels(OBS_INFO)
    assert ("utilities.obs_data_helpers", "obs_item_labels") in ca_obs._cache


def test_reset_cache_invalidates_the_stamp():
    """`ca_imports.reset_cache()` drops CA modules from sys.modules, which is what a change
    of CA directory does. The adapter must not keep serving the old CA's function."""
    ca_obs.item_labels(OBS_INFO)
    before = ca_obs._cache[("utilities.obs_data_helpers", "obs_item_labels")][0]

    ca_imports.reset_cache()
    after = ca_obs._stamp("utilities.obs_data_helpers")
    assert after != before, (
        "the stamp did not change across reset_cache(), so a stale symbol would be reused")


def test_a_fake_module_does_not_leak_past_its_test(monkeypatch):
    """The reason the stamp is sys.modules identity rather than a counter.

    A test installs a fake CA module; monkeypatch removes it at teardown. A generation
    counter would not notice either event, so the fake -- or the negative it produced --
    would be served to whatever ran next.
    """
    set_ca_module(monkeypatch, "utilities.obs_data_helpers", None)
    ca_obs._cache.clear()
    assert ca_obs.item_labels(OBS_INFO) == ["x (mean)", "y (max)"]   # fallback path
    stamped_with_fake = ca_obs._stamp("utilities.obs_data_helpers")

    monkeypatch.undo()
    assert ca_obs._stamp("utilities.obs_data_helpers") != stamped_with_fake, (
        "the stamp survived the fake being removed, so the cached negative would persist")


def test_numpy_columns_do_not_raise_on_truthiness():
    """CA hands several of these back as numpy arrays, not lists.

    `array or []` raises ValueError: the truth value of an array with more than one element
    is ambiguous. The accessors in circulatory_autogen get away with that idiom because the
    keys they read are always lists; the ground-truth vectors and the parameter bounds are
    arrays, and the first version of this adapter copied the idiom and broke 47 tests.
    """
    import numpy as np

    info = {
        "ground_truth_const": np.array([1.0, 2.0]),
        "std_const_vec": np.array([0.1, 0.2]),
        "const_idx_to_obs_idx": np.array([0, 1]),
    }
    assert ca_obs.scalar_ground_truth(info) == [1.0, 2.0]
    assert ca_obs.scalar_std(info) == [0.1, 0.2]
    assert ca_obs.scalar_rows(info) == [0, 1]

    bounds = {"param_mins": np.array([0.0]), "param_maxs": np.array([1.0])}
    assert ca_obs.param_bounds(bounds) == ([0.0], [1.0])

    # An empty array is falsy *and* ambiguous-free, but must still come back as []
    assert ca_obs.scalar_ground_truth({"ground_truth_const": np.array([])}) == []
