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

import os
import sys
from pathlib import Path

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
