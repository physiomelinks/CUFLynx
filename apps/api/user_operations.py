"""User-authored observable *operations* (obs_funcs) for circulatory_autogen.

Issue #58: let a user write their own operation func (the reduction applied to a
data_item's operand series -> the scalar that a cost function compares) without
opening circulatory_autogen. The functions are stored in a dedicated, CUFLynx-
managed file inside CA's ``funcs_user/`` dir::

    <CA_root>/funcs_user/operation_funcs_user_CUFLynx.py

CA only discovers user operations through ``funcs_user/operation_funcs_user.py``
(its ``register_user_operations`` registers every top-level function *defined in
that module*). So a one-time, idempotent **bridge** is appended to that file which
imports our CUFLynx module and re-exposes the listed functions into its namespace
(``__module__`` retargeted) so CA registers them like any other user op. All
subsequent edits touch only the CUFLynx file.

After a save the obs_data option cache (``obs_options``) is reset and the user
modules are reloaded, so the new/edited operation shows up in the editor's
operation dropdown immediately.

Security: this writes and later executes arbitrary user Python inside CA at run
time. That is inherent to the feature and consistent with CUFLynx's localhost,
single-user assumption (see CLAUDE.md "Security caveats"). We validate that the
name is a safe identifier and that the code parses, but we do not sandbox.
"""

from __future__ import annotations

import ast
import importlib
import keyword
import sys
from pathlib import Path

from engine import _circulatory_autogen_src

USER_MODULE = "operation_funcs_user_CUFLynx"
USER_FILENAME = f"{USER_MODULE}.py"
BRIDGE_FILENAME = "operation_funcs_user.py"

_BRIDGE_BEGIN = "# === CUFLynx user operations bridge (auto-managed; do not edit) BEGIN ==="
_BRIDGE_END = "# === CUFLynx user operations bridge (auto-managed; do not edit) END ==="
_BRIDGE_SNIPPET = f'''

{_BRIDGE_BEGIN}
# Written once by CUFLynx (issue #58). CA's register_user_operations only registers
# functions defined in THIS module, so we re-expose the CUFLynx user operations here
# (retargeting __module__) to have them discovered like any other user op.
try:
    import {USER_MODULE} as _cuflynx_user_ops
except Exception:  # pragma: no cover - missing/broken CUFLynx user-ops file
    _cuflynx_user_ops = None
if _cuflynx_user_ops is not None:
    for _cuflynx_name in getattr(_cuflynx_user_ops, "CUFLYNX_OPERATIONS", ()):
        _cuflynx_fn = getattr(_cuflynx_user_ops, _cuflynx_name, None)
        if callable(_cuflynx_fn):
            try:
                _cuflynx_fn.__module__ = __name__
            except (AttributeError, TypeError):
                pass
            globals()[_cuflynx_name] = _cuflynx_fn
{_BRIDGE_END}
'''

# Structural / builtin names a user op must not shadow (CA helpers + this file's
# own scaffolding). Core operation names (max, min, mean, ...) are intentionally
# *not* here: overriding one is a deliberate power-user choice.
_RESERVED_NAMES = frozenset(
    {
        "CUFLYNX_OPERATIONS",
        "series_to_constant",
        "differentiable",
        "register_user_operations",
        "np",
    }
)

_FILE_HEADER = '''"""User-defined observable operations authored via CUFLynx (issue #58).

Each top-level function listed in ``CUFLYNX_OPERATIONS`` is registered as a
selectable "operation" in the obs_data editor and used by circulatory_autogen
during calibration / sensitivity / UQ.

An operation receives the operand array(s) for a data_item and returns a scalar.
Return the operand unchanged when ``series_output=True`` so the reduction can be
plotted on top of the series. ``np`` (numpy) is available; the optional
``@differentiable`` / ``@series_to_constant`` decorators mirror CA's markers.

This file is managed by CUFLynx (the "Custom operations" dialog); hand-edits to
the function bodies are preserved, but the header may be regenerated.
"""
import numpy as np  # noqa: F401 -- available to user operations

try:  # CA's real markers when circulatory_autogen is importable...
    from param_id.differentiable import differentiable  # noqa: F401
except Exception:  # pragma: no cover - ...else harmless no-op fallbacks

    def differentiable(func):  # noqa: F811
        return func


def series_to_constant(func):  # noqa: F811 -- mirrors CA's marker
    func.series_to_constant = True
    return func
'''

