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
        # How well the posterior reproduces the data it was fitted to, if the
        # engine could run the check. None means "not measured", not "bad".
        self.coverage: dict | None = None
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

    @staticmethod
    def _read_coverage(output_dir: str) -> dict | None:
        """The coverage summary the runner's posterior predictive check wrote.

        Read from the run directory rather than passed over the pipe, for the
        same reason the posterior is: the file is the result, and a run that was
        salvaged or reattached to has the file but not the pipe.
        """
        run_dir = ca_run_history.find_run_dir(output_dir) or output_dir
        path = os.path.join(run_dir, "posterior_predictive_coverage.json")
        if not os.path.isfile(path):
            return None
        try:
            with open(path) as handle:
                return json.load(handle)
        except (OSError, ValueError):
            return None

    def _finalize(self, job: UQJob, code: int) -> None:
        with job.lock:
            if job.state == "cancelled":
                self._salvage_partial(job)
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
                    job.coverage = self._read_coverage(job.output_dir)
                    if code != 0:
                        job.warning = teardown_warning(code, job.lines)
                except Exception as exc:  # noqa: BLE001
                    job.state = "error"
                    job.error = f"failed to read results: {exc}"
            else:
                job.state = "error"
                job.error = job.error or f"runner exited with code {code}"

    def _salvage_partial(self, job: UQJob) -> None:
        """Build the posterior from the chain a cancelled run had already sampled.

        Cancelling used to throw the run away: the posterior is derived from uq_samples.npy,
        which the runner writes only when it finishes, so the Analysis tab stayed empty even
        though thousands of usable draws were sitting on disk. Since CA #418 writes the chain as
        it samples, a cancelled run has one -- it is just shorter than the one that was asked
        for, which is a reason to label it, not to discard it.

        Best effort throughout: a cancel that cannot be salvaged must still be a clean cancel.
        """
        try:
            import numpy as np  # noqa: PLC0415

            samples = mcmc_progress.read_chain(job.output_dir)
            if samples is None or samples.shape[0] < 2:
                return
            steps = samples.shape[0]
            cut = mcmc_progress.burn_in_index(steps, job.burn_in, job.target_steps)
            note = f"cancelled after {steps} steps"
            if cut >= steps - 1:
                # The configured burn-in is past where the run got to. Half the chain is CA's
                # own default, and saying so is better than reporting one sample.
                cut = steps // 2
                note += "; the configured burn-in was never reached, so half the chain was"
                note += " discarded instead"
            flat = samples[cut:].reshape(-1, samples.shape[2])

            run_dir = ca_run_history.find_run_dir(job.output_dir) or job.output_dir
            qnames = [row[0] for row in ca_run_history.param_names(run_dir) or []]
            if len(qnames) != samples.shape[2]:
                qnames = [f"param_{i}" for i in range(samples.shape[2])]

            ca_run_history.write_uq_samples(job.output_dir, np.asarray(flat), qnames)
            params = ca_run_history.uq_distributions(job.output_dir)
            if params:
                job.params = params
                job.method = job.method or "mcmc"
                job.warning = f"Posterior from a partial chain ({note})."
        except Exception:  # noqa: BLE001 - a failed salvage is still a clean cancel
            return

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
                "coverage": job.coverage,
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

    def posterior_predictive(self, job_id: str) -> dict | None:
        """The predictive check for this run, scaled for plotting.

        Scaled here rather than in the browser because the scaling is what makes
        the numbers comparable, and two clients doing it differently would draw
        two different figures from one run.
        """
        job = self._job
        if job is None or job.id != job_id:
            return None

        run_dir = ca_run_history.find_run_dir(job.output_dir) or job.output_dir
        path = os.path.join(run_dir, "posterior_predictive.npz")
        if not os.path.isfile(path):
            return {"available": False, "coverage": job.coverage}

        import numpy as np  # noqa: PLC0415 (only needed on this path)

        try:
            with np.load(path, allow_pickle=True) as data:
                preds = np.asarray(data["predictions"], dtype=float)
                truth = np.asarray(data["ground_truth"], dtype=float)
                std = np.asarray(data["std"], dtype=float)
                labels = [str(x) for x in data["labels"]]
        except Exception as exc:  # noqa: BLE001
            # numpy's failure modes for a damaged .npz are varied -- OSError,
            # ValueError, KeyError and UnpicklingError have all been seen -- and
            # a file that cannot be read is a missing figure, not a 500.
            return {"available": False, "error": str(exc),
                    "coverage": job.coverage}

        keep = ~np.all(np.isnan(preds), axis=0)
        idx = np.where(keep)[0]
        if idx.size == 0:
            return {"available": False, "coverage": job.coverage}

        # A zero std would divide the whole observable away; leave those in real
        # units rather than dropping the row.
        scale = np.where(np.abs(std[idx]) > 0, np.abs(std[idx]), 1.0)
        centred = lambda values: ((values - truth[idx]) / scale).tolist()  # noqa: E731

        return {
            "available": True,
            "coverage": job.coverage,
            "labels": [labels[i] for i in idx],
            "lo": centred(np.nanpercentile(preds[:, idx], 2.5, axis=0)),
            "median": centred(np.nanmedian(preds[:, idx], axis=0)),
            "hi": centred(np.nanpercentile(preds[:, idx], 97.5, axis=0)),
            "num_samples": int(preds.shape[0]),
            "units": "measurement standard deviations from the measured value",
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
