#!/usr/bin/env python3
"""One-time setup for CUFLynx — cross-platform (Linux / macOS / Windows).

Installs the backend Python deps and the frontend, then builds the frontend so
the app can run as a single server. Run with the Python you want to serve from:

    python scripts/install.py

The interpreter you use here is the one that gets the backend deps; use the same
one with ``scripts/run.py``. (Calibration / sensitivity / UQ runs use a separate
interpreter chosen in the app's top bar — point that at your circulatory_autogen
venv.)
"""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
API_DIR = ROOT / "apps" / "api"
WEB_DIR = ROOT / "apps" / "web"

# Per-OS hint for installing an MPI runtime that provides `mpiexec`. MPI is only
# needed for multi-core calibration / sensitivity / UQ runs (num_cores > 1);
# without it those runs transparently fall back to a single core.
_MPI_INSTALL_HINTS = {
    "Windows": (
        "Install Microsoft MPI (both the runtime and the SDK):\n"
        "      winget install Microsoft.MPI\n"
        "    or download from "
        "https://www.microsoft.com/en-us/download/details.aspx?id=57467\n"
        "    then reopen your terminal so mpiexec is on PATH."
    ),
    "Linux": (
        "Install OpenMPI (or MPICH), e.g.:\n"
        "      sudo apt install openmpi-bin        # Debian/Ubuntu\n"
        "      sudo dnf install openmpi            # Fedora/RHEL"
    ),
    "Darwin": (
        "Install OpenMPI via Homebrew:\n"
        "      brew install open-mpi"
    ),
}


def warn_if_no_mpiexec() -> None:
    """Warn (non-fatally) when ``mpiexec`` is absent, with install instructions.

    Multi-core runs need an MPI runtime; single-core runs do not. The app falls
    back to a single core when mpiexec is missing, so this is a warning, not an
    error.
    """
    if shutil.which("mpiexec"):
        return
    hint = _MPI_INSTALL_HINTS.get(
        platform.system(), "Install an MPI runtime that provides 'mpiexec'."
    )
    # ASCII only: a Windows console (cp1252) raises UnicodeEncodeError on
    # non-ASCII glyphs, which would crash the installer.
    print(
        "\nWARNING: 'mpiexec' was not found on PATH.\n"
        "  Multi-core (num_cores > 1) calibration / sensitivity / UQ runs need MPI;\n"
        "  without it those runs fall back to a single core (slower, still correct).\n"
        f"  To enable parallel runs:\n    {hint}",
        flush=True,
    )


# The C/C++ build tools Myokit needs to JIT-compile models at run time. Kept as
# a module constant so the prompt text and the actual command can't drift apart.
_VC_TOOLS_COMPONENT = "Microsoft.VisualStudio.Component.VC.Tools.x86.x64"
_BUILD_TOOLS_WINGET_ID = "Microsoft.VisualStudio.2022.BuildTools"
_BUILD_TOOLS_OVERRIDE = (
    "--quiet --wait --norestart "
    "--add Microsoft.VisualStudio.Workload.VCTools --includeRecommended"
)


def _has_cpp_compiler() -> bool:
    """True if an MSVC C/C++ compiler is discoverable on Windows.

    Checks ``cl.exe`` on PATH first, then asks ``vswhere`` whether any install
    provides the VC tools component (the way setuptools/Myokit locate it).
    """
    if shutil.which("cl"):
        return True
    program_files_x86 = os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")
    vswhere = Path(program_files_x86) / "Microsoft Visual Studio" / "Installer" / "vswhere.exe"
    if vswhere.is_file():
        try:
            out = subprocess.run(
                [str(vswhere), "-products", "*", "-latest",
                 "-requires", _VC_TOOLS_COMPONENT,
                 "-property", "installationPath"],
                capture_output=True, text=True, timeout=30,
            )
            if out.stdout.strip():
                return True
        except Exception:  # noqa: BLE001 - if vswhere misbehaves, treat as absent
            pass
    return False


