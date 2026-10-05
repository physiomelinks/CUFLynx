"""Calibration workflows: CA's ``libcuflynx.calibration_workflow`` in the GUI.

A workflow (``calibration_workflow.json``) is an ordered set of calibrations of
module-library instances -- typically a supermodule's submodules, then the
supermodule -- merged into one supermodule instance. **All of it is CA's**: the
file format and its checks, where each step's instance lives, how a submodule's
parameter is named in the supermodule, running the steps, and the run directory.
This module only holds which workflow the session has open, and runs
``workflow_runner.py`` as a subprocess the way the calibration manager runs
its runner.

**Not named ``calibration_workflow``**, deliberately: ``ca_from`` resolves CA's
modules by their flat name as well as ``libcuflynx.<name>``, so an app module
called ``calibration_workflow`` already sitting in ``sys.modules`` would be handed
back as CA's -- and the fake the tests register for CA's would replace this one.

What CA provides, reached through ``ca_from`` (never an import statement):

* ``load_workflow`` / ``plan_workflow`` -- read and check a workflow, and say
  which parameter of each step lands where (no generation);
* ``workflow_status`` -- where each step stands in a run directory;
* ``workflow_model`` -- the model one tab shows, from the results so far;
* ``run_calibration_workflow`` -- the run (in the runner);
* ``load_workflow_run`` -- a run directory read back.

A CA without them (older than the workflow feature) raises
:class:`WorkflowUnavailable`, which the routes turn into a 501 that says so.

**The run is stored by CA.** ``run_calibration_workflow`` writes the workflow it
ran (``calibration_workflow.json``), each step's results and the merged set into
the run directory, so reopening that directory reopens the workflow -- nothing
CUFLynx-authored is written there (#210).
"""

from __future__ import annotations

import json
import os
import subprocess
import threading
import time
import uuid

from ca_imports import CaImportError, ca_from
from calibration import (
    _warn_no_mpiexec,
    calibration,
    clear_run_config,
    finished_before_exiting,
    resolve_mpiexec,
    write_run_config,
)
from runtime_paths import runner_command, runner_launch_env, runner_path

RUNNER_PATH = str(runner_path("workflow_runner.py"))
WORKFLOW_FILE_NAME = "calibration_workflow.json"
TARGET_VIEW = "target"

NEEDS_CA = (
    "Calibration workflows need a circulatory_autogen (libcuflynx) with "
    "libcuflynx.calibration_workflow (physiomelinks/circulatory_autogen#541). "
    "Point Settings -> CA dir at one, or upgrade libcuflynx."
)


class WorkflowUnavailable(RuntimeError):
    """The configured CA has no calibration_workflow module."""


class WorkflowError(ValueError):
    """A workflow CA refused, with CA's own message."""


def ca_workflow(name: str):
    """``libcuflynx.calibration_workflow.<name>`` from the configured CA."""
    try:
        return ca_from("calibration_workflow", name)
    except CaImportError as exc:
        raise WorkflowUnavailable(NEEDS_CA) from exc


def _ca_call(name: str, *args, **kwargs):
    """Call a CA workflow function, turning CA's refusal into a WorkflowError.

    CA raises its own ``WorkflowError`` (a ``ValueError``) with the reason in the
    message, e.g. "step "rest" takes fixed values from "fit_a", which it does not
    depend on" -- which is exactly what the user needs to read, so it is passed
    through verbatim rather than wrapped in a CUFLynx sentence.
    """
    fn = ca_workflow(name)
    try:
        return fn(*args, **kwargs)
    except (ValueError, FileNotFoundError, KeyError) as exc:
        raise WorkflowError(str(exc)) from exc


def default_output_dir(outputs_dir: str, workflow_name: str, fallback_root: str) -> str:
    """``<outputs>/workflows/<name>``, or under ``fallback_root`` with no outputs dir."""
    root = outputs_dir.strip() if outputs_dir else ""
    return os.path.join(root or fallback_root, "workflows", workflow_name)


class WorkflowJob:
    def __init__(self, job_id: str, output_dir: str):
        self.id = job_id
        self.output_dir = output_dir
        self.lines: list[str] = []
        self.events: list[dict] = []
        self.state = "running"  # running | done | error | cancelled
        self.error: str | None = None
        self.warning: str | None = None
        self.current_step: str | None = None
        self.step_states: dict[str, str] = {}
        self.proc: subprocess.Popen | None = None
        self.config_path: str | None = None
        self.started_at = time.time()
        self.lock = threading.Lock()


