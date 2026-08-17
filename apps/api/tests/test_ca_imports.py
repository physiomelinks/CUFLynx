"""The one resolver for circulatory_autogen's two module layouts (CA #428/#437).

CA moved every module under a ``libcuflynx.`` namespace, and CUFLynx cannot pick
a side: the CA directory is chosen by the user at runtime, so one build gets
pointed at old flat checkouts and new namespaced ones on the same machine. The
rules being pinned here are the ones a reader of ``ca_imports`` should be able to
rely on:

* namespaced wins when both are importable (on a shimmed CA the flat module is
  the deprecated copy, and CUFLynx's users should not see its warnings);
* flat still works when it is all there is;
* when neither works the message says the CA directory is not a checkout, rather
  than leaving the user with ``No module named 'generators'`` (the exact
  confusion issue #180's ca_dir bug produced).

The two layouts are built as real packages in a tmp dir rather than borrowed
from a CA checkout, so the test says what it means and runs anywhere.
"""

from __future__ import annotations

import importlib
import types
import sys
from pathlib import Path

import pytest

import ca_imports


TOP = "cuflynx_fake_ca"  # a CA top-level package name, invented for these tests
MOD = f"{TOP}.thing"


def _write_pkg(root, *parts, body=""):
    """Create ``root/<parts>/`` as a package, with ``__init__.py`` = ``body``."""
    pkg = root
    for part in parts:
        pkg = pkg / part
        pkg.mkdir(exist_ok=True)
        (pkg / "__init__.py").write_text("")
    (pkg / "__init__.py").write_text(body)
    return pkg


@pytest.fixture
def layouts(tmp_path, monkeypatch):
    """A tmp sys.path entry, with helpers to lay down either CA layout.

    ``CA_PACKAGES`` is pointed at the invented top-level name so the real
    ``parsers``/``param_id`` of whatever CA the developer has configured cannot
    take part.
    """
    monkeypatch.setattr(ca_imports, "CA_PACKAGES", frozenset({TOP}))
    monkeypatch.syspath_prepend(str(tmp_path))

    def flat(marker):
        _write_pkg(tmp_path, TOP)
        (tmp_path / TOP / "thing.py").write_text(f"WHICH = {marker!r}\n")

    def namespaced(marker):
        _write_pkg(tmp_path, ca_imports.NAMESPACE)
        _write_pkg(tmp_path, ca_imports.NAMESPACE, TOP)
        (tmp_path / ca_imports.NAMESPACE / TOP / "thing.py").write_text(
            f"WHICH = {marker!r}\n"
        )

    ca_imports.reset_cache()
    try:
        yield type("Layouts", (), {"flat": staticmethod(flat),
                                   "namespaced": staticmethod(namespaced)})
    finally:
        for name in [n for n in sys.modules
                     if n == TOP or n.startswith((f"{TOP}.", ca_imports.NAMESPACE))]:
            del sys.modules[name]
        ca_imports.reset_cache()
        importlib.invalidate_caches()


def _fresh():
    """Forget the cached layout *and* any module already imported under it."""
    ca_imports.reset_cache()
    importlib.invalidate_caches()
    for name in [n for n in sys.modules
                 if n == TOP or n.startswith((f"{TOP}.", ca_imports.NAMESPACE))]:
        del sys.modules[name]


# ---------------------------------------------------------------------------
# Which layout wins
# ---------------------------------------------------------------------------
def test_the_namespaced_layout_wins_when_both_are_importable(layouts):
    """Both present is the *shim* release: the flat module is the deprecated
    copy, so preferring it would make CUFLynx's users read CA's DeprecationWarnings."""
    layouts.flat("flat")
    layouts.namespaced("namespaced")
    _fresh()

    mod = ca_imports.ca_import(MOD)

    assert mod.WHICH == "namespaced"
    assert mod.__name__ == f"{ca_imports.NAMESPACE}.{MOD}"
    assert ca_imports.resolved_name(MOD) == f"{ca_imports.NAMESPACE}.{MOD}"


def test_the_flat_layout_is_used_when_it_is_the_only_one(layouts):
    """Every CA checkout that predates the move. Breaking these was the whole
    reason this is a resolver and not a rename."""
    layouts.flat("flat")
    _fresh()

    mod = ca_imports.ca_import(MOD)

    assert mod.WHICH == "flat"
    assert mod.__name__ == MOD
    assert ca_imports.resolved_name(MOD) == MOD


