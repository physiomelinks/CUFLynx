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
_cache_output_dir: str | None = None


def reset_cache() -> None:
    """Drop the cached options (call when the CA directory changes)."""
    global _cache, _cache_output_dir
    _cache = None
    _cache_output_dir = None


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


def _introspect(output_dir: str | None = None) -> dict:
    for p in _ca_paths():
        if p not in sys.path:
            sys.path.insert(0, p)
    import operation_funcs  # noqa: E402 (CA module, resolved via sys.path)
    import cost_funcs_user  # noqa: E402

    # numpy mode keeps this light (no casadi/myokit). CUFLynx-authored funcs live
    # in external files (issue #104); hand their paths to CA's builders so the
    # merged set — user funcs included, with correct @differentiable / @is_MLE
    # flags — is discovered by CA itself (CA #303).
    op_path, cost_path = _external_func_paths(output_dir)
    op_funcs = _op_funcs_dict(operation_funcs, op_path)
    operations = sorted(op_funcs)
    if "" not in operations:
        operations = [""] + operations  # allow "no operation"
    cost_types = sorted(_cost_funcs_dict(cost_funcs_user, cost_path))
    # Defensive: some CA builds also enumerate the ``cost_func_metadata`` accessor
    # itself as if it were a cost function — it isn't, so keep it out of the
    # dropdown (and its self-referential metadata entry never renders).
    cost_types = [c for c in cost_types if c not in _NON_COST_FUNC_NAMES]
    data_types, plot_types = _introspect_schema()
    return {
        "operations": operations,
        "cost_types": cost_types,
        "cost_func_metadata": _introspect_cost_func_metadata(cost_funcs_user, cost_path),
        # op name -> @differentiable, so the editor can flag data_items whose
        # operation blocks AD gradients. Empty on an older CA without the marker.
        "differentiable_operations": _introspect_operation_differentiability(op_funcs),
        "data_types": data_types,
        "plot_types": plot_types,
    }


def _external_func_paths(output_dir: str | None = None) -> tuple:
    """(operation path, cost path) for the CUFLynx-authored external files under
    ``output_dir``, or (None, None) — passed to CA's builders so it registers the
    user funcs."""
    try:
        import user_funcs

        return (
            user_funcs.external_path("operation", output_dir),
            user_funcs.external_path("cost", output_dir),
        )
    except Exception:  # noqa: BLE001 - external paths are best-effort
        return None, None


def _op_funcs_dict(operation_funcs, external_path):
    """CA's operation dict incl. the external file. Falls back to the built-ins on
    an older CA whose builder predates the ``external_path`` arg (CA #303)."""
    try:
        return operation_funcs.get_operation_funcs_dict_for_mode("numpy", external_path=external_path)
    except TypeError:  # older CA without the external_path parameter
        return operation_funcs.get_operation_funcs_dict_for_mode("numpy")


def _cost_funcs_dict(cost_funcs_user, external_path):
    try:
        return cost_funcs_user.get_cost_funcs_dict_for_mode("numpy", external_path=external_path)
    except TypeError:
        return cost_funcs_user.get_cost_funcs_dict_for_mode("numpy")


def _introspect_operation_differentiability(op_funcs) -> dict:
    """Map each CA operation name -> whether it's ``@differentiable`` (so AD can use
    it). Best-effort: an older CA without ``is_circulatory_differentiable`` yields
    ``{}``, leaving the editor unable to flag ops (no false warnings)."""
    try:
        from param_id.differentiable import is_circulatory_differentiable  # noqa: E402
    except Exception:  # noqa: BLE001 - older CA without the marker
        return {}
    return {name: bool(is_circulatory_differentiable(fn)) for name, fn in op_funcs.items()}


def _introspect_cost_func_metadata(cost_funcs_user, external_path=None) -> dict:
    """Per-cost-function flags (is_MLE / is_combiner / differentiable) from CA's
    ``cost_func_metadata()`` — including CUFLynx's external cost funcs (CA #303) —
    so the obs-data editor can label cost types without poking at function
    attributes. Best-effort: an older CA without the accessor (or the
    ``external_path`` arg, or a partial payload) yields ``{}`` / defaults, leaving
    the plain cost_types list working.
    """
    try:
        raw = cost_funcs_user.cost_func_metadata(external_path=external_path)
    except TypeError:  # older CA without the external_path parameter
        try:
            raw = cost_funcs_user.cost_func_metadata()
        except Exception:  # noqa: BLE001 - older CA without the accessor
            return {}
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


def get_obs_data_options(refresh: bool = False, output_dir: str | None = None) -> dict:
    """Return ``{"operations": [...], "cost_types": [...]}`` from CA, including the
    user's custom funcs under ``output_dir``.

    Caches a successful introspection (keyed on ``output_dir``); returns fallbacks
    (uncached) when CA is unavailable so a later CA-dir change can still succeed.
    """
    global _cache, _cache_output_dir
    if _cache is not None and not refresh and _cache_output_dir == output_dir:
        return _cache
    try:
        _cache = _introspect(output_dir)
        _cache_output_dir = output_dir
        return _cache
    except Exception:  # noqa: BLE001 - CA missing / import failure → fallbacks
        return {
            "operations": list(FALLBACK_OPERATIONS),
            "cost_types": list(FALLBACK_COST_TYPES),
            "cost_func_metadata": {},
            "differentiable_operations": {},
            "data_types": list(FALLBACK_DATA_TYPES),
            "plot_types": list(FALLBACK_PLOT_TYPES),
        }
