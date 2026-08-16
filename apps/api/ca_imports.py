"""The one place CUFLynx imports circulatory_autogen, whichever layout it is in.

circulatory_autogen is moving every module under a ``libcuflynx.`` namespace
(CA #428 / #437): ``parsers`` becomes ``libcuflynx.parsers``, ``param_id``
becomes ``libcuflynx.param_id``, and so on for the whole of :data:`CA_PACKAGES`.

**CUFLynx has to support both layouts at once.** The CA directory is chosen by
the user at runtime (Settings -> "CA dir", ``CIRCULATORY_AUTOGEN_SRC``), so one
CUFLynx build gets pointed at old flat checkouts and new namespaced ones on the
same machine. Hard-switching the imports would break everyone whose checkout
predates the move; staying flat breaks against the new one. Upstream ships
deprecation shims for exactly one release, which is a grace period rather than a
solution.

So every CA import goes through :func:`ca_import` / :func:`ca_from`, named by
the **flat** spelling, and resolution tries ``libcuflynx.<name>`` first and the
flat ``<name>`` second. Namespaced wins when both work: on a shimmed CA the flat
module is the one that emits ``DeprecationWarning``, and CUFLynx's users should
not have to see those.

Two rules this module exists to keep:

- **No scattered ``try: import parsers / except ImportError: import
  libcuflynx.parsers``.** One resolver, one error message, one cache.
- :class:`CaImportError` subclasses :class:`ImportError` on purpose. Call sites
  all over the app degrade to a built-in fallback when CA is missing or too old
  (``except ImportError``), and they must keep doing so.

**This file is shipped into the ``runners/`` subdir too** (``packaging/cuflynx.spec``),
alongside the other modules both tiers share — ``local_sensitivity``,
``ca_run_history``, ``params_for_id``, ``obs_data``, ``emulator_config``. It is a
leaf: nothing here imports an app module except lazily, inside a ``try``, so the
analysis runners can use the one resolver rather than each carrying a copy.

The single exception is ``sim_worker_runner.py``, which CLAUDE.md requires to
stay free of *every* app import (it is the live tier's standalone child). It
carries a **deliberate duplicate** of this rule in its ``_ca_import`` /
``_ca_from`` helpers. Change one, change the other.
"""

from __future__ import annotations

import importlib
import importlib.util
import os
import sys
from pathlib import Path

#: The package every CA module now lives under.
NAMESPACE = "libcuflynx"

#: CA's top-level packages, in their flat spelling. Anything else handed to
#: :func:`ca_import` is left alone (``operation_funcs`` and ``cost_funcs_user``
#: are loaded from a directory on ``sys.path``, not from a CA package).
#: ``identifiabilty_analysis`` really is spelled that way upstream.
CA_PACKAGES = frozenset({
    "checks",
    "coupler",
    "emulators",
    "generators",
    "identifiabilty_analysis",
    "models",
    "param_id",
    "parsers",
    "protocol_runners",
    "scripts",
    "sensitivity_analysis",
    "solver1d",
    "solver_wrappers",
    "utilities",
})


class CaImportError(ImportError):
    """A circulatory_autogen module could not be imported in either layout.

    Subclasses :class:`ImportError` so the many ``except ImportError`` fallbacks
    around the app (older CA -> built-in default) keep working unchanged.
    """


# None = not yet probed. Reset via :func:`reset_cache` when the CA dir changes,
# because that changes the answer.
_namespaced: bool | None = None


def reset_cache() -> None:
    """Forget which layout the configured CA uses (call when the CA dir changes)."""
    global _namespaced
    _namespaced = None


def _namespace_available() -> bool:
    """Whether ``libcuflynx`` is importable at all, cached.

    Only an ordering hint: :func:`ca_import` still tries both spellings, so a
    stale answer costs at most one extra failed import on a path that was going
    to raise anyway.
    """
    global _namespaced
    if _namespaced is None:
        if NAMESPACE in sys.modules:
            _namespaced = True
        else:
            try:
                _namespaced = importlib.util.find_spec(NAMESPACE) is not None
            except (ImportError, ValueError, AttributeError):
                # A half-installed or shadowed libcuflynx: treat as absent and
                # let the real import attempt produce the real error.
                _namespaced = False
    return bool(_namespaced)


def candidates(name: str) -> list[str]:
    """Both spellings of CA module ``name``, most-preferred first.

    ``name`` is the flat spelling (``"parsers.PrimitiveParsers"``). Namespaced
    is preferred whenever ``libcuflynx`` is importable; otherwise the flat
    spelling leads and the namespaced one stays on the list so a failure reports
    both.
    """
    top = name.split(".", 1)[0]
    if top not in CA_PACKAGES:
        return [name]
    namespaced = f"{NAMESPACE}.{name}"
    return [namespaced, name] if _namespace_available() else [name, namespaced]


def _candidate_absent(cand: str, exc: ImportError) -> bool:
    """Whether ``cand`` itself is missing, rather than failing on its own imports.

    The difference matters more than it looks. ``libcuflynx.sensitivity_analysis``
    exists but raises ``No module named 'SALib'`` when SALib is not installed —
    falling back to the flat spelling there, and then reporting "this CA does not
    provide it", buries the real answer under a wrong one. Only a
    ``ModuleNotFoundError`` naming the candidate (or a package of it) means the
    module is not there; anything else is re-raised as-is.
    """
    if not isinstance(exc, ModuleNotFoundError):
        return False
    name = exc.name
    return bool(name) and (cand == name or cand.startswith(f"{name}."))