def ensure_cpp_build_tools() -> None:
    """On Windows, offer to install the MSVC C++ Build Tools if none are present.

    Myokit's CVODE solver compiles each model into a native extension at run
    time and needs a C/C++ compiler; without one, ``/api/simulate`` (and any real
    simulation) fails with an HTTP 500. Prompts before installing (default: No),
    since the Build Tools are a multi-GB download.
    """
    if platform.system() != "Windows":
        return  # Linux/macOS ship a usable C compiler via the OS / Xcode CLT.
    if _has_cpp_compiler():
        return

    print(
        "\nWARNING: no MSVC C/C++ compiler found.\n"
        "  Myokit (the CVODE solver) compiles each model to a native extension at\n"
        "  run time and needs the Microsoft C++ Build Tools. Without them, model\n"
        "  simulation fails (the API returns HTTP 500).",
        flush=True,
    )

    winget = shutil.which("winget")
    if winget is None:
        print(
            "  winget is unavailable; install the Build Tools manually:\n"
            "    https://visualstudio.microsoft.com/visual-cpp-build-tools/\n"
            "    (select the 'Desktop development with C++' workload).",
            flush=True,
        )
        return

    try:
        answer = input(
            "  Install Microsoft C++ Build Tools now via winget? [y/N] "
        ).strip().lower()
    except EOFError:  # non-interactive stdin -> default to No
        answer = ""
    if answer not in ("y", "yes"):
        print(
            "  Skipped. Install later from "
            "https://visualstudio.microsoft.com/visual-cpp-build-tools/",
            flush=True,
        )
        return

    cmd = [
        winget, "install", "--id", _BUILD_TOOLS_WINGET_ID,
        "--accept-package-agreements", "--accept-source-agreements",
        "--override", _BUILD_TOOLS_OVERRIDE,
    ]
    print(f"\n$ {' '.join(cmd)}", flush=True)
    result = subprocess.run(cmd, check=False)
    if result.returncode == 0:
        print(
            "  Build Tools installed. Restart your terminal (and the app) so the\n"
            "  compiler is picked up.",
            flush=True,
        )
    else:
        print(
            f"  winget exited with code {result.returncode}. If the install did not\n"
            "  complete, try again or install manually from\n"
            "    https://visualstudio.microsoft.com/visual-cpp-build-tools/",
            flush=True,
        )


def run(cmd: list[str], cwd: Path) -> None:
    print(f"\n$ {' '.join(cmd)}   (in {cwd})", flush=True)
    subprocess.run(cmd, cwd=str(cwd), check=True)


def node_tool() -> tuple[list[str], list[str]]:
    """Return (install_cmd, build_cmd) for yarn if present, else npm.

    Uses the resolved ``shutil.which`` path (not the bare name) so the command
    works on Windows, where npm/yarn are ``.CMD`` shims: ``CreateProcess`` does
    not consult PATHEXT, so a bare ``"npm"`` arg fails with WinError 2.
    """
    yarn = shutil.which("yarn")
    if yarn:
        return ([yarn], [yarn, "build"])
    npm = shutil.which("npm")
    if npm:
        return ([npm, "install"], [npm, "run", "build"])
    sys.exit(
        "error: neither 'yarn' nor 'npm' found on PATH. Install Node.js "
        "(https://nodejs.org) — it's only needed to build the frontend."
    )


def main() -> int:
    print(f"Using Python: {sys.executable}")

    # Backend deps (fastapi, numpy, myokit, libcellml, ...).
    run([sys.executable, "-m", "pip", "install", "-e", ".[dev]"], cwd=API_DIR)

    # Myokit JIT-compiles models at run time; on Windows that needs a C/C++
    # compiler. Offer to install the Build Tools (prompted) if none is found.
    ensure_cpp_build_tools()

    # Frontend: install tooling + build the production bundle.
    install_cmd, build_cmd = node_tool()
    run(install_cmd, cwd=WEB_DIR)
    run(build_cmd, cwd=WEB_DIR)

    # Optional: MPI enables multi-core runs. Warn (don't fail) if it's missing.
    warn_if_no_mpiexec()

    print("\nSetup complete. Start the app with:  python scripts/run.py")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except subprocess.CalledProcessError as exc:
        sys.exit(f"\nerror: command failed ({exc.returncode}): {' '.join(exc.cmd)}")