class WorkflowManager:
    """The workflow the session has open, and its one run at a time."""

    def __init__(self):
        self.runner_path = RUNNER_PATH
        self.path: str | None = None
        self.output_dir: str | None = None
        self.module_library_dirs: list[str] = []
        self._job: WorkflowJob | None = None
        self._lock = threading.Lock()

    def reset(self) -> None:
        with self._lock:
            job = self._job
            self._job = None
            self.path = self.output_dir = None
            self.module_library_dirs = []
        if job and job.proc and job.proc.poll() is None:
            job.proc.terminate()

    @property
    def busy(self) -> bool:
        job = self._job
        return job is not None and job.state == "running"

    # -- loading -------------------------------------------------------------

    def load(self, path: str, output_dir: str, module_library_dirs: list[str]) -> dict:
        """Open the workflow file at ``path``, its run going to ``output_dir``."""
        if self.busy:
            raise RuntimeError("a workflow is running; cancel it before opening another")
        path = os.path.abspath(path)
        if not os.path.isfile(path):
            raise WorkflowError(f"no workflow file at {path}")
        workflow = _ca_call("load_workflow", path, module_library_dirs)
        self.path = path
        self.output_dir = os.path.abspath(output_dir) if output_dir else None
        self.module_library_dirs = list(module_library_dirs)
        return {"name": workflow.name}

    def load_run(self, run_dir: str, module_library_dirs: list[str]) -> dict:
        """Reopen a workflow run directory: the workflow CA stored there, and its results."""
        run_dir = os.path.abspath(run_dir)
        snapshot = os.path.join(run_dir, WORKFLOW_FILE_NAME)
        if not os.path.isfile(snapshot):
            raise WorkflowError(
                f"{run_dir} is not a calibration workflow run: it has no {WORKFLOW_FILE_NAME}")
        # The snapshot's relative module_library_dirs were relative to the
        # original file, not to the run; the original is named in the result.
        result_path = os.path.join(run_dir, "workflow_result.json")
        original = None
        if os.path.isfile(result_path):
            try:
                with open(result_path) as fh:
                    original = json.load(fh).get("workflow_file")
            except (OSError, ValueError):
                original = None
        source = original if original and os.path.isfile(original) else snapshot
        return self.load(source, run_dir, module_library_dirs)

    @property
    def loaded(self) -> bool:
        return self.path is not None

    def describe(self) -> dict:
        """Everything the workflow bar shows: the file, its plan, each step's state."""
        if not self.loaded:
            return {"loaded": False}
        out: dict = {
            "loaded": True,
            "path": self.path,
            "output_dir": self.output_dir,
            "module_library_dirs": self.module_library_dirs,
        }
        try:
            with open(self.path) as fh:
                out["workflow"] = json.load(fh)
        except (OSError, ValueError) as exc:
            out["error"] = f"cannot read {self.path}: {exc}"
            return out
        try:
            out["plan"] = _ca_call("plan_workflow", self.path, self.module_library_dirs)
        except WorkflowError as exc:
            # A workflow whose library is not configured still shows what it
            # says; it just cannot be resolved or run until the library is set.
            out["plan_error"] = str(exc)
            return out
        if self.output_dir and os.path.isdir(self.output_dir):
            try:
                out["status"] = _ca_call("workflow_status", self.path, self.output_dir,
                                         self.module_library_dirs)
            except WorkflowError as exc:
                out["status_error"] = str(exc)
        job = self._job
        if job is not None:
            with job.lock:
                out["job"] = {"job_id": job.id, "state": job.state,
                              "current_step": job.current_step,
                              "step_states": dict(job.step_states), "error": job.error}
        return out

    def view(self, view: str, work_dir: str) -> dict:
        """The model, obs_data and params_for_id one tab shows (CA's workflow_model)."""
        if not self.loaded:
            raise WorkflowError("no workflow is open")
        if not self.output_dir:
            raise WorkflowError("the workflow has no run directory; set an outputs directory")
        return _ca_call("workflow_model", self.path, view, output_dir=self.output_dir,
                        work_dir=work_dir, module_library_dirs=self.module_library_dirs)

    # -- running -------------------------------------------------------------

    def build_command(self, config: dict, config_path: str) -> list[str]:
        python = config.get("python") or calibration.python
        base = runner_command(python, self.runner_path, config_path)
        num_cores = int(config.get("num_cores", 1) or 1)
        if num_cores > 1:
            mpiexec = resolve_mpiexec(python)
            if mpiexec is None:
                _warn_no_mpiexec(num_cores)
                return base
            return [mpiexec, "-n", str(num_cores), *base]
        return base

    def start(self, from_step: str | None = None, only: str | None = None,
              num_cores: int = 1, python: str | None = None) -> str:
        with self._lock:
            if not self.loaded:
                raise WorkflowError("no workflow is open")
            if not self.output_dir:
                raise WorkflowError("the workflow has no run directory; set an outputs directory")
            if self.busy:
                raise RuntimeError("a workflow is already running")
            if calibration.busy:
                raise RuntimeError("a calibration is running; a workflow runs after it")
            config = {
                "workflow": self.path, "output_dir": self.output_dir,
                "module_library_dirs": self.module_library_dirs,
                "from_step": from_step or None, "only": only or None,
                "num_cores": num_cores, "python": python,
            }
            os.makedirs(self.output_dir, exist_ok=True)
            config_path = write_run_config(config, "workflow_config.json")
            job = WorkflowJob(uuid.uuid4().hex, self.output_dir)
            job.config_path = config_path
            env = runner_launch_env(python or calibration.python)
            job.proc = subprocess.Popen(
                self.build_command(config, config_path),
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, bufsize=1, env=env,
                # An explicit, lasting working directory. Without one the runner
                # inherits the server's *current* one -- and Myokit chdirs the whole
                # server into a temporary build directory while it compiles a live
                # simulation, then deletes it. A run started in that window began in
                # a directory that no longer existed, and failed at its first Myokit
                # compile with "[Errno 2] No such file or directory".
                cwd=self.output_dir,
            )
            self._job = job
        threading.Thread(target=self._reader, args=(job,), daemon=True).start()
        return job.id

    def _reader(self, job: WorkflowJob) -> None:
        from workflow_runner import EVENT_MARKER  # noqa: PLC0415

        try:
            assert job.proc and job.proc.stdout is not None
            for raw in job.proc.stdout:
                line = raw.rstrip("\n")
                with job.lock:
                    job.lines.append(line)
                    if line.startswith(EVENT_MARKER):
                        try:
                            self._apply_event(job, json.loads(line[len(EVENT_MARKER):]))
                        except ValueError:
                            pass
        finally:
            code = job.proc.wait() if job.proc else -1
            clear_run_config(job.config_path)
            self._finalize(job, code)

    @staticmethod
    def _apply_event(job: WorkflowJob, event: dict) -> None:
        job.events.append(event)
        kind, step = event.get("event"), event.get("step")
        if kind == "workflow_started":
            for step_id in event.get("running", []):
                job.step_states[step_id] = "queued"
        elif kind == "step_started":
            job.current_step = step
            job.step_states[step] = "running"
        elif kind == "step_finished":
            job.step_states[step] = "done"
            job.current_step = None
        elif kind == "step_failed":
            job.step_states[step] = "failed"
            job.error = f"step {step}: {event.get('error', 'failed')}"
        elif kind == "step_skipped":
            job.step_states[step] = "reused"

    def _finalize(self, job: WorkflowJob, code: int) -> None:
        from workflow_runner import DONE_MARKER, FAIL_MARKER  # noqa: PLC0415

        with job.lock:
            if job.state == "cancelled":
                if job.current_step:
                    job.step_states[job.current_step] = "cancelled"
                return
            finished = code == 0 or finished_before_exiting(job.lines, DONE_MARKER, FAIL_MARKER)
            if finished and any(e.get("event") == "workflow_finished" for e in job.events):
                job.state = "done"
            else:
                job.state = "error"
                if job.error is None:
                    failure = next((ln[len(FAIL_MARKER):].strip() for ln in reversed(job.lines)
                                    if ln.startswith(FAIL_MARKER)), "")
                    job.error = failure or f"workflow runner exited with code {code}"

    def status(self, job_id: str, offset: int = 0) -> dict | None:
        job = self._job
        if job is None or job.id != job_id:
            return None
        with job.lock:
            lines = job.lines[offset:]
            return {
                "job_id": job.id, "state": job.state, "lines": lines,
                "next_offset": offset + len(lines), "current_step": job.current_step,
                "step_states": dict(job.step_states), "events": list(job.events),
                "error": job.error, "warning": job.warning,
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


workflow = WorkflowManager()