def test_a_namespaced_only_checkout_needs_no_flat_fallback(layouts):
    """The post-grace-period CA, once the shims are dropped."""
    layouts.namespaced("namespaced")
    _fresh()

    assert ca_imports.ca_import(MOD).WHICH == "namespaced"


def test_the_preference_survives_a_stale_layout_cache(layouts):
    """The cached "is libcuflynx importable" answer only orders the candidates.

    A CA directory swapped mid-session (Settings -> CA dir) must not be able to
    strand the resolver on the layout the *previous* one had, so both spellings
    are tried whichever way the cache points.
    """
    layouts.flat("flat")
    _fresh()
    assert ca_imports.ca_import(MOD).WHICH == "flat"  # caches "no namespace"

    layouts.namespaced("namespaced")
    importlib.invalidate_caches()
    del sys.modules[MOD]
    # No reset_cache(): the stale answer still finds the module, via the flat arm.
    assert ca_imports.ca_import(MOD).WHICH == "flat"

    ca_imports.reset_cache()  # what engine.reset() does on a CA-dir change
    _fresh()
    assert ca_imports.ca_import(MOD).WHICH == "namespaced"


def test_a_name_that_is_not_a_ca_package_is_left_alone(layouts):
    """``operation_funcs`` and friends are loaded from a directory on sys.path,
    not from a CA package, so they must not acquire a ``libcuflynx.`` prefix."""
    assert ca_imports.candidates("json") == ["json"]


# ---------------------------------------------------------------------------
# Failure has to stay legible
# ---------------------------------------------------------------------------
def test_neither_layout_present_blames_the_ca_directory(layouts, monkeypatch):
    """Not "No module named 'generators'". That line is what a wiped ca_dir
    looked like in the packaged app (issue #180), and it told the user nothing."""
    import engine as engine_mod

    monkeypatch.setattr(engine_mod, "_circulatory_autogen_src", lambda: "/not/a/checkout/src")
    # The developer may well have a real (namespaced) CA configured; rename the
    # namespace so nothing at all is importable, which is the state being described.
    monkeypatch.setattr(ca_imports, "NAMESPACE", "cuflynx_absent_namespace")
    _fresh()

    with pytest.raises(ImportError) as excinfo:
        ca_imports.ca_import(MOD)

    msg = str(excinfo.value)
    assert "/not/a/checkout/src" in msg
    assert "does not look like a circulatory_autogen checkout" in msg
    # Both spellings are named, so the reader can see what was actually tried.
    assert MOD in msg and f"{ca_imports.NAMESPACE}.{MOD}" in msg


def test_an_unconfigured_ca_directory_says_so_instead(layouts, monkeypatch):
    """The packaged app's starting state: nothing chosen yet, no sibling to guess."""
    import engine as engine_mod

    monkeypatch.setattr(engine_mod, "_circulatory_autogen_src", lambda: "")
    monkeypatch.delenv("CIRCULATORY_AUTOGEN_SRC", raising=False)
    monkeypatch.setattr(ca_imports, "NAMESPACE", "cuflynx_absent_namespace")
    _fresh()

    with pytest.raises(ImportError) as excinfo:
        ca_imports.ca_import(MOD)

    assert "No circulatory_autogen directory is configured" in str(excinfo.value)


def test_a_checkout_that_simply_predates_the_module_says_that(layouts, monkeypatch):
    """"Wrong directory" and "too old" are different faults and used to read the
    same. A checkout *was* found here — the module in it was not."""
    import engine as engine_mod

    monkeypatch.setattr(engine_mod, "_circulatory_autogen_src", lambda: "/some/ca/src")
    layouts.namespaced("namespaced")
    _fresh()

    with pytest.raises(ImportError) as excinfo:
        ca_imports.ca_import(f"{TOP}.feature_added_later")

    msg = str(excinfo.value)
    assert "was found but does not provide it" in msg
    assert "older" in msg


def test_a_missing_dependency_of_cas_own_is_reported_as_itself(layouts, tmp_path):
    """``libcuflynx.sensitivity_analysis`` imports SALib. When SALib is not
    installed the module *is* there and the honest answer is "No module named
    'SALib'" — falling through to the flat spelling and then saying "this CA does
    not provide sensitivity_analysis" would bury the real cause under a wrong one.
    """
    layouts.flat("flat")
    layouts.namespaced("namespaced")
    (tmp_path / ca_imports.NAMESPACE / TOP / "thing.py").write_text(
        "import a_third_party_package_that_is_not_installed\n"
    )
    _fresh()

    with pytest.raises(ModuleNotFoundError) as excinfo:
        ca_imports.ca_import(MOD)

    assert excinfo.value.name == "a_third_party_package_that_is_not_installed"


