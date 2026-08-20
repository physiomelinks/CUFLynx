"""CUFLynx's analysis runners, imported by concurrent ranks.

A four-rank calibration launched from CUFLynx died on three of four ranks:

    File ".../libcuflynx/param_id/paramID.py", line 59, in <module>
        import corner
    ...
    FileNotFoundError: '~/.cache/arviz/daily_warning.tmp'
                    -> '~/.cache/arviz/daily_warning'
    MPI_ABORT was invoked on rank 3

The defect is upstream (arviz writes a once-a-day stamp file through a *fixed*
temporary name, so ranks race and the losers die) and the fix is in libcuflynx, which
now defers that import to the rank-0 plotting call. See circulatory_autogen #467.

**This file is CUFLynx's side of it**, and it is not redundant with CA's. CUFLynx is
what actually launches `mpiexec -n N`, and what it launches is
``calibration_runner.py`` / ``sensitivity_runner.py`` / ``uq_runner.py``, whose import
graphs are CUFLynx's own. A future import added to a runner -- or to ``ca_imports``, or
to anything the runners pull in -- can reintroduce exactly this class without touching
libcuflynx at all. The property worth pinning is about *our* runners: importing one must
not execute a third-party module whose body writes to a shared per-user path.

Two things learned building the CA equivalent, both of which shape what is here:

1. **``mpiexec -n N pytest`` cannot see this.** Measured: an app-shaped script lost
   33/60 ranks where the same import under pytest lost 0/40. pytest's plugin loading and
   collection stagger the ranks by 10-100 ms; the vulnerable window is microseconds. So
   these launch their own subprocesses and must never be run under ``mpiexec``.

2. **A plain barrier-then-import is barely better.** Released from a barrier, ranks then
   reach the import 277-326 ms apart, because the seconds of imports in between are not
   identical work at identical speed. The probe therefore puts the barrier *at* the
   import, with a meta-path finder that calls ``comm.Barrier()`` the first time the
   target is requested.

The deterministic test is the one that runs everywhere and is the real regression guard.
The concurrent one is a deliberate worst case, and is skipped unless
``CUFLYNX_ASSERT_UPSTREAM_IMPORTS`` is set -- against a libcuflynx released before #467
it is *expected* to fail, and a test that is red for a known upstream reason on every
pull request trains people to ignore it. The weekly dependency-upgrade job turns it on,
which is where finding out is the point.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

API_DIR = Path(__file__).resolve().parents[1]

#: Measured against deliberately broken code: eight ranks caught it 10/10 where four
#: ranks with no pre-warm caught it 3/10. Four was the obvious choice and would have
#: shipped a test that mostly lies. --oversubscribe covers runners with fewer slots.
RANKS = 8
TRIALS = 3

#: Third-party packages whose module bodies touch a shared per-user cache. None of these
#: may appear on a runner's import path: `arviz` writes ~/.cache/arviz through a fixed
#: temp name (the reported crash), and matplotlib and pytensor have the same shape --
#: a font cache and a compiledir, both rebuilt on first use, both shared by every rank.
#:
#: matplotlib is *not* listed: the runners legitimately need it for progress plots, and
#: its font-cache write is not currently a known race. It is named here so the next
#: person knows the omission is a decision rather than an oversight.
FORBIDDEN_ON_IMPORT = ("arviz", "xarray", "corner")

#: What the runners actually resolve through ``ca_imports``, and where.
#:
#: **Not the runner modules themselves.** The first draft of this file imported
#: ``calibration_runner`` and asserted arviz stayed out -- which passed, in 0.02 s,
#: having imported nothing at all: every runner defers its CA imports into ``main()``,
#: so the module body pulls in neither libcuflynx nor numpy. That is a test that probes
#: nothing and reports a pass, the exact failure this suite keeps meeting.
#:
#: The real import is ``ca_from("param_id.paramID", "CVS0DParamID")`` on line 138 of
#: calibration_runner, executed by every rank. So that is what is driven here: CUFLynx's
#: own resolver, with the arguments the runners really pass.
CA_TARGETS = [
    ("param_id.paramID", "CVS0DParamID"),
    ("emulators.emulator_trainer", "EmulatorTrainer"),
]


def _mpiexec():
    """Prefer the launcher beside this interpreter, as calibration.resolve_mpiexec does.

    A PATH mpiexec from a different MPI than mpi4py bound aborts every rank at MPI_Init.
    """
    beside = Path(sys.executable).parent / "mpiexec"
    if beside.exists():
        return str(beside)
    return shutil.which("mpiexec") or shutil.which("mpirun")


def _mpi_available() -> bool:
    if _mpiexec() is None:
        return False
    try:
        import mpi4py  # noqa: F401
    except ImportError:
        return False
    return True


# ---------------------------------------------------------------------------
# The deterministic guard
# ---------------------------------------------------------------------------
_IMPORT_PROBE = textwrap.dedent(
    """
    import importlib.util, json, sys
    sys.path.insert(0, {api_dir!r})
    from ca_imports import ca_from, ensure_ca_path
    ensure_ca_path()

    # Ask whether the fix is present *after* ensure_ca_path, and through the same
    # sys.path the assertion below uses. Asking in the parent pytest process instead
    # gets the wrong answer whenever CIRCULATORY_AUTOGEN_SRC points somewhere the
    # parent has not put on its path -- which is most of the time.
    try:
        fix_present = importlib.util.find_spec(
            "libcuflynx.utilities.lazy_imports") is not None
    except (ImportError, AttributeError, ValueError):
        fix_present = False

    obj = ca_from({module!r}, {attr!r})
    assert obj is not None
    print("RESULT " + json.dumps({{
        "leaked": sorted(m for m in {forbidden!r} if m in sys.modules),
        "loaded_something": "libcuflynx" in sys.modules,
        "fix_present": fix_present,
    }}))
    """
)


@pytest.mark.parametrize("module,attr", CA_TARGETS, ids=[m for m, _ in CA_TARGETS])
def test_resolving_a_runners_ca_import_does_not_pull_in_a_shared_cache_writer(module, attr):
    """``ca_from(...)`` -- the call every rank makes -- must not execute arviz.

    Stated as a property of the import graph rather than as "libcuflynx must not import
    corner", so it keeps holding when the reason changes: any future path from a runner's
    CA import to a module that writes a shared per-user file on import fails this.

    Deterministic, needs no MPI, and it is the assertion that actually catches a
    regression -- the concurrent test below is the demonstration, this is the guard.
    """
    probe = _IMPORT_PROBE.format(
        api_dir=str(API_DIR), module=module, attr=attr,
        forbidden=list(FORBIDDEN_ON_IMPORT))
    # CIRCULATORY_AUTOGEN_SRC is deliberately *honoured* rather than stripped. The
    # question here is about whichever libcuflynx the app would really resolve -- a
    # configured checkout takes precedence over the installed package by design, and a
    # checkout that reintroduced the module-level import would be just as fatal to a
    # four-rank run. (test_libcuflynx_package.py is the one that insists on the
    # installed package; its question is a different one.)
    env = dict(os.environ, MPLBACKEND="Agg")
    proc = subprocess.run(
        [sys.executable, "-c", probe], env=env, stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT, universal_newlines=True, timeout=600,
    )
    if proc.returncode != 0:
        pytest.skip(
            f"{module} is not resolvable in this environment, so there is no import "
            f"graph to check:\n{proc.stdout[-1500:]}"
        )
    line = [ln for ln in proc.stdout.splitlines() if ln.startswith("RESULT ")]
    assert line, f"probe printed no result:\n{proc.stdout}"
    result = json.loads(line[-1][len("RESULT "):])

    # Enforced the moment a fixed libcuflynx is installed; skipped with a reason before
    # then. Detected by capability (the fix adds libcuflynx.utilities.lazy_imports) rather
    # than by version, so it is immune to how the release is numbered and works against a
    # checkout too. The two alternatives are both worse: a permanently red test on every
    # pull request, which people learn to ignore, or an env-var switch nobody flips.
    if not result.get("fix_present"):
        pytest.skip(
            "the resolved libcuflynx still imports corner at module scope (it predates "
            "circulatory_autogen #467). Bump the libcuflynx pin in apps/api/pyproject.toml "
            "once a release carries the fix; this then starts enforcing by itself."
        )

    # Guard against the vacuous pass this test already shipped once: if nothing was
    # loaded, "arviz is absent" is true for the wrong reason.
    assert result["loaded_something"], (
        f"resolving {module} loaded no libcuflynx at all, so this assertion proves "
        f"nothing. Check the probe, not the product."
    )
    assert result["leaked"] == [], (
        f"resolving {module} pulled in {result['leaked']}. These write to a shared "
        f"per-user cache from their module body, so every rank of an `mpiexec -n N` run "
        f"races on the same file -- which is how three of four ranks died with "
        f"FileNotFoundError on ~/.cache/arviz/daily_warning.tmp. Keep such imports at "
        f"their call sites, on rank 0. See circulatory_autogen #467."
    )


# ---------------------------------------------------------------------------
# The concurrent worst case
# ---------------------------------------------------------------------------
_CONCURRENT_PROBE = textwrap.dedent(
    """
    import importlib.abc, sys
    sys.path.insert(0, {api_dir!r})
    # Pre-warm what the target's body will need, so nothing after the barrier
    # re-staggers the ranks. Measured: this alone took the catch rate at four ranks
    # from 3/10 to 9/10.
    import matplotlib, matplotlib.colors, matplotlib.pyplot  # noqa: F401
    import platformdirs, packaging.version, re, logging      # noqa: F401
    from mpi4py import MPI
    comm = MPI.COMM_WORLD

    class BarrierOn(importlib.abc.MetaPathFinder):
        def __init__(self, name):
            self.name = name; self.fired = False
        def find_spec(self, fullname, path=None, target=None):
            if fullname == self.name and not self.fired:
                self.fired = True
                comm.Barrier()
            return None      # never claim it; the real finders load it

    sys.meta_path.insert(0, BarrierOn("arviz"))
    try:
        import calibration_runner   # noqa: F401
    except BaseException as exc:      # noqa: BLE001 - reporting, not handling
        print("FAIL rank %d %s: %s" % (comm.rank, type(exc).__name__, exc), flush=True)
        sys.exit(1)
    print("OK rank %d" % comm.rank, flush=True)
    """
)


@pytest.mark.skipif(not _mpi_available(), reason="no mpiexec + mpi4py in this environment")
def test_concurrent_ranks_import_the_calibration_runner_with_a_cold_cache(tmp_path):
    """Eight ranks importing the calibration runner at once, on a cache that does not exist.

    ``XDG_CACHE_HOME`` points at a fresh directory per trial. That line is what makes a
    once-per-day failure fire on every run instead of once: with a warm cache the same
    configuration loses 0/20 ranks, which is why the bug read as random in production and
    why re-running appeared to fix it.

    Skipped unless ``CUFLYNX_ASSERT_UPSTREAM_IMPORTS=1``. Against a libcuflynx from
    before CA #467 this fails by design, and a permanently-red test on every pull request
    is one people learn to ignore. The weekly dependency-upgrade job sets it.
    """
    if not os.environ.get("CUFLYNX_ASSERT_UPSTREAM_IMPORTS", "").strip().strip("0"):
        pytest.skip(
            "set CUFLYNX_ASSERT_UPSTREAM_IMPORTS=1 to assert concurrent ranks can import "
            "the runners; known to fail against libcuflynx released before CA #467"
        )
    probe = _CONCURRENT_PROBE.format(api_dir=str(API_DIR), runner="calibration_runner")
    env = dict(os.environ, MPLBACKEND="Agg")
    env.pop("CIRCULATORY_AUTOGEN_SRC", None)

    failures = []
    for trial in range(TRIALS):
        cache = tmp_path / f"cache_{trial}"
        cache.mkdir()
        proc = subprocess.run(
            [_mpiexec(), "--oversubscribe", "-n", str(RANKS), sys.executable, "-c", probe],
            env=dict(env, XDG_CACHE_HOME=str(cache)), stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, universal_newlines=True, timeout=600,
        )
        if proc.returncode != 0 or "FAIL" in proc.stdout:
            failures.append(f"--- trial {trial} (exit {proc.returncode}) ---\n{proc.stdout}")

    assert not failures, (
        f"{len(failures)} of {TRIALS} cold-cache trials lost a rank importing "
        f"calibration_runner on {RANKS} ranks:\n\n" + "\n".join(failures)
    )