# Prefilled in the dialog's code editor as a starting point.
OPERATION_TEMPLATE = '''def my_operation(x, series_output=False):
    """Return a scalar summarising the operand series ``x``.

    Replace the body with your own reduction. Keep ``series_output`` so the value
    can be plotted on top of the series.
    """
    if series_output:
        return x
    return float(np.max(x) - np.min(x))
'''


class UserOperationError(ValueError):
    """Raised for an invalid operation name or code (surface as HTTP 422)."""


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
def _funcs_user_dir() -> Path:
    """CA's ``funcs_user/`` dir (sibling of ``src/``), where CA discovers user ops."""
    src = _circulatory_autogen_src()
    if not src:
        raise UserOperationError(
            "circulatory_autogen is not configured (set the CA dir in Settings)."
        )
    return Path(src).parent / "funcs_user"


def _user_file() -> Path:
    return _funcs_user_dir() / USER_FILENAME


def _bridge_file() -> Path:
    return _funcs_user_dir() / BRIDGE_FILENAME


def _ca_sys_paths() -> list[str]:
    src = Path(_circulatory_autogen_src())
    return [str(src), str(src / "param_id"), str(src.parent / "funcs_user")]


def _ensure_sys_path() -> None:
    for p in _ca_sys_paths():
        if p and p not in sys.path:
            sys.path.insert(0, p)


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------
def _validate_name(name: str) -> str:
    name = (name or "").strip()
    if not name:
        raise UserOperationError("operation name is required")
    if not name.isidentifier() or keyword.iskeyword(name):
        raise UserOperationError(f"'{name}' is not a valid Python function name")
    if name.startswith("_"):
        raise UserOperationError("operation name must not start with '_'")
    if name in _RESERVED_NAMES:
        raise UserOperationError(f"'{name}' is a reserved name")
    return name


def _validate_source(name: str, source: str) -> str:
    """Validate ``source`` is a single top-level ``def <name>(...)`` and return it."""
    source = (source or "").strip("\n")
    if not source.strip():
        raise UserOperationError("operation code is required")
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        raise UserOperationError(
            f"invalid Python: {exc.msg} (line {exc.lineno})"
        ) from exc
    defs = [n for n in tree.body if isinstance(n, ast.FunctionDef)]
    if len(tree.body) != 1 or len(defs) != 1:
        raise UserOperationError(
            "code must be exactly one top-level function definition"
        )
    if defs[0].name != name:
        raise UserOperationError(
            f"the function must be named '{name}' to match the operation name "
            f"(found 'def {defs[0].name}')"
        )
    return source


# ---------------------------------------------------------------------------
# Read / parse the user file
# ---------------------------------------------------------------------------
def _node_source(text: str, node: ast.FunctionDef) -> str | None:
    """Source for ``node`` *including* any decorator lines.

    ``ast.get_source_segment`` starts at the ``def`` line, dropping ``@differentiable``
    / ``@series_to_constant`` decorators, so we extend the range up to the first
    decorator when present.
    """
    seg = ast.get_source_segment(text, node)
    if seg is None:
        return None
    if node.decorator_list:
        start = min(d.lineno for d in node.decorator_list) - 1
        return "\n".join(text.splitlines()[start : node.end_lineno])
    return seg


