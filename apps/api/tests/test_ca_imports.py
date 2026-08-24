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


def test_switching_the_ca_directory_takes_effect_without_a_restart(tmp_path, monkeypatch):
    """The promise ``reset_cache`` makes, which it did not keep.

    Clearing ``_namespaced`` could never have been enough: ``_namespace_available``
    re-answers ``True`` straight from ``sys.modules``, ``ca_import`` returns from
    ``sys.modules`` before it touches the filesystem at all, and once ``libcuflynx``
    has been imported its ``__path__`` pins the directory it came from — so putting
    a new ``src`` at ``sys.path[0]`` changes nothing. Switching from a namespaced
    checkout to any other CA kept running the old one, which made the new layout
    *worse* at this than the flat one had been.

    It matters more now that the packaged app bundles a libcuflynx: something is
    always importable, so the wrong CA is always available to be kept.

    No hand-purging here (unlike ``_fresh``): ``reset_cache()`` is the whole
    subject, so the test does exactly what ``engine.reset()`` does and no more.
    """
    import engine as engine_mod

    monkeypatch.setattr(ca_imports, "CA_PACKAGES", frozenset({TOP}))

    def make_checkout(name, marker):
        src = tmp_path / name / "src"
        src.mkdir(parents=True)
        _write_pkg(src, ca_imports.NAMESPACE)
        _write_pkg(src, ca_imports.NAMESPACE, TOP)
        (src / ca_imports.NAMESPACE / TOP / "thing.py").write_text(f"WHICH = {marker!r}\n")
        return src

    first = make_checkout("first", "first")
    second = make_checkout("second", "second")

    def use(src):
        monkeypatch.setattr(engine_mod, "_circulatory_autogen_src", lambda: str(src))
        ca_imports.reset_cache()  # exactly what engine.reset() calls
        ca_imports.ensure_ca_path()

    try:
        use(first)
        assert ca_imports.ca_import(MOD).WHICH == "first"

        use(second)  # Settings -> "CA dir", mid-session
        assert ca_imports.ca_import(MOD).WHICH == "second"

        use(first)  # and back again
        assert ca_imports.ca_import(MOD).WHICH == "first"
    finally:
        ca_imports.reset_cache()
        importlib.invalidate_caches()


def test_reset_takes_the_previous_ca_directory_off_sys_path(tmp_path, monkeypatch):
    """``ensure_ca_path`` inserts permanently, so without this the abandoned
    checkout stays in front of everything and keeps answering for any module the
    newly-chosen CA happens not to have."""
    import engine as engine_mod

    src = tmp_path / "src"
    src.mkdir()
    monkeypatch.setattr(engine_mod, "_circulatory_autogen_src", lambda: str(src))
    try:
        ca_imports.reset_cache()
        ca_imports.ensure_ca_path()
        assert str(src) in sys.path

        ca_imports.reset_cache()
        assert str(src) not in sys.path
    finally:
        ca_imports.reset_cache()


def test_a_name_that_is_not_a_ca_package_is_left_alone(layouts):
    """A name that is neither one of CA's top-level packages nor in
    RELOCATED_MODULES must not acquire a ``libcuflynx.`` prefix — there is no
    such module to find, and the wrong candidate only muddies the error."""
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

    # Both ways of providing one, since in the app either would work. (A runner gets
    # different advice -- see TestTheAdviceMatchesTheTierItIsGivenIn.)
    message = str(excinfo.value)
    assert "No circulatory_autogen found" in message
    assert "pip install libcuflynx" in message
    assert "CA dir" in message


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
@pytest.fixture
def sim_worker(monkeypatch):
    """``sim_worker_runner`` with its own ``CA_PACKAGES`` gate pointed at TOP.

    The copy carries the gate too now, so the invented top-level name has to be
    in *its* table as well as in ``ca_imports``' — otherwise ``TOP.thing`` is
    "not a CA module", keeps its bare name, and the test would be asserting the
    default rather than the rule.
    """
    import sim_worker_runner as swr

    monkeypatch.setattr(swr, "_CA_PACKAGES", frozenset({TOP}))
    return swr