def test_the_failure_is_an_importerror_so_the_older_ca_fallbacks_still_fire(layouts):
    """Half the call sites degrade to a built-in default on ImportError. A new
    exception type would turn every one of those into a 500."""
    _fresh()
    assert issubclass(ca_imports.CaImportError, ImportError)
    with pytest.raises(ImportError):
        ca_imports.ca_import(MOD)


# ---------------------------------------------------------------------------
# ca_from
# ---------------------------------------------------------------------------
def test_ca_from_returns_one_object_or_a_tuple(layouts, tmp_path):
    layouts.flat("flat")
    (tmp_path / TOP / "thing.py").write_text("A = 1\nB = 2\n")
    _fresh()

    assert ca_imports.ca_from(MOD, "A") == 1
    assert ca_imports.ca_from(MOD, "A", "B") == (1, 2)


def test_ca_from_raises_importerror_for_a_name_the_module_lacks(layouts, tmp_path):
    """So ``try: ... except ImportError: <older CA fallback>`` keeps working for
    names as well as modules — which is how CUFLynx detects CA features."""
    layouts.flat("flat")
    (tmp_path / TOP / "thing.py").write_text("A = 1\n")
    _fresh()

    with pytest.raises(ImportError) as excinfo:
        ca_imports.ca_from(MOD, "A", "added_in_a_later_ca")

    assert "added_in_a_later_ca" in str(excinfo.value)
    assert "predates" in str(excinfo.value)


# ---------------------------------------------------------------------------
# sys.path entries
# ---------------------------------------------------------------------------
#: ca_paths() returns ``str(Path(...))``, i.e. entries in the platform's own
#: form -- which is right, since they go on ``sys.path``. Expectations here have
#: to be built the same way or they are a POSIX assertion: on Windows the code
#: answers ``\\ca\\src`` and a hardcoded ``/ca/src`` fails for no real reason.
def _p(*parts):
    return str(Path(*parts))


def test_ca_paths_offers_both_spellings_of_the_operation_funcs_directory(monkeypatch):
    """``import operation_funcs`` is a bare-name import off a directory, so the
    directory itself moves with the namespace. The namespaced one must be
    preferred, for the same reason the namespaced module is."""
    import engine as engine_mod

    monkeypatch.setattr(engine_mod, "_circulatory_autogen_src", lambda: "/ca/src")
    paths = ca_imports.ca_paths()

    assert _p("/ca/src") in paths
    assert _p("/ca/src/param_id") in paths
    assert _p("/ca/src", ca_imports.NAMESPACE, "param_id") in paths
    # funcs_user/ is the user's, beside src/, and does not move.
    assert _p("/ca/funcs_user") in paths
    # ensure_ca_path() inserts at 0 in order, so the *later* entry wins.
    assert paths.index(_p("/ca/src", ca_imports.NAMESPACE, "param_id")) > paths.index(
        _p("/ca/src/param_id")
    )


def test_ensure_ca_path_puts_the_namespaced_funcs_dir_ahead_of_the_flat_one(monkeypatch):
    import engine as engine_mod

    monkeypatch.setattr(engine_mod, "_circulatory_autogen_src", lambda: "/ca/src")
    monkeypatch.setattr(sys, "path", [p for p in sys.path])
    ca_imports.ensure_ca_path()

    ns = sys.path.index(_p("/ca/src", ca_imports.NAMESPACE, "param_id"))
    flat = sys.path.index(_p("/ca/src/param_id"))
    assert ns < flat


# ---------------------------------------------------------------------------
# The duplicate in sim_worker_runner.py
# ---------------------------------------------------------------------------
def test_the_sim_worker_runners_copy_follows_the_same_rule(layouts):
    """``sim_worker_runner.py`` is executed as a file by an external interpreter
    and must stay free of app imports (CLAUDE.md), so it carries its own copy of
    this rule. The copy is what runs the sliders — pin it too."""
    import sim_worker_runner as swr

    layouts.flat("flat")
    layouts.namespaced("namespaced")
    _fresh()

    assert swr._ca_import(MOD).WHICH == "namespaced"

    _fresh()
    with pytest.raises(ImportError) as excinfo:
        swr._ca_import(f"{TOP}.never_existed")
    assert "circulatory_autogen" in str(excinfo.value)
    assert f"{ca_imports.NAMESPACE}.{TOP}.never_existed" in str(excinfo.value)