def _parse_existing() -> tuple[list[str], dict[str, str]]:
    """Return (ordered names, {name: source}) from the on-disk CUFLynx user file."""
    path = _user_file()
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
            isinstance(t, ast.Name) and t.id == "CUFLYNX_OPERATIONS"
            for t in node.targets
        ):
            try:
                order = [str(x) for x in ast.literal_eval(node.value)]
            except Exception:  # noqa: BLE001 - tolerate a hand-mangled list
                order = []
    sources: dict[str, str] = {}
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name not in _RESERVED_NAMES:
            seg = _node_source(text, node)
            if seg is not None:
                sources[node.name] = seg
    # Preserve declared order, then append any stray funcs not in the list.
    ordered = [n for n in order if n in sources]
    ordered += [n for n in sources if n not in ordered]
    return ordered, sources


def read_user_operations() -> dict:
    """List the current user operations plus the editor template.

    Shape: ``{"functions": [{"name", "source"}], "template", "available", "path"}``.
    ``available`` is False when CA isn't configured (dir can't be resolved).
    """
    try:
        path = _user_file()
    except UserOperationError:
        return {
            "functions": [],
            "template": OPERATION_TEMPLATE,
            "available": False,
            "path": None,
        }
    order, sources = _parse_existing()
    return {
        "functions": [{"name": n, "source": sources[n]} for n in order],
        "template": OPERATION_TEMPLATE,
        "available": True,
        "path": str(path),
    }


# ---------------------------------------------------------------------------
# Write
# ---------------------------------------------------------------------------
def _render(order: list[str], sources: dict[str, str]) -> str:
    names = ", ".join(f'"{n}"' for n in order)
    parts = [_FILE_HEADER, "", "", f"CUFLYNX_OPERATIONS = [{names}]"]
    for name in order:
        parts.append("")
        parts.append("")
        parts.append(sources[name].rstrip("\n"))
    return "\n".join(parts).rstrip("\n") + "\n"


def _ensure_bridge() -> None:
    """Append the one-time bridge to CA's operation_funcs_user.py (idempotent)."""
    bridge = _bridge_file()
    if not bridge.is_file():
        raise UserOperationError(
            f"{BRIDGE_FILENAME} not found in the CA funcs_user dir "
            "(is the CA dir set correctly?)"
        )
    text = bridge.read_text(encoding="utf-8")
    if _BRIDGE_BEGIN in text:
        return
    if not text.endswith("\n"):
        text += "\n"
    bridge.write_text(text + _BRIDGE_SNIPPET, encoding="utf-8")


def _reload_user_modules() -> None:
    """Reload the user + bridge modules so an updated file is picked up in-process."""
    _ensure_sys_path()
    for modname in (USER_MODULE, "operation_funcs_user"):
        mod = sys.modules.get(modname)
        if mod is None:
            continue
        try:
            importlib.reload(mod)
        except Exception:  # noqa: BLE001 - drop it so the next import is fresh
            sys.modules.pop(modname, None)


def _refresh_options() -> None:
    _reload_user_modules()
    try:
        import obs_options

        obs_options.reset_cache()
    except Exception:  # noqa: BLE001 - options cache is best-effort
        pass


def save_user_operation(name: str, source: str) -> dict:
    """Create or update the operation ``name`` with body ``source``; return the list.

    Raises :class:`UserOperationError` (HTTP 422) on an invalid name or code.
    """
    name = _validate_name(name)
    source = _validate_source(name, source)
    order, sources = _parse_existing()
    if name not in sources:
        order.append(name)
    sources[name] = source

    _funcs_user_dir().mkdir(parents=True, exist_ok=True)
    _user_file().write_text(_render(order, sources), encoding="utf-8")
    _ensure_bridge()
    _refresh_options()
    return read_user_operations()


def delete_user_operation(name: str) -> dict:
    """Remove the operation ``name``; return the updated list.

    Raises :class:`UserOperationError` (HTTP 422) if it doesn't exist.
    """
    order, sources = _parse_existing()
    if name not in sources:
        raise UserOperationError(f"no user operation named '{name}'")
    order = [n for n in order if n != name]
    del sources[name]
    _user_file().write_text(_render(order, sources), encoding="utf-8")
    _refresh_options()
    return read_user_operations()