def test_the_sim_worker_runners_copy_follows_the_same_rule(layouts, sim_worker):
    """``sim_worker_runner.py`` is executed as a file by an external interpreter
    and must stay free of app imports (CLAUDE.md), so it carries its own copy of
    this rule. The copy is what runs the sliders — pin it too."""
    swr = sim_worker

    layouts.flat("flat")
    layouts.namespaced("namespaced")
    _fresh()

    assert swr._ca_import(MOD).WHICH == "namespaced"

    _fresh()
    with pytest.raises(ImportError) as excinfo:
        swr._ca_import(f"{TOP}.never_existed")
    assert "circulatory_autogen" in str(excinfo.value)
    assert f"{ca_imports.NAMESPACE}.{TOP}.never_existed" in str(excinfo.value)


def test_the_sim_worker_runners_copy_falls_back_to_flat(layouts, tmp_path, sim_worker):
    swr = sim_worker

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

    # Reset *before* injecting, not after: reset_cache() drops every imported CA
    # module from sys.modules (that is what makes a CA-dir switch take effect),
    # so a fake registered first would be thrown away by it.
    ca_imports.reset_cache()
    monkeypatch.setitem(sys.modules, ca_imports.NAMESPACE,
                        types.ModuleType(ca_imports.NAMESPACE))
    monkeypatch.setitem(sys.modules, f"{ca_imports.NAMESPACE}.{flat_name}", module)


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


def test_solver_options_reads_operation_funcs_from_a_namespaced_ca(monkeypatch):
    """``import operation_funcs`` was a bare-name import off the ``<src>/param_id``
    entry ``ca_paths()`` adds. An **installed** libcuflynx (which the packaged app
    bundles, #18) has no directory to add, so the bare name could not resolve — and
    ``_safe`` swallows the failure, leaving the app on ``_FALLBACK_DIFFERENTIABLE``:
    a hardcoded copy of the very vocabulary CA is supposed to own."""
    import types

    import solver_options as so

    fake_ops = types.SimpleNamespace(
        get_operation_funcs_dict_for_mode=lambda mode: {"max": max, "mean": min},
    )
    _namespaced(monkeypatch, "param_id.operation_funcs", fake_ops)
    monkeypatch.setitem(sys.modules, f"{ca_imports.NAMESPACE}.param_id.differentiable",
                        types.SimpleNamespace(
                            is_circulatory_differentiable=lambda fn: fn is max))
    monkeypatch.setattr(so, "_ensure_ca_path", lambda: None)
    try:
        assert so._introspect_differentiable() == {"max": True, "mean": False}
    finally:
        ca_imports.reset_cache()


def test_obs_options_reads_operation_funcs_from_a_namespaced_ca(monkeypatch):
    """Same import, the other half of the vocabulary: the operations offered in
    the obs_data editor. Its bare import sat inside ``except Exception: return
    None`` / a fallback list, so the packaged app showed a hardcoded menu."""
    import types

    import obs_options as oo

    fake_ops = types.SimpleNamespace(
        get_operation_funcs_dict_for_mode=lambda mode, external_path=None: {
            "max": max, "spread": min,
        },
    )
    _namespaced(monkeypatch, "param_id.operation_funcs", fake_ops)
    monkeypatch.setattr(oo, "_ensure_ca_path", lambda: None)
    monkeypatch.setattr(oo, "_external_func_paths", lambda _d=None: (None, None))
    try:
        assert sorted(oo.get_operation_funcs()) == ["max", "spread"]
    finally:
        ca_imports.reset_cache()


