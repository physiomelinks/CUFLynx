#!/usr/bin/env python3
"""Run CUFLynx as a single server and open it in the browser.

Cross-platform (Linux / macOS / Windows): just needs Python + the deps from
scripts/install.py. The frontend is built automatically if it hasn't been yet.

    python scripts/run.py                 # build if needed, serve, open browser
    python scripts/run.py --port 9000     # different port
    python scripts/run.py --build         # force a fresh frontend build first
    python scripts/run.py --no-browser    # don't auto-open the browser

Stop with Ctrl+C. The same interpreter you run this with serves the API.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import threading
import time
import urllib.request
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
API_DIR = ROOT / "apps" / "api"
WEB_DIR = ROOT / "apps" / "web"
DIST = WEB_DIR / "dist"


def build_frontend() -> None:
    if shutil.which("yarn"):
        cmd = ["yarn", "build"]
    elif shutil.which("npm"):
        cmd = ["npm", "run", "build"]
    else:
        sys.exit(
            "error: frontend not built and neither 'yarn' nor 'npm' is on PATH. "
            "Run 'python scripts/install.py' first."
        )
    print(f"Building frontend: {' '.join(cmd)}", flush=True)
    subprocess.run(cmd, cwd=str(WEB_DIR), check=True)


def open_when_ready(url: str) -> None:
    """Poll the health endpoint, then open the browser once the server is up."""
    health = url + "/api/health"
    for _ in range(120):  # ~60s
        try:
            with urllib.request.urlopen(health, timeout=1) as resp:
                if resp.status == 200:
                    webbrowser.open(url)
                    return
        except Exception:  # noqa: BLE001 - server not up yet
            time.sleep(0.5)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run CUFLynx (single server).")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--build", action="store_true", help="force a fresh build")
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()

    if args.build or not DIST.is_dir():
        build_frontend()

    url = f"http://localhost:{args.port}"
    if not args.no_browser:
        threading.Thread(target=open_when_ready, args=(url,), daemon=True).start()

    print(f"Serving CUFLynx on {url}  (Ctrl+C to stop)", flush=True)
    cmd = [sys.executable, "-m", "uvicorn", "main:app", "--port", str(args.port)]
    try:
        return subprocess.run(cmd, cwd=str(API_DIR)).returncode
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except subprocess.CalledProcessError as exc:
        sys.exit(f"\nerror: command failed ({exc.returncode}): {' '.join(exc.cmd)}")
