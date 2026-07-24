"""Path/interpreter resolution that works both from source and frozen (PyInstaller).

The desktop build (``scripts/package.py``) freezes the API + the built frontend
into one executable. Two things behave differently there and both are load-bearing:

``sys.executable``
    In a frozen app this is the *bundle*, not a Python interpreter. Every
    ``subprocess.Popen([sys.executable, runner.py, ...])`` would therefore
    relaunch the GUI instead of running a calibration. :func:`default_python`
    returns ``None`` when frozen so callers fall back to a discovered/chosen
    interpreter rather than re-executing the bundle.

``__file__``
    Frozen modules live inside the PyInstaller archive, so paths derived from
    ``__file__`` don't point at real files on disk. :func:`resource_path`
    resolves data files against the unpacked bundle dir (``sys._MEIPASS``)
    instead.

Deliberately dependency-free so the unit tier imports it without FastAPI/Myokit.
"""

from __future__ import annotations

import hashlib
import os
import sys
from pathlib import Path
from typing import Mapping, Optional

_SOURCE_API_DIR = Path(__file__).resolve().parent


def is_frozen() -> bool:
    """True when running inside a PyInstaller bundle."""
    return getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS")


def bundle_root() -> Path:
    """Directory that bundled data files were unpacked into.

    Frozen: PyInstaller's ``sys._MEIPASS`` temp dir. From source: ``apps/api``,
    so ``resource_path("calibration_runner.py")`` resolves either way.
    """
    if is_frozen():
        return Path(sys._MEIPASS)  # type: ignore[attr-defined]
    return _SOURCE_API_DIR


def resource_path(*parts: str) -> Path:
    """Absolute path to a bundled data file (runner scripts, frontend dist)."""
    return bundle_root().joinpath(*parts)


def bundled_mpiexec() -> str | None:
    """The MPICH Hydra launcher bundled beside the app, or None.

    Only the frozen app bundles a launcher, and only where the build had a real
    MPICH wheel to take it from (Linux, macOS-arm64 -- see packaging/cuflynx.spec).
    It is the launcher matching the bundle's own MPICH runtime, so using it avoids
    the launcher/runtime mismatch that a PATH ``mpiexec`` from a different MPI
    causes. Returns None from source, on platforms without a bundled launcher, or
    if the file is somehow absent -- callers then fall back to PATH.
    """
    if not is_frozen():
        return None
    exe = "mpiexec.hydra" + (".exe" if sys.platform == "win32" else "")
    cand = resource_path("mpi", "bin", exe)
    return str(cand) if cand.is_file() else None


def runner_path(name: str) -> Path:
    """Absolute path to an analysis runner script (executed by an *external*
    interpreter).

    Frozen, these live in a **subdirectory** (``<bundle>/runners``), not the
    bundle root. That's load-bearing: Python puts the running script's directory
    on ``sys.path[0]``, and the bundle root holds the app's own numpy / scipy /
    etc. If the runner sat there, the external interpreter would import the
    *bundle's* packages instead of its own and crash on the ABI mismatch
    (``numpy.core.multiarray failed to import``). A dedicated subdir keeps the
    bundle's Python packages off the runner's path. From source the runners live
    beside this module in ``apps/api``.
    """
    if is_frozen():
        return resource_path("runners", name)
    return _SOURCE_API_DIR / name


def frontend_dist() -> Path:
    """The built Vue app.

    Frozen, the spec bundles ``apps/web/dist`` as ``web/dist``. From source it
    sits at ``apps/web/dist``, i.e. a sibling of ``apps/api``.
    """
    if is_frozen():
        return resource_path("web", "dist")
    return _SOURCE_API_DIR.parent / "web" / "dist"


def resources_dir() -> Path:
    """The bundled ``resources/`` directory (example models, test fixtures).

    Frozen, the spec bundles the repo-root ``resources`` dir as ``resources``.
    From source it sits at the repo root, i.e. two levels up from ``apps/api``.
    """
    if is_frozen():
        return resource_path("resources")
    return _SOURCE_API_DIR.parent.parent / "resources"


# argv sentinel that makes the frozen exe run an analysis runner in-process
# instead of launching the GUI. See apps/desktop/app.py.
RUNNER_MODE_FLAG = "--_cuflynx-run-analysis"


