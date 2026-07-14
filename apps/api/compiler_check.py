"""Detect the C/C++ compiler Myokit needs, and explain how to install it.

A missing compiler is a **limitation, not a fatal error**. Only the Myokit
backend compiles: ``CVODE_myokit`` (generated_model_format ``cellml_only``) turns
each model into a native extension *at run time*. The other backends are pure
Python / precompiled and need no toolchain:

    python        -> solve_ivp          (scipy)
    casadi_python -> casadi_integrator  (casadi)

(Confirmed against circulatory_autogen: of ``src/solver_wrappers/*``, only
``myokit_helper.py`` compiles anything.)

So the app warns and points at those alternatives rather than pretending it's
broken. Freezing with PyInstaller can't bundle a compiler away, which is why the
packaged build detects this at startup instead of failing later with an opaque
HTTP 500 on the first simulation.

Shared by ``scripts/install.py`` (prompt to install) and ``GET /api/config``
(in-app banner), so the two can't drift.
"""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
from pathlib import Path

# The MSVC component that provides cl.exe, as setuptools/Myokit locate it.
VC_TOOLS_COMPONENT = "Microsoft.VisualStudio.Component.VC.Tools.x86.x64"

_HINTS = {
    "Windows": (
        "Install the Microsoft C++ Build Tools (select the 'Desktop development "
        "with C++' workload):\n"
        "  winget install --id Microsoft.VisualStudio.2022.BuildTools\n"
        "or download from https://visualstudio.microsoft.com/visual-cpp-build-tools/\n"
        "Then restart CUFLynx so the compiler is picked up."
    ),
    "Linux": (
        "Install a C compiler, e.g.:\n"
        "  sudo apt install build-essential   # Debian/Ubuntu\n"
        "  sudo dnf groupinstall 'Development Tools'   # Fedora/RHEL"
    ),
    "Darwin": (
        "Install the Xcode command-line tools:\n"
        "  xcode-select --install"
    ),
}


def _has_msvc() -> bool:
    """True if an MSVC C/C++ compiler is discoverable on Windows.

    Checks ``cl.exe`` on PATH first, then asks ``vswhere`` whether any install
    provides the VC tools component (an installed-but-not-on-PATH MSVC still
    works for Myokit, which locates it via setuptools).
    """
    if shutil.which("cl"):
        return True
    program_files_x86 = os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")
    vswhere = (
        Path(program_files_x86) / "Microsoft Visual Studio" / "Installer" / "vswhere.exe"
    )
    if not vswhere.is_file():
        return False
    try:
        out = subprocess.run(
            [
                str(vswhere), "-products", "*", "-latest",
                "-requires", VC_TOOLS_COMPONENT,
                "-property", "installationPath",
            ],
            capture_output=True, text=True, timeout=30,
        )
        return bool(out.stdout.strip())
    except Exception:  # noqa: BLE001 - if vswhere misbehaves, treat as absent
        return False


def has_cpp_compiler() -> bool:
    """True if Myokit will be able to compile a model on this machine."""
    if platform.system() == "Windows":
        return _has_msvc()
    return any(shutil.which(cc) for cc in ("cc", "gcc", "clang"))


def compiler_hint() -> str:
    """Per-OS instructions for installing the missing compiler."""
    return _HINTS.get(
        platform.system(), "Install a C compiler that Python can use to build extensions."
    )


# The backends that work without any C toolchain, for the "you can still..." half
# of the warning. Names match CA's SOLVER_SCHEMA (see solver_options.py).
COMPILER_FREE_BACKENDS = (
    {"generated_model_format": "python", "solver": "solve_ivp", "label": "Python (scipy solve_ivp)"},
    {"generated_model_format": "casadi_python", "solver": "casadi_integrator", "label": "CasADi"},
)


def compiler_status() -> dict:
    """Compiler availability, install hint, and what still works without it.

    Consumed by ``GET /api/config``; drives a *warning* (not an error) banner —
    only the Myokit/CVODE backend is blocked, so ``affects`` says what's lost and
    ``alternatives`` says what to use instead.
    """
    present = has_cpp_compiler()
    return {
        "present": present,
        "hint": "" if present else compiler_hint(),
        # Only this backend JIT-compiles; everything else is unaffected.
        "affects": "" if present else "CVODE_myokit (generated model format 'cellml_only')",
        "alternatives": [] if present else [dict(b) for b in COMPILER_FREE_BACKENDS],
    }
