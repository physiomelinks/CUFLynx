"""CUFLynx desktop shell — a native window around the existing web app.

This is deliberately a *thin* wrapper, not a second frontend. It starts the same
uvicorn + ``main:app`` server the browser deployment uses, then points a pywebview
window at ``http://127.0.0.1:<port>``. It uses **no** pywebview JS bridge and no
``window.pywebview`` APIs, so the frontend stays a plain web app: dropping this
shell (or running ``--browser``) gives you the ordinary served-web-app experience
with nothing to unpick. See "Desktop packaging" in CLAUDE.md.

    python apps/desktop/app.py              # native window
    python apps/desktop/app.py --browser    # serve only, open the system browser
    python apps/desktop/app.py --port 8000  # pin the port (default: a free one)

Frozen into a single executable by ``scripts/package.py``.
"""

from __future__ import annotations

import argparse
import multiprocessing
import os
import socket
import sys
import threading
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path

APP_NAME = "CUFLynx"

# Import the API package the same way whether we're frozen or running from source.
# Frozen, PyInstaller puts apps/api's modules on sys.path already (see the spec's
# `pathex`); from source we add it here so `import main` resolves.
if not getattr(sys, "frozen", False):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "api"))


def _run_analysis_runner(script: str, config_path: str) -> None:
    """Runner mode: execute an analysis runner in *this* (bundled) interpreter.

    The packaged app spawns sensitivity/calibration/UQ by re-invoking itself with
    ``RUNNER_MODE_FLAG`` (see runtime_paths.runner_command) instead of an external
    Python, so the analysis runs against the bundle's own dependencies — no user
    Python setup needed. This replicates ``python runner.py <config>``: the runner
    reads ``sys.argv[1]`` and imports its siblings (e.g. local_sensitivity) from
    its own directory, so put that directory on the path.
    """
    import runpy

    runner_dir = os.path.dirname(script)
    if runner_dir and runner_dir not in sys.path:
        sys.path.insert(0, runner_dir)
    sys.argv = [script, config_path]
    # The runner's `if __name__ == "__main__"` block raises SystemExit(code); let
    # that propagate so the parent sees the runner's exit status.
    runpy.run_path(script, run_name="__main__")


def free_port() -> int:
    """An OS-assigned free port.

    Binding to port 0 and reading back the assignment leaves a small race (the
    port is free when we release it, but could in principle be taken before
    uvicorn binds). That's acceptable for a single-user desktop app and avoids
    the worse failure of a hardcoded port already being in use.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def wait_for_health(url: str, timeout: float = 60.0) -> bool:
    """Poll ``/api/health`` until the server answers, or give up."""
    import time

    deadline = time.monotonic() + timeout
    health = f"{url}/api/health"
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(health, timeout=1) as resp:
                if resp.status == 200:
                    return True
        except (urllib.error.URLError, OSError, TimeoutError):
            time.sleep(0.15)
    return False


def start_server(port: int):
    """Run uvicorn on a daemon thread; return the server so we can stop it."""
    import uvicorn
    from main import app  # noqa: PLC0415 - deferred so sys.path is set up first

    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(config)
    threading.Thread(target=server.run, daemon=True).start()
    return server


def warn_if_no_compiler() -> None:
    """Print the compiler hint to the console at startup.

    The UI also shows this (GET /api/config -> cpp_compiler), but a packaged app
    is often launched with no console in view, so the in-app banner is the one
    users actually see. This is the belt-and-braces copy for terminal launches.
    """
    try:
        from compiler_check import compiler_hint, has_cpp_compiler
    except ImportError:
        return
    if has_cpp_compiler():
        return
    print(
        "\nWARNING: no C compiler found — the Myokit CVODE solver is unavailable.\n"
        "  Myokit compiles each model to a native extension at run time. The other\n"
        "  backends need no compiler: choose 'python' (scipy solve_ivp) or\n"
        "  'casadi_python' (CasADi) under Settings.\n\n"
        f"  To enable CVODE_myokit:\n{compiler_hint()}\n",
        file=sys.stderr,
        flush=True,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=f"{APP_NAME} desktop app")
    parser.add_argument("--port", type=int, default=0, help="0 = pick a free port")
    parser.add_argument(
        "--browser",
        action="store_true",
        help="serve only and open the system browser instead of a native window",
    )
    args = parser.parse_args()

    port = args.port or free_port()
    url = f"http://127.0.0.1:{port}"

    warn_if_no_compiler()
    server = start_server(port)
    if not wait_for_health(url):
        print(f"error: {APP_NAME} server did not start on {url}", file=sys.stderr)
        return 1

    if args.browser:
        webbrowser.open(url)
        print(f"Serving {APP_NAME} on {url}  (Ctrl+C to stop)", flush=True)
        try:
            threading.Event().wait()
        except KeyboardInterrupt:
            pass
        return 0

    try:
        import webview
    except ImportError:
        print(
            "error: pywebview is not installed — falling back to the browser.\n"
            "  Install it with: pip install pywebview",
            file=sys.stderr,
        )
        webbrowser.open(url)
        threading.Event().wait()
        return 0

    webview.create_window(APP_NAME, url, width=1400, height=900)
    try:
        webview.start()
    except Exception as exc:  # noqa: BLE001
        # No usable GUI backend (headless box, missing GTK/Qt/WebKit). Serving
        # already works, so degrade to the browser rather than dying.
        print(
            f"warning: could not open a native window ({exc}); opening a browser instead.",
            file=sys.stderr,
        )
        webbrowser.open(url)
        threading.Event().wait()
        return 0

    # The window closed — stop uvicorn so the process actually exits.
    server.should_exit = True
    return 0


if __name__ == "__main__":
    # Required before any threads/processes when frozen: PyInstaller re-executes
    # the bundle for each child process, and without this a spawned child would
    # relaunch the whole GUI instead of running its worker payload.
    multiprocessing.freeze_support()
    os.environ.setdefault("MPLBACKEND", "Agg")

    # Runner mode: the app spawned itself to run an analysis runner in-process
    # (see runtime_paths.runner_command). Handle it before argparse — the flag and
    # its args aren't the GUI's options. argv: [FLAG, runner_script, config_path].
    from runtime_paths import RUNNER_MODE_FLAG  # noqa: PLC0415

    if len(sys.argv) >= 2 and sys.argv[1] == RUNNER_MODE_FLAG:
        _run_analysis_runner(sys.argv[2], sys.argv[3])
    else:
        raise SystemExit(main())