def default_python() -> str | None:
    """Default *external* interpreter for the analysis runners, or None.

    From source this is the interpreter serving the API (it has the deps). Frozen,
    there is no external default — None means "run the analysis in the bundle
    itself" (the exe re-invokes itself in runner mode; see :func:`runner_command`).
    The bundle carries CA's analysis deps, so this works with no user setup; the
    user can still pick an external interpreter (e.g. a local CA checkout) in
    Settings, which overrides this.

    ``CUFLYNX_PYTHON`` overrides both (handy for the packaged app and for tests).
    """
    override = os.environ.get("CUFLYNX_PYTHON")
    if override:
        return override
    if is_frozen():
        return None
    return sys.executable


def runner_command(python: str | None, runner_script: str, config_path: str) -> list:
    """Build the argv to run an analysis runner.

    - An explicit external ``python`` runs the runner script directly.
    - Frozen with no external python: re-invoke the bundle in runner mode, so the
      analysis runs in the app's own interpreter (which has CA's analysis deps).
    - From source with no external python: this interpreter runs the script.
    """
    if python:
        return [python, "-u", runner_script, config_path]
    if is_frozen():
        return [sys.executable, RUNNER_MODE_FLAG, runner_script, config_path]
    return [sys.executable, "-u", runner_script, config_path]


# Dynamic-linker search-path variables PyInstaller rewrites to point at the
# unpacked bundle. It stashes the caller's original value in ``<VAR>_ORIG``.
_LOADER_VARS = ("LD_LIBRARY_PATH", "DYLD_LIBRARY_PATH", "DYLD_FRAMEWORK_PATH")


def subprocess_env() -> dict:
    """Environment for spawning an *external* interpreter (the analysis runners).

    In a PyInstaller bundle the running process has ``LD_LIBRARY_PATH`` /
    ``DYLD_*`` pointing at the unpacked bundle so its own libs resolve. Inheriting
    that into a subprocess that runs a **different** Python is a trap: the external
    interpreter then loads the bundle's native libraries (numpy/OpenBLAS/…), built
    for the frozen interpreter, and imports blow up with things like
    ``numpy.core.multiarray failed to import`` / ``_ARRAY_API not found``.

    Restore each loader var to the value PyInstaller saved in ``<VAR>_ORIG`` (or
    drop it if there was none), so the runner sees a clean, non-bundle
    environment. A no-op when not frozen.
    """
    env = dict(os.environ)
    if not is_frozen():
        return env
    for var in _LOADER_VARS:
        original = env.get(f"{var}_ORIG")
        if original is not None:
            env[var] = original
        else:
            env.pop(var, None)
    return env


# ---------------------------------------------------------------------------
# Onefile extraction relocation (issue #67)
#
# In the packaged app a multi-core analysis is launched as
# ``mpiexec -n N <bundle> --_cuflynx-run-analysis ...``. Each of the N ranks is a
# fresh copy of the PyInstaller *onefile* executable, and each one unpacks its own
# ``_MEIxxxxxx`` tree on startup. By default that lands in the system ``$TMPDIR``,
# so a full/small ``$TMPDIR`` turns into an opaque rank crash before any Python
# runs ("[PYI-...:ERROR] Could not create temporary directory!").
#
# We can't fix this in the spec: PyInstaller 6.x's ``runtime_tmpdir`` does NOT do
# environment-variable/``~`` expansion on POSIX, and the spec runs on the *build*
# machine, so any absolute path baked there points at the CI runner's home, not the
# user's. So we do it at *launch* time, where a real per-user path is available:
# set ``TMPDIR`` / ``TMP`` / ``TEMP`` to a roomy, predictable per-build cache dir,
# relocating every rank's extraction off the volatile system temp.
#
# Scope note (why this is relocation, not de-duplication). An earlier version also
# tried to make the ranks *share* one extraction via PyInstaller's ``_MEIPASS2``
# "already unpacked here" signal. That signal does not exist in PyInstaller 6.x
# (the shipped toolchain, >=6.0): the bootloader was rewritten and the parent->child
# reuse is now an internal ``_PYI_APPLICATION_HOME_DIR`` / ``_PYI_PARENT_PROCESS_LEVEL``
# protocol that independent ``mpiexec`` ranks can't opt into from the environment
# (verified against a real 6.21 onefile build -- see packaging/README.md). So each
# rank still extracts its own copy; this change only *relocates* those extractions
# to a location that won't run the system temp out of space. The N x on-disk cost
# during a run is unchanged.
#
# The cache directory is keyed by the executable's identity (path + size + mtime)
# so different builds don't pile transient extractions into one directory; a
# rebuilt/redownloaded exe gets a fresh sub-dir without parsing a version string.
# ---------------------------------------------------------------------------

