"""UQ (uncertainty quantification) job manager: runs ``uq_runner.py`` as a
subprocess and streams its stdout into a buffer the API can poll.

A near-copy of :mod:`sensitivity`. One job at a time with its own slot, so a UQ
run doesn't block (or get blocked by) calibration/sensitivity. Tests inject a
fake runner by setting ``uq.runner_path``.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import threading
import uuid
from pathlib import Path

# Interpreter discovery is shared with calibration — same machine, same probe.
from calibration import (  # noqa: F401  (list_python_interpreters re-exported)
    WORKFLOW_RUNNER_PATH,
    study_popen_kwargs,
    study_read_dir,
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
        # Profile likelihood (laplace method with ia_options.method
        # profile_likelihood): CA's profiles, in place of a posterior.
        self.profiles: list | None = None
        self.ia_method: str | None = None
        self.error: str | None = None
        # Set when the run finished but its process failed on the way out.
        self.warning: str | None = None
        # How well the posterior reproduces the data it was fitted to, if the
        # engine could run the check. None means "not measured", not "bad".
        self.coverage: dict | None = None
        # A Laplace step's draws that fell outside the params_for_id bounds and were dropped
        # from its stored distribution: ``{count, of}``, or None when it has no such count.
        self.outside_bounds: dict | None = None
        self.proc: subprocess.Popen | None = None
        # The temp file the runner was handed as argv[1], removed when it exits.
        self.config_path: str | None = None
        # The study-workflow step this run is, or None for a direct run.
        self.study_step: dict | None = None
        # Where this run's posterior predictive check was saved, once it has finished: a
        # workflow uq step's ``posterior_predictive_dir``, the directory a direct Laplace
        # run names on its meta line, else the MCMC run's directory.
        self.predictive_dir: str | None = None
        self.lock = threading.Lock()


class UQManager:
    def __init__(self):
        self.runner_path = RUNNER_PATH
        self.study_runner_path = WORKFLOW_RUNNER_PATH
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
        script = self.study_runner_path if config.get("study_step") else self.runner_path
        base = runner_command(python, script, config_path)
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

            # A workflow uq step runs in <step>/uq/: its MCMC run dir is there.
            job = UQJob(uuid.uuid4().hex, study_read_dir(config, "uq") or output_dir)
            job.study_step = config.get("study_step")
            settings = config.get("settings") or config
            if job.study_step:
                job.method = settings.get("method", "mcmc")
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
                **study_popen_kwargs(config),
            )
            self._job = job
            job.lines.extend(config.get("log_preamble") or [])

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
            # Before finalising, not after -- `_finalize` publishes the terminal state a
            # client is polling for, and it must not arrive while the temp dir is still on
            # disk. The full reasoning is beside the same two lines in calibration.py.
            clear_run_config(job.config_path)
            self._finalize(job, code)

    @staticmethod
    def _read_coverage(output_dir: str) -> dict | None:
        """The coverage summary the runner's posterior predictive check wrote.

        Read from the run directory rather than passed over the pipe, for the
        same reason the posterior is: the file is the result, and a run that was
        salvaged or reattached to has the file but not the pipe.
        """
        return ca_run_history.predictive_coverage(ca_run_history.uq_source_dir(output_dir))

    @staticmethod
    def _predictive_dir(job: UQJob, meta: dict) -> str | None:
        """Where a finished run's posterior predictive check is (see ``UQJob.predictive_dir``)."""
        if job.study_step:
            return ca_run_history.step_predictive_dir(
                ca_run_history.step_result(job.study_step["step_dir"]))
        return meta.get("predictive_dir") or ca_run_history.uq_source_dir(job.output_dir)

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
            meta = read_meta_line(job.lines, META_MARKER)
            job.ia_method = meta.get("ia_method")
            if finished and job.ia_method:
                job.profiles = ca_run_history.profile_likelihood(job.output_dir)
                if job.profiles is not None:
                    job.method = meta.get("method")
                    job.state = "done"
                    if code != 0:
                        job.warning = teardown_warning(code, job.lines)
                    return
            # A workflow uq step's posterior is the one CA stored for it.
            params = None
            if finished and not job.ia_method:
                params = (self._study_params(job) if job.study_step
                          else ca_run_history.uq_distributions(job.output_dir))
            if params is not None:
                try:
                    meta = read_meta_line(job.lines, META_MARKER)
                    job.method = meta.get("method") or job.method
                    job.params = params
                    warnings = []
                    if job.study_step:
                        job.outside_bounds = self._study_outside_bounds(job)
                        failed = ca_run_history.step_predictive_error(
                            ca_run_history.step_result(job.study_step["step_dir"]))
                        if failed:
                            warnings.append(f"The posterior predictive check failed: {failed}")
                    job.predictive_dir = self._predictive_dir(job, meta)
                    job.coverage = ca_run_history.predictive_coverage(job.predictive_dir)
                    job.state = "done"
                    if code != 0:
                        warnings.append(teardown_warning(code, job.lines))
                    job.warning = " ".join(w for w in warnings if w) or None
                except Exception as exc:  # noqa: BLE001
                    job.state = "error"
                    job.error = f"failed to read results: {exc}"
            else:
                from sensitivity import fail_message  # noqa: PLC0415

                job.state = "error"
                job.error = (job.error or fail_message(job.lines, FAIL_MARKER)
                             or f"runner exited with code {code}")

    @staticmethod
    def _study_params(job: UQJob) -> list | None:
        """A workflow uq step's posterior: a Laplace approximation's normal marginals from
        ``step_result.json`` (as the direct run's draws approximate them), an MCMC's stored
        distribution (the chain after CA's burn-in)."""
        return ca_run_history.step_uq_marginals(
            ca_run_history.step_result(job.study_step["step_dir"]))

    @staticmethod
    def _study_outside_bounds(job: UQJob) -> dict | None:
        """How many of a Laplace step's draws fell outside the params_for_id bounds (CA's
        ``laplace.samples_outside_bounds``), of how many: those kept, in the stored
        distribution's samples, plus those dropped. None when CA did not count them."""
        result = ca_run_history.step_result(job.study_step["step_dir"]) or {}
        count = (result.get("laplace") or {}).get("samples_outside_bounds")
        if not isinstance(count, int):
            return None
        kept = ca_run_history.stored_sample_count(result.get("distribution_dir") or "")
        return {"count": count, "of": None if kept is None else kept + count}

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

            run_dir = ca_run_history.uq_source_dir(job.output_dir)
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
                "profiles": job.profiles,
                "ia_method": job.ia_method,
                "error": job.error,
                "warning": job.warning,
                "coverage": job.coverage,
                "outside_bounds": job.outside_bounds,
                "study_step": job.study_step,
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
        run_dir = ca_run_history.uq_source_dir(job.output_dir)
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

        directory = job.predictive_dir
        if directory is None and not job.study_step:
            directory = ca_run_history.uq_source_dir(job.output_dir)
        return ca_run_history.posterior_predictive_band(directory, job.coverage)

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