class TestRelocatedModules:
    """The CA modules whose namespaced spelling is *not* the flat one with a prefix.

    They were never reached by a dotted path at all: a directory was simply on
    ``sys.path`` and the module was imported by bare name, so no prefix rule can derive
    ``libcuflynx.funcs.cost_funcs_user`` from ``cost_funcs_user``. Hence an explicit map.

    Three of them are CA #433, which moved the built-in cost/operation/modifier funcs out
    of the repo's ``funcs_user/`` and into the package. The fourth, ``operation_funcs``,
    never moved -- it was always ``<src>/param_id/operation_funcs.py`` -- but the bare
    import needed the ``<src>/param_id`` entry ``ca_paths()`` adds, and an **installed**
    or bundled libcuflynx has no directory to add (#18): ``ca_paths()`` correctly returns
    ``[]`` there.

    The regression this guards: ``obs_options.get_cost_funcs`` used a bare
    ``import cost_funcs_user`` inside a blanket ``except Exception: return None``. Against
    a CA that had moved the module, the import failed, the exception was swallowed, and
    the live cost silently disappeared from the Parameters tab -- no error anywhere, just
    a missing number. It was caught by the calibration cost-parity test, not by anything
    that looked like an import test. ``operation_funcs`` sits behind the same shape of
    swallowed fallback in four places, and in the packaged app it would have taken the
    whole operation/cost vocabulary down to a hardcoded copy.
    """

    def test_relocated_names_map_to_their_real_dotted_module(self):
        assert ca_imports.RELOCATED_MODULES == {
            "cost_funcs_user": "libcuflynx.funcs.cost_funcs_user",
            "operation_funcs_user": "libcuflynx.funcs.operation_funcs_user",
            "modifier_funcs_user": "libcuflynx.funcs.modifier_funcs_user",
            "operation_funcs": "libcuflynx.param_id.operation_funcs",
        }

    def test_operation_funcs_resolves_without_any_sys_path_entry(self, monkeypatch):
        """The packaged app's state: a libcuflynx that is installed, not a checkout.

        ``ca_paths()`` is then empty, so ``<src>/param_id`` is on ``sys.path``
        nowhere and a bare ``import operation_funcs`` cannot work. The dotted
        module must.
        """
        import engine as engine_mod

        monkeypatch.setattr(engine_mod, "_circulatory_autogen_src", lambda: "")
        monkeypatch.setattr(ca_imports, "_namespace_available", lambda: True)
        assert ca_imports.ca_paths() == []
        assert ca_imports.candidates("operation_funcs") == [
            # importable with no path entry at all...
            "libcuflynx.param_id.operation_funcs",
            # ...and the flat spelling stays on the list, for a checkout predating
            # the namespace, where param_id/operation_funcs.py is a real file.
            "operation_funcs",
        ]

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


class TestTheAdviceMatchesTheTierItIsGivenIn:
    """A runner cannot be fixed from Settings, so it must not be told to try.

    The app bundles libcuflynx inside the executable, which is not importable from
    outside it. A runner launched with the interpreter chosen in Settings therefore has
    no engine unless that interpreter installed one -- and the old message, written when
    a CA directory was the only way to find CA at all, told the user to set a directory.
    Correct advice for the app; useless in a subprocess that has no Settings.
    """

    def test_a_runner_is_told_to_install_into_its_own_interpreter(self, monkeypatch):
        monkeypatch.setattr(ca_imports, "_in_runner_tier", lambda: True)
        monkeypatch.setattr(ca_imports, "_ca_src_quiet", lambda: "")
        monkeypatch.setattr(ca_imports, "_checkout_found", lambda name: False)

        message = ca_imports._failure_message("parsers.PrimitiveParsers", [])

        assert sys.executable in message, "name the interpreter that actually needs it"
        assert "pip install libcuflynx" in message
        assert "CA dir" not in message, "a runner has no Settings to change"

    def test_the_app_is_told_both_ways_to_provide_one(self, monkeypatch):
        monkeypatch.setattr(ca_imports, "_in_runner_tier", lambda: False)
        monkeypatch.setattr(ca_imports, "_ca_src_quiet", lambda: "")
        monkeypatch.setattr(ca_imports, "_checkout_found", lambda name: False)

        message = ca_imports._failure_message("parsers.PrimitiveParsers", [])

        assert "pip install libcuflynx" in message
        assert "CA dir" in message

    def test_the_app_tier_is_not_mistaken_for_a_runner_when_engine_is_broken(self, monkeypatch):
        """A broken `engine` is the app tier with a problem, not a runner."""
        def raise_other(name):
            raise ModuleNotFoundError("No module named 'numpy'", name="numpy")

        monkeypatch.delitem(sys.modules, "engine", raising=False)
        monkeypatch.setattr(importlib, "import_module", raise_other)

        assert ca_imports._in_runner_tier() is False


