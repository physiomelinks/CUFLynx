"""User-authored observable *operation* and *cost* funcs for circulatory_autogen.

Issue #58 (+ #104 rework): let a user write their own operation func (the
reduction applied to a data_item's operand series -> the scalar a cost function
compares) *and* their own cost func (compares a model output to a target ->
scalar cost) from the GUI, without opening circulatory_autogen.

**External-file design (no bridge into CA's tree).** Each kind is stored in a
single CUFLynx-managed file under the user config dir::

    <config_dir>/user_funcs/operation_funcs_user.py
    <config_dir>/user_funcs/cost_funcs_user.py

CUFLynx passes the file path to circulatory_autogen through CA's config keys
``operation_funcs_external_path`` / ``cost_funcs_external_path`` (CA #303): the
analysis runners include them in the run config (forwarded to ``CVS0DParamID`` /
``SensitivityAnalysis``), and ``obs_options`` hands the same paths to CA's
``get_operation_funcs_dict_for_mode`` / ``get_cost_funcs_dict_for_mode`` /
``cost_func_metadata`` builders so the editor's dropdowns show the merged set.
:func:`external_path` is the single source of a kind's path.

CA loads the files and registers their top-level funcs alongside its built-ins,
keeping only funcs whose ``__module__`` is the file itself — so the decorators we
*import* (``differentiable`` / ``series_to_constant`` / ``is_MLE`` /
``cost_combiner``) are auto-excluded. We never define fallbacks for them (that
would get them registered as funcs).

Security: this writes and later executes arbitrary user Python inside CA at run
time. That is inherent to the feature and consistent with CUFLynx's localhost,
single-user assumption (see CLAUDE.md "Security caveats"). We validate that the
name is a safe identifier and that the code parses, but we do not sandbox.
"""

from __future__ import annotations

import ast
import keyword
from dataclasses import dataclass
from pathlib import Path

from engine import _circulatory_autogen_src
from settings_store import config_dir


class UserFuncError(ValueError):
    """Raised for an invalid func name or code (surface as HTTP 422)."""


# Back-compat alias: the original module exported ``UserOperationError``.
UserOperationError = UserFuncError


# ---------------------------------------------------------------------------
# Kind configuration
# ---------------------------------------------------------------------------
_OPERATION_HEADER = '''"""User-defined observable operations authored via CUFLynx (issues #58 / #104).

Each top-level function here is registered as a selectable "operation" in the
obs_data editor and used by circulatory_autogen during calibration / sensitivity
/ UQ (loaded from CA's operation_funcs_external_path config input; CA #303).

An operation receives the operand array(s) for a data_item and returns a scalar.
Return the operand series when ``series_output=True`` so the reduction can be
plotted on top of the series — that returned series is what is drawn with the
feature in the plots. ``np`` (numpy) is available; the ``@differentiable`` /
``@series_to_constant`` markers mirror CA's (imported, never redefined).

Managed by CUFLynx's "Custom funcs" dialog; the header may be regenerated.
"""
import numpy as np  # noqa: F401 -- available to user operations

# Imported (not defined) so CA registers only the user funcs below, never these.
from param_id.differentiable import differentiable  # noqa: F401
from param_id.operation_funcs import series_to_constant  # noqa: F401
'''

_COST_HEADER = '''"""User-defined cost functions authored via CUFLynx (issues #58 / #104).

Each top-level function here is registered as a selectable "cost_type" in the
obs_data editor and used by circulatory_autogen during calibration / sensitivity
/ UQ (loaded from CA's cost_funcs_external_path config input; CA #303).

A cost func compares a model ``output`` to its target and returns a scalar cost
(lower = better fit). It must work for both scalars and arrays. ``np`` (numpy) is
available; the ``@differentiable`` / ``@is_MLE`` / ``@cost_combiner`` markers
mirror CA's (imported, never redefined). For AD-differentiable costs use CA's
math backend instead of numpy (see CA's ``cost_funcs_user.py``).

Managed by CUFLynx's "Custom funcs" dialog; the header may be regenerated.
"""
import numpy as np  # noqa: F401 -- available to user cost funcs

# Imported (not defined) so CA registers only the user funcs below, never these.
from param_id.differentiable import differentiable  # noqa: F401
from cost_funcs_user import is_MLE, cost_combiner  # noqa: F401
'''

