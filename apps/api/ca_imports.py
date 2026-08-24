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

Two files cannot import it and carry **deliberate duplicates** of this rule
instead:

- ``sim_worker_runner.py``, which CLAUDE.md requires to stay free of *every* app
  import (it is the live tier's standalone child), in its ``_ca_import`` /
  ``_ca_from`` helpers;
- the exported ``run_pipeline.py`` (``PIPELINE_SCRIPT`` in
  ``export_pipeline.py``), which runs in the user's own environment with only
  circulatory_autogen beside it.

Change one, change all three — ``tests/test_ca_import_parity.py`` pins them to
the tables here.
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
#: ``identifiabilty_analysis`` really is spelled that way upstream, and
#: ``external_testing`` is CA's builder of a real run, which only tests import --
#: it is listed for the same reason as the rest: without it the namespaced
#: spelling is never tried and the import silently falls back to a flat name
#: that no CA has ever had.
CA_PACKAGES = frozenset({
    "checks",
    "coupler",
    "emulators",
    "external_testing",
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


#: CA's spelling -> CUFLynx's, for ``generated_model_format`` values CA renamed.
#: ``cellml_only`` became ``cellml`` upstream; a checkout from before that still
#: says the old word in its schema, in its run configs and in everything it hands
#: back. CUFLynx carries **one** spelling internally (the current one) and
#: translates only at the two boundaries: :func:`solver_options.canonical_model_type`
#: on the way in, :func:`solver_options.ca_model_type` on the way out.
#:
#: It lives here rather than in ``solver_options`` because the runner tier needs
#: it and ``solver_options`` does not ship into ``runners/``: a run config carries
#: CA's spelling (``main`` writes ``ca_model_type(...)`` into it), and
#: ``local_sensitivity`` compares that value against its own ``cellml`` /
#: ``casadi_python`` tables. Against a pre-rename CA the un-canonicalised
#: ``cellml_only`` matched nothing, so an FSA/AUTO local-sensitivity run the menu
#: had offered was then refused with ``NotImplementedError`` -- the #122 failure,
#: reintroduced through the config rather than through the menu.
MODEL_TYPE_ALIASES = {"cellml_only": "cellml"}


def canonical_model_type(model_type: str | None) -> str | None:
    """CUFLynx's spelling of a CA ``generated_model_format``. Unknown names pass through.

    Apply it to anything that came from CA or from a run config CA will also
    read. Never to a value on its way *out* -- that is :func:`solver_options.ca_model_type`.
    """
    if not model_type:
        return model_type
    return MODEL_TYPE_ALIASES.get(model_type, model_type)


# None = not yet probed. Reset via :func:`reset_cache` when the CA dir changes,
# because that changes the answer.
_namespaced: bool | None = None

#: Every ``sys.path`` entry :func:`ensure_ca_path` has inserted, so
#: :func:`reset_cache` can take the previous CA directory's entries back out.
#: Without this a switch leaves the old ``src`` on the path for ever.
_inserted_paths: list[str] = []


def _ca_module_roots() -> frozenset[str]:
    """Top-level names under which a CA module can end up in ``sys.modules``.

    The namespace package, the flat top-level packages, and the bare-name modules
    CA loads off a directory (``operation_funcs``, ``cost_funcs_user``, ...) —
    those are keyed by the bare name however they were reached.
    """
    return frozenset({NAMESPACE, *CA_PACKAGES, *RELOCATED_MODULES})


def reset_cache() -> None:
    """Forget the configured CA entirely (call when the CA dir changes).

    Three things have to go, and clearing only the first is why switching CA
    directories mid-session used to keep running the *old* one:

    1. the remembered layout (namespaced vs flat) — a new directory can be the
       other one;
    2. the ``sys.path`` entries :func:`ensure_ca_path` added for the old
       directory, since they would otherwise keep shadowing the new one for any
       module the new CA does not have;
    3. **every already-imported CA module**. This is the one that actually
       matters: ``ca_import`` answers from ``sys.modules`` before it touches the
       filesystem, and ``libcuflynx.__path__`` pins the directory the package was
       first found in — so inserting a new ``src`` at ``sys.path[0]`` has no
       effect at all on a package that is already imported. It matters more now
       that the app bundles a ``libcuflynx``, which makes *some* CA importable
       from the very first request.

    What this cannot undo is objects already built from the old CA: a class read
    out of it before the switch is still that class. Callers therefore drop their
    own caches around this — :meth:`engine.SimulationEngine.reset` clears the
    helpers and stops the sim worker, ``solver_options`` / ``obs_options`` drop
    their introspection caches — and CA's deprecation shim re-installs itself on
    the next flat import (its finder declines when neither spelling is in
    ``sys.modules``, which is exactly the state left here).
    """
    global _namespaced
    _namespaced = None
    for p in _inserted_paths:
        while p in sys.path:
            sys.path.remove(p)
    _inserted_paths.clear()
    roots = _ca_module_roots()
    for name in list(sys.modules):
        if name.split(".", 1)[0] in roots:
            del sys.modules[name]
    # A new directory means new files; the per-directory listings importlib
    # caches predate them.
    importlib.invalidate_caches()


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


#: Modules whose namespaced spelling is not the flat one with the namespace glued on.
#: CA #433 moved the built-in cost/operation/modifier funcs out of the repo's
#: ``funcs_user/`` directory and into the package as ``libcuflynx.funcs.*``. They were
#: never reached by a dotted path -- ``funcs_user/`` was simply on ``sys.path``, so the
#: flat spelling is the bare module name and no prefix rule can derive the new one.
#:
#: ``operation_funcs`` is here for the same reason and one more. It has always been
#: imported by **bare name** — CUFLynx copying what CA's own user-func machinery did —
#: which works only because :func:`ca_paths` puts ``<src>/param_id`` on ``sys.path``.
#: An **installed** libcuflynx (the packaged app bundles one, #18) has no such
#: directory to add and ``ca_paths()`` correctly returns ``[]``, so the bare import
#: fails there. Every one of its call sites sits inside a fallback arm, so the
#: packaged app silently dropped to a hardcoded operation vocabulary instead of CA's —
#: exactly what "introspect CA, never hardcode" exists to stop. Routed through the
#: real dotted module the flat spelling still works as the second candidate, for a CA
#: old enough to predate the namespace.
RELOCATED_MODULES = {
    "cost_funcs_user": f"{NAMESPACE}.funcs.cost_funcs_user",
    "operation_funcs_user": f"{NAMESPACE}.funcs.operation_funcs_user",
    "modifier_funcs_user": f"{NAMESPACE}.funcs.modifier_funcs_user",
    "operation_funcs": f"{NAMESPACE}.param_id.operation_funcs",
}


def candidates(name: str) -> list[str]:
    """Both spellings of CA module ``name``, most-preferred first.

    ``name`` is the flat spelling (``"parsers.PrimitiveParsers"``). Namespaced
    is preferred whenever ``libcuflynx`` is importable; otherwise the flat
    spelling leads and the namespaced one stays on the list so a failure reports
    both.
    """
    if name in RELOCATED_MODULES:
        namespaced = RELOCATED_MODULES[name]
        return [namespaced, name] if _namespace_available() else [name, namespaced]
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

    The two arms are separate on purpose. A blanket ``except Exception`` around
    both read "engine is not here" for *any* failure, so a genuine breakage
    inside :mod:`engine` (a syntax error, a missing dependency of its own) was
    silently downgraded to the env var and never reported. Only a
    :exc:`ModuleNotFoundError` naming ``engine`` itself means the runner tier;
    anything else is engine's problem and is re-raised, except at the one place
    that must never raise (:func:`_failure_message`, which passes ``quiet``).
    """
    mod = sys.modules.get("engine")
    if mod is None:
        try:
            mod = importlib.import_module("engine")
        except ModuleNotFoundError as exc:
            if exc.name != "engine":
                raise  # engine is here; something *it* imports is not
            return os.environ.get("CIRCULATORY_AUTOGEN_SRC", "") or ""
    return mod._circulatory_autogen_src() or ""


def _in_runner_tier() -> bool:
    """Whether this is a standalone runner rather than the app.

    ``engine`` is an app module; the runners are executed as files by whichever
    interpreter the user chose, with only ``runners/`` on the path. The distinction
    decides what the advice should be -- a runner cannot be fixed from Settings, only
    by installing into the interpreter it is running as.
    """
    if "engine" in sys.modules:
        return False
    try:
        importlib.import_module("engine")
    except ModuleNotFoundError as exc:
        return exc.name == "engine"
    except Exception:  # noqa: BLE001 - engine is here, just broken; that is the app tier
        return False
    return False


def _ca_src_quiet() -> str:
    """:func:`_ca_src` for an error message, which must never raise itself."""
    try:
        return _ca_src()
    except Exception:  # noqa: BLE001 - reporting a failure cannot fail
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
    src = _ca_src_quiet()
    if _checkout_found(name):
        where = f"The circulatory_autogen at {src!r}" if src else "The circulatory_autogen on sys.path"
        return (
            f"{head} {where} was found but does not provide it — it is probably "
            f"older than the feature that needs it."
        )
    if not src:
        if _in_runner_tier():
            # The interpreter chosen in Settings runs this, and the app's bundled
            # libcuflynx is inside the executable -- not importable from out here. So the
            # fix is to install into *this* interpreter, which Settings cannot do.
            return (
                f"{head} This interpreter has no libcuflynx: install it with "
                f'"{sys.executable} -m pip install libcuflynx", or clear the Python '
                f"interpreter in Settings to use the one bundled with the app."
            )
        return (
            f"{head} No circulatory_autogen found: install it with "
            f'"pip install libcuflynx", or point Settings -> "CA dir" at the "src" '
            f"folder of a circulatory_autogen clone."
        )
    return (
        f"{head} {src!r} does not look like a circulatory_autogen checkout: "
        f'point Settings -> "CA dir" at the "src" folder of a circulatory_autogen '
        f"clone (or install the libcuflynx package)."
    )


def _finished_importing(mod) -> bool:
    """Whether ``mod`` has finished executing, rather than being mid-import.

    **This is the fix for the reported emulator failure.** Python inserts a module object
    into ``sys.modules`` *before* running its body, so a thread that reads ``sys.modules``
    directly, while another thread is partway through the import, gets a half-built
    module. ``libcuflynx.parsers.PrimitiveParsers`` is 4487 lines and defines
    ``ANALYSIS_OPTIONS`` on line 1497, so the window is wide -- and what comes out the
    other side is ``'libcuflynx.parsers.PrimitiveParsers' has no ANALYSIS_OPTIONS``
    against a copy that plainly has it, which is exactly what v0.4.1 reported.

    ``importlib.import_module`` does not have this problem: it blocks on the per-module
    import lock and hands back the finished module. So the fast path just has to decline
    a module that is still initialising and let the import below do it properly.

    A module the tests inject (``sys.modules[x] = ModuleType(...)``) has ``__spec__``
    None, which reads as finished -- which is right, nothing is importing it.
    """
    spec = getattr(mod, "__spec__", None)
    return not getattr(spec, "_initializing", False)


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
        if mod is not None and _finished_importing(mod):
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
        # ca_import answers with the *first* spelling that imports, and it judges only
        # whether the module loads -- not whether it is the one carrying what was asked
        # for. A hollow or half-written `libcuflynx` (a checkout mid-branch-switch, an
        # interrupted install, a partially extracted bundle) imports perfectly well as a
        # PEP 420 namespace package and then answers "no" for everything, losing the
        # caller a flat `parsers.PrimitiveParsers` sitting right there with the attribute
        # in it. Trying the other spelling costs one import on a path that was about to
        # raise anyway.
        #
        # **What this cannot reach**, and it is the case that looks most like the bug
        # report: two copies of the *same* spelling. A current checkout is namespaced
        # too, so it and the bundled package are both `libcuflynx.parsers.
        # PrimitiveParsers`; only one can be in sys.modules, `ensure_ca_path` puts the
        # checkout's `src` at sys.path[0], and there is no second candidate to try. The
        # resolved path in the message below is what diagnoses that one.
        alternative = _candidate_providing(module, names, getattr(mod, "__name__", None))
        if alternative is not None:
            mod, missing = alternative, []
    if missing:
        # Name the *file*, not just the dotted name. "libcuflynx.parsers.PrimitiveParsers
        # has no ANALYSIS_OPTIONS" is unactionable when several copies are reachable --
        # the whole question is which one answered, and only the path says that.
        where = getattr(mod, "__file__", None)
        raise CaImportError(
            f"{getattr(mod, '__name__', module)!r} has no {', '.join(missing)}"
            + (f" (resolved to {where})" if where else "")
            + " — that copy of libcuflynx predates it."
        )
    values = tuple(getattr(mod, n) for n in names)
    return values[0] if len(names) == 1 else values


def _candidate_providing(module: str, names, already: str | None):
    """The first other spelling of ``module`` that has every one of ``names``, or None.

    Deliberately silent about import failures: this runs only after a module has already
    been resolved, so a candidate that cannot be imported is not news -- the caller
    already has something, it simply lacks the attribute.
    """
    for cand in candidates(module):
        if cand == already:
            continue
        mod = sys.modules.get(cand)
        if mod is None or not _finished_importing(mod):
            try:
                mod = importlib.import_module(cand)
            except ImportError:
                continue
        if all(hasattr(mod, n) for n in names):
            return mod
    return None


def resolved_name(name: str) -> str:
    """The dotted name ``name`` actually resolves to, for string-based targets.

    ``mock.patch`` targets and anything else that names a module as a string
    must not hardcode one layout.
    """
    return getattr(ca_import(name), "__name__", name)


def ca_paths() -> list[str]:
    """``sys.path`` entries CA's own modules need, least-preferred first.

    Three kinds of entry, and **all three are for older checkouts only** — a
    current CA is reached entirely through dotted ``libcuflynx.`` names, and an
    installed one (the bundle) needs no path entry at all:

    - the CA ``src`` directory, which is what makes ``libcuflynx`` (or the flat
      packages) importable;
    - the directory that held ``operation_funcs.py`` back when CUFLynx imported
      it **by bare name**. Both spellings are listed, namespaced last so it wins,
      because a shimmed CA has the flat one too and the shim is the deprecated
      copy. CUFLynx no longer imports it that way (it goes through
      :data:`RELOCATED_MODULES` now, so the packaged app can reach it), but a CA
      predating the namespace still resolves ``param_id.operation_funcs`` off
      ``src`` and CA's own user-func loading uses these directories;
    - the repo's ``funcs_user/``. It used to hold CA's built-in
      ``cost_funcs_user.py`` / ``operation_funcs_user.py``; CA #433 moved those
      into the package (:data:`RELOCATED_MODULES`) and a current checkout's
      ``funcs_user/`` holds only ``*_example.py``. The entry stays for the older
      checkouts where those modules really are only findable there — it is a
      user-code directory going in front of everything on ``sys.path``, so it
      earns its place by that alone.
    """
    src_str = _ca_src()
    if not src_str:
        # No directory configured. An **installed** libcuflynx (the packaged app
        # bundles one, CUFLynx #18) needs no sys.path entry at all: it is already
        # importable, and ca_import finds it through plain importlib. With neither
        # a directory nor a package there is likewise nothing useful to add -- the
        # import then fails with CaImportError, which names the CA directory.
        #
        # Returning early also removes a hazard that predates the bundling: every
        # entry below derives from `src`, and `Path("")` is `.`, so an unset CA dir
        # used to put the **current working directory** -- and `./param_id`,
        # `./funcs_user` -- on a server process's sys.path.
        return []
    src = Path(src_str)
    root = src.parent  # repo root holds funcs_user/ alongside src/
    return [
        str(src),
        str(src / "param_id"),
        str(src / NAMESPACE / "param_id"),
        str(root / "funcs_user"),
    ]


def installed_package_available() -> bool:
    """Whether ``libcuflynx`` is present as an **installed package** (or bundled).

    The packaged app ships one, so "no CA dir" stops meaning "no CA" (#18). Callers
    use this to decide whether CA is *present*, not which spelling to import --
    :func:`ca_import` already owns that.

    Importable is not enough, and the difference is not academic: ``ensure_ca_path``
    inserts a configured checkout's ``src`` into ``sys.path`` permanently, so once
    any directory has been used, ``libcuflynx`` stays importable for the life of the
    process even after the setting is cleared. Treating that as "installed" would
    report CA present with nothing configured and skip the first-run prompt -- the
    exact failure the ``bool(src)`` guard in ``main`` exists to prevent.

    So the origin has to be somewhere other than a checkout. A checkout puts the
    package at ``<repo>/src/libcuflynx/``; an install (or the bundle) does not have
    that ``src`` parent. This is the same shape of test circulatory_autogen applies
    to itself in ``libcuflynx.utilities.paths.repo_root``.
    """
    try:
        spec = importlib.util.find_spec(NAMESPACE)
    except (ImportError, AttributeError, ValueError):
        return False
    origin = getattr(spec, "origin", None) if spec is not None else None
    if not origin:
        return False
    # <...>/libcuflynx/__init__.py -> the directory holding the package
    parent = Path(origin).resolve().parent.parent
    return parent.name != "src"


def ensure_ca_path() -> None:
    """Put :func:`ca_paths` on ``sys.path`` (most-preferred entry ends up first).

    Each insertion is recorded so :func:`reset_cache` can undo it: a CA directory
    that stays on ``sys.path`` after the user has picked a different one keeps
    answering for anything the new one does not have.
    """
    for p in ca_paths():
        if p not in sys.path:
            sys.path.insert(0, p)
            _inserted_paths.append(p)
