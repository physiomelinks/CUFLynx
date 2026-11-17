"""The study workflow: every analysis run from the GUI is a step of one calibration workflow.

A working dir ``<W>`` holds one growing workflow, ``<W>/workflow/calibration_workflow.json``,
written with CA's ``WorkflowBuilder`` (``libcuflynx.calibration_workflow``). Each calibration,
sensitivity analysis and UQ run from a panel adds a step to it and runs that step alone
(``only=<step>``, or ``from_step=`` when the run added a set step it starts from) through
``workflow_runner.py``, into the workflow's run directory ``<W>/workflows/<name>/``. So the
workflow file is the record of what was run, in order, and CA's ``provenance.svg`` in the run
directory draws it after every run.

* **The target** is the loaded model: ``cellml_model`` for a CellML (a ``.mmt`` runs as the
  CellML it was converted into) or ``external_model`` for an external Python model, the stored
  copy in ``<W>/inputs/``. Every step names the same model, as CA requires. Loading another
  model (or another version of it) into the same working dir starts a new workflow; the old
  file is kept beside it as ``calibration_workflow_<name>.json``.
* **Inputs** are the working dir's stored versions (``<W>/inputs/<kind>/<stamp>/``), so each
  step names the obs_data and params_for_id it ran with.
* **Values nobody fitted** -- the slider values, a bounds midpoint, the best-so-far of a
  calibration that was stopped -- go in a ``set`` step with the reason, and the step that uses
  them takes them with ``start_from`` / ``result_from``. The step is ``"point": true`` (where
  the CA has the key), so those values never reach the merged target. A previous calibration
  step is named directly.

Which runs have no workflow form, and so keep the direct runner, is decided in
:func:`direct_reason`; the run log says why. A CA without the workflow features this needs
(``cellml_model``, the ``uq`` step kind, ``start_from``) keeps every run on the direct runners
(:func:`unavailable_reason`).

Nothing here writes to the run directory: CA does (#210). This module writes the workflow
file, through CA's builder, and reads ``step_result.json`` back through ``ca_run_history``.
"""

from __future__ import annotations

import inspect
import os
import re
import threading

import ca_run_history
from ca_imports import CaImportError, ca_from, ensure_ca_path

WORKFLOW_DIR = "workflow"
WORKFLOW_FILE_NAME = "calibration_workflow.json"
#: The workflow's name, and its run directory's: ``<W>/workflows/<name>``. A new workflow in
#: the same working dir (another model) is ``study-2``, ...
WORKFLOW_NAME = "study"
RUNS_DIR = "workflows"
#: The reason a set step of slider values carries into the provenance.
SLIDER_REASON = "slider values in CUFLynx"

#: ``CUFLYNX_STUDY_WORKFLOW=0`` runs every analysis on its direct runner, as before.
ENV_SWITCH = "CUFLYNX_STUDY_WORKFLOW"

#: Step id labels, by kind of run.
LABELS = {"calibration": "calibration", "sobol": "sobol", "local": "local-sa",
          "mcmc": "mcmc", "laplace": "laplace", "profile": "profile-likelihood",
          "sliders": "sliders",
          "bounds": "nominal", "best_fit": "best-fit"}

#: Settings keys of a panel that are CUFLynx's own and never reach CA's option dicts. A uq
#: step's ``posterior_predictive`` / ``posterior_predictive_samples`` do: they are its
#: ``UQ_options``.
_PANEL_ONLY = {"start_from", "start_from_current", "use_emulator", "emulator_dir", "min_r2",
               "out_of_bounds", "fd_rel_step", "config_outputs_dir", "python_path",
               "num_cores"}

_lock = threading.Lock()


class StudyWorkflowError(RuntimeError):
    """The step could not be added (CA refused the workflow); the message is CA's."""


def enabled() -> bool:
    return os.environ.get(ENV_SWITCH, "1").strip() != "0"


