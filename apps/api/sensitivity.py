"""Sensitivity-analysis job manager: runs ``sensitivity_runner.py`` as a
subprocess and streams its stdout into a buffer the API can poll.

A near-copy of :mod:`calibration` minus the per-generation history (the Sobol
engine emits no incremental history — only a final set of indices). One job at a
time (``start`` raises if busy); it has its own slot so a sensitivity run and a
calibration run don't block each other. Tests inject a fake runner by setting
``sensitivity.runner_path`` (mirrors the calibration manager's seam).
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import threading
import time
import uuid
from pathlib import Path

# Interpreter discovery is shared with calibration — same machine, same probe.
import ca_run_history
from calibration import (  # noqa: F401  (list_python_interpreters re-exported)
    WORKFLOW_RUNNER_PATH,
    _warn_no_mpiexec,
    clear_run_config,
    finished_before_exiting,
    read_meta_line,
    list_python_interpreters,
    resolve_mpiexec,
    study_popen_kwargs,
    study_read_dir,
    teardown_warning,
    write_run_config,
)
from runtime_paths import default_python, runner_command, runner_launch_env, runner_path

RUNNER_PATH = str(runner_path("sensitivity_runner.py"))


#: A runner's protocol line (__SENSITIVITY_DONE__, __SENSITIVITY_META__ {...}, ...).
_MARKER_LINE = re.compile(r"__[A-Z][A-Z_]*__")

class SensitivityJob:
    def __init__(self, job_id: str, output_dir: str):
        self.id = job_id
        self.output_dir = output_dir
        self.lines: list[str] = []
        self.state = "running"  # running | done | error | cancelled
        self.indices: dict | None = None
        self.param_names: list[str] = []
        self.output_names: list[str] = []
        # Local SA only: the nominal (linearisation) point + where it came from,
        # surfaced so the Analysis panel can show what values it was taken about.
        self.nominal: list | None = None
        self.nominal_source: str | None = None
        # The outputs that are prediction features (include_prediction_items), so the
        # heatmap can tag them: libcuflynx's sobol_output_features.json for Sobol, the
        # runner's meta line for local.
        self.prediction_outputs: list[str] = []
        # The gradient source the run *resolved* to. The request may say "auto"
        # (CA's own default spelling), which names no arm -- so a label built from
        # the request reads "Local - auto", telling the user nothing about what
        # actually produced the numbers.
        self.gradient_method: str | None = None
        self.error: str | None = None
        # Parameter reduction: the Sobol run directory its report is searched
        # under (None when the job reduces nothing), and the report once read.
        self.reduction_root: str | None = None
        self.reduce_only = False
        self.reduction: dict | None = None
        self.started_at = time.time()
        # Set when the run finished but its process failed on the way out.
        self.warning: str | None = None
        self.proc: subprocess.Popen | None = None
        # The temp file the runner was handed as argv[1], removed when it exits.
        self.config_path: str | None = None
        # The study-workflow step this run is, or None for a direct run; with ``meta``, what
        # the runner's meta line says for a direct run (the method, the gradient source).
        self.study_step: dict | None = None
        self.meta: dict = {}
        self.lock = threading.Lock()


def _failure_reason(lines: list[str], fail_marker: str) -> str | None:
    """The text of the runner's last ``<FAIL_MARKER> reason`` line, or None."""
    for line in reversed(lines):
        if line.startswith(fail_marker):
            return line[len(fail_marker):].strip() or None
    return None


