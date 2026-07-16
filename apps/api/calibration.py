"""Calibration job manager: runs ``calibration_runner.py`` as a subprocess and
streams its stdout into a buffer the API can poll.

One job at a time (``start`` raises if busy). Tests inject a fake runner by
setting ``calibration.runner_path`` (mirrors the engine's factory seam).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import threading
import uuid
from pathlib import Path

from runtime_paths import default_python, runner_command, runner_path, subprocess_env

RUNNER_PATH = str(runner_path("calibration_runner.py"))


def _warn_no_mpiexec(num_cores: int) -> None:
    """Warn (to the server log) that a requested parallel run fell back to a
    single core because ``mpiexec`` is not installed."""
    print(
        f"warning: num_cores={num_cores} requested but 'mpiexec' was not found "
        "on PATH; running on a single core instead. Install an MPI runtime "
        "(see scripts/install.py) to enable parallel runs.",
        file=sys.stderr,
        flush=True,
    )


COST_HISTORY_FILE = "best_cost_history.csv"
PARAM_HISTORY_FILE = "best_param_vals_history.csv"


def _find_history_file(output_dir: str, name: str) -> str | None:
    """Locate a history CSV under output_dir, tolerating the ``<case_type>``
    subdir circulatory_autogen creates (e.g. ``genetic_algorithm_<prefix>_…``)."""
    import glob

    direct = os.path.join(output_dir, name)
    if os.path.exists(direct):
        return direct
    matches = glob.glob(os.path.join(output_dir, "**", name), recursive=True)
    return matches[0] if matches else None


def _read_history(output_dir: str) -> dict:
    """Parse the calibration history CSVs into JSON-friendly arrays.

    ``best_cost_history.csv`` has one row of (up to 10) comma-separated costs per
    generation, best first. ``best_param_vals_history.csv`` has a header row of
    display-friendly param names followed by one row of normalised best param
    values per generation. Never raises: missing files / partially-written final
    rows yield empty or truncated arrays so a mid-run poll is always safe.
    """
    param_names: list[str] = []
    cost_history: list[list[float]] = []
    param_history: list[list[float]] = []

    cost_path = _find_history_file(output_dir, COST_HISTORY_FILE)
    if cost_path:
        try:
            for line in Path(cost_path).read_text().splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    cost_history.append([float(x) for x in line.split(",")])
                except ValueError:
                    # partially-flushed final row mid-write; skip it
                    continue
        except OSError:
            pass

    param_path = _find_history_file(output_dir, PARAM_HISTORY_FILE)
    if param_path:
        try:
            lines = Path(param_path).read_text().splitlines()
            if lines:
                param_names = [c.strip() for c in lines[0].split(",")]
                width = len(param_names)
                for line in lines[1:]:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        row = [float(x) for x in line.split(",")]
                    except ValueError:
                        continue
                    if len(row) == width:
                        param_history.append(row)
        except OSError:
            pass

    return {
        "param_names": param_names,
        "cost_history": cost_history,
        "param_history": param_history,
    }

# Modules a Python interpreter needs to run calibrations. myokit + libcellml are
# required for any run; nevergrad (CMA-ES) and mpi4py (multi-core) are optional.
REQUIRED_MODULES = ["myokit", "libcellml"]
OPTIONAL_MODULES = ["nevergrad", "mpi4py"]


def _candidate_python_paths() -> list[str]:
    """Best-effort list of Python interpreters on this machine.

    ``default_python()`` (rather than ``sys.executable``) seeds the list so the
    packaged desktop build never offers its own frozen bundle as an interpreter.
    """
    import glob

    cands = [default_python()]
    for name in (
        "python3",
        "python",
        "python3.13",
        "python3.12",
        "python3.11",
        "python3.10",
        "python3.9",
    ):
        found = shutil.which(name)
        if found:
            cands.append(found)

    home = os.path.expanduser("~")
    for base in ("miniconda3", "anaconda3", "miniforge3", "mambaforge"):
        cands.append(os.path.join(home, base, "bin", "python"))
        cands.extend(glob.glob(os.path.join(home, base, "envs", "*", "bin", "python")))
    cands.extend(glob.glob(os.path.join(home, ".conda", "envs", "*", "bin", "python")))
    cands.extend(glob.glob("/opt/conda/envs/*/bin/python"))
    if os.environ.get("CONDA_PREFIX"):
        cands.append(os.path.join(os.environ["CONDA_PREFIX"], "bin", "python"))

    out: list[str] = []
    seen: set[str] = set()
    for c in cands:
        if not c:
            continue
        real = os.path.realpath(c)
        if os.path.isfile(real) and os.access(real, os.X_OK) and real not in seen:
            seen.add(real)
            out.append(c)
    return out


def _probe_python(path: str) -> dict | None:
    """Return {path, version, ready, missing} for an interpreter, or None."""
    try:
        ver = subprocess.run(
            [path, "-c", "import sys;print('.'.join(map(str, sys.version_info[:3])))"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if ver.returncode != 0:
            return None
        version = ver.stdout.strip()
        mods = REQUIRED_MODULES + OPTIONAL_MODULES
        check = subprocess.run(
            [
                path,
                "-c",
                "import importlib.util as u;"
                f"print(','.join(m for m in {mods!r} if u.find_spec(m) is None))",
            ],
            capture_output=True,
            text=True,
            timeout=20,
        )
        missing = (
            [m for m in check.stdout.strip().split(",") if m]
            if check.returncode == 0
            else mods
        )
        ready = all(m not in missing for m in REQUIRED_MODULES)
        return {"path": path, "version": version, "ready": ready, "missing": missing}
    except Exception:  # noqa: BLE001 - a bad interpreter just gets skipped
        return None


_python_cache: list[dict] | None = None


def list_python_interpreters(refresh: bool = False) -> list[dict]:
    """Discover + probe available interpreters (cached for the process)."""
    global _python_cache
    if _python_cache is not None and not refresh:
        return _python_cache
    result = []
    seen: set[str] = set()
    for path in _candidate_python_paths():
        real = os.path.realpath(path)
        if real in seen:
            continue
        seen.add(real)
        info = _probe_python(path)
        if info:
            result.append(info)
    _python_cache = result
    return result


class CalibrationJob:
    def __init__(self, job_id: str, output_dir: str, model_id: str | None = None):
        self.id = job_id
        self.output_dir = output_dir
        self.model_id = model_id
        self.lines: list[str] = []
        self.state = "running"  # running | done | error | cancelled
        self.best_params: dict | None = None
        self.cost = None
        # Post-calibration per-observable fit errors (Analysis-tab bar charts).
        self.percent_error: list | None = None
        self.std_error: list | None = None
        self.error_labels: list = []
        self.error: str | None = None
        self.proc: subprocess.Popen | None = None
        self.lock = threading.Lock()


class CalibrationManager:
    def __init__(self):
        self.runner_path = RUNNER_PATH
        self.python = default_python()
        self._job: CalibrationJob | None = None
        self._lock = threading.Lock()

    def reset(self) -> None:
        """Terminate any running job and clear state (used between tests)."""
        with self._lock:
            job = self._job
            self._job = None
        if job and job.proc and job.proc.poll() is None:
            job.proc.terminate()

    @property
    def busy(self) -> bool:
        job = self._job
        return job is not None and job.state == "running"

    def last_completed_best_params(self, model_id: str) -> dict | None:
        """Best-fit params of the most recent completed calibration for ``model_id``
        (for the UQ tab to reuse), or None if none has completed for it."""
        job = self._job
        if job is None or job.model_id != model_id or job.state != "done":
            return None
        return job.best_params or None

    def build_command(self, config: dict, config_path: str) -> list[str]:
        """Single-process by default; ``mpiexec -n N`` when num_cores > 1.

        The genetic algorithm parallelises population evaluation across MPI
        ranks, exactly like circulatory_autogen's run_param_id.sh.

        If ``num_cores > 1`` but ``mpiexec`` is not on PATH (common on Windows,
        where MPI is rarely installed), fall back to a single-core run rather
        than launching a non-existent ``mpiexec`` — which would raise
        ``FileNotFoundError`` and surface to the client as an HTTP 500.
        """
        # An explicit interpreter runs the runner script; with none, the frozen app
        # runs it in-process (runner mode) and from source uses the serving Python.
        python = config.get("python") or self.python
        base = runner_command(python, self.runner_path, config_path)
        num_cores = int(config.get("num_cores", 1) or 1)
        if num_cores > 1:
            mpiexec = shutil.which("mpiexec")
            if mpiexec is None:
                _warn_no_mpiexec(num_cores)
                return base
            return [mpiexec, "-n", str(num_cores), *base]
        return base

    def start(self, config: dict) -> str:
        with self._lock:
            if self.busy:
                raise RuntimeError("a calibration job is already running")
            output_dir = config["output_dir"]
            os.makedirs(output_dir, exist_ok=True)
            config_path = os.path.join(output_dir, "calib_config.json")
            with open(config_path, "w") as fh:
                json.dump(config, fh)

            job = CalibrationJob(uuid.uuid4().hex, output_dir, config.get("model_id"))
            env = subprocess_env()
            job.proc = subprocess.Popen(
                self.build_command(config, config_path),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                env=env,
            )
            self._job = job

        threading.Thread(target=self._reader, args=(job,), daemon=True).start()
        return job.id

    def _reader(self, job: CalibrationJob) -> None:
        try:
            assert job.proc and job.proc.stdout is not None
            for line in job.proc.stdout:
                with job.lock:
                    job.lines.append(line.rstrip("\n"))
        finally:
            code = job.proc.wait() if job.proc else -1
            self._finalize(job, code)

    def _finalize(self, job: CalibrationJob, code: int) -> None:
        with job.lock:
            if job.state == "cancelled":
                return
            results = os.path.join(job.output_dir, "results.json")
            if code == 0 and os.path.exists(results):
                try:
                    data = json.loads(Path(results).read_text())
                    job.best_params = data.get("params", {})
                    job.cost = data.get("cost")
                    job.percent_error = data.get("percent_error")
                    job.std_error = data.get("std_error")
                    job.error_labels = data.get("error_labels") or []
                    job.state = "done"
                except Exception as exc:  # noqa: BLE001
                    job.state = "error"
                    job.error = f"failed to read results: {exc}"
            else:
                job.state = "error"
                job.error = job.error or f"runner exited with code {code}"

    def status(self, job_id: str, offset: int = 0) -> dict | None:
        job = self._job
        if job is None or job.id != job_id:
            return None
        with job.lock:
            lines = job.lines[offset:]
            return {
                "job_id": job.id,
                "state": job.state,
                "lines": lines,
                "next_offset": offset + len(lines),
                "best_params": job.best_params,
                "cost": job.cost,
                "percent_error": job.percent_error,
                "std_error": job.std_error,
                "error_labels": job.error_labels,
                "error": job.error,
            }

    def progress(self, job_id: str) -> dict | None:
        """Per-generation cost/param history for the live progress charts.

        Reads the history CSVs the runner subprocess writes (no lock needed —
        a separate process owns the files). ``state`` lets the client stop
        polling once the run is no longer ``running``.
        """
        job = self._job
        if job is None or job.id != job_id:
            return None
        hist = _read_history(job.output_dir)
        return {"job_id": job.id, "state": job.state, **hist}

    def cancel(self, job_id: str) -> bool:
        job = self._job
        if job is None or job.id != job_id:
            return False
        with job.lock:
            if job.state == "running":
                job.state = "cancelled"
                if job.proc and job.proc.poll() is None:
                    job.proc.terminate()
        return True


# Module-level singleton shared by the FastAPI routes.
calibration = CalibrationManager()
