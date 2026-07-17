"""Enumerate circulatory_autogen observable operations and cost-function names.

The obs_data editor's "operation" and "cost_type" dropdowns are populated from
CA's own registries so they stay in sync with the installed CA (including any
user-defined funcs in ``funcs_user/``) instead of hardcoding the lists. Falls
back to a small built-in set when CA can't be imported (missing clone / heavy
deps), and caches a successful introspection.
"""

from __future__ import annotations

import sys
from pathlib import Path

from engine import _circulatory_autogen_src

# Used only when CA can't be introspected (kept intentionally small).
FALLBACK_OPERATIONS = [
    "",
    "max",
    "min",
    "mean",
    "max_minus_min",
    "addition",
    "subtraction",
    "multiplication",
    "division",
]
FALLBACK_COST_TYPES = ["MSE", "AE", "gaussian_MLE"]
# Accessor/helper names that CA's cost-func registry may enumerate but which are
# not selectable cost functions.
_NON_COST_FUNC_NAMES = {"cost_func_metadata"}
FALLBACK_DATA_TYPES = ["constant", "series", "frequency", "prob_dist"]
FALLBACK_PLOT_TYPES = ["", "horizontal", "vertical", "horizontal_from_min", "series", "frequency"]

_cache: dict | None = None


def reset_cache() -> None:
    """Drop the cached options (call when the CA directory changes)."""
    global _cache
    _cache = None


def _ca_paths() -> list[str]:
    """The sys.path entries CA's operation/cost modules need to import."""
    src = Path(_circulatory_autogen_src())
    root = src.parent  # repo root holds funcs_user/ alongside src/
    return [str(src), str(src / "param_id"), str(root / "funcs_user")]


def _introspect_schema() -> tuple[list, list]:
    """Valid data_type/plot_type vocabularies from CA's obs_data_helpers.

    Independent fallback so the (newer) schema accessors being absent on an older
    CA doesn't lose the operation/cost lists.
    """
    try:
        from utilities import obs_data_helpers as odh  # noqa: E402

        data_types = list(odh.get_valid_data_types())
        plot_types = list(odh.get_valid_plot_types())
        if "" not in plot_types:
            plot_types = [""] + plot_types  # allow "no marker"
        return data_types, plot_types
    except Exception:  # noqa: BLE001 - older CA without the accessors
        return list(FALLBACK_DATA_TYPES), list(FALLBACK_PLOT_TYPES)


def _introspect() -> dict:
    for p in _ca_paths():
        if p not in sys.path:
            sys.path.insert(0, p)
    import operation_funcs  # noqa: E402 (CA module, resolved via sys.path)
    import cost_funcs_user  # noqa: E402

    # numpy mode keeps this light (no casadi/myokit). The op dict includes the
    # user operations registered from funcs_user/operation_funcs_user.py.
    operations = sorted(operation_funcs.get_operation_funcs_dict_for_mode("numpy"))
    if "" not in operations:
        operations = [""] + operations  # allow "no operation"
    cost_types = sorted(cost_funcs_user.get_cost_funcs_dict_for_mode("numpy"))
    # Defensive: some CA builds also enumerate the ``cost_func_metadata`` accessor
    # itself as if it were a cost function — it isn't, so keep it out of the
    # dropdown (and its self-referential metadata entry never renders).
    cost_types = [c for c in cost_types if c not in _NON_COST_FUNC_NAMES]
    data_types, plot_types = _introspect_schema()
    return {
        "operations": operations,
        "cost_types": cost_types,
        "cost_func_metadata": _introspect_cost_func_metadata(cost_funcs_user),
        "data_types": data_types,
        "plot_types": plot_types,
    }


def _introspect_cost_func_metadata(cost_funcs_user) -> dict:
    """Per-cost-function flags (is_MLE / is_combiner / differentiable) from CA's
    ``cost_func_metadata()``, so the obs-data editor can label cost types without
    poking at function attributes. Best-effort: an older CA without the accessor
    (or a partial payload) yields ``{}`` / defaults, leaving the plain cost_types
    list working.
    """
    try:
        raw = cost_funcs_user.cost_func_metadata()
    except Exception:  # noqa: BLE001 - older CA without the accessor
        return {}
    out = {}
    for name, meta in (raw or {}).items():
        meta = meta or {}
        out[name] = {
            "is_MLE": bool(meta.get("is_MLE", False)),
            "is_combiner": bool(meta.get("is_combiner", False)),
            "differentiable": bool(meta.get("differentiable", False)),
        }
    return out


def get_obs_data_options(refresh: bool = False) -> dict:
    """Return ``{"operations": [...], "cost_types": [...]}`` from CA.

    Caches a successful introspection; returns fallbacks (uncached) when CA is
    unavailable so a later CA-dir change can still succeed.
    """
    global _cache
    if _cache is not None and not refresh:
        return _cache
    try:
        _cache = _introspect()
        return _cache
    except Exception:  # noqa: BLE001 - CA missing / import failure → fallbacks
        return {
            "operations": list(FALLBACK_OPERATIONS),
            "cost_types": list(FALLBACK_COST_TYPES),
            "cost_func_metadata": {},
            "data_types": list(FALLBACK_DATA_TYPES),
            "plot_types": list(FALLBACK_PLOT_TYPES),
        }
