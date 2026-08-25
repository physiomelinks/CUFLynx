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
import urllib.parse
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


def _check_full_stack(binary: str) -> None:
    """The full bundle's two headline features, exercised inside the frozen process.

    Everything else here works identically in both Linux bundles, so nothing else notices
    whether the emulator and pyMC actually survived being frozen. The build-time guard in
    cuflynx.spec only proves they import in the *build* interpreter, which is a different
    Python with a real site-packages -- so without this, the one asset that exists for
    these two features is published having never run either.

    The pytensor compile is the specific risk. pytensor ships C templates that it
    compiles at *run time*, the same shape of problem as myokit's headers (the bug that
    made every CVODE simulation fail in v0.1.x), and a frozen process is exactly where
    that goes wrong. Compiling a trivial function and checking its answer proves the
    templates were collected and the compiler can reach them.
    """
    with tempfile.TemporaryDirectory() as td:
        probe = Path(td) / "full_stack_probe.py"
        probe.write_text(
            "import numpy as np\n"
            "import torch\n"
            # A CUDA build would mean the asset is over GitHub's 2 GiB limit; the build job
            # checks that too, but this catches a bundle handed here from anywhere else.
            "assert '+cpu' in torch.__version__, f'not a CPU torch: {torch.__version__}'\n"
            "import autoemulate\n"
            "import pytensor\n"
            "import pytensor.tensor as pt\n"
            "x = pt.dscalar('x')\n"
            "f = pytensor.function([x], x * 2)\n"
            "assert abs(float(f(3.0)) - 6.0) < 1e-9, 'pytensor compiled the wrong function'\n"
            "import pymc as pm\n"
            "with pm.Model() as m:\n"
            "    mu = pm.Normal('mu', 0.0, 1.0)\n"
            "    pm.Normal('y', mu, 1.0, observed=np.zeros(3))\n"
            # compile_logp goes through pymc's real pytensor pipeline without paying for
            # an MCMC run -- if the graph compiles and evaluates finite, sampling works.
            "lp = float(m.compile_logp()(m.initial_point()))\n"
            "assert np.isfinite(lp), f'pymc logp is {lp}'\n"
            "print('FULL_STACK_OK', torch.__version__, pm.__version__)\n"
        )
        cfg = Path(td) / "cfg.json"  # runner mode expects a config-path argv[2]
        cfg.write_text("{}")
        out = subprocess.run(
            [binary, RUNNER_MODE_FLAG, str(probe), str(cfg)],
            capture_output=True, text=True, timeout=600,
        )
        combined = out.stdout + out.stderr
        if "FULL_STACK_OK" not in combined:
            _fail("the emulator / pyMC stack failed in the built app -- this is the only "
                  "thing the -full asset adds", combined.splitlines())
        print("full stack OK (autoemulate imports, pytensor compiles, pymc logp evaluates)")


def _check_emulator_is_usable(base: str) -> None:
    """The Emulator tab must actually work in the built app, not merely import.

    The previous probe re-invoked the binary in runner mode and imported autoemulate,
    pytensor and pymc successfully -- and v0.4.1 still shipped an -full bundle whose
    Emulator tab said "the emulator options could not be read". Importing the packages
    is not the feature: the tab needs the app's own /api/emulator/defaults to answer,
    which means the app process must introspect libcuflynx's ANALYSIS_OPTIONS *and*
    find autoemulate's registered models. Ask the endpoint the tab asks.

    Both flags matter and they fail for different reasons:

    * ``supported`` False -- libcuflynx's schema could not be read at all, so the form
      has nothing to render. Nothing else in this script notices, because every other
      call still works off the fallback schema.
    * ``available`` False -- the schema read, but no emulator models were found, so
      there is nothing to train with.
    """
    _, d = _req("GET", f"{base}/api/emulator/defaults")
    reason = d.get("unavailable_reason") or "(no reason given)"
    if not d.get("supported"):
        _fail("the built app cannot read libcuflynx's emulator schema, so the Emulator "
              f"tab has no form to show. unavailable_reason: {reason}")
    if not d.get("available"):
        _fail("the built app has the emulator schema but no usable emulator models. "
              f"unavailable_reason: {reason}")
    print(f"emulator OK ({len(d.get('options') or [])} settings, "
          f"{len(d.get('models') or [])} models)")


