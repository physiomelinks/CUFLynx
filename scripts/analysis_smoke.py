#!/usr/bin/env python3
"""End-to-end smoke test for the analysis tier in the *built* CUFLynx app.

The live-simulation tier runs in-process, but sensitivity / calibration / UQ run
in a **separate interpreter** the app spawns (Settings -> Python interpreter). In
the packaged app that interpreter can't default to ``sys.executable`` (it's the
frozen bundle), so the whole orchestration path — resolve interpreter, spawn the
runner, stream progress, detect completion — is frozen-specific and can only be
exercised against a real built binary. This driver does exactly that.

It launches the given binary, points it at a circulatory_autogen checkout and a
runner interpreter (as a user does in the top bar), uploads the 3-compartment
fixture, then runs a short **local sensitivity** and a short **calibration** to
completion — asserting each reaches state ``done`` with sane results.

    python scripts/analysis_smoke.py \
        --binary dist/CUFLynx \
        --ca-dir ../circulatory_autogen \
        --runner-python /path/to/python-with-CA-deps

``--runner-python`` needs circulatory_autogen and its analysis dependencies
(emcee / SALib / nevergrad / myokit / ...) importable. Omit it to fall back to the
``CUFLYNX_PYTHON`` env var. Exit code is non-zero on any failure, with the runner
log printed, so it plugs straight into CI (see .github/workflows/release.yml).
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

# The flag runtime_paths.RUNNER_MODE_FLAG uses to re-invoke the frozen bundle as
# an analysis runner. Kept in sync with apps/api/runtime_paths.py.
RUNNER_MODE_FLAG = "--_cuflynx-run-analysis"

ROOT = Path(__file__).resolve().parents[1]
RESOURCES = ROOT / "resources"
MODEL = RESOURCES / "3compartment_flat.cellml"
OBS = RESOURCES / "3compartment_obs_data.json"
PARAMS = RESOURCES / "3compartment_params_for_id.csv"


def _req(method: str, url: str, *, data=None, headers=None, timeout=30):
    body = json.dumps(data).encode() if data is not None else None
    hdrs = {"Content-Type": "application/json"} if data is not None else {}
    hdrs.update(headers or {})
    req = urllib.request.Request(url, data=body, headers=hdrs, method=method)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.status, json.loads(resp.read().decode())


def _multipart(url: str, field: str, path: Path, extra: dict, timeout=60):
    """Minimal multipart/form-data POST (stdlib only) — mirrors the UI upload."""
    boundary = "----cuflynxsmoke"
    parts: list[bytes] = []
    for k, v in extra.items():
        parts.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n{v}\r\n".encode())
    filename = path.name
    parts.append(
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"{field}\"; "
        f"filename=\"{filename}\"\r\nContent-Type: application/octet-stream\r\n\r\n".encode()
    )
    parts.append(path.read_bytes())
    parts.append(f"\r\n--{boundary}--\r\n".encode())
    body = b"".join(parts)
    req = urllib.request.Request(
        url, data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.status, json.loads(resp.read().decode())


def _wait_health(base: str, timeout=90) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            status, _ = _req("GET", f"{base}/api/health", timeout=2)
            if status == 200:
                return True
        except (urllib.error.URLError, OSError, ValueError):
            time.sleep(0.5)
    return False


def _poll_job(base: str, kind: str, job_id: str, timeout: int) -> dict:
    """Poll /api/<kind>/<id>/status until it leaves 'running'. Returns the final
    status; raises with the captured runner log on timeout/error."""
    offset = 0
    lines: list[str] = []
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        _, s = _req("GET", f"{base}/api/{kind}/{job_id}/status?offset={offset}")
        lines += s.get("lines", [])
        offset = s.get("next_offset", offset)
        if s.get("state") != "running":
            s["_lines"] = lines
            return s
        time.sleep(1.0)
    raise TimeoutError(f"{kind} job {job_id} did not finish in {timeout}s\n" + "\n".join(lines))


def _fail(msg: str, lines: list[str] | None = None) -> None:
    print(f"\nANALYSIS SMOKE FAILED: {msg}", file=sys.stderr)
    if lines:
        print("--- runner log (tail) ---", file=sys.stderr)
        print("\n".join(lines[-40:]), file=sys.stderr)
    sys.exit(1)


def _check_bundled_scipy_data(binary: str) -> None:
    """Guard that scipy's runtime *data files* are in the bundle.

    scipy.stats.qmc.Sobol reads scipy/stats/_sobol_direction_numbers.npz at
    construction; if the spec doesn't collect scipy's data files that .npz is
    absent and a Sobol run (e.g. multi-start with start_sampling='sobol') fails
    with FileNotFoundError. scipy *ignores* that exception, so it never surfaces
    as a job failure -- which is exactly how it shipped unnoticed. Probe it
    directly by re-invoking the bundle in runner mode on a tiny script.
    """
    with tempfile.TemporaryDirectory() as td:
        probe = Path(td) / "sobol_probe.py"
        probe.write_text(
            "from scipy.stats import qmc\n"
            "pts = qmc.Sobol(d=3, scramble=False).random(4)\n"
            # point index 1 of an unscrambled Sobol sequence is [.5,.5,.5]; it is
            # 0 if the direction numbers failed to load -> proves the .npz loaded.
            "assert abs(float(pts[1].sum()) - 1.5) < 1e-9, f'bad Sobol pts: {pts.tolist()}'\n"
            "print('SCIPY_SOBOL_OK')\n"
        )
        cfg = Path(td) / "cfg.json"  # runner mode expects a config-path argv[2]
        cfg.write_text("{}")
        out = subprocess.run(
            [binary, RUNNER_MODE_FLAG, str(probe), str(cfg)],
            capture_output=True, text=True, timeout=120,
        )
        combined = out.stdout + out.stderr
        if "SCIPY_SOBOL_OK" not in combined:
            _fail("scipy Sobol probe failed in the built app (missing bundled "
                  "scipy data file?)", combined.splitlines())
        print("scipy data OK (Sobol direction numbers load in the bundle)")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--binary", required=True, help="path to the built CUFLynx executable")
    ap.add_argument("--ca-dir", required=True, help="circulatory_autogen checkout")
    ap.add_argument("--runner-python", default=None,
                    help="interpreter for analysis runs (has CA + analysis deps); "
                         "defaults to the CUFLYNX_PYTHON env var")
    ap.add_argument("--port", type=int, default=8777)
    ap.add_argument("--calibration-timeout", type=int, default=600)
    ap.add_argument("--sensitivity-timeout", type=int, default=600)
    args = ap.parse_args()

    base = f"http://127.0.0.1:{args.port}"
    for p in (MODEL, OBS, PARAMS):
        if not p.is_file():
            _fail(f"fixture missing: {p}")

    proc = subprocess.Popen(
        [args.binary, "--port", str(args.port), "--browser"],
        cwd=str(ROOT),
    )
    try:
        if not _wait_health(base):
            _fail("app did not become healthy")

        # 0. Cheap guard: scipy's runtime data files are bundled (see below).
        _check_bundled_scipy_data(args.binary)

        # 1. Configure CA dir + runner interpreter + backend, as a user would.
        #    Pin the backend to cellml_only / CVODE_myokit explicitly: the analysis
        #    runs inherit the engine's solver, and leaving it at whatever was
        #    persisted (or a CA default of CVODE_opencor, which isn't installed)
        #    makes the run non-deterministic. CVODE_myokit JIT-compiles, but every
        #    CI runner has a C compiler.
        cfg = {
            "ca_dir": str(Path(args.ca_dir).resolve()),
            "generated_model_format": "cellml_only",
            "solver": "CVODE_myokit",
            "solver_info": {"dt": 0.01},
        }
        if args.runner_python:
            cfg["python_path"] = str(Path(args.runner_python).resolve())
        _, conf = _req("POST", f"{base}/api/config", data=cfg)
        if not conf.get("ca_exists"):
            _fail(f"CA dir not accepted: {conf.get('ca_dir')!r}")
        if not conf.get("packaged"):
            print("WARNING: app does not report itself as packaged (running from source?)")
        print(f"configured: ca_dir={conf['ca_dir']}  python={conf.get('python_path')!r}")

        # 2. Upload the 3-compartment fixture (model + obs_data + params_for_id).
        _, up = _multipart(f"{base}/api/models/upload", "file", MODEL, {})
        model_id = up["model_id"]
        _, _ = _multipart(f"{base}/api/obs_data/upload", "file", OBS, {"model_id": model_id})
        _, _ = _multipart(f"{base}/api/params_for_id/upload", "file", PARAMS, {"model_id": model_id})
        print(f"uploaded model_id={model_id}")

        # 3. Local sensitivity about the current point (no calibration needed) —
        #    the cheapest real analysis run: a few finite-difference evaluations.
        sa_settings = {
            "method": "local", "gradient_method": "FD", "nominal": "current",
            "rel_step": 0.05, "dt": 0.01, "num_cores": 1,
        }
        _, r = _req("POST", f"{base}/api/sensitivity/run",
                    data={"model_id": model_id, "settings": sa_settings})
        sa = _poll_job(base, "sensitivity", r["job_id"], args.sensitivity_timeout)
        if sa["state"] != "done":
            _fail(f"sensitivity ended in state {sa['state']!r}", sa.get("_lines"))
        print(f"sensitivity OK (state=done)")

        # 4. Short genetic-algorithm calibration (DEBUG => small population).
        cal_settings = {
            "param_id_method": "genetic_algorithm",
            "num_calls_to_function": 30, "DEBUG": True, "dt": 0.01,
        }
        _, r = _req("POST", f"{base}/api/calibration/run",
                    data={"model_id": model_id, "settings": cal_settings})
        cal = _poll_job(base, "calibration", r["job_id"], args.calibration_timeout)
        if cal["state"] != "done":
            _fail(f"calibration ended in state {cal['state']!r}", cal.get("_lines"))
        best = cal.get("best_params") or {}
        if not best or not all(isinstance(v, (int, float)) for v in best.values()):
            _fail(f"calibration produced no usable best_params: {best!r}", cal.get("_lines"))
        print(f"calibration OK (state=done, {len(best)} best params, cost={cal.get('cost')})")

        print("\nANALYSIS SMOKE PASSED: sensitivity + calibration ran in the built app")
        return 0
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()


if __name__ == "__main__":
    raise SystemExit(main())