# Operation editor templates (basic / multi-operand / kwargs). The dialog offers
# each as a tab; the backend is the single source of truth for their text.
_OPERATION_TEMPLATES = {
    "basic": '''def my_operation(x, series_output=False):
    """Reduce the operand series ``x`` to a scalar (what a cost func compares).

    When ``series_output=True`` return the *series* to draw on top of the data;
    that same series is what gets plotted with the feature in the plots.
    """
    if series_output:
        return x
    return float(np.max(x) - np.min(x))
''',
    "multi_operand": '''def my_operation(x, y, series_output=False):
    """Combine two operand series into a scalar.

    List the operands (in this order) as the data_item's ``operands`` — ``x`` and
    ``y`` arrive as arrays. Return a series when ``series_output=True`` to plot the
    feature on top of the data.
    """
    if series_output:
        return x - y
    return float(np.trapz(x - y))
''',
    "kwargs": '''def my_operation(x, threshold=0.0, n_peaks=1, series_output=False):
    """Operation with tunable keyword arguments.

    Every keyword argument (here ``threshold`` and ``n_peaks``) is parsed from
    this signature and becomes an editable input on each data_item that selects
    this operation in the obs_data editor (CUFLynx #112) — enter a value next to
    the operation and it is passed in per data_item. Give every kwarg a default.
    ``series_output=True`` returns the series that is plotted with the feature.
    """
    above = x[x > threshold]
    if series_output:
        return x
    return float(np.mean(above[:n_peaks]) if len(above) else 0.0)
''',
}

_COST_TEMPLATES = {
    "basic": '''def my_cost(output, desired_mean, std, weight):
    """Scalar cost between model ``output`` and the target ``desired_mean``.

    Must work for scalars and arrays; lower = better fit. Select it as a
    data_item's ``cost_type`` in the obs_data editor.
    """
    return float(np.sum(((output - desired_mean) / std) ** 2 * weight))
''',
    "MLE": '''@differentiable
@is_MLE
def my_mle_cost(output, desired_mean, std, weight):
    """Negative-log-likelihood cost (required by the Bayesian method).

    ``@is_MLE`` marks the value as a negative log likelihood; keep
    ``@differentiable`` only if the body is CasADi-safe. Use CA's math backend
    instead of numpy for AD gradients (see CA's ``cost_funcs_user.py``).
    """
    per = ((output - desired_mean) / std) ** 2 * weight
    return float(0.5 * np.mean(per))
''',
}


@dataclass(frozen=True)
class _Kind:
    key: str  # "operation" | "cost"
    filename: str
    config_key: str  # the CA config key CUFLynx sets to the file path (CA #303)
    list_marker: str  # top-level assignment CUFLynx uses to remember ordering
    header: str
    templates: dict
    reserved: frozenset  # structural names a user func must not shadow


_KINDS = {
    "operation": _Kind(
        key="operation",
        filename="operation_funcs_user.py",
        config_key="operation_funcs_external_path",
        list_marker="CUFLYNX_OPERATIONS",
        header=_OPERATION_HEADER,
        templates=_OPERATION_TEMPLATES,
        reserved=frozenset(
            {"CUFLYNX_OPERATIONS", "series_to_constant", "differentiable", "np"}
        ),
    ),
    "cost": _Kind(
        key="cost",
        filename="cost_funcs_user.py",
        config_key="cost_funcs_external_path",
        list_marker="CUFLYNX_COSTS",
        header=_COST_HEADER,
        templates=_COST_TEMPLATES,
        reserved=frozenset(
            {"CUFLYNX_COSTS", "is_MLE", "cost_combiner", "differentiable", "np"}
        ),
    ),
}


def _kind(kind: str) -> _Kind:
    try:
        return _KINDS[kind]
    except KeyError:
        raise UserFuncError(f"unknown func kind '{kind}'") from None


# ---------------------------------------------------------------------------
# Paths / environment
# ---------------------------------------------------------------------------
def _user_dir() -> Path:
    """CUFLynx-managed dir for the external func files (in the user config dir)."""
    return config_dir() / "user_funcs"


def _user_file(kind: str) -> Path:
    return _user_dir() / _kind(kind).filename


def external_path(kind: str) -> str | None:
    """The external func file path for ``kind`` when it exists, else ``None``.

    Single source of the path CUFLynx passes to CA — into the analysis run configs
    (forwarded to ``CVS0DParamID`` / ``SensitivityAnalysis``) and to CA's discovery
    builders in ``obs_options`` (CA #303).
    """
    path = _user_file(kind)
    return str(path) if path.is_file() else None


def external_paths() -> dict:
    """``{ca_config_key: path}`` for every kind whose file exists — splat into a
    run config so CA loads the user funcs (``operation_funcs_external_path`` /
    ``cost_funcs_external_path``)."""
    return {
        k.config_key: str(_user_dir() / k.filename)
        for k in _KINDS.values()
        if (_user_dir() / k.filename).is_file()
    }


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------
def _validate_name(kind: str, name: str) -> str:
    k = _kind(kind)
    name = (name or "").strip()
    if not name:
        raise UserFuncError(f"{k.key} name is required")
    if not name.isidentifier() or keyword.iskeyword(name):
        raise UserFuncError(f"'{name}' is not a valid Python function name")
    if name.startswith("_"):
        raise UserFuncError(f"{k.key} name must not start with '_'")
    if name in k.reserved:
        raise UserFuncError(f"'{name}' is a reserved name")
    return name