_APP_CACHE_NAME = "CUFLynx"


def user_cache_base(platform_name: str, environ: Mapping[str, str], home: str) -> Optional[Path]:
    """OS-conventional per-user cache root, or None if it can't be determined.

    Pure (all inputs injected) so it's unit-testable without touching the real
    environment: Windows -> ``%LOCALAPPDATA%``; macOS -> ``~/Library/Caches``;
    Linux/other POSIX -> ``$XDG_CACHE_HOME`` or ``~/.cache``.
    """
    if platform_name == "win32":
        base = environ.get("LOCALAPPDATA") or (
            str(Path(home) / "AppData" / "Local") if home else None
        )
    elif platform_name == "darwin":
        base = str(Path(home) / "Library" / "Caches") if home else None
    else:  # linux and other POSIX
        base = environ.get("XDG_CACHE_HOME") or (str(Path(home) / ".cache") if home else None)
    return Path(base) if base else None


def extraction_cache_key(exe_path: str, size: int, mtime: float) -> str:
    """Short, stable-per-build key for the onefile extraction directory.

    Derived from the executable's identity, so all ranks of one launch (same exe)
    share one parent dir, while a rebuilt/redownloaded exe (different size/mtime)
    gets a fresh sub-dir rather than mixing with an old build's extractions.
    """
    raw = f"{exe_path}|{size}|{int(mtime)}".encode("utf-8", "surrogatepass")
    return hashlib.blake2b(raw, digest_size=8).hexdigest()


def extraction_cache_dir(base: Path, key: str) -> Path:
    """The versioned onefile extraction dir under a cache ``base`` for ``key``."""
    return base / _APP_CACHE_NAME / "onefile-cache" / key


def _ensure_extraction_cache_dir() -> Optional[Path]:
    """Create (if needed) and return the per-build extraction cache dir, or None.

    Best-effort: any failure (no home, unwritable cache, odd platform) returns
    None so the caller leaves the environment untouched and the bundle falls back
    to its normal ``$TMPDIR`` behaviour -- never a regression.
    """
    try:
        base = user_cache_base(sys.platform, os.environ, os.path.expanduser("~"))
        if base is None:
            return None
        st = os.stat(sys.executable)
        target = extraction_cache_dir(base, extraction_cache_key(
            sys.executable, st.st_size, st.st_mtime))
        target.mkdir(parents=True, exist_ok=True)
        return target
    except OSError:
        return None


def _relocate_bundle_extraction_env(env: dict) -> None:
    """Point the onefile extraction temp (``TMPDIR``/``TMP``/``TEMP``) at the roomy
    per-build cache dir, for a bundle-re-invocation launch.

    Mutates ``env`` in place. Only meaningful when the child processes are this
    same frozen executable (see :func:`runner_launch_env`). No-op if the cache dir
    can't be created, leaving the bundle's default ``$TMPDIR`` behaviour intact.
    """
    cache = _ensure_extraction_cache_dir()
    if cache is not None:
        cache_str = str(cache)
        for var in ("TMPDIR", "TMP", "TEMP"):
            env[var] = cache_str


def runner_launch_env(python: Optional[str]) -> dict:
    """Environment for spawning an analysis runner (calibration/sensitivity/UQ).

    Starts from :func:`subprocess_env`. When the runner is the *bundle itself*
    re-invoked in runner mode (``python is None`` and frozen -- see
    :func:`runner_command`), also relocate the onefile extraction temp onto a roomy
    per-build cache dir so N MPI ranks don't exhaust the system ``$TMPDIR`` (issue
    #67). For an external interpreter this is irrelevant, so it's omitted.
    """
    env = subprocess_env()
    if python is None and is_frozen():
        _relocate_bundle_extraction_env(env)
    return env