def test_the_sim_worker_runners_copy_falls_back_to_flat(layouts, tmp_path):
    import sim_worker_runner as swr

    layouts.flat("flat")
    _fresh()

    assert swr._ca_import(MOD).WHICH == "flat"
    (tmp_path / TOP / "thing.py").write_text("A = 1\nB = 2\n")
    _fresh()
    assert swr._ca_from(MOD, "A") == 1
    assert swr._ca_from(MOD, "A", "B") == (1, 2)


# ---------------------------------------------------------------------------
# The app tier really goes through it
# ---------------------------------------------------------------------------
def _namespaced(monkeypatch, flat_name, module):
    """Register ``module`` as ``libcuflynx.<flat_name>`` only, and make the
    namespace look importable so it is the preferred candidate."""
    import types

    monkeypatch.setitem(sys.modules, ca_imports.NAMESPACE,
                        types.ModuleType(ca_imports.NAMESPACE))
    monkeypatch.setitem(sys.modules, f"{ca_imports.NAMESPACE}.{flat_name}", module)
    ca_imports.reset_cache()


def test_solver_options_reads_a_namespaced_ca(monkeypatch):
    """The schema accessors CA #437 calls out by name (SOLVER_SCHEMA and friends)
    come from ``parsers.PrimitiveParsers``; nothing in solver_options may spell
    that module itself any more."""
    import types

    import solver_options as so

    _namespaced(monkeypatch, "parsers.PrimitiveParsers",
                types.SimpleNamespace(SOLVER_SCHEMA={"from": "libcuflynx"}))
    monkeypatch.setattr(so, "_ensure_ca_path", lambda: None)
    try:
        assert so._introspect_solver_schema() == {"from": "libcuflynx"}
    finally:
        ca_imports.reset_cache()


def test_the_live_engine_builds_its_helper_from_a_namespaced_ca(monkeypatch):
    """The in-process simulation tier — ``solver_wrappers.get_simulation_helper``,
    the import the packaging notes call the live tier's one CA dependency."""
    import types

    import engine as engine_mod

    called = {}

    def fake_get_simulation_helper(**kwargs):
        called.update(kwargs)
        return "helper"

    _namespaced(monkeypatch, "solver_wrappers",
                types.SimpleNamespace(get_simulation_helper=fake_get_simulation_helper))
    monkeypatch.setattr(engine_mod, "_ensure_ca_on_path", lambda: None)
    try:
        helper = engine_mod._default_helper_factory(
            model_path="m.cellml", dt=0.01, sim_time=1.0, pre_time=0.0, solver_info={})
    finally:
        ca_imports.reset_cache()

    assert helper == "helper"
    assert called["model_path"] == "m.cellml"


class TestRelocatedModules:
    """CA #433 moved the built-in funcs out of ``funcs_user/`` and into the package.

    These three are the only CA modules whose namespaced spelling is *not* the flat one
    with ``libcuflynx.`` glued on: they were never reached by a dotted path at all --
    ``funcs_user/`` was simply on ``sys.path``, so the flat spelling is a bare module
    name. No prefix rule can derive ``libcuflynx.funcs.cost_funcs_user`` from
    ``cost_funcs_user``, which is why they need an explicit map.

    The regression this guards: ``obs_options.get_cost_funcs`` used a bare
    ``import cost_funcs_user`` inside a blanket ``except Exception: return None``. Against
    a CA that had moved the module, the import failed, the exception was swallowed, and
    the live cost silently disappeared from the Parameters tab -- no error anywhere, just
    a missing number. It was caught by the calibration cost-parity test, not by anything
    that looked like an import test.
    """

    def test_relocated_names_map_to_the_funcs_subpackage(self):
        assert ca_imports.RELOCATED_MODULES == {
            "cost_funcs_user": "libcuflynx.funcs.cost_funcs_user",
            "operation_funcs_user": "libcuflynx.funcs.operation_funcs_user",
            "modifier_funcs_user": "libcuflynx.funcs.modifier_funcs_user",
        }

    def test_candidates_offer_both_spellings(self, monkeypatch):
        monkeypatch.setattr(ca_imports, "_namespace_available", lambda: True)
        ca_imports.reset_cache()
        assert ca_imports.candidates("cost_funcs_user") == [
            "libcuflynx.funcs.cost_funcs_user",
            "cost_funcs_user",
        ]

    def test_candidates_prefer_flat_without_the_namespace(self, monkeypatch):
        monkeypatch.setattr(ca_imports, "_namespace_available", lambda: False)
        ca_imports.reset_cache()
        assert ca_imports.candidates("cost_funcs_user") == [
            "cost_funcs_user",
            "libcuflynx.funcs.cost_funcs_user",
        ]

    def test_resolves_the_moved_module_when_only_the_package_has_it(self, monkeypatch):
        """A current CA: the built-ins live in the package and the bare name is gone."""
        moved = types.ModuleType("libcuflynx.funcs.cost_funcs_user")
        moved.gaussian_MLE = lambda *a, **k: 0.0
        monkeypatch.setattr(ca_imports, "_namespace_available", lambda: True)
        ca_imports.reset_cache()
        monkeypatch.setitem(sys.modules, "libcuflynx.funcs.cost_funcs_user", moved)
        monkeypatch.setitem(sys.modules, "cost_funcs_user", None)
        assert ca_imports.ca_import("cost_funcs_user") is moved

    def test_resolves_the_bare_name_on_an_older_ca(self, monkeypatch):
        """A CA from before the move: only ``funcs_user/cost_funcs_user.py`` exists."""
        legacy = types.ModuleType("cost_funcs_user")
        legacy.gaussian_MLE = lambda *a, **k: 0.0
        monkeypatch.setattr(ca_imports, "_namespace_available", lambda: False)
        ca_imports.reset_cache()
        monkeypatch.setitem(sys.modules, "cost_funcs_user", legacy)
        monkeypatch.setitem(sys.modules, "libcuflynx.funcs.cost_funcs_user", None)
        assert ca_imports.ca_import("cost_funcs_user") is legacy