def _validate_source(kind: str, name: str, source: str) -> str:
    """Validate ``source`` is a single top-level ``def <name>(...)`` and return it."""
    k = _kind(kind)
    source = (source or "").strip("\n")
    if not source.strip():
        raise UserFuncError(f"{k.key} code is required")
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        raise UserFuncError(f"invalid Python: {exc.msg} (line {exc.lineno})") from exc
    defs = [n for n in tree.body if isinstance(n, ast.FunctionDef)]
    if len(tree.body) != 1 or len(defs) != 1:
        raise UserFuncError("code must be exactly one top-level function definition")
    if defs[0].name != name:
        raise UserFuncError(
            f"the function must be named '{name}' to match the {k.key} name "
            f"(found 'def {defs[0].name}')"
        )
    return source


# ---------------------------------------------------------------------------
# Read / parse the user file
# ---------------------------------------------------------------------------
def _node_source(text: str, node: ast.FunctionDef) -> str | None:
    """Source for ``node`` *including* any decorator lines.

    ``ast.get_source_segment`` starts at the ``def`` line, dropping decorators, so
    extend the range up to the first decorator when present.
    """
    seg = ast.get_source_segment(text, node)
    if seg is None:
        return None
    if node.decorator_list:
        start = min(d.lineno for d in node.decorator_list) - 1
        return "\n".join(text.splitlines()[start : node.end_lineno])
    return seg


def _parse_existing(kind: str) -> tuple[list[str], dict[str, str]]:
    """Return (ordered names, {name: source}) from the on-disk user file."""
    k = _kind(kind)
    path = _user_file(kind)
    if not path.is_file():
        return [], {}
    text = path.read_text(encoding="utf-8")
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return [], {}
    order: list[str] = []
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == k.list_marker for t in node.targets
        ):
            try:
                order = [str(x) for x in ast.literal_eval(node.value)]
            except Exception:  # noqa: BLE001 - tolerate a hand-mangled list
                order = []
    sources: dict[str, str] = {}
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name not in k.reserved:
            seg = _node_source(text, node)
            if seg is not None:
                sources[node.name] = seg
    ordered = [n for n in order if n in sources]
    ordered += [n for n in sources if n not in ordered]
    return ordered, sources


def read_user_funcs(kind: str) -> dict:
    """List the current user funcs of ``kind`` plus the editor templates.

    Shape: ``{"kind", "functions": [{"name","source"}], "templates", "template",
    "available", "path"}``. ``available`` is False when CA isn't configured (the
    imported decorators can't resolve, so the funcs can't load).
    """
    k = _kind(kind)
    available = bool(_circulatory_autogen_src())
    order, sources = _parse_existing(kind)
    return {
        "kind": k.key,
        "functions": [{"name": n, "source": sources[n]} for n in order],
        "templates": dict(k.templates),
        "template": next(iter(k.templates.values())),  # back-compat: the first tab
        "available": available,
        "path": str(_user_file(kind)),
    }


# ---------------------------------------------------------------------------
# Write
# ---------------------------------------------------------------------------
def _render(kind: str, order: list[str], sources: dict[str, str]) -> str:
    k = _kind(kind)
    names = ", ".join(f'"{n}"' for n in order)
    parts = [k.header, "", "", f"{k.list_marker} = [{names}]"]
    for name in order:
        parts.append("")
        parts.append("")
        parts.append(sources[name].rstrip("\n"))
    return "\n".join(parts).rstrip("\n") + "\n"


def save_user_func(kind: str, name: str, source: str) -> dict:
    """Create or update the ``kind`` func ``name`` with body ``source``.

    Raises :class:`UserFuncError` (HTTP 422) on an invalid name or code.
    """
    name = _validate_name(kind, name)
    source = _validate_source(kind, name, source)
    order, sources = _parse_existing(kind)
    if name not in sources:
        order.append(name)
    sources[name] = source

    _user_dir().mkdir(parents=True, exist_ok=True)
    _user_file(kind).write_text(_render(kind, order, sources), encoding="utf-8")
    _refresh_options()
    return read_user_funcs(kind)


def delete_user_func(kind: str, name: str) -> dict:
    """Remove the ``kind`` func ``name``; return the updated list.

    Raises :class:`UserFuncError` (HTTP 422) if it doesn't exist.
    """
    order, sources = _parse_existing(kind)
    if name not in sources:
        raise UserFuncError(f"no user {kind} named '{name}'")
    order = [n for n in order if n != name]
    del sources[name]
    _user_file(kind).write_text(_render(kind, order, sources), encoding="utf-8")
    _refresh_options()
    return read_user_funcs(kind)


def _refresh_options() -> None:
    try:
        import obs_options

        obs_options.reset_cache()
    except Exception:  # noqa: BLE001 - options cache is best-effort
        pass


# ---------------------------------------------------------------------------
# Back-compat shims for the original operation-only API (issue #58)
# ---------------------------------------------------------------------------
def read_user_operations() -> dict:
    return read_user_funcs("operation")


def save_user_operation(name: str, source: str) -> dict:
    return save_user_func("operation", name, source)


def delete_user_operation(name: str) -> dict:
    return delete_user_func("operation", name)
