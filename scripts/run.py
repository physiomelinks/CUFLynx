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


def node_cmd(script: str) -> list[str]:
    """yarn <script> if yarn is present, else npm run <script>.

    Uses the resolved ``shutil.which`` path (not the bare name) so the command
    works on Windows, where npm/yarn are ``.CMD`` shims: ``CreateProcess`` does
    not consult PATHEXT, so a bare ``"npm"`` arg fails with WinError 2.
    """
    yarn = shutil.which("yarn")
    if yarn:
        return [yarn, script]
    npm = shutil.which("npm")
    if npm:
        return [npm, "run", script]
    sys.exit(
        "error: neither 'yarn' nor 'npm' is on PATH. Run 'python scripts/install.py' "
        "first (and install Node.js if needed)."
    )


def build_frontend() -> None:
    cmd = node_cmd("build")
    print(f"Building frontend: {' '.join(cmd)}", flush=True)
    subprocess.run(cmd, cwd=str(WEB_DIR), check=True)


def run_dev(port: int, open_browser: bool) -> int:
    """Dev mode: uvicorn --reload (API) + Vite dev server (HMR), both concurrent.

    The Vite dev server (http://localhost:5173) proxies /api to the backend on
    :8000, so run the backend on 8000 for the proxy to line up.
    """
    backend = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "main:app", "--reload", "--port", str(port)],
        cwd=str(API_DIR),
    )
    frontend = subprocess.Popen(node_cmd("dev"), cwd=str(WEB_DIR))
    procs = [backend, frontend]

    if open_browser:
        # Vite serves on 5173; its /api proxy lets the health check pass.
        threading.Thread(
            target=open_when_ready, args=("http://localhost:5173",), daemon=True
        ).start()

    print("Dev mode: API :%d (reload) + Vite :5173 (HMR). Ctrl+C to stop." % port)
    try:
        while all(p.poll() is None for p in procs):
            time.sleep(0.3)
    except KeyboardInterrupt:
        pass
    finally:
        for p in procs:
            if p.poll() is None:
                p.terminate()
        for p in procs:
            try:
                p.wait(timeout=5)
            except Exception:  # noqa: BLE001
                p.kill()
    return 0


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
    parser.add_argument(
        "--dev",
        action="store_true",
        help="dev mode: uvicorn --reload + Vite dev server (HMR) on :5173",
    )
    args = parser.parse_args()

    if args.dev:
        return run_dev(args.port, not args.no_browser)

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
