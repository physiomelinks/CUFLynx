"""Detect the C/C++ compiler Myokit needs, and explain how to install it.

A missing compiler is a **limitation, not a fatal error**. Only the Myokit
backend compiles: ``CVODE_myokit`` (generated_model_format ``cellml``) turns
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

import functools
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
        "  sudo dnf groupinstall 'Development Tools'   # Fedora/RHEL\n"
        "Then restart CUFLynx so the compiler is picked up."
    ),
    # `xcode-select -p` matters as much as the install: macOS ships /usr/bin/clang
    # as an xcrun shim whatever happens, so the toolchain can also be "installed"
    # yet unusable because the active developer directory points at an Xcode that
    # has since been deleted. Both states fail identically, and both are fixed
    # from here.
    "Darwin": (
        "Install the Xcode command-line tools:\n"
        "  xcode-select --install\n"
        "If they are already installed, check that the active developer directory "
        "still exists:\n"
        "  xcode-select -p            # then, if it names a missing folder:\n"
        "  sudo xcode-select --reset\n"
        "Then restart CUFLynx so the compiler is picked up."
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


def _compiler_runs(path: str) -> bool:
    """True if this compiler binary actually runs.

    ``shutil.which`` is not enough on macOS, and that is not a detail: ``/usr/bin/cc``,
    ``/usr/bin/gcc`` and ``/usr/bin/clang`` are shipped by macOS **itself** as
    ``xcrun`` shims and are therefore *always* present, whether or not any toolchain
    is installed behind them. With none installed they exit 1 with

        xcode-select: note: No developer tools were found, requesting install.

    so a which-only check reports a compiler on a machine that cannot compile. The
    app then offered CVODE_myokit and the run died much later, inside distutils,
    with the opaque

        DistutilsExecError: command '/usr/bin/clang' failed with exit code 1

    and no console for a packaged app to print the reason to. Running the thing is
    the only question worth asking; ``--version`` is the cheapest way to ask it
    (~50 ms, and the broken shims fail it immediately).
    """
    try:
        proc = subprocess.run(
            [path, "--version"], capture_output=True, timeout=30, check=False
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return proc.returncode == 0


@functools.lru_cache(maxsize=1)
def has_cpp_compiler() -> bool:
    """True if Myokit will be able to compile a model on this machine.

    Cached: ``GET /api/config`` asks on every settings open, and this spawns
    processes. A toolchain installed while the app is running therefore needs a
    restart to be picked up, which is what every ``_HINTS`` entry now says.
    """
    if platform.system() == "Windows":
        return _has_msvc()
    return any(
        _compiler_runs(path)
        for cc in ("cc", "gcc", "clang")
        if (path := shutil.which(cc))
    )


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
        "affects": "" if present else "CVODE_myokit (generated model format 'cellml')",
        "alternatives": [] if present else [dict(b) for b in COMPILER_FREE_BACKENDS],
    }
