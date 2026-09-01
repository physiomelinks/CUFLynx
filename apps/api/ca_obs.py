"""The fields this app reads out of circulatory_autogen's parsed dictionaries.

``obs_info``, ``protocol_info`` and ``param_id_info`` are plain dicts with about forty keys
between them, and this app used to index them by string literal in thirty-one places across
four modules. That made every CA key rename a thirty-one-site edit here, and -- because the
sites are spread across modules that are only exercised end to end -- the way we found out
was a released binary raising ``KeyError`` at a user.

So the literals live here, once. A CA rename is one edit in this file.

**Every function returns a whole column, never one element.** ``item_labels(obs_info)``, not
``label_of(obs_info, i)``. That is deliberate: a caller that wants one element has to bind the
column to a local first, which makes "is this resolution inside a loop?" visible at the call
site. Two call sites were calling CA's accessor once per observable before this existed.

**Every function takes the dict, never a ``pid``.** ``obs_cost`` builds a synthetic
``ParamID`` and ``local_sensitivity`` uses a real one; a dict-taking API serves both and is
testable with a plain dict.

Nothing here imports ``libcuflynx`` at module scope. The CA directory is chosen at runtime, so
a top-level import would make this module unimportable whenever CA is not configured -- which
is the same rule ``ca_imports`` itself follows, and the reason it exists.
"""
from __future__ import annotations

import sys

import ca_imports

#: Resolved CA symbols, keyed by (module, name), each stored with the ``sys.modules`` stamp
#: it was resolved under. See :func:`_symbol`.
_cache: dict[tuple[str, str], tuple[tuple, object]] = {}

#: Distinguishes "no such entry in sys.modules" from "an entry whose value is None".
#: ``set_ca_module(monkeypatch, name, None)`` is this app's idiom for "make importing this
#: raise ImportError", so the two states are both real and must not compare equal -- with a
#: plain ``.get()`` they both read as ``None``, and a negative cached inside such a test
#: would survive its teardown and be served to the next one.
_ABSENT = object()


def _stamp(module: str) -> tuple:
    """Identity of every spelling of ``module`` currently in ``sys.modules``.

    This is the cache key, and it is chosen rather than a generation counter because
    ``ca_imports.reset_cache()`` -- what runs when the CA directory changes -- works by
    deleting CA modules from ``sys.modules``. So the stamp changes by construction, with no
    invalidation call to add to ``main.py`` and ``engine.reset()`` and remember for ever.

    It also catches what a counter would not: a test that monkeypatches ``sys.modules`` to
    install a fake CA module, *and the teardown that removes it*. Under a counter a fake
    resolved in one test would leak into the next.
    """
    return tuple(sys.modules.get(name, _ABSENT) for name in ca_imports.candidates(module))


def _symbol(module: str, name: str):
    """The CA symbol, or ``None`` if this CA has not got it.

    Negative answers are cached under the same stamp, so a CA too old to carry a symbol does
    not pay an import attempt -- and the construction of a failure message -- on every call.
    """
    key = (module, name)
    stamp = _stamp(module)
    hit = _cache.get(key)
    if hit is not None and hit[0] == stamp:
        return hit[1]
    try:
        ca_imports.ensure_ca_path()
        symbol = ca_imports.ca_from(module, name)
    except Exception:  # noqa: BLE001 - no CA at all, or one predating the symbol
        symbol = None
    # Re-stamp: resolving imported the module, which changed sys.modules.
    _cache[key] = (_stamp(module), symbol)
    return symbol


def _helper(name: str):
    return _symbol("utilities.obs_data_helpers", name)


def _seq(info, key) -> list:
    """The column at *key*, as a list.

    Tested against ``None`` rather than for truthiness: several of these values are numpy
    arrays, and ``array or []`` raises ``ValueError: The truth value of an array with more
    than one element is ambiguous``. The accessors in circulatory_autogen use ``or []``
    safely because the keys they read are always lists; ``ground_truth_const``,
    ``std_const_vec`` and the parameter bounds are not.
    """
    value = (info or {}).get(key)
    return [] if value is None else list(value)


# ---------------------------------------------------------------------------
# obs_info -- per data_item. Every list here is indexed by the data_item row.
# ---------------------------------------------------------------------------
def item_names(obs_info) -> list:
    """Each item's identity -- what an operation_kwargs reference resolves against."""
    fn = _helper("obs_item_names")
    return list(fn(obs_info)) if fn else _seq(obs_info, "data_item_names")


