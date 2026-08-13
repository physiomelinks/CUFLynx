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
import threading
import uuid
from pathlib import Path

# Interpreter discovery is shared with calibration — same machine, same probe.
from calibration import (  # noqa: F401  (list_python_interpreters re-exported)
    _warn_no_mpiexec,
    clear_run_config,
    finished_before_exiting,
    read_meta_line,
    list_python_interpreters,
    resolve_mpiexec,
    teardown_warning,
    write_run_config,
)
import ca_run_history
import mcmc_progress
from runtime_paths import default_python, runner_command, runner_launch_env, runner_path

RUNNER_PATH = str(runner_path("uq_runner.py"))


class UQJob:
    def __init__(self, job_id: str, output_dir: str):
        self.id = job_id
        self.output_dir = output_dir
        self.lines: list[str] = []
        self.state = "running"  # running | done | error | cancelled
        self.method: str | None = None
        # The sampling settings the cumulative-mean plot needs: where CA will cut the burn-in,
        # and the run length the fraction is taken against.
        self.burn_in: float = 0.5
        self.target_steps: int | None = None
        self.params: list | None = None  # per-parameter posterior summaries
        self.error: str | None = None
        # Set when the run finished but its process failed on the way out.
        self.warning: str | None = None
        self.proc: subprocess.Popen | None = None
        # The temp file the runner was handed as argv[1], removed when it exits.
        self.config_path: str | None = None
        self.lock = threading.Lock()


class UQManager:
    def __init__(self):
        self.runner_path = RUNNER_PATH
        self.python = default_python()
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

        The launcher is resolved from the selected interpreter's environment
        (see :func:`calibration.resolve_mpiexec`) so it matches that
        interpreter's mpi4py.

        Falls back to a single core when ``num_cores > 1`` but no ``mpiexec``
        can be found (common on Windows), instead of launching a non-existent
        ``mpiexec`` (which would crash the request with an HTTP 500).
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
                raise RuntimeError("a UQ job is already running")
            output_dir = config["output_dir"]
            os.makedirs(output_dir, exist_ok=True)
            config_path = write_run_config(config, "uq_config.json")

            job = UQJob(uuid.uuid4().hex, output_dir)
            settings = config.get("settings") or config
            job.burn_in = settings.get("burn_in", 0.5)
            # The configured run length, which the burn-in fraction is taken against. Coerced
            # rather than trusted: it arrives from a form, so "100000" is as likely as 100000,
            # and a string here silently fell back to half the chain *so far* -- a burn-in
            # marker that crawled forward on every poll.
            try:
                job.target_steps = int(float(settings.get("num_steps")))
            except (TypeError, ValueError):
                job.target_steps = None
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

    def _reader(self, job: UQJob) -> None:
        try:
            assert job.proc and job.proc.stdout is not None
            for line in job.proc.stdout:
                with job.lock:
                    job.lines.append(line.rstrip("\n"))
        finally:
            code = job.proc.wait() if job.proc else -1
            self._finalize(job, code)
            clear_run_config(job.config_path)

    def _finalize(self, job: UQJob, code: int) -> None:
        with job.lock:
            if job.state == "cancelled":
                return
            # A non-zero exit *after* the DONE marker is a teardown failure,
            # not a failed run -- see calibration.finished_before_exiting.
            from uq_runner import DONE_MARKER, FAIL_MARKER, META_MARKER  # noqa: PLC0415

            finished = code == 0 or finished_before_exiting(
                job.lines, DONE_MARKER, FAIL_MARKER
            )
            # The posterior is re-derived from the samples the run persisted, not
            # from a summary it serialised for us (#210). Which method ran is the
            # only thing no file holds, so it comes over the pipe.
            params = (
                ca_run_history.uq_distributions(job.output_dir) if finished else None
            )
            if params is not None:
                try:
                    job.method = read_meta_line(job.lines, META_MARKER).get("method")
                    job.params = params
                    job.state = "done"
                    if code != 0:
                        job.warning = teardown_warning(code, job.lines)
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
                "warning": job.warning,
            }

    def progress(self, job_id: str) -> dict | None:
        """The growing chain, for the live plots (#244).

        Reads mcmc_chain.npy straight from the run directory rather than going through the job:
        the runner subprocess owns that file and rewrites it every checkpoint (CA #417), so
        there is nothing here to lock. Before the first checkpoint it simply reports no steps.
        """
        job = self._job
        if job is None or job.id != job_id:
            return None
        # param_names.csv lives in CA's run directory, not the one CA was handed -- the same
        # place the chain does. Reading the job dir found nothing, so every parameter came back
        # as "parameter 1".
        run_dir = ca_run_history.find_run_dir(job.output_dir) or job.output_dir
        labels = [row[0] for row in ca_run_history.param_names(run_dir) or []]
        return {
            "job_id": job.id,
            "state": job.state,
            **mcmc_progress.progress(job.output_dir, labels, job.burn_in, job.target_steps),
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
