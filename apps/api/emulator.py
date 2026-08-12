"""Emulator-training job manager: runs ``emulator_runner.py`` as a subprocess and
streams its stdout into a buffer the API can poll.

A near-copy of :mod:`sensitivity` — same one-job-at-a-time slot, same marker
protocol, same "results come from circulatory_autogen's own files" rule (#210).
The result here is CA's ``emulator_metadata.json``: held-out R2 and RMSE per
feature, the parameter box, the design and the provenance. Only the bundle's
*location* arrives on the pipe, because no file records it.

Its own slot rather than sharing the sensitivity one: training is the thing a
user does *before* an analysis, and blocking a Sobol run on it (or the reverse)
would be an odd coupling. Tests inject a fake runner via ``emulator.runner_path``.
"""

from __future__ import annotations

import os
import subprocess
import threading
import uuid

import ca_run_history
from calibration import (
    _warn_no_mpiexec,
    clear_run_config,
    finished_before_exiting,
    read_meta_line,
    resolve_mpiexec,
    teardown_warning,
    write_run_config,
)
from runtime_paths import default_python, runner_command, runner_launch_env, runner_path

RUNNER_PATH = str(runner_path("emulator_runner.py"))


class EmulatorJob:
    def __init__(self, job_id: str, output_dir: str):
        self.id = job_id
        self.output_dir = output_dir
        self.lines: list[str] = []
        self.state = "running"  # running | done | error | cancelled
        #: CA's emulator_metadata.json, read back once the run finishes.
        self.metadata: dict | None = None
        self.error: str | None = None
        self.warning: str | None = None
        self.proc: subprocess.Popen | None = None
        self.config_path: str | None = None
        self.lock = threading.Lock()


class EmulatorManager:
    def __init__(self):
        self.runner_path = RUNNER_PATH
        self.python = default_python()
        self._job: EmulatorJob | None = None
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
        """``mpiexec -n N`` when num_cores > 1 — CA splits the training design
        across ranks exactly as it splits Sobol samples.

        Same launcher resolution and same single-core fallback as the sensitivity
        manager: a missing mpiexec costs the user parallelism, not the run.
        """
        python = config.get("python") or self.python
        base = runner_command(python, self.runner_path, config_path)
        num_cores = int(config.get("num_cores", 1) or 1)
        if num_cores > 1:
            mpiexec = resolve_mpiexec(python)
            if mpiexec is None:
                _warn_no_mpiexec(num_cores)
                return base
            return [mpiexec, "-n", str(num_cores), *base]
        return base

    def start(self, config: dict) -> str:
        with self._lock:
            if self.busy:
                raise RuntimeError("an emulator training job is already running")
            output_dir = config["output_dir"]
            os.makedirs(output_dir, exist_ok=True)
            config_path = write_run_config(config, "emulator_config.json")

            job = EmulatorJob(uuid.uuid4().hex, output_dir)
            job.config_path = config_path
            env = runner_launch_env(config.get("python") or self.python)
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

    def _reader(self, job: EmulatorJob) -> None:
        try:
            assert job.proc and job.proc.stdout is not None
            for line in job.proc.stdout:
                with job.lock:
                    job.lines.append(line.rstrip("\n"))
        finally:
            code = job.proc.wait() if job.proc else -1
            self._finalize(job, code)
            clear_run_config(job.config_path)

    def _finalize(self, job: EmulatorJob, code: int) -> None:
        with job.lock:
            if job.state == "cancelled":
                return
            from emulator_runner import (  # noqa: PLC0415
                DONE_MARKER,
                FAIL_MARKER,
                META_MARKER,
            )

            finished = code == 0 or finished_before_exiting(
                job.lines, DONE_MARKER, FAIL_MARKER
            )
            meta = read_meta_line(job.lines, META_MARKER)
            metadata = None
            if finished and meta.get("emulator_dir"):
                metadata = ca_run_history.emulator_metadata(meta["emulator_dir"])
            if metadata is not None:
                job.metadata = metadata
                job.state = "done"
                if code != 0:
                    job.warning = teardown_warning(code, job.lines)
            else:
                job.state = "error"
                job.error = job.error or (
                    "the run finished but wrote no emulator metadata"
                    if finished
                    else f"runner exited with code {code}"
                )

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
                "metadata": job.metadata,
                "error": job.error,
                "warning": job.warning,
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
emulator = EmulatorManager()