class HttpTransport:
    """`apps/api/tests/acceptance`'s transport, over HTTP to a launched binary.

    The checks themselves live with the test suite and run there on every push;
    this is the same list asked of the artifact a user downloads. See that module
    for why they are not written twice.
    """

    def __init__(self, base: str):
        self.base = base

    def get(self, path):
        _, body = _req("GET", f"{self.base}{path}", timeout=120)
        return body

    def get_raw(self, path):
        request = urllib.request.Request(f"{self.base}{path}", method="GET")
        with urllib.request.urlopen(request, timeout=120) as response:
            return response.read(), dict(response.headers)

    def post(self, path, payload=None):
        _, body = _req("POST", f"{self.base}{path}", data=payload or {}, timeout=600)
        return body

    def upload(self, path, filename, blob, field="file", content_type="application/zip"):
        boundary = "----cuflynxacceptance"
        body = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"{field}\"; "
                f"filename=\"{filename}\"\r\nContent-Type: {content_type}\r\n\r\n").encode()
        body += blob + f"\r\n--{boundary}--\r\n".encode()
        request = urllib.request.Request(
            f"{self.base}{path}", data=body,
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
        with urllib.request.urlopen(request, timeout=600) as response:
            return json.loads(response.read())

    @staticmethod
    def quote(value):
        return urllib.parse.quote(value)


def _run_shared_acceptance_checks(base: str) -> None:
    """The checks the test suite runs in-process, asked of the built app.

    Run *before* a CA directory is configured, so what answers them is the engine
    inside the bundle -- which is what a user who never opens Settings has, and
    which nothing else in this pipeline exercises: every other step here points the
    app at a checked-out circulatory_autogen.
    """
    sys.path.insert(0, str(ROOT / "apps" / "api" / "tests"))
    import acceptance  # noqa: PLC0415 - path is set up immediately above

    app = HttpTransport(base)
    config = app.get("/api/config")
    if config.get("ca_dir"):
        _fail(f"a CA directory is already configured ({config['ca_dir']}), so these "
              f"checks would not be testing the bundled engine")

    for name, check in (
        ("engine vocabulary", lambda: acceptance.check_engine_vocabulary(app)),
        ("example is current", lambda: acceptance.check_example_is_current(app)),
        ("example loads whole", lambda: acceptance.check_example_loads_whole(app)),
    ):
        try:
            print(f"  bundled engine -- {name}: {check()}")
        except AssertionError as exc:
            _fail(f"{name} failed against the bundled engine: {exc}")


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
    ap.add_argument("--full", action="store_true",
                    help="also probe the emulator / pyMC stack (the -full Linux asset)")
    ap.add_argument("--num-cores", type=int, default=1,
                    help="with --full, also run sensitivity, calibration and emulator "
                         "training across this many MPI ranks (the bundle-as-runner path)")
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

        # 0. Cheap guards: scipy's runtime data files are bundled (see below), and --
        #    for the -full asset only -- the emulator/pyMC stack it exists to carry.
        _check_bundled_scipy_data(args.binary)
        if args.full:
            _check_full_stack(args.binary)
            _check_emulator_is_usable(base)

        # 0b. The shared acceptance checks, while no CA directory is configured --
        #     so the bundled engine is what answers them.
        _run_shared_acceptance_checks(base)

        # 1. Configure CA dir + runner interpreter + backend, as a user would.
        #    Pin the backend to cellml / CVODE_myokit explicitly: the analysis
        #    runs inherit the engine's solver, and leaving it at whatever was
        #    persisted (or a CA default of CVODE_opencor, which isn't installed)
        #    makes the run non-deterministic. CVODE_myokit JIT-compiles, but every
        #    CI runner has a C compiler.
        cfg = {
            "ca_dir": str(Path(args.ca_dir).resolve()),
            "generated_model_format": "cellml",
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

        # 5. The same analyses again across MPI ranks. Only for the full asset, which
        #    is the one that runs analysis in its own interpreter for users who never
        #    configure one -- and therefore the only one where `mpiexec` launches the
        #    *bundle* rather than a Python. That path has its own failure mode: the
        #    ranks are children of mpiexec while inheriting this process's PyInstaller
        #    parent state, and their bootloader refuses to start ("Security validation
        #    failure: parent process has different executable!", exit 255, no
        #    traceback). Nothing here ran multi-core, so it shipped.
        if args.full and args.num_cores > 1:
            print(f"\n--- multi-core arm ({args.num_cores} ranks) ---")
            _, r = _req("POST", f"{base}/api/sensitivity/run",
                        data={"model_id": model_id,
                              "settings": {**sa_settings, "num_cores": args.num_cores}})
            sa_par = _poll_job(base, "sensitivity", r["job_id"], args.sensitivity_timeout)
            if sa_par["state"] != "done":
                _fail(f"a {args.num_cores}-rank sensitivity ended in state "
                      f"{sa_par['state']!r}", sa_par.get("_lines"))
            print(f"sensitivity on {args.num_cores} ranks OK")

            _, r = _req("POST", f"{base}/api/calibration/run",
                        data={"model_id": model_id,
                              "settings": {**cal_settings, "num_cores": args.num_cores}})
            cal_par = _poll_job(base, "calibration", r["job_id"], args.calibration_timeout)
            if cal_par["state"] != "done":
                _fail(f"a {args.num_cores}-rank calibration ended in state "
                      f"{cal_par['state']!r}", cal_par.get("_lines"))
            print(f"calibration on {args.num_cores} ranks OK")

            # Training is where the ranks do the expensive part -- CA splits the design
            # across them -- and it is what the bug above was reported from.
            _, r = _req("POST", f"{base}/api/emulator/train",
                        data={"model_id": model_id,
                              "settings": {"num_train_samples": 12, "dt": 0.01,
                                           "num_cores": args.num_cores}})
            emu = _poll_job(base, "emulator", r["job_id"], args.calibration_timeout)
            if emu["state"] != "done":
                _fail(f"a {args.num_cores}-rank emulator training ended in state "
                      f"{emu['state']!r}", emu.get("_lines"))
            print(f"emulator training on {args.num_cores} ranks OK")

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