@pytest.mark.unit
def test_ca_from_names_the_file_when_no_copy_has_it(monkeypatch, tmp_path):
    """"X has no ANALYSIS_OPTIONS" is unactionable when several copies are reachable.

    Which one answered is the whole question, and only the path says that.
    """
    stale = types.ModuleType("libcuflynx.parsers.PrimitiveParsers")
    stale.__file__ = str(tmp_path / "somewhere" / "PrimitiveParsers.py")
    monkeypatch.setitem(sys.modules, "libcuflynx.parsers.PrimitiveParsers", stale)
    monkeypatch.setitem(sys.modules, "parsers.PrimitiveParsers", stale)

    with pytest.raises(ca_imports.CaImportError) as excinfo:
        ca_imports.ca_from("parsers.PrimitiveParsers", "ANALYSIS_OPTIONS")

    message = str(excinfo.value)
    assert stale.__file__ in message, f"the error does not say which copy answered: {message}"
    assert "ANALYSIS_OPTIONS" in message


@pytest.mark.unit
@pytest.mark.parametrize("namespace_available", [True, False])
def test_a_hollow_copy_does_not_veto_the_one_that_has_the_attribute(
    monkeypatch, namespace_available
):
    """``ca_import`` answers with the first spelling that *imports*, which is a different
    question from which one carries what the caller asked for.

    A hollow or half-written ``libcuflynx`` -- a checkout caught mid-branch-switch, an
    interrupted install, a partially extracted bundle -- is a valid PEP 420 namespace
    package. It imports, it has no attributes, and it used to end the search.

    Parametrised over both candidate orderings **because the ordering is not a constant**:
    ``candidates()`` leads with the namespaced spelling whenever ``libcuflynx`` is
    importable, which is always true in the packaged app and on any dev machine that has
    it installed -- but false on CI, which installs no libcuflynx. Pinning one ordering
    wrote a test that passed only where the packaged app's condition does *not* hold, and
    that asserted a state which cannot occur: a real import always populates the parent
    package, so ``libcuflynx.parsers.PrimitiveParsers`` cannot sit in ``sys.modules``
    while ``libcuflynx`` is absent.
    """
    monkeypatch.setattr(ca_imports, "_namespaced", namespace_available)
    hollow_name, good_name = ca_imports.candidates("parsers.PrimitiveParsers")

    hollow = types.ModuleType(hollow_name)
    good = types.ModuleType(good_name)
    good.ANALYSIS_OPTIONS = {"emulation": {"options": [1]}}
    for name, mod in ((hollow_name, hollow), (good_name, good)):
        monkeypatch.setitem(sys.modules, name, mod)
        if "." in name:  # a dotted name is only reachable with its parent imported
            top = name.split(".")[0]
            monkeypatch.setitem(sys.modules, top, types.ModuleType(top))

    got = ca_imports.ca_from("parsers.PrimitiveParsers", "ANALYSIS_OPTIONS")

    assert got is good.ANALYSIS_OPTIONS, (
        f"{hollow_name!r} answered no and nothing asked {good_name!r}, which had it"
    )