def _ca(*names):
    """Names from CA's ``calibration_workflow.builder``, where they are defined. Not from
    the package: its ``cellml_model`` function is replaced by the ``cellml_model`` submodule
    of the same name once anything imports that submodule (CA's calibrated-CellML writer
    does), after which ``cellml_model(path)`` raises "'module' object is not callable"."""
    ensure_ca_path()
    found = ca_from("calibration_workflow.builder", *names)
    return found if len(names) > 1 else (found,)


def unavailable_reason() -> str | None:
    """None when the configured CA can record runs as workflow steps; else why not."""
    if not enabled():
        return f"{ENV_SWITCH}=0"
    try:
        builder, _, _ = _ca("WorkflowBuilder", "cellml_model", "external_model")
        kinds = ca_from("calibration_workflow.spec", "STEP_KINDS")
    except (CaImportError, ImportError) as exc:
        return f"this libcuflynx has no calibration workflow builder with CellML models ({exc})"
    if "uq" not in kinds or not hasattr(builder, "add_uq"):
        return "this libcuflynx's calibration workflows have no uq step"
    if "start_from" not in inspect.signature(builder.add_calibration).parameters:
        return "this libcuflynx's calibration workflows have no start_from"
    return None


def point_steps() -> bool:
    """Whether the configured CA's workflow schema has a set step's ``point`` key: its values
    are then only a point for ``start_from`` / ``result_from`` and are never merged into the
    workflow's target. Without it, CUFLynx's set steps are merged like any other."""
    try:
        ensure_ca_path()
        load_schema, name = ca_from("schemas", "load_schema", "CALIBRATION_WORKFLOW_SCHEMA")
        step = load_schema(name)["$defs"]["step"]["properties"]
    except (CaImportError, ImportError, KeyError, TypeError, OSError, ValueError):
        return False
    return "point" in step


def workflow_path(working_dir: str) -> str:
    return os.path.join(working_dir, WORKFLOW_DIR, WORKFLOW_FILE_NAME)


def run_dir(working_dir: str, name: str) -> str:
    return os.path.join(working_dir, RUNS_DIR, name)


def cellml_model_types() -> tuple:
    """The model types a CellML workflow model runs as: CA's ``CELLML_MODEL_TYPES`` (CA
    generates the step's backend from the CellML), or ``cellml`` alone on a CA without it."""
    try:
        ensure_ca_path()
        return tuple(ca_from("calibration_workflow.spec", "CELLML_MODEL_TYPES"))
    except ImportError:
        return ("cellml",)


def runs_from_cellml(model_type: str) -> bool:
    """Whether a run of this model type is a CellML workflow model."""
    return model_type in (None, "") or model_type in cellml_model_types()


def target_model(model_path: str, model_type: str) -> dict | None:
    """The workflow model for the loaded model, or None when it has no workflow form.

    ``model_path`` is the CellML for every model type CA generates from it (never the
    generated ``.py``), and the user's own file for ``external_python``.
    """
    _, cellml_model, external_model = _ca("WorkflowBuilder", "cellml_model", "external_model")
    if model_type == "external_python":
        return external_model(os.path.abspath(model_path))
    if runs_from_cellml(model_type):
        return cellml_model(os.path.abspath(model_path))
    return None


def step_records(kind: str, output: str) -> bool:
    """Whether CA's workflow steps of ``kind`` record ``output`` (``runner.STEP_OUTPUTS``):
    how a feature a step gained is detected, False on a CA without it."""
    try:
        ensure_ca_path()
        outputs = ca_from("calibration_workflow.runner", "STEP_OUTPUTS")
    except ImportError:
        return False
    return output in (outputs.get(kind) or ())


