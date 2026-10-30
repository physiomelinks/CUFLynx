#!/usr/bin/env python3
"""Build the CUFLynx desktop app: one double-clickable executable.

    python scripts/package.py                # build frontend + freeze
    python scripts/package.py --no-build     # reuse the existing apps/web/dist
    python scripts/package.py --clean        # wipe build/ and dist/ first

Output: ``dist/CUFLynx`` (``dist/CUFLynx.exe`` on Windows).

PyInstaller cannot cross-compile: run this on the OS you want to ship for. The
release workflow (.github/workflows/release.yml) does exactly that on Linux,
macOS and Windows runners.

The build interpreter's installed packages are what get frozen, so run this in an
environment that has the app's deps *and* the simulation stack (myokit, libcellml,
casadi) — see packaging/cuflynx.spec for why. ``scripts/install.py`` sets that up.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WEB_DIR = ROOT / "apps" / "web"
SPEC = ROOT / "packaging" / "cuflynx.spec"
DIST = ROOT / "dist"
BUILD = ROOT / "build"

# Needed to build, but not to run the app from source, so they're not in
# apps/api/pyproject.toml's runtime deps.
BUILD_REQUIREMENTS = ["pyinstaller>=6.0", "pywebview>=5.0"]


def node_cmd(script: str) -> list[str]:
    """yarn <script> if available, else npm run <script> (resolved via which, so
    Windows' .CMD shims work — CreateProcess doesn't consult PATHEXT)."""
    yarn = shutil.which("yarn")
    if yarn:
        return [yarn, script]
    npm = shutil.which("npm")
    if npm:
        return [npm, "run", script]
    sys.exit(
        "error: neither 'yarn' nor 'npm' is on PATH. Install Node.js — it's only "
        "needed to build the frontend."
    )


def ensure_build_deps() -> None:
    """Install PyInstaller / pywebview into the *building* interpreter if absent."""
    missing = []
    for mod, req in (("PyInstaller", BUILD_REQUIREMENTS[0]), ("webview", BUILD_REQUIREMENTS[1])):
        try:
            __import__(mod)
        except ImportError:
            missing.append(req)
    if not missing:
        return
    print(f"Installing build deps: {' '.join(missing)}", flush=True)
    subprocess.run([sys.executable, "-m", "pip", "install", *missing], check=True)


def check_mpi_is_freezable() -> None:
    """Refuse to build against an MPI that cannot survive being frozen (#330).

    A locally built macOS app aborted on its first simulation with

        The MPI_Comm_dup() function was called before MPI_INIT was invoked
        Local abort before MPI_INIT completed

    while the *downloaded* build of the same version worked. That wording is
    OpenMPI's, and the asymmetry is the whole diagnosis: CI installs
    ``.[analysis]``, which brings the pip ``mpich`` wheel, and MPICH is a single
    shared library. OpenMPI instead loads its MCA components as separate plugins
    from ``<prefix>/lib/openmpi/*.so``, and PyInstaller does not collect them --
    so inside the bundle ``MPI_Init`` cannot complete and the next MPI call
    aborts exactly like that.

    The failure is therefore invisible at build time and fatal at run time, on a
    toolchain that works perfectly when running from source. That is precisely
    the shape the spec already refuses to ship for casadi, Sundials and
    ``Python.h``: fail here, where the fix is one ``pip install``, rather than in
    a user's hands.

    A warning rather than a hard error when MPI cannot be inspected at all: the
    bundle is usable single-core without MPI, and this must not block a build
    that never wanted it.
    """
    try:
        from mpi4py import MPI  # noqa: PLC0415 - optional, and only needed here
    except Exception as exc:  # noqa: BLE001 - no mpi4py is a legitimate build
        print(
            f"note: mpi4py is not importable ({exc.__class__.__name__}), so this build "
            "will be single-core only.",
            flush=True,
        )
        return

    try:
        library = MPI.Get_library_version().strip().replace("\x00", "")
    except Exception as exc:  # noqa: BLE001
        print(f"warning: could not identify the MPI library ({exc}).", flush=True)
        return

    first = library.splitlines()[0] if library else "(unknown)"
    print(f"MPI: {first}", flush=True)

    if "open mpi" in library.lower() or "open-mpi" in library.lower():
        sys.exit(
            "error: mpi4py in this environment is linked against Open MPI, which does "
            "not survive PyInstaller.\n"
            "  Open MPI loads its MCA components as plugins from <prefix>/lib/openmpi,\n"
            "  which are not collected into the bundle, so the built app dies at the\n"
            "  first simulation with 'MPI_Comm_dup() ... called before MPI_INIT' (#330)\n"
            "  -- even though running from source works fine.\n"
            "\n"
            "  Use the self-contained MPICH wheel the released builds use:\n"
            "      pip install mpich\n"
            "      pip install --force-reinstall --no-cache-dir --no-binary=mpi4py mpi4py\n"
            "  (the second line rebuilds mpi4py against it), then re-run this script."
        )


def main() -> int:
    parser = argparse.ArgumentParser(description="Package CUFLynx as a desktop app.")
    parser.add_argument(
        "--no-build", action="store_true", help="reuse the existing apps/web/dist"
    )
    parser.add_argument(
        "--clean", action="store_true", help="remove build/ and dist/ before building"
    )
    args = parser.parse_args()

    if args.clean:
        for d in (BUILD, DIST):
            if d.is_dir():
                print(f"Removing {d}", flush=True)
                shutil.rmtree(d)

    ensure_build_deps()
    check_mpi_is_freezable()

    if not args.no_build:
        cmd = node_cmd("build")
        print(f"Building frontend: {' '.join(cmd)}", flush=True)
        subprocess.run(cmd, cwd=str(WEB_DIR), check=True)

    print("Freezing with PyInstaller...", flush=True)
    subprocess.run(
        [
            sys.executable, "-m", "PyInstaller",
            "--noconfirm",
            "--distpath", str(DIST),
            "--workpath", str(BUILD),
            str(SPEC),
        ],
        cwd=str(ROOT),
        check=True,
    )

    exe = DIST / ("CUFLynx.exe" if sys.platform == "win32" else "CUFLynx")
    if not exe.exists():
        sys.exit(f"error: expected executable not found at {exe}")
    size_mb = exe.stat().st_size / (1024 * 1024)
    print(f"\nBuilt {exe}  ({size_mb:.0f} MB)\nDouble-click it, or run it from a terminal.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except subprocess.CalledProcessError as exc:
        sys.exit(f"\nerror: command failed ({exc.returncode}): {' '.join(map(str, exc.cmd))}")
