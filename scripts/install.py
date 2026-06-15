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

import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
API_DIR = ROOT / "apps" / "api"
WEB_DIR = ROOT / "apps" / "web"


def run(cmd: list[str], cwd: Path) -> None:
    print(f"\n$ {' '.join(cmd)}   (in {cwd})", flush=True)
    subprocess.run(cmd, cwd=str(cwd), check=True)


def node_tool() -> tuple[list[str], list[str]]:
    """Return (install_cmd, build_cmd) for yarn if present, else npm."""
    if shutil.which("yarn"):
        return (["yarn"], ["yarn", "build"])
    if shutil.which("npm"):
        return (["npm", "install"], ["npm", "run", "build"])
    sys.exit(
        "error: neither 'yarn' nor 'npm' found on PATH. Install Node.js "
        "(https://nodejs.org) — it's only needed to build the frontend."
    )


def main() -> int:
    print(f"Using Python: {sys.executable}")

    # Backend deps (fastapi, numpy, myokit, libcellml, ...).
    run([sys.executable, "-m", "pip", "install", "-e", ".[dev]"], cwd=API_DIR)

    # Frontend: install tooling + build the production bundle.
    install_cmd, build_cmd = node_tool()
    run(install_cmd, cwd=WEB_DIR)
    run(build_cmd, cwd=WEB_DIR)

    print("\n✔ Setup complete. Start the app with:  python scripts/run.py")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except subprocess.CalledProcessError as exc:
        sys.exit(f"\nerror: command failed ({exc.returncode}): {' '.join(exc.cmd)}")
