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

# Raised when an analysis run has no interpreter to spawn. Only reachable in the
# packaged build (from source, default_python() is always the serving interpreter).
NO_PYTHON_ERROR = (
    "no Python interpreter selected. The packaged CUFLynx app cannot run "
    "calibration / sensitivity / UQ by itself: those run in a separate process "
    "that needs Python with circulatory_autogen and Myokit installed. Pick one "
    "under Settings -> Python interpreter."
)


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


def default_python() -> str | None:
    """Interpreter to run calibration/sensitivity/UQ runners with, or None.

    From source this is the interpreter serving the API — it has the backend deps,
    so it's a sane default. Frozen, ``sys.executable`` is the bundle itself and
    running it would relaunch the desktop app, so return None: the caller must
    use a discovered interpreter or one the user picked in Settings.

    ``CUFLYNX_PYTHON`` overrides both (handy for the packaged app and for tests).
    """
    override = os.environ.get("CUFLYNX_PYTHON")
    if override:
        return override
    if is_frozen():
        return None
    return sys.executable


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
