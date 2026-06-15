"""UQ (uncertainty quantification) job manager: runs ``uq_runner.py`` as a
subprocess and streams its stdout into a buffer the API can poll.

A near-copy of :mod:`sensitivity`. One job at a time with its own slot, so a UQ
run doesn't block (or get blocked by) calibration/sensitivity. Tests inject a
fake runner by setting ``uq.runner_path``.
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

# Interpreter discovery is shared with calibration — same machine, same probe.
from calibration import (  # noqa: F401  (list_python_interpreters re-exported)
    _warn_no_mpiexec,
    list_python_interpreters,
)

RUNNER_PATH = str(Path(__file__).resolve().parent / "uq_runner.py")


class UQJob:
    def __init__(self, job_id: str, output_dir: str):
        self.id = job_id
        self.output_dir = output_dir
        self.lines: list[str] = []
        self.state = "running"  # running | done | error | cancelled
        self.method: str | None = None
        self.params: list | None = None  # per-parameter posterior summaries
        self.error: str | None = None
        self.proc: subprocess.Popen | None = None
        self.lock = threading.Lock()


class UQManager:
    def __init__(self):
        self.runner_path = RUNNER_PATH
        self.python = sys.executable
        self._job: UQJob | None = None
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
        """Single-process by default; ``mpiexec -n N`` when num_cores > 1 (MCMC
        and the GA calibration step parallelise across MPI ranks).

        Falls back to a single core when ``num_cores > 1`` but ``mpiexec`` is not
        on PATH (common on Windows), instead of launching a non-existent
        ``mpiexec`` (which would crash the request with an HTTP 500).
        """
        python = config.get("python") or self.python
        base = [python, "-u", self.runner_path, config_path]
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
                raise RuntimeError("a UQ job is already running")
            output_dir = config["output_dir"]
            os.makedirs(output_dir, exist_ok=True)
            config_path = os.path.join(output_dir, "uq_config.json")
            with open(config_path, "w") as fh:
                json.dump(config, fh)

            job = UQJob(uuid.uuid4().hex, output_dir)
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

    def _reader(self, job: UQJob) -> None:
        try:
            assert job.proc and job.proc.stdout is not None
            for line in job.proc.stdout:
                with job.lock:
                    job.lines.append(line.rstrip("\n"))
        finally:
            code = job.proc.wait() if job.proc else -1
            self._finalize(job, code)

    def _finalize(self, job: UQJob, code: int) -> None:
        with job.lock:
            if job.state == "cancelled":
                return
            results = os.path.join(job.output_dir, "results.json")
            if code == 0 and os.path.exists(results):
                try:
                    data = json.loads(Path(results).read_text())
                    job.method = data.get("method")
                    job.params = data.get("params", [])
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
                "method": job.method,
                "params": job.params,
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
uq = UQManager()