def direct_reason(model_type: str, *, held_out: bool = False,
                  modifier_point: bool = False, profile: bool = False) -> str | None:
    """Why this run keeps its direct runner, or None when it can be a workflow step.

    * a model type with no workflow model kind: a CellML model runs only as the model types
      CA generates from it in a workflow (:func:`cellml_model_types`), so ``cpp`` has none;
    * held-out data in the obs_data, on a CA whose calibration steps do not validate the
      best fit against it (``validation_results.json``) as the direct calibration does;
    * a point to start from or analyse at, when the params_for_id has a modifier entry: the
      point is a modifier's theta, and a set step sets model values;
    * a profile likelihood (the Identifiability tab): a uq step runs MCMC or a Laplace
      approximation, and the UQ panel reads the profiles from the direct run's files.
    """
    if model_type != "external_python" and not runs_from_cellml(model_type):
        return (f"the {model_type} model type has no calibration workflow model kind "
                f"(a CellML model runs in a workflow as {', '.join(cellml_model_types())})")
    if held_out and not step_records("calibration", "validation_results"):
        return ("the obs_data has held-out data, which a workflow calibration step does not "
                "validate the best fit against")
    if modifier_point:
        return ("the params_for_id has a modifier, and a set step cannot give a modifier's "
                "theta as the point to start from or analyse at")
    if profile and not step_records("identifiability", "profile_likelihood_dir"):
        return ("a profile likelihood is not a uq step (those run MCMC or a Laplace "
                "approximation), and this CA's identifiability steps do not record one")
    return None


def _abs(path: str, directory: str) -> str:
    return os.path.normpath(path if os.path.isabs(path) else os.path.join(directory, path))


def _model_file(model: dict | str | None, directory: str) -> str | None:
    if not isinstance(model, dict):
        return None
    for key in ("cellml_model_path", "external_model_path"):
        if isinstance(model.get(key), str):
            return _abs(model[key], directory)
    return None


def _taken_names(directory: str, working_dir: str) -> set[str]:
    names = set()
    if os.path.isdir(directory):
        for entry in os.listdir(directory):
            m = re.fullmatch(r"calibration_workflow_(.+)\.json", entry)
            if m:
                names.add(m.group(1))
    runs = os.path.join(working_dir, RUNS_DIR)
    if os.path.isdir(runs):
        names.update(os.listdir(runs))
    return names


def open_workflow(working_dir: str, target: dict):
    """The study workflow's builder: the existing file when its target is this model, else a
    new workflow (the old file kept as ``calibration_workflow_<name>.json``)."""
    (Builder,) = _ca("WorkflowBuilder")
    path = workflow_path(working_dir)
    directory = os.path.dirname(path)
    if os.path.isfile(path):
        builder = Builder.load(path)
        current = builder.to_dict()
        if _model_file(current.get("target"), directory) == _model_file(target, directory):
            return builder
        os.replace(path, os.path.join(directory,
                                      f"calibration_workflow_{current['workflow_name']}.json"))
    taken = _taken_names(directory, working_dir)
    name, n = WORKFLOW_NAME, 2
    while name in taken:
        name, n = f"{WORKFLOW_NAME}-{n}", n + 1
    os.makedirs(directory, exist_ok=True)
    return Builder(name, target, directory=directory)


def next_id(builder, label: str, working_dir: str) -> str:
    """``<label>-<n>``: one more than any step (or step directory) of that label."""
    pattern = re.compile(rf"{re.escape(label)}-(\d+)")
    taken = list(builder.steps)
    runs = run_dir(working_dir, builder.name)
    if os.path.isdir(runs):
        taken += os.listdir(runs)
    numbers = [int(m.group(1)) for s in taken if (m := pattern.fullmatch(s))]
    return f"{label}-{max(numbers, default=0) + 1}"


def step_settings(*, model_type: str, solver: str | None, solver_info: dict | None,
                  dt: float | None, funcs: dict, debug: bool = False,
                  extra: dict | None = None) -> dict:
    """A step's settings: the solve and the user funcs the run is given, and ``extra``.

    Per step rather than the workflow's, so changing the solver for one run does not make
    every earlier step stale. An external model's solver is CA's to set (``external_inputs``),
    and so is the values file each step's model is generated with.
    """
    out: dict = {}
    if model_type != "external_python" and runs_from_cellml(model_type):
        # CA generates this backend from the step's CellML; without it the step runs as cellml
        out["model_type"] = model_type or "cellml"
        if solver:
            out["solver"] = solver
    if solver_info:
        out["solver_info"] = {k: v for k, v in solver_info.items()
                              if k != "external_parameters_file"}
    if dt not in (None, ""):
        out["dt"] = float(dt)
    if debug:
        out["DEBUG"] = True
    for key, path in funcs.items():
        if path:
            out[key] = path
    out.update(extra or {})
    return out