class SensitivityManager:
    def __init__(self):
        self.runner_path = RUNNER_PATH
        self.study_runner_path = WORKFLOW_RUNNER_PATH
        self.python = default_python()
        self._job: SensitivityJob | None = None
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

        The Sobol engine parallelises sample evaluation across MPI ranks, exactly
        like circulatory_autogen's sensitivity_analysis_run_script.

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
                raise RuntimeError("a sensitivity job is already running")
            output_dir = config["output_dir"]
            os.makedirs(output_dir, exist_ok=True)
            config_path = write_run_config(config, "sa_config.json")

            # Results are read where the runner writes them.
            job = SensitivityJob(uuid.uuid4().hex, study_read_dir(config, "sensitivity")
                                 or config.get("sa_output_dir") or output_dir)
            job.config_path = config_path
            job.study_step = config.get("study_step")
            job.meta = dict(config.get("study_meta") or {})
            settings = config.get("settings") or {}
            job.reduce_only = bool(config.get("reduce_only"))
            if job.reduce_only or settings.get("do_param_reduction"):
                # The Sobol run's directory: where the runner wrote its indices.
                job.reduction_root = (
                    (settings.get("param_reduction_options") or {}).get("sensitivity_dir")
                    or job.output_dir
                )
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

    def _reader(self, job: SensitivityJob) -> None:
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

    def _finalize(self, job: SensitivityJob, code: int) -> None:
        with job.lock:
            if job.state == "cancelled":
                return
            # A non-zero exit *after* the DONE marker is a teardown failure,
            # not a failed run -- see calibration.finished_before_exiting.
            from sensitivity_runner import (  # noqa: PLC0415
                DONE_MARKER,
                FAIL_MARKER,
                META_MARKER,
            )

            finished = code == 0 or finished_before_exiting(
                job.lines, DONE_MARKER, FAIL_MARKER
            )
            # Indices come from circulatory_autogen's own files (#210): the Sobol
            # CSV CA writes, or the local-sensitivity CSVs in CA's format. Only
            # the run metadata that no file holds -- which method ran, and the
            # point a local run was linearised about -- arrives on the pipe.
            meta = read_meta_line(job.lines, META_MARKER) or self._study_meta(job)
            data = None
            if finished and job.reduction_root:
                # CA's report, found under the Sobol run rather than its location
                # derived again here; only one this job wrote counts.
                job.reduction = ca_run_history.param_reduction(
                    ca_run_history.find_param_reduction(
                        job.reduction_root, newer_than=job.started_at - 1))
                if job.reduction is None:
                    finished = False
                    job.error = "the parameter reduction wrote no param_reduction.json"
            if finished and job.reduce_only:
                # Nothing was simulated: the Sobol indices are the run's it reduced.
                data = ca_run_history.sobol_indices(job.reduction_root) or {
                    "indices": None, "param_names": [], "output_names": []}
            elif finished:
                if meta.get("method") == "local" and job.study_step:
                    # libcuflynx's signed d ln Y / d ln p, labelled as its Sobol indices
                    data = ca_run_history.step_local_sensitivity(
                        ca_run_history.step_result(job.study_step["step_dir"]))
                elif meta.get("method") == "local":
                    data = ca_run_history.local_sensitivity(job.output_dir)
                else:
                    data = ca_run_history.sobol_indices(job.output_dir)
            if data is not None:
                try:
                    job.indices = data["indices"]
                    job.param_names = data["param_names"]
                    job.output_names = data["output_names"]
                    job.nominal = meta.get("nominal")
                    job.nominal_source = meta.get("nominal_source")
                    job.prediction_outputs = list(
                        data.get("prediction_outputs") or meta.get("prediction_outputs") or [])
                    job.gradient_method = meta.get("gradient_method")
                    job.state = "done"
                    if code != 0:
                        job.warning = teardown_warning(code, job.lines)
                except Exception as exc:  # noqa: BLE001
                    job.state = "error"
                    job.error = f"failed to read results: {exc}"
            else:
                job.state = "error"
                # The runner's own reason when it gave one -- e.g. libcuflynx refusing an
                # emulator trained without the prediction features this run includes
                # ("Retrain it with emulator_settings.include_prediction_items: true").
                job.error = job.error or _failure_reason(job.lines, FAIL_MARKER) or (
                    f"runner exited with code {code}")

    @staticmethod
    def _study_meta(job: SensitivityJob) -> dict:
        """A workflow step's meta, which no runner line carries: the method and gradient
        source it was started with, and the point a local analysis was about, from CA's
        ``step_result.json`` (``nominal``)."""
        if not job.study_step:
            return {}
        meta = dict(job.meta)
        result = ca_run_history.step_result(job.study_step["step_dir"]) or {}
        nominal = result.get("nominal") or {}
        if nominal.get("values") is not None:
            meta["nominal"] = list(nominal["values"])
            source = nominal.get("source") or "model"
            meta["nominal_source"] = ("the model's own values" if source == "model"
                                      else f"study workflow step {source}")
        return meta

    def status(self, job_id: str, offset: int = 0) -> dict | None:
        job = self._job
        if job is None or job.id != job_id:
            return None
        with job.lock:
            raw = job.lines[offset:]
            return {
                "job_id": job.id,
                "state": job.state,
                # the runner's __*__ marker lines are for this manager, not the user: a
                # reduction's log ended in __SENSITIVITY_DONE__, which hid what it found
                "lines": [line for line in raw if not _MARKER_LINE.match(line)],
                "next_offset": offset + len(raw),
                "indices": job.indices,
                "param_names": job.param_names,
                "output_names": job.output_names,
                "nominal": job.nominal,
                "nominal_source": job.nominal_source,
                "prediction_outputs": job.prediction_outputs,
                "gradient_method": job.gradient_method,
                "reduction": job.reduction,
                "error": job.error,
                "warning": job.warning,
                "study_step": job.study_step,
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


def fail_message(lines: list, fail_marker: str) -> str | None:
    """The message the runner printed after its FAIL marker, or None.

    CA's own wording -- e.g. a SensitivityFilesError saying which Sobol files a
    reduction is missing and to rerun the Sobol analysis -- is what the user needs,
    rather than an exit code.
    """
    for line in reversed(lines):
        if fail_marker in line:
            message = line.split(fail_marker, 1)[1].strip()
            return message or None
    return None


# Module-level singletons shared by the FastAPI routes. Parameter reduction runs
# the same runner in its own slot, so it neither blocks nor is blocked by a run
# started from the Sensitivity tab.
sensitivity = SensitivityManager()
param_reduction = SensitivityManager()
