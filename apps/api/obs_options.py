"""Enumerate circulatory_autogen observable operations and cost-function names.

The obs_data editor's "operation" and "cost_type" dropdowns are populated from
CA's own registries so they stay in sync with the installed CA (including any
user-defined funcs in ``funcs_user/``) instead of hardcoding the lists. Falls
back to a small built-in set when CA can't be imported (missing clone / heavy
deps), and caches a successful introspection.
"""

from __future__ import annotations

import inspect
import sys
from pathlib import Path

from engine import _circulatory_autogen_src

# Keyword arguments that are part of the operation-calling machinery rather than
# user-tunable knobs, so they're never surfaced as editable per-data_item inputs.
_RESERVED_OP_KWARGS = frozenset({"series_output"})

# The same idea for cost funcs (CA #370): circulatory_autogen supplies these from
# the data_item's own fields, so they are never per-data_item ``cost_kwargs``.
# Only a fallback -- CA owns the list, and _reserved_cost_kwargs() asks it first.
FALLBACK_RESERVED_COST_KWARGS = frozenset({"std", "weight"})

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
# Operand arity for the operations FALLBACK_OPERATIONS lists, used when CA can't
# be introspected. The fallback already carries its own copy of the operation
# *names* for exactly that case, so their arity belongs with them -- otherwise a
# CA-less install silently loses the operand-count feature (#147) and every
# editor row goes back to being hand-managed.
#
# "" (no operation chosen) is deliberately absent: with no operation there is no
# arity to enforce, and the editor treats a missing entry as "manage by hand".
FALLBACK_OPERATION_OPERANDS = {
    "max": {"count": 1, "names": ["x"], "variadic": False},
    "min": {"count": 1, "names": ["x"], "variadic": False},
    "mean": {"count": 1, "names": ["x"], "variadic": False},
    "max_minus_min": {"count": 1, "names": ["x"], "variadic": False},
    "addition": {"count": 2, "names": ["x1", "x2"], "variadic": False},
    "subtraction": {"count": 2, "names": ["x1", "x2"], "variadic": False},
    "multiplication": {"count": 2, "names": ["x1", "x2"], "variadic": False},
    "division": {"count": 2, "names": ["x1", "x2"], "variadic": False},
}
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
    cost_funcs = _cost_funcs_dict(cost_funcs_user, cost_path)
    cost_types = sorted(cost_funcs)
    # Defensive: some CA builds also enumerate the ``cost_func_metadata`` accessor
    # itself as if it were a cost function — it isn't, so keep it out of the
    # dropdown (and its self-referential metadata entry never renders).
    cost_types = [c for c in cost_types if c not in _NON_COST_FUNC_NAMES]
    cost_kwargs_schema, cost_kwargs_accepts_any = _introspect_cost_kwargs(
        {n: f for n, f in cost_funcs.items() if n not in _NON_COST_FUNC_NAMES})
    data_types, plot_types = _introspect_schema()
    return {
        "operations": operations,
        "cost_types": cost_types,
        # What CA applies to a data_item that states no cost_type. Published as
        # a constant since CA #392 (CUFLynx #212); before that it was a literal
        # inside PrimitiveParsers and could only be mirrored, which is how the
        # three different answers in CA came about. Introspected, never
        # restated -- if CA changes it, the editor's label follows.
        "default_cost_type": _introspect_default_cost_type(),
        "cost_func_metadata": _introspect_cost_func_metadata(cost_funcs_user, cost_path),
        # cost name -> [{name, default, type}] tunable keyword args, the cost-func
        # twin of operation_kwargs_schema (CA #370). Empty on an older CA.
        "cost_kwargs_schema": cost_kwargs_schema,
        # cost name -> declares ``**kwargs``. Full map (not just the True entries)
        # so the editor can tell "CA says this func accepts nothing else" from "CA
        # never answered" -- only the former justifies deleting a stored kwarg.
        "cost_kwargs_accepts_any": cost_kwargs_accepts_any,
        # op name -> @differentiable, so the editor can flag data_items whose
        # operation blocks AD gradients. Empty on an older CA without the marker.
        "differentiable_operations": _introspect_operation_differentiability(op_funcs),
        # op name -> [{name, default, type}] tunable keyword args, so the editor
        # can render an input per kwarg on each data_item that selects that op.
        "operation_kwargs_schema": _introspect_operation_kwargs(op_funcs),
        # How many operands each operation consumes, so the editor can offer the
        # right number of fields instead of leaving the user to guess (#147).
        "operation_operands": _introspect_operation_operands(op_funcs),
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
def _infer_kwarg_type(default) -> str:
    """Map a keyword-arg default to an input type the editor can render.

    ``bool`` is checked before ``int`` because ``bool`` subclasses ``int``.
    Unknown / ``None`` defaults fall back to a free-text ``string`` input.
    """
    if isinstance(default, bool):
        return "boolean"
    if isinstance(default, int):
        return "integer"
    if isinstance(default, float):
        return "number"
    if isinstance(default, str):
        return "string"
    return "string"


def _jsonable_default(default):
    """The kwarg default as a JSON-serialisable value (else ``None``)."""
    if isinstance(default, (bool, int, float, str)) or default is None:
        return default
    return None


def _introspect_operation_operands(op_funcs) -> dict:
    """Map each operation name -> how many operands it consumes.

    ``{"count": int, "names": [...], "variadic": bool}``. `count` is the number of
    operand fields the editor should show; `variadic` means the operation takes
    ``*args`` and the count is a minimum, not a limit.

    CA already derives this in ``get_operation_kwarg_spec`` (the ``from_operands``
    list it uses to validate operation_kwargs against the operands), so prefer
    that over a second signature parse that could disagree with the validation the
    user's config will actually be checked against. Fall back to parsing here for
    an older CA without the helper.
    """
    try:
        from operation_funcs import get_operation_kwarg_spec  # noqa: PLC0415
    except Exception:  # noqa: BLE001 - older CA; parse the signature ourselves
        get_operation_kwarg_spec = None

    out: dict[str, dict] = {}
    for name, fn in op_funcs.items():
        if get_operation_kwarg_spec is not None:
            try:
                _accepted, from_operands, accepts_any = get_operation_kwarg_spec(fn)
                out[name] = {
                    "count": len(from_operands),
                    "names": list(from_operands),
                    "variadic": bool(accepts_any),
                }
                continue
            except Exception:  # noqa: BLE001 - fall through to the local parse
                pass
        try:
            params = inspect.signature(fn).parameters
        except (ValueError, TypeError):
            continue
        names = []
        variadic = False
        for pname, p in params.items():
            if p.kind == p.VAR_POSITIONAL:
                variadic = True
            elif p.kind == p.VAR_KEYWORD:
                variadic = True
            elif pname not in _RESERVED_OP_KWARGS and p.default is inspect.Parameter.empty:
                names.append(pname)
        out[name] = {"count": len(names), "names": names, "variadic": variadic}
    return out


def _introspect_operation_kwargs(op_funcs) -> dict:
    """Map each operation name -> its list of tunable keyword args.

    Parses each operation func's signature. Operands are *positional* args (passed
    as ``*operands_outputs``) and carry no default, so they're skipped; the
    reserved ``series_output`` flag is skipped by name; ``*args`` / ``**kwargs``
    are skipped. Only parameters that carry a default (the real tunables) surface,
    each as ``{"name", "default", "type"}``. Ops with no tunable kwargs are
    omitted, keeping the payload small (the editor treats a missing entry as []).
    Best-effort per func: an un-introspectable callable is skipped, not fatal.
    """
    out: dict[str, list] = {}
    for name, fn in op_funcs.items():
        try:
            params = inspect.signature(fn).parameters
        except (ValueError, TypeError):  # C funcs / builtins without signatures
            continue
        kwargs = []
        for pname, p in params.items():
            if pname in _RESERVED_OP_KWARGS:
                continue
            if p.kind in (p.VAR_POSITIONAL, p.VAR_KEYWORD):
                continue
            if p.default is inspect.Parameter.empty:
                continue  # positional operand, not a tunable kwarg
            kwargs.append(
                {
                    "name": pname,
                    "default": _jsonable_default(p.default),
                    "type": _infer_kwarg_type(p.default),
                }
            )
        if kwargs:
            out[name] = kwargs
    return out


def _reserved_cost_kwargs() -> frozenset:
    """The kwarg names circulatory_autogen fills itself for a cost func (CA #370).

    Asked of CA rather than restated here: the reserved set is what CA *rejects* in
    a data_item's ``cost_kwargs``, so a local copy that drifted would offer the user
    an input whose value CA refuses to accept.
    """
    try:
        from param_id.cost_kwargs import RESERVED_COST_KWARGS  # noqa: PLC0415

        return frozenset(RESERVED_COST_KWARGS)
    except Exception:  # noqa: BLE001 - CA without the contract (pre-#370)
        return FALLBACK_RESERVED_COST_KWARGS


def _local_cost_kwarg_spec(params, reserved) -> tuple[list, list, bool]:
    """``get_cost_kwarg_spec`` reimplemented for a CA that predates it (CA #370)."""
    accepted, positional, accepts_any = [], [], False
    for name, p in params.items():
        if p.kind in (p.VAR_KEYWORD, p.VAR_POSITIONAL):
            accepts_any = True
        elif p.kind == p.POSITIONAL_ONLY:
            positional.append(name)
        else:
            accepted.append(name)
            if p.default is inspect.Parameter.empty and name not in reserved:
                positional.append(name)
    return accepted, positional, accepts_any


def _introspect_cost_kwargs(cost_funcs) -> tuple[dict, dict]:
    """(schema, accepts_any) for every cost func: which keyword args a data_item's
    ``cost_kwargs`` may set, so the obs_data editor can offer an input per kwarg.

    The cost-func twin of :func:`_introspect_operation_kwargs`, and deliberately
    the same shape (``{"name", "default", "type"}``) because the two are the same
    user-extension point on either side of a data_item.

    What is *not* a tunable: the model output and the ground truth (filled
    positionally), and ``std`` / ``weight``, which CA supplies from the data_item's
    own fields. CA's ``get_cost_kwarg_spec`` decides which is which -- the same
    function that validates the user's config at calibration setup -- so an input
    the editor offers is one CA will accept. A local parse covers a pre-#370 CA.

    Funcs with no tunable kwargs are omitted from the schema (the editor treats a
    missing entry as ``[]``); ``accepts_any`` is reported for *every* func, since
    "declares no kwargs" and "was never introspected" must not look the same.
    """
    reserved = _reserved_cost_kwargs()
    try:
        from param_id.cost_kwargs import get_cost_kwarg_spec  # noqa: PLC0415
    except Exception:  # noqa: BLE001 - older CA; parse the signature ourselves
        get_cost_kwarg_spec = None

    schema: dict[str, list] = {}
    accepts_any_map: dict[str, bool] = {}
    for name, fn in cost_funcs.items():
        try:
            params = inspect.signature(fn).parameters
        except (ValueError, TypeError):  # C funcs / builtins without signatures
            continue
        spec = None
        if get_cost_kwarg_spec is not None:
            try:
                spec = get_cost_kwarg_spec(fn)
            except Exception:  # noqa: BLE001 - fall through to the local parse
                spec = None
        accepted, positional, accepts_any = spec or _local_cost_kwarg_spec(params, reserved)
        accepts_any_map[name] = bool(accepts_any)
        kwargs = []
        for pname in accepted:
            if pname in positional or pname in reserved:
                continue
            default = params[pname].default if pname in params else inspect.Parameter.empty
            if default is inspect.Parameter.empty:
                default = None
            kwargs.append(
                {
                    "name": pname,
                    "default": _jsonable_default(default),
                    "type": _infer_kwarg_type(default),
                }
            )
        if kwargs:
            schema[name] = kwargs
    return schema, accepts_any_map


def _introspect_operation_differentiability(op_funcs) -> dict:
    """Map each CA operation name -> whether it's ``@differentiable`` (so AD can use
    it). Best-effort: an older CA without ``is_circulatory_differentiable`` yields
    ``{}``, leaving the editor unable to flag ops (no false warnings)."""
    try:
        from param_id.differentiable import is_circulatory_differentiable  # noqa: E402
    except Exception:  # noqa: BLE001 - older CA without the marker
        return {}
    return {name: bool(is_circulatory_differentiable(fn)) for name, fn in op_funcs.items()}


def _introspect_default_cost_type() -> str:
    """The cost function CA applies to a data_item that names none.

    Published by CA as ``obs_data_helpers.DEFAULT_COST_TYPE`` since CA #392
    (CUFLynx #212). Before that it was a bare literal inside two places in
    ``PrimitiveParsers``, which is how CA came to have three different answers
    -- the parser's, the OMEX importer's, and the Bayesian path's. Introspected
    so the editor's label cannot become a fourth.

    Empty string when CA is older or unreachable: the editor then says plain
    "default" rather than naming a cost function that may not be the one used.
    """
    try:
        from utilities.obs_data_helpers import DEFAULT_COST_TYPE  # noqa: PLC0415

        return str(DEFAULT_COST_TYPE or "")
    except Exception:  # noqa: BLE001 - older CA, or no CA at all
        return ""


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


def get_operation_funcs(output_dir: str | None = None):
    """CA's numpy observable-operation registry (built-ins + user + external
    funcs under ``output_dir``), or ``None`` when CA can't be imported.

    Unlike :func:`get_obs_data_options` this returns the *callables* themselves,
    so the Output plots overlay can apply a data_item's operation to a simulated
    trace and plot its ``series_output`` (transformed) series (issue #111). Not
    cached: it's called per run, and the registry is cheap to rebuild in numpy
    mode (no casadi/myokit).
    """
    try:
        for p in _ca_paths():
            if p not in sys.path:
                sys.path.insert(0, p)
        import operation_funcs  # noqa: E402 (CA module, resolved via sys.path)

        op_path, _ = _external_func_paths(output_dir)
        return _op_funcs_dict(operation_funcs, op_path)
    except Exception:  # noqa: BLE001 - CA missing / import failure
        return None


def get_cost_funcs(output_dir: str | None = None):
    """CA's cost-function registry (built-ins + the user's), or None without CA.

    The callables, not the names: scoring the current sliders has to use the
    same ``gaussian_MLE`` a calibration minimises (#159). One written here that
    disagreed would look authoritative while ranking parameter sets differently.
    """
    try:
        for p in _ca_paths():
            if p not in sys.path:
                sys.path.insert(0, p)
        import cost_funcs_user  # noqa: E402 (CA module, resolved via sys.path)

        _, cost_path = _external_func_paths(output_dir)
        return _cost_funcs_dict(cost_funcs_user, cost_path)
    except Exception:  # noqa: BLE001 - CA missing / import failure
        return None


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
            # Empty, not guessed: an older CA's default is not knowable from
            # here, and the editor says plain "default" rather than naming the
            # wrong cost function.
            "default_cost_type": "",
            "cost_func_metadata": {},
            "cost_kwargs_schema": {},
            "cost_kwargs_accepts_any": {},
            "differentiable_operations": {},
            "operation_kwargs_schema": {},
            "operation_operands": {k: dict(v) for k, v in FALLBACK_OPERATION_OPERANDS.items()},
            "data_types": list(FALLBACK_DATA_TYPES),
            "plot_types": list(FALLBACK_PLOT_TYPES),
        }
