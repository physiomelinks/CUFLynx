"""Calibration workflows: CA's ``libcuflynx.calibration_workflow`` in the GUI.

A workflow (a JSON file) is an ordered set of calibrations merged into one target model.
Each step names its model (a CA module array, or a module of a module library), its
obs_data and its params_for_id. **All of it is CA's**: the file format and its checks, how
a step's parameters are named in the target, running the steps, and the run directory.
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
* ``load_workflow_run`` -- a run directory read back;
* ``pack_workflow`` -- every file a run needs, in one directory (the OMEX export);
* ``write_calibrated_parameters_to`` -- the target's parameters with a run's merged
  values (nested JSON), sent back to PhLynx as the parameters CSV PhLynx reads
  (``utilities.parameters_file.parameter_rows``).

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

from ca_imports import CaImportError, ca_from, ensure_ca_path
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
#: A step's own input files, and the suffix a new one is named with (``<step><suffix>``).
STEP_INPUTS = {"obs_data": "_obs_data.json", "params_for_id": "_params_for_id.json"}
#: The columns of the parameters CSV PhLynx reads, in the order PhLynx writes them; any other
#: key of a parameter (a library's ``sourced``) follows.
PHLYNX_PARAMETER_COLUMNS = ("variable_name", "units", "value", "data_reference")

NEEDS_CA = (
    "Calibration workflows need a circulatory_autogen (libcuflynx) with "
    "libcuflynx.calibration_workflow (physiomelinks/circulatory_autogen#541). "
    "Point Settings -> CA dir at one, or upgrade libcuflynx."
)


def _params_for_id_json(content: str) -> str:
    """A step's params_for_id as the JSON text to write: JSON as given (checked to be
    JSON), a CSV converted as ``cuflynx-upgrade params-for-id`` converts it."""
    import params_json  # noqa: PLC0415

    try:
        if params_json.looks_like_json(content):
            json.loads(content)
            return content
        return params_json.csv_to_file_text(content)
    except (ValueError, params_json.ParamsJsonError) as exc:
        raise WorkflowError(f"the params_for_id is neither JSON nor a params_for_id CSV: "
                            f"{exc}") from exc


def phlynx_parameters_csv(path: str) -> tuple[str, str]:
    """The model parameters file ``path`` (``<prefix>_parameters.json``, nested) as the
    parameters CSV PhLynx reads: ``(<prefix>_parameters.csv, text)``. One row per
    parameter, named ``<instance>/<variable>`` (``heart/lv/E_A``; a global by its name),
    as CA's ``parameter_rows`` names it."""
    import csv  # noqa: PLC0415
    import io  # noqa: PLC0415

    read, rows_of = _ca_from_parameters_file("read_parameters_file", "parameter_rows")
    rows = rows_of(read(path))
    extra = [k for row in rows for k in row if k not in PHLYNX_PARAMETER_COLUMNS]
    out = io.StringIO()
    writer = csv.DictWriter(out, fieldnames=list(PHLYNX_PARAMETER_COLUMNS)
                            + list(dict.fromkeys(extra)), lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return os.path.splitext(os.path.basename(path))[0] + ".csv", out.getvalue()


def _ca_from_parameters_file(*names):
    try:
        ensure_ca_path()
        return ca_from("utilities.parameters_file", *names)
    except (CaImportError, ImportError) as exc:
        raise WorkflowUnavailable(f"{NEEDS_CA} ({exc})") from exc


class WorkflowUnavailable(RuntimeError):
    """The configured CA has no calibration_workflow module."""


class WorkflowError(ValueError):
    """A workflow CA refused, with CA's own message."""


class UnsetParameters(WorkflowError):
    """A step whose model has parameters nothing gives a value (CA's
    ``UnsetParametersError``): ``step_id``, and ``names`` as the target's parameters
    file names them (``C/x``). A cut boundary condition is the usual one; a protocol in
    the step's obs_data (``params_to_change``) sets it."""

    def __init__(self, message: str, step_id: str, names: list[str]):
        super().__init__(message)
        self.step_id, self.names = step_id, list(names)

    def as_dict(self) -> dict:
        return {"step_id": self.step_id, "names": self.names}


def ca_workflow(name: str):
    """``libcuflynx.calibration_workflow.<name>`` from the configured CA.

    The configured CA dir is put on ``sys.path`` first, as every other CA-backed
    module does: ``ca_from`` imports from whatever is already importable. Without
    it, a venv with an editable libcuflynx (pointing at some other checkout) answers
    instead, and the user's Settings -> CA dir is silently ignored -- and when that
    other checkout predates #541, the error blames the CA dir the user *did* pick.
    """
    ensure_ca_path()
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
    from workflow_runner import unset_parameters  # noqa: PLC0415

    fn = ca_workflow(name)
    try:
        return fn(*args, **kwargs)
    except (ValueError, FileNotFoundError, KeyError) as exc:
        unset = unset_parameters(exc)
        if unset:
            raise UnsetParameters(str(exc), **unset) from exc
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
        # {"step_id", "names"}: the step that stopped on parameters nothing gives a value
        self.unset_parameters: dict | None = None
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
        # model_id -> the tab it was loaded for, so the target's variable mapping can be
        # kept where CA's methods writer looks for it (beside the target's parameters file).
        self.view_models: dict[str, str] = {}
        self._job: WorkflowJob | None = None
        self._lock = threading.Lock()

    def reset(self) -> None:
        with self._lock:
            job = self._job
            self._job = None
            self.path = self.output_dir = None
            self.module_library_dirs = []
            self.view_models = {}
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
        # The snapshot's relative paths (module_library_dirs, each step's files) were
        # relative to the original file, not to the run; the original is named in the result.
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
                              "step_states": dict(job.step_states), "error": job.error,
                              "unset_parameters": job.unset_parameters}
        return out

    def view(self, view: str, work_dir: str) -> dict:
        """The model, obs_data and params_for_id one tab shows (CA's workflow_model)."""
        if not self.loaded:
            raise WorkflowError("no workflow is open")
        if not self.output_dir:
            raise WorkflowError("the workflow has no run directory; set an outputs directory")
        return _ca_call("workflow_model", self.path, view, output_dir=self.output_dir,
                        work_dir=work_dir, module_library_dirs=self.module_library_dirs)

    def pack(self, dest_dir: str) -> dict[str, bytes]:
        """The open workflow as CA packs it: ``{path in the pack: bytes}``, inputs only."""
        if not self.loaded:
            raise WorkflowError("no workflow is open")
        packed = _ca_call("pack_workflow", self.path, dest_dir,
                          module_library_dirs=self.module_library_dirs)
        files = {}
        for relative in packed["files"]:
            with open(os.path.join(dest_dir, relative), "rb") as fh:
                files[relative.replace(os.sep, "/")] = fh.read()
        return files

    def target_model(self, work_dir: str) -> dict:
        """The target as the workflow names it, with no calibrated value (no results)."""
        if not self.loaded:
            raise WorkflowError("no workflow is open")
        return _ca_call("workflow_model", self.path, TARGET_VIEW,
                        output_dir=os.path.join(work_dir, "no_run"),
                        work_dir=os.path.join(work_dir, "model"),
                        module_library_dirs=self.module_library_dirs)

    # -- a step's inputs -----------------------------------------------------

    def _step(self, raw: dict, step_id: str, role: str) -> dict:
        if role not in STEP_INPUTS:
            raise WorkflowError(f"a step's input is obs_data or params_for_id, not {role!r}")
        step = next((s for s in raw.get("steps") or [] if s.get("id") == step_id), None)
        if step is None:
            raise WorkflowError(f'the workflow has no step "{step_id}"')
        if step.get("kind") == "reduction":
            raise WorkflowError(f'step "{step_id}" is a reduction, which takes no {role}')
        if role == "params_for_id" and step.get("params_for_id_from"):
            raise WorkflowError(f'step "{step_id}" fits what step "{step["params_for_id_from"]}" '
                                f'kept, so it takes no params_for_id of its own')
        return step

    def _read_raw(self) -> dict:
        if not self.loaded:
            raise WorkflowError("no workflow is open")
        with open(self.path) as fh:
            return json.load(fh)

    def _resolve(self, path: str) -> str:
        return path if os.path.isabs(path) else os.path.join(os.path.dirname(self.path), path)

    def step_input(self, step_id: str, role: str) -> dict:
        """A step's obs_data or params_for_id as the workflow names it: ``{path, content}``,
        both None when it names none. A selection step's file names the target's instances."""
        step = self._step(self._read_raw(), step_id, role)
        named = step.get(role)
        if not isinstance(named, str) or not named:
            return {"path": None, "content": None}
        path = self._resolve(named)
        try:
            with open(path, encoding="utf-8") as fh:
                return {"path": path, "content": fh.read()}
        except OSError as exc:
            raise WorkflowError(f'step "{step_id}": cannot read its {role} {path}: {exc}') from exc

    def write_step_input(self, step_id: str, role: str, content: str) -> str:
        """Write a step's obs_data or params_for_id, and return its path.

        The file the step names is overwritten; CA then marks the step and every step after
        it stale. A step naming none gets ``<step>_obs_data.json`` (or
        ``<step>_params_for_id.json``) beside the workflow file, which the workflow file then
        names. A params_for_id CSV is converted to the JSON CA reads (``cuflynx-upgrade
        params-for-id``'s form), and a step that names a CSV is pointed at a new ``.json``.
        """
        if self.busy:
            raise RuntimeError("a workflow is running; its inputs change after it")
        if role == "obs_data":
            try:
                json.loads(content)
            except ValueError as exc:
                raise WorkflowError(f"the obs_data is not JSON: {exc}") from exc
        raw = self._read_raw()
        step = self._step(raw, step_id, role)
        if role == "params_for_id":
            content = _params_for_id_json(content)
        named = step.get(role)
        if isinstance(named, str) and named and (
                role != "params_for_id" or named.lower().endswith(".json")):
            path = self._resolve(named)
        else:
            # a new file: beside the workflow, or a CSV's .json beside the CSV; one already
            # there is never taken over
            first = (os.path.splitext(named)[0] + ".json" if isinstance(named, str) and named
                     else f"{step_id}{STEP_INPUTS[role]}")
            name, n = first, 2
            while os.path.exists(self._resolve(name)):
                stem, ext = os.path.splitext(first)
                name, n = f"{stem}_{n}{ext}", n + 1
            path = self._resolve(name)
            step[role] = name
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8", newline="") as fh:
            fh.write(content)
        if step.get(role) != named:
            with open(self.path, "w") as fh:
                json.dump(raw, fh, indent=2)
                fh.write("\n")
        return path

    def calibrated_target(self, dest_dir: str) -> dict[str, bytes]:
        """The target's module array and its parameters file with the run's merged values
        (CA's ``write_calibrated_parameters_to``), by their file names: what a completed
        workflow sends back to PhLynx. WorkflowError when the target is not a module array,
        or a step has no current result."""
        # CA's reading of the target: a bare module-array path names its parameters file too
        target = _ca_call("plan_workflow", self.path, self.module_library_dirs).get("target") or {}
        if not (isinstance(target.get("module_array"), str) and isinstance(target.get("parameters"), str)):
            raise WorkflowError("the target is not a module array, so there is none to send back")
        if not self.output_dir:
            raise WorkflowError("the workflow has no run directory")
        array = self._resolve(target["module_array"])
        params = os.path.join(dest_dir, os.path.basename(target["parameters"]))
        _ca_call("write_calibrated_parameters_to", self.path, self.output_dir, params,
                 module_library_dirs=self.module_library_dirs)
        with open(array, "rb") as fh:
            files = {os.path.basename(array): fh.read()}
        name, csv_text = phlynx_parameters_csv(params)
        files[name] = csv_text.encode("utf-8")
        return files

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
        elif kind == "unset_parameters":
            job.unset_parameters = {"step_id": step, "names": list(event.get("names") or [])}

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
                "unset_parameters": job.unset_parameters,
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