def item_labels(obs_info) -> list:
    """Each item's own display label -- the scalar feature, not the trace behind it."""
    fn = _helper("obs_item_labels")
    return list(fn(obs_info)) if fn else _seq(obs_info, "item_names_for_plotting")


def trace_labels(obs_info) -> list:
    """Each item's trace label -- the axis caption. May repeat across items."""
    fn = _helper("obs_trace_labels")
    return list(fn(obs_info)) if fn else _seq(obs_info, "trace_names_for_plotting")


def operand_lists(obs_info) -> list:
    """The model variables each item reduces, as a list per item."""
    fn = _helper("obs_operand_lists")
    return list(fn(obs_info)) if fn else _seq(obs_info, "operands")


def experiment_indices(obs_info) -> list:
    fn = _helper("obs_experiment_indices")
    return list(fn(obs_info)) if fn else _seq(obs_info, "experiment_idxs")


def subexperiment_indices(obs_info) -> list:
    fn = _helper("obs_subexperiment_indices")
    return list(fn(obs_info)) if fn else _seq(obs_info, "subexperiment_idxs")


def operations(obs_info) -> list:
    """The reduction applied to each item's trace; an entry is ``None`` for a raw series."""
    return _seq(obs_info, "operations")


def cost_types(obs_info) -> list:
    """The cost function per item. Note the engine rewrites this in place for Bayesian
    runs, so it is the *current* setting rather than what the file said."""
    return _seq(obs_info, "cost_type")


def count(obs_info) -> int:
    """How many data_items there are -- the length of every per-item column above."""
    return int((obs_info or {}).get("num_obs") or 0)


# ---------------------------------------------------------------------------
# obs_info -- the scalar ("constant") observables, a *different* index space
#
# These three are a triple: index them by position in `scalar_rows`, i.e. the enumerate
# counter, NOT by the data_item row. The row is what `scalar_rows` holds, and it is what the
# per-item columns above want. Mixing the two reads a different observable's value and
# nothing raises (circulatory_autogen #349).
# ---------------------------------------------------------------------------
def scalar_rows(obs_info) -> list:
    """For each constant observable in order, the data_item row it came from."""
    fn = _helper("obs_scalar_rows")
    return list(fn(obs_info)) if fn else _seq(obs_info, "const_idx_to_obs_idx")


def scalar_ground_truth(obs_info) -> list:
    """The measured value of each constant observable. Indexed as `scalar_rows` is."""
    return _seq(obs_info, "ground_truth_const")


def scalar_std(obs_info) -> list:
    """The std each constant is scored against. Indexed as `scalar_rows` is."""
    return _seq(obs_info, "std_const_vec")


# ---------------------------------------------------------------------------
# protocol_info
# ---------------------------------------------------------------------------
def subexperiment_counts(protocol_info) -> list:
    return _seq(protocol_info, "num_sub_per_exp")


def sim_times(protocol_info) -> list:
    return _seq(protocol_info, "sim_times")


def scaled_scalar_weights(protocol_info) -> dict:
    """Post-scaling constant weights, keyed by (experiment, sub-experiment).

    The weights to score with. ``obs_info``'s ``weight_const_vec`` is the pre-scaling input
    to ``process_protocol_and_weights`` and is deliberately not exposed here (#349).
    """
    return (protocol_info or {}).get("scaled_weight_const_from_exp_sub") or {}


# ---------------------------------------------------------------------------
# param_id_info
# ---------------------------------------------------------------------------
def param_row_members(param_id_info) -> list:
    """Each parameter row's members. A grouped or modifier row names several."""
    return _seq(param_id_info, "param_names")


def param_row_keys(param_id_info) -> list:
    """One name per parameter row -- the first member, which is how a row is addressed."""
    return [n[0] if isinstance(n, (list, tuple)) else n
            for n in param_row_members(param_id_info)]


def param_bounds(param_id_info) -> tuple:
    """``(mins, maxs)`` as given, one per parameter row."""
    return (_seq(param_id_info, "param_mins"), _seq(param_id_info, "param_maxs"))


def param_row_labels(param_id_info) -> list:
    """Display label per parameter row, falling back to the row key.

    CA keys its sensitivity columns by *entry label* -- a grouped entry is ``'a/E+b/E'`` --
    so looking them up by qname misses every grouped row and reports an empty cell, which
    reads as "no sensitivity" rather than "asked the wrong question".
    """
    fn = _symbol("parsers.PrimitiveParsers", "param_entry_labels")
    if fn is not None:
        try:
            return list(fn(param_id_info))
        except Exception:  # noqa: BLE001 - a CA whose accessor takes something else
            pass
    return param_row_keys(param_id_info)