@pytest.mark.unit
def test_the_fallback_gives_up_when_no_reachable_copy_has_it(monkeypatch):
    """Two copies of the *same* spelling are indistinguishable here, and must be.

    A current checkout is namespaced too, so it and the bundled package are both
    ``libcuflynx.parsers.PrimitiveParsers``; only one can be in ``sys.modules``. This
    fallback tries other *spellings*, not other *copies*, so it has nothing to offer --
    the resolved path in the message is what diagnoses that case. The limit is written
    down here rather than left to be rediscovered.

    The failure must stay a :class:`CaImportError`. It subclasses ``ImportError`` because
    call sites all over the app degrade to a built-in fallback on one; a candidate handed
    back without checking it has the attribute turns this into an ``AttributeError`` at
    the ``getattr`` below, which sails straight past every one of those arms.
    """
    monkeypatch.setattr(ca_imports, "_namespaced", True)
    monkeypatch.setitem(sys.modules, "libcuflynx", types.ModuleType("libcuflynx"))
    for name in ca_imports.candidates("parsers.PrimitiveParsers"):
        monkeypatch.setitem(sys.modules, name, types.ModuleType(name))  # neither has it

    with pytest.raises(ca_imports.CaImportError):
        ca_imports.ca_from("parsers.PrimitiveParsers", "ANALYSIS_OPTIONS")


@pytest.mark.unit
def test_a_module_still_being_imported_is_not_handed_out(tmp_path, monkeypatch):
    """The reported v0.4.1 emulator failure, reproduced.

    Python puts a module into ``sys.modules`` *before* executing its body, so the
    ``sys.modules`` fast path could hand a caller a half-built module while another
    thread was still importing it. ``libcuflynx.parsers.PrimitiveParsers`` is 4487 lines
    and defines ``ANALYSIS_OPTIONS`` on line 1497, so the window is wide: the Emulator tab
    reported ``'libcuflynx.parsers.PrimitiveParsers' has no ANALYSIS_OPTIONS`` against a
    copy that had it, and the next call quietly succeeded.

    ``importlib.import_module`` blocks on the per-module import lock and returns the
    finished module, so declining an initialising one in the fast path is the whole fix.
    """
    import threading
    import time

    pkg = tmp_path / "slowpkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "SlowParsers.py").write_text(
        "import time\n"
        "EARLY = 1\n"
        "time.sleep(0.6)\n"                       # the body of a long module, mid-execution
        "ANALYSIS_OPTIONS = {'emulation': {'options': [1]}}\n"
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    name = "slowpkg.SlowParsers"
    monkeypatch.setattr(ca_imports, "candidates", lambda _n: [name])
    for mod_name in (name, "slowpkg"):
        monkeypatch.delitem(sys.modules, mod_name, raising=False)

    result = {}

    def importer():
        __import__(name)

    def asker():
        time.sleep(0.2)  # land while importer is inside the module body
        try:
            result["value"] = ca_imports.ca_from(name, "ANALYSIS_OPTIONS")
        except BaseException as exc:  # noqa: BLE001 - the failure is the point
            result["error"] = f"{type(exc).__name__}: {exc}"

    threads = [threading.Thread(target=importer), threading.Thread(target=asker)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=20)

    assert "error" not in result, (
        "a half-built module was handed out while another thread was importing it: "
        + str(result.get("error"))
    )
    assert result["value"] == {"emulation": {"options": [1]}}


@pytest.mark.unit
def test_an_initialising_module_is_not_finished(monkeypatch):
    """The predicate itself, without the timing.

    A threaded test proves the behaviour but is a poor place to notice that the
    ``_initializing`` probe stopped working -- a wrong answer here just makes the race
    rare again rather than failing outright.
    """
    import types

    class _Spec:                       # what importlib puts on a module while it runs
        _initializing = True

    mod = types.ModuleType("half.built")
    mod.__spec__ = _Spec()
    assert ca_imports._finished_importing(mod) is False

    mod.__spec__._initializing = False
    assert ca_imports._finished_importing(mod) is True

    # An injected fake has no spec at all; nothing is importing it, so it is finished.
    assert ca_imports._finished_importing(types.ModuleType("injected")) is True


