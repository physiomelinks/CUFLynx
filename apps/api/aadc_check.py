"""Detect the AADC (Matlogica) library and explain how to obtain it (issue #122).

AADC is optional, proprietary third-party software: circulatory_autogen's
``aadc_python`` model type records the forward integration on a tape and replays
it, giving an exact gradient from one evaluation. CA imports it lazily, so a
machine without it only finds out when a run starts.

CUFLynx therefore offers the format **only when the library is actually
importable**. Surfacing a model format that cannot run is the same mistake the
OpenCOR exclusion exists to avoid (see ``solver_options.UNSUPPORTED_SOLVERS``):
the user picks it, and the failure arrives later and unexplained.

Unlike the C compiler check, this is not a warning banner. A missing compiler
degrades an otherwise-working install; a missing AADC licence simply means one
optional backend is not on the menu, so the format is hidden and the reason is
available in Settings for anyone who wants it.
"""

from __future__ import annotations

import importlib.util
import shutil
import subprocess

# Matlogica offer AADC free for academic use, which is the route most users here
# will take -- hence naming it rather than only saying "not installed".
LICENCE_URL = "https://matlogica.com/"
INSTALL_HINT = (
    "AADC is proprietary software from Matlogica, free for academic use. Request a "
    "licence at https://matlogica.com/, then install the wheel they provide into the "
    "Python you use for analysis runs (Settings -> Python)."
)


def _importable(module: str = "aadc") -> bool:
    """Whether ``module`` can be imported in *this* process, without importing it.

    find_spec rather than import: importing a licensed library can contact a
    licence server, and a capability probe must not have side effects.
    """
    try:
        return importlib.util.find_spec(module) is not None
    except (ImportError, ValueError):
        # A namespace-package clash or a broken install raises rather than
        # returning None; either way it is not usable.
        return False


def _importable_in(python_path: str | None, module: str = "aadc") -> bool | None:
    """Whether ``module`` is importable in another interpreter, or None if unknown.

    Analysis runs happen in the user's own Python (see CLAUDE.md), which is where
    AADC actually has to be installed -- the in-process answer can differ.
    """
    if not python_path:
        return None
    exe = python_path if "/" in python_path or "\\" in python_path else shutil.which(python_path)
    if not exe:
        return None
    try:
        proc = subprocess.run(
            [exe, "-c", f"import importlib.util,sys; sys.exit(0 if importlib.util.find_spec('{module}') else 1)"],
            capture_output=True,
            timeout=15,
        )
        return proc.returncode == 0
    except Exception:  # noqa: BLE001 - a bad interpreter just means "unknown"
        return None


def aadc_status(python_path: str | None = None) -> dict:
    """AADC availability for the Settings UI and the model-format gating.

    ``available`` drives whether ``aadc_python`` is offered at all; it is true
    when either interpreter can import it, because the live engine runs in-process
    while analysis runs in the user's Python and a user may reasonably have it in
    only one.
    """
    here = _importable()
    there = _importable_in(python_path)
    return {
        "available": bool(here or there),
        "in_app": here,
        # None when there is no external interpreter configured, or it could not
        # be probed -- distinct from a definite "not installed".
        "in_analysis_python": there,
        "hint": INSTALL_HINT,
        "licence_url": LICENCE_URL,
    }