def options(settings: dict, reserved=()) -> dict:
    """A panel's settings without the keys that are CUFLynx's own."""
    return {k: v for k, v in (settings or {}).items()
            if k not in _PANEL_ONLY and k not in reserved and v is not None}


def set_entries(values: dict, qnames, reason: str) -> list[dict]:
    """Set-step entries for ``qnames`` (in that order) that ``values`` gives."""
    set_value = ca_from("calibration_workflow.builder", "set_value")
    return [set_value(q, float(values[q]), reason=reason)
            for q in dict.fromkeys(qnames) if q in values and values[q] is not None]


class Plan:
    """The steps one GUI run adds, and how the runner runs them."""

    def __init__(self, builder, working_dir: str):
        self.builder = builder
        self.working_dir = working_dir
        self.added: list[str] = []

    def add(self, adder: str, step_id: str, *args, **kwargs) -> str:
        try:
            getattr(self.builder, adder)(step_id, *args, **kwargs)
        except (ValueError, TypeError) as exc:
            raise StudyWorkflowError(f"the study workflow refused step {step_id}: {exc}") from exc
        self.added.append(step_id)
        return step_id

    def new_id(self, label: str) -> str:
        return next_id(self.builder, label, self.working_dir)

    def add_set(self, kind: str, entries: list[dict]) -> str | None:
        """A set step of ``entries``, its id labelled by ``kind`` (:data:`LABELS`). It is only
        the point a later step starts from or analyses at, so it is written ``point: true``
        where the CA has that key (:func:`point_steps`), and stays out of the merged target."""
        if not entries:
            return None
        point = {"point": True} if point_steps() else {}
        return self.add("add_set", self.new_id(LABELS[kind]), entries, **point)

    @property
    def step(self) -> str:
        """The step whose results the panel reads: the last one added."""
        return self.added[-1]

    @property
    def run_dir(self) -> str:
        return run_dir(self.working_dir, self.builder.name)

    def step_dir(self, step_id: str | None = None) -> str:
        return os.path.join(self.run_dir, step_id or self.step)

    def write(self) -> str:
        try:
            return self.builder.write()
        except (ValueError, OSError) as exc:
            raise StudyWorkflowError(f"the study workflow was not written: {exc}") from exc

    def completes(self) -> bool:
        """Whether every step outside this run has a done result, so a run whose own steps
        succeed leaves the workflow complete."""
        for step in self.builder.to_dict().get("steps") or []:
            step_id = step.get("id")
            if step_id in self.added:
                continue
            if (ca_run_history.step_result(self.step_dir(step_id)) or {}).get("status") != "done":
                return False
        return True

    def runner_config(self, markers: dict) -> dict:
        """The keys a manager's config gains so it runs the step through workflow_runner.

        One step runs ``only``; a run that added several (a set step, or a calibration, then
        the step that uses it) runs ``from_step`` its first, which are the last in the file.

        ``write_calibrated``: the study's target is a CellML or external model, so a run
        writes the merged values beside it (``<stem>_calibrated_parameters.json``, and for a
        CellML the calibrated CellML) -- when it leaves the workflow complete, as CA refuses
        the write for an incomplete run (an earlier step failed or was cancelled).
        """
        only = self.added[0] if len(self.added) == 1 else None
        return {
            "write_calibrated": self.completes(),
            "study_step": {"workflow": self.builder.path, "run_dir": self.run_dir,
                           "step": self.step, "steps": list(self.added),
                           "step_dir": self.step_dir()},
            "workflow": self.builder.path,
            "workflow_output_dir": self.run_dir,
            "only": only,
            "from_step": None if only else self.added[0],
            "module_library_dirs": [],
            "markers": markers,
        }


def lock():
    """Serialises edits of the workflow file (two panels starting runs at once)."""
    return _lock