class TestInstalledPackageNeedsNoDirectory:
    """The packaged app bundles libcuflynx, so "no CA dir" stops meaning "no CA" (#18).

    Before bundling, every way of reaching CA went through a *directory*: ca_paths built
    sys.path entries from it, and ca_exists was `bool(src) and is_dir(src)`. An installed
    package is reached by neither -- plain importlib finds it -- so both had to learn that
    a missing directory is not a missing CA, or the packaged app would prompt for a
    directory the user does not have and does not need.
    """

    def test_no_configured_directory_contributes_no_path_entries(self, monkeypatch):
        """And in particular never the current working directory.

        Every entry ca_paths returns derives from the configured src, and `Path("")` is
        `.` -- so an unset CA dir used to put the cwd, `./param_id` and `./funcs_user` on
        a server process's sys.path. That predates the bundling and is a hazard on its
        own.
        """
        monkeypatch.setattr(ca_imports, "_ca_src", lambda: "")

        assert ca_imports.ca_paths() == []

    def test_a_configured_directory_still_wins(self, monkeypatch, tmp_path):
        """A developer pointing the app at a checkout must still override the bundle."""
        src = tmp_path / "circulatory_autogen" / "src"
        src.mkdir(parents=True)
        monkeypatch.setattr(ca_imports, "_ca_src", lambda: str(src))

        paths = ca_imports.ca_paths()

        assert str(src) in paths
        assert str(src / "libcuflynx" / "param_id") in paths

    def test_a_checkout_on_sys_path_does_not_count_as_installed(self, monkeypatch, tmp_path):
        """The distinction the first-run prompt depends on.

        `ensure_ca_path` inserts a configured checkout's `src` permanently, so once any
        directory has been used, `libcuflynx` stays importable for the life of the
        process -- even after the setting is cleared. Reporting that as an installed
        package would say CA is present with nothing configured.
        """
        checkout = tmp_path / "circulatory_autogen" / "src" / "libcuflynx"
        checkout.mkdir(parents=True)
        (checkout / "__init__.py").write_text("")
        spec = importlib.util.spec_from_file_location(
            "libcuflynx", checkout / "__init__.py")
        monkeypatch.setattr(importlib.util, "find_spec", lambda name: spec)

        assert ca_imports.installed_package_available() is False

    def test_a_site_packages_install_does_count(self, monkeypatch, tmp_path):
        installed = tmp_path / "site-packages" / "libcuflynx"
        installed.mkdir(parents=True)
        (installed / "__init__.py").write_text("")
        spec = importlib.util.spec_from_file_location(
            "libcuflynx", installed / "__init__.py")
        monkeypatch.setattr(importlib.util, "find_spec", lambda name: spec)

        assert ca_imports.installed_package_available() is True

    def test_absent_package_is_not_installed(self, monkeypatch):
        monkeypatch.setattr(importlib.util, "find_spec", lambda name: None)
        assert ca_imports.installed_package_available() is False
