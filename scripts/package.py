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