@pytest.mark.unit
def test_the_other_spelling_is_not_read_while_it_is_still_importing(monkeypatch):
    """``_candidate_providing`` re-reads ``sys.modules`` and carries the same hazard.

    It runs after one module has resolved without the attribute, so it is looking at a
    *second* copy -- and reading that one out of ``sys.modules`` mid-import would report
    "no copy has it" while the copy that does was seconds from finishing. Deterministic
    here rather than threaded: the point is that an initialising entry is declined, and
    ``importlib.import_module`` is asked instead.
    """
    class _Spec:
        _initializing = True

    half = types.ModuleType("libcuflynx.parsers.PrimitiveParsers")   # no attribute yet
    half.__spec__ = _Spec()
    whole = types.ModuleType("libcuflynx.parsers.PrimitiveParsers")
    whole.ANALYSIS_OPTIONS = {"emulation": {"options": [1]}}

    monkeypatch.setattr(ca_imports, "candidates",
                        lambda _n: ["parsers.PrimitiveParsers",
                                    "libcuflynx.parsers.PrimitiveParsers"])
    monkeypatch.setitem(sys.modules, "libcuflynx.parsers.PrimitiveParsers", half)
    monkeypatch.setattr(ca_imports.importlib, "import_module",
                        lambda name: whole if name == whole.__name__ else None)

    got = ca_imports._candidate_providing(
        "parsers.PrimitiveParsers", ("ANALYSIS_OPTIONS",), "parsers.PrimitiveParsers")

    assert got is whole, (
        "the half-built copy was read straight out of sys.modules, so the caller was "
        "told no reachable copy has the attribute"
    )


class TestCaFirstOf:
    """Resolving a class CA has renamed, from either side of the rename.

    ``ParamID`` and ``MCMC`` were ``OpencorParamID`` and ``OpencorMCMC`` until CA renamed
    them -- they were never about OpenCOR, they are the parameter-identification and MCMC
    engines and run against myokit/CVODE, casadi and emulators alike. CUFLynx has to work
    against an engine from either side of that change, so it asks for the new name and
    accepts the old one.
    """

    def test_the_new_name_wins_when_both_are_there(self, monkeypatch):
        """CA keeps the old name as an alias, so both are present on a current engine.

        Order matters rather than being cosmetic: whichever is returned is what appears in
        tracebacks and error messages for the rest of the run.
        """
        import ca_imports

        module = types.SimpleNamespace(ParamID="new", OpencorParamID="old")
        monkeypatch.setattr(ca_imports, "ca_import", lambda name: module)
        assert ca_imports.ca_first_of("param_id.paramID", "ParamID",
                                      "OpencorParamID") == "new"

    def test_the_old_name_is_accepted_on_an_engine_that_predates_the_rename(
            self, monkeypatch):
        """The whole point: an older CA has only the old spelling and must still work."""
        import ca_imports

        module = types.SimpleNamespace(OpencorParamID="old")
        monkeypatch.setattr(ca_imports, "ca_import", lambda name: module)
        assert ca_imports.ca_first_of("param_id.paramID", "ParamID",
                                      "OpencorParamID") == "old"

    def test_neither_spelling_raises_the_usual_diagnostic(self, monkeypatch):
        """A genuinely absent class must fail the way every other missing one does.

        ``ca_from``'s message names the file that answered, which is what tells a hollow
        checkout apart from an old engine -- losing it here would make exactly this
        failure the hardest one to read.
        """
        import ca_imports

        module = types.SimpleNamespace(__name__="libcuflynx.param_id.paramID")
        monkeypatch.setattr(ca_imports, "ca_import", lambda name: module)
        with pytest.raises(ca_imports.CaImportError, match="has no ParamID"):
            ca_imports.ca_first_of("param_id.paramID", "ParamID", "OpencorParamID")