def _ca_src() -> str:
    """The configured CA directory, or "".

    :mod:`engine` is imported lazily (it imports this module) and is *absent* in
    the runner tier, where this file is shipped into ``runners/`` without it — so
    the env var, which is how the runners are told about CA in the first place,
    is both the fallback and the correct answer there.
    """
    try:
        from engine import _circulatory_autogen_src  # noqa: PLC0415

        return _circulatory_autogen_src() or ""
    except Exception:  # noqa: BLE001 - an error message must never raise
        return os.environ.get("CIRCULATORY_AUTOGEN_SRC", "") or ""


def _checkout_found(name: str) -> bool:
    """Whether *some* CA top-level package is importable, so the dir is a checkout."""
    for top in (NAMESPACE, name.split(".", 1)[0]):
        if top in sys.modules and sys.modules[top] is not None:
            return True
        try:
            if importlib.util.find_spec(top) is not None:
                return True
        except Exception:  # noqa: BLE001 - probing must not raise
            continue
    return False


def _failure_message(name: str, errors: list[tuple[str, BaseException]]) -> str:
    """Say what is actually wrong, rather than "No module named 'generators'".

    Two different faults used to produce the same baffling line: the CA
    directory not being a circulatory_autogen checkout at all, and a checkout
    that simply predates the module being asked for. They get different
    sentences here.
    """
    tried = " and ".join(f"{cand!r} ({exc})" for cand, exc in errors)
    head = f"circulatory_autogen module {name!r} could not be imported (tried {tried})."
    src = _ca_src()
    if _checkout_found(name):
        where = f"The circulatory_autogen at {src!r}" if src else "The circulatory_autogen on sys.path"
        return (
            f"{head} {where} was found but does not provide it — it is probably "
            f"older than the feature that needs it."
        )
    if not src:
        return (
            f"{head} No circulatory_autogen directory is configured: set "
            f'Settings -> "CA dir" to the "src" folder of a circulatory_autogen '
            f"clone (or install the libcuflynx package)."
        )
    return (
        f"{head} {src!r} does not look like a circulatory_autogen checkout: "
        f'point Settings -> "CA dir" at the "src" folder of a circulatory_autogen '
        f"clone (or install the libcuflynx package)."
    )


def ca_import(name: str):
    """Import circulatory_autogen module ``name``, given in its **flat** spelling.

    ``ca_import("parsers.PrimitiveParsers")`` returns
    ``libcuflynx.parsers.PrimitiveParsers`` when that exists and
    ``parsers.PrimitiveParsers`` otherwise.

    Raises :class:`CaImportError` (an ``ImportError``) when neither is
    importable, with a message that names the CA directory rather than leaving
    the user with ``No module named 'generators'``.
    """
    names = candidates(name)
    # An already-imported module wins without a filesystem probe. This is also
    # what lets the unit tests inject fakes with ``sys.modules[...] = fake``.
    # A ``None`` entry is skipped here on purpose: tests use it to *force* an
    # ImportError, which the real import below then raises.
    for cand in names:
        mod = sys.modules.get(cand)
        if mod is not None:
            return mod
    errors: list[tuple[str, BaseException]] = []
    for cand in names:
        try:
            return importlib.import_module(cand)
        except ImportError as exc:
            if not _candidate_absent(cand, exc):
                raise  # the module is there; something *it* imports is not
            errors.append((cand, exc))
    raise CaImportError(_failure_message(name, errors)) from errors[0][1]


def ca_from(module: str, *names: str):
    """``from <module> import <names>``, resolved through :func:`ca_import`.

    One name returns the object; several return a tuple, so a call site reads
    like the import it replaces. A name the module does not have raises
    :class:`CaImportError` — an ``ImportError``, which is what the "older CA"
    fallbacks around the app catch.
    """
    mod = ca_import(module)
    missing = [n for n in names if not hasattr(mod, n)]
    if missing:
        raise CaImportError(
            f"circulatory_autogen's {getattr(mod, '__name__', module)!r} has no "
            f"{', '.join(missing)} — this circulatory_autogen predates it."
        )
    values = tuple(getattr(mod, n) for n in names)
    return values[0] if len(names) == 1 else values


def resolved_name(name: str) -> str:
    """The dotted name ``name`` actually resolves to, for string-based targets.

    ``mock.patch`` targets and anything else that names a module as a string
    must not hardcode one layout.
    """
    return getattr(ca_import(name), "__name__", name)


def ca_paths() -> list[str]:
    """``sys.path`` entries CA's own modules need, least-preferred first.

    Three kinds of entry:

    - the CA ``src`` directory, which is what makes ``libcuflynx`` (or the flat
      packages) importable;
    - the directory holding ``operation_funcs.py``, which CUFLynx imports **by
      bare name** the way CA's own user-func machinery does. Both spellings of
      that directory are listed, namespaced last so it wins, because a shimmed
      CA has the flat one too and the shim is the deprecated copy;
    - the repo's ``funcs_user/``, which holds ``cost_funcs_user.py``. That one is
      the user's, not a CA package, and does not move.
    """
    src = Path(_ca_src())
    root = src.parent  # repo root holds funcs_user/ alongside src/
    return [
        str(src),
        str(src / "param_id"),
        str(src / NAMESPACE / "param_id"),
        str(root / "funcs_user"),
    ]


def ensure_ca_path() -> None:
    """Put :func:`ca_paths` on ``sys.path`` (most-preferred entry ends up first)."""
    for p in ca_paths():
        if p not in sys.path:
            sys.path.insert(0, p)
