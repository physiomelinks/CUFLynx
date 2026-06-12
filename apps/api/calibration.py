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

RUNNER_PATH = str(Path(__file__).resolve().parent / "calibration_runner.py")

# Modules a Python interpreter needs to run calibrations. myokit + libcellml are
# required for any run; nevergrad (CMA-ES) and mpi4py (multi-core) are optional.
REQUIRED_MODULES = ["myokit", "libcellml"]
OPTIONAL_MODULES = ["nevergrad", "mpi4py"]


def _candidate_python_paths() -> list[str]:
    """Best-effort list of Python interpreters on this machine."""
    import glob

    cands = [sys.executable]
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
    def __init__(self, job_id: str, output_dir: str):
        self.id = job_id
        self.output_dir = output_dir
        self.lines: list[str] = []
        self.state = "running"  # running | done | error | cancelled
        self.best_params: dict | None = None
        self.cost = None
        self.error: str | None = None
        self.proc: subprocess.Popen | None = None
        self.lock = threading.Lock()


class CalibrationManager:
    def __init__(self):
        self.runner_path = RUNNER_PATH
        self.python = sys.executable
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

    def build_command(self, config: dict, config_path: str) -> list[str]:
        """Single-process by default; ``mpiexec -n N`` when num_cores > 1.

        The genetic algorithm parallelises population evaluation across MPI
        ranks, exactly like circulatory_autogen's run_param_id.sh.
        """
        python = config.get("python") or self.python
        base = [python, "-u", self.runner_path, config_path]
        num_cores = int(config.get("num_cores", 1) or 1)
        if num_cores > 1:
            mpiexec = shutil.which("mpiexec") or "mpiexec"
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

            job = CalibrationJob(uuid.uuid4().hex, output_dir)
            env = dict(os.environ)
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
                "error": job.error,
            }

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
