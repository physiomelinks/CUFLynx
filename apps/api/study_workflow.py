"""The study workflow: every analysis run from the GUI is a step of one calibration workflow.

A working dir ``<W>`` holds independent workflows, each in its own directory,
``<W>/workflow/<name>/calibration_workflow.json``, written with CA's ``WorkflowBuilder``
(``libcuflynx.calibration_workflow``); an older single ``<W>/workflow/calibration_workflow.json``
(and the ``calibration_workflow_<name>.json`` kept beside it) is still read. Each calibration,
sensitivity analysis and UQ run from a panel is a step of one of them, run through
``workflow_runner.py`` into the workflow's run directory ``<W>/workflows/<name>/``. So the
workflow file is the record of what was run, and CA's ``provenance.svg`` in the run directory
draws it after every run.

* **Which step** a run is comes from its ``workflow_target`` (the Workflow view): by default
  it changes the workflow's latest step of the same kind (:func:`edited_step`), and runs that
  step and the steps that take from it; a new step, or a new workflow, has to be asked for.
  A run that adds a step runs it alone (``only=<step>``, or ``from_step=`` when the run added a
  set step it starts from).
* **The target** is the loaded model: ``cellml_model`` for a CellML (a ``.mmt`` runs as the
  CellML it was converted into) or ``external_model`` for an external Python model, the stored
  copy in ``<W>/inputs/``. Every step names the same model, as CA requires. A run of another
  model (or another version of it) goes to the working dir's workflow of that model, or a new
  one.
* **Editing** the file -- deleting a step (reconnecting what took from it), moving one -- is
  :func:`delete_step` and :func:`move_step`, checked by CA's ``parse_workflow``.
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

import copy
import inspect
import json
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
          "sliders": "sliders", "emulation": "surrogate",
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


def workflow_files(working_dir: str) -> list[str]:
    """Every workflow file of the working dir: ``<W>/workflow/<name>/calibration_workflow.json``
    for each workflow, and an older layout's ``<W>/workflow/calibration_workflow.json`` and
    the ``calibration_workflow_<name>.json`` files kept beside it."""
    base = os.path.join(working_dir, WORKFLOW_DIR)
    if not os.path.isdir(base):
        return []
    out = []
    for entry in sorted(os.listdir(base)):
        path = os.path.join(base, entry)
        if os.path.isfile(os.path.join(path, WORKFLOW_FILE_NAME)):
            out.append(os.path.join(path, WORKFLOW_FILE_NAME))
        elif os.path.isfile(path) and (entry == WORKFLOW_FILE_NAME or re.fullmatch(
                r"calibration_workflow_(.+)\.json", entry)):
            out.append(path)
    return out


def working_dir_of(path: str | None) -> str | None:
    """The working dir whose study workflow ``path`` is, or None for any other file."""
    if not path:
        return None
    path = os.path.abspath(path)
    for up in (2, 3):
        folder = path
        for _ in range(up):
            folder = os.path.dirname(folder)
        if path in (os.path.abspath(f) for f in workflow_files(folder)):
            return folder
    return None


def list_workflows(working_dir: str) -> list[dict]:
    """``{path, name, steps, modified}`` of each workflow of the working dir, the most
    recently written first. A file that is not a workflow is left out."""
    out = []
    for path in workflow_files(working_dir):
        try:
            with open(path, encoding="utf-8-sig") as handle:
                raw = json.load(handle)
            steps = [{"id": s["id"], "kind": s.get("kind", "calibration")}
                     for s in raw.get("steps") or []]
            out.append({"path": path, "name": str(raw["workflow_name"]), "steps": steps,
                        "modified": os.path.getmtime(path)})
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            continue
    return sorted(out, key=lambda w: -w["modified"])


def workflow_path(working_dir: str) -> str:
    """The working dir's latest workflow (the one written last); where a first one named
    :data:`WORKFLOW_NAME` goes when it has none."""
    found = list_workflows(working_dir)
    if found:
        return found[0]["path"]
    return os.path.join(working_dir, WORKFLOW_DIR, WORKFLOW_NAME, WORKFLOW_FILE_NAME)


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


def _taken_names(working_dir: str) -> set[str]:
    """Workflow names the working dir has used: its workflows' and run directories'."""
    names = {w["name"] for w in list_workflows(working_dir)}
    base = os.path.join(working_dir, WORKFLOW_DIR)
    if os.path.isdir(base):
        names.update(e for e in os.listdir(base) if os.path.isdir(os.path.join(base, e)))
    runs = os.path.join(working_dir, RUNS_DIR)
    if os.path.isdir(runs):
        names.update(os.listdir(runs))
    return names


def new_workflow(working_dir: str, target: dict):
    """A new workflow of ``target`` in its own ``<W>/workflow/<name>/``: ``study``, else
    ``study-2``, ... (a name no workflow or run directory of the working dir has)."""
    (Builder,) = _ca("WorkflowBuilder")
    taken = _taken_names(working_dir)
    name, n = WORKFLOW_NAME, 2
    while name in taken:
        name, n = f"{WORKFLOW_NAME}-{n}", n + 1
    directory = os.path.join(working_dir, WORKFLOW_DIR, name)
    os.makedirs(directory, exist_ok=True)
    return Builder(name, target, directory=directory)


def open_workflow(working_dir: str, target: dict, *, path: str | None = None,
                  new: bool = False):
    """``(builder, note)``: the workflow a run of ``target`` goes to, and a note for the run
    log when it is not the one asked for (None when it is).

    ``new``: a new workflow. Else ``path`` (the workflow the user has open) when it is one
    of this working dir's and of this model; else the working dir's latest workflow of this
    model; else a new one.
    """
    (Builder,) = _ca("WorkflowBuilder")
    if new:
        return new_workflow(working_dir, target), None
    files = [w["path"] for w in list_workflows(working_dir)]
    asked = os.path.abspath(path) if path else None
    if asked in files:
        files.remove(asked)
        files.insert(0, asked)
    for candidate in files:
        builder = Builder.load(candidate)
        directory = os.path.dirname(candidate)
        if _model_file(builder.to_dict().get("target"), directory) == _model_file(
                target, directory):
            note = None
            if asked and candidate != asked:
                note = (f"the open workflow {asked} is not of this model, so this run is a "
                        f"step of {candidate}, which is")
            return builder, note
    builder = new_workflow(working_dir, target)
    note = None
    if asked:
        note = (f"the open workflow {asked} is not of this model, so this run starts the new "
                f"workflow {builder.name}")
    return builder, note


#: The keys of a step's model that are file paths, relative to the workflow's folder.
_MODEL_PATH_KEYS = ("cellml_model_path", "external_model_path", "module_array")


def resolved_model(model, directory: str):
    """``model`` (a step's or the target's, as the workflow file writes it) with its file
    paths made absolute against the workflow's folder ``directory``."""
    if isinstance(model, str):
        return os.path.normpath(os.path.join(directory, model))
    if not isinstance(model, dict):
        return model
    out = copy.deepcopy(model)
    for key in _MODEL_PATH_KEYS:
        if isinstance(out.get(key), str):
            out[key] = os.path.normpath(os.path.join(directory, out[key]))
    return out


def model_words(model) -> str:
    """A step's model in a few words, for the run log: ``i_K1 · krapivinsky1998``, a file's
    name, or ``the target``."""
    if not model:
        return "the target"
    if isinstance(model, str):
        return os.path.basename(model)
    if model.get("module_type"):
        return " · ".join(str(model[k]) for k in ("module_type", "parameterisation") if model.get(k))
    for key in ("cellml_model_path", "external_model_path", "module_array"):
        if model.get(key):
            return os.path.basename(str(model[key]))
    if model.get("select"):
        return "a selection of " + ", ".join(map(str, model["select"]))
    return "its model"


#: How a run says which step it is (``workflow_target.mode``): ``default`` changes the
#: workflow's latest step of the same kind of analysis, or adds one when it has none;
#: ``new_step`` adds one; ``edit`` changes ``step``; ``new_workflow`` starts a workflow.
TARGET_MODES = ("default", "new_step", "edit", "new_workflow")


def flavour(step: dict) -> str:
    """The kind of analysis a step is, as the panels tell them apart: CA's step kind, with
    a sensitivity step's ``sobol`` / ``local`` and a uq step's ``mcmc`` / ``laplace``."""
    kind = step.get("kind", "calibration")
    settings = step.get("settings") or {}
    if kind == "sensitivity":
        return "local" if (settings.get("sa_options") or {}).get("method") == "local" else "sobol"
    if kind == "uq":
        return str((settings.get("UQ_options") or {}).get("method") or "mcmc")
    return kind


def kind_of(flavour_name: str) -> str:
    """CA's step kind of a :func:`flavour`."""
    return {"sobol": "sensitivity", "local": "sensitivity", "mcmc": "uq",
            "laplace": "uq"}.get(flavour_name, flavour_name)


def file_steps(builder) -> list[dict]:
    """The builder's steps as its file has them (none for a new workflow)."""
    if not builder.steps:
        return []
    return list(builder.to_dict().get("steps") or [])


def edited_step(builder, target: dict | None, flavour_name: str) -> str | None:
    """The step a run changes, from its ``workflow_target``; None when it adds one.

    Raises:
        StudyWorkflowError: an unknown mode, or ``edit`` naming no step of the workflow or
            one of another kind.
    """
    target = target or {}
    mode = target.get("mode") or "default"
    if mode not in TARGET_MODES:
        raise StudyWorkflowError(f"workflow_target mode {mode!r} is not one of {TARGET_MODES}")
    if mode in ("new_step", "new_workflow"):
        return None
    steps = file_steps(builder)
    if mode == "edit":
        step_id = target.get("step")
        found = next((s for s in steps if s["id"] == step_id), None)
        if found is None:
            raise StudyWorkflowError(f"workflow {builder.name} has no step {step_id!r} to change")
        kind = found.get("kind", "calibration")
        if kind != kind_of(flavour_name):
            raise StudyWorkflowError(
                f"step {step_id} is a {kind} step and this run is a {kind_of(flavour_name)}, "
                f"so it cannot change it: choose a {kind_of(flavour_name)} step, or a new "
                f"step, in the Workflow view")
        return step_id
    same = [s["id"] for s in steps if flavour(s) == flavour_name]
    return same[-1] if same else None


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


def first_description(step_id: str) -> str:
    """The description of a calibration a sensitivity or UQ run makes first, for its step
    ``step_id`` ("run a calibration first"): how the Workflow view tells it from a
    calibration of the user's own, which the step may be at too."""
    return f"calibrated first for step {step_id}"


def set_entries(values: dict, qnames, reason: str) -> list[dict]:
    """Set-step entries for ``qnames`` (in that order) that ``values`` gives."""
    set_value = ca_from("calibration_workflow.builder", "set_value")
    return [set_value(q, float(values[q]), reason=reason)
            for q in dict.fromkeys(qnames) if q in values and values[q] is not None]


class Plan:
    """The steps one GUI run adds, and how the runner runs them."""

    def __init__(self, builder, working_dir: str, edit: str | None = None):
        self.builder = builder
        self.working_dir = working_dir
        self.added: list[str] = []
        #: The step this run changes (:func:`edited_step`); None when it adds one.
        self.edit = edit
        #: The workflow's steps before this run, by id.
        self.before = {s["id"]: s for s in file_steps(builder)}
        #: Every step the run runs, in CA's order (set by :meth:`write` when changing a step).
        self.run_steps: list[str] | None = None

    def main_id(self, label: str) -> str:
        """The id of the run's own step: the step it changes, else a new ``<label>-<n>``."""
        return self.edit or self.new_id(label)

    def first_id(self, label: str, key: str, for_step: str) -> str:
        """The id of a calibration the run makes first for its step ``for_step`` (whose
        ``key`` names it). When changing a step whose ``key`` is the calibration made first
        for it (:func:`first_description`) and no other step takes from it, that calibration
        is changed too, rather than left behind unused; a calibration of the user's own is
        never replaced."""
        old = self.edit and self.before.get(self.edit, {}).get(key)
        was = self.before.get(old) or {}
        if old and was.get("description") == first_description(for_step):
            others = (s for i, s in self.before.items() if i not in (self.edit, old))
            if not any(r == old for s in others for _, r in references(s)):
                return old
        return self.new_id(label)

    def _drop_unused_points(self) -> None:
        """Remove the point set steps the changed step took from that nothing takes from now."""
        old = self.before.get(self.edit) or {}
        now = file_steps(self.builder)
        used = {r for s in now for _, r in references(s)}
        for _, source in references(old):
            was = self.before.get(source) or {}
            if was.get("kind") == "set" and was.get("point") and source not in used \
                    and source in self.builder.steps:
                self.builder.remove_step(source)

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

    #: Set for a run into a workflow that is not one of the working dir's study workflows
    #: (one opened from elsewhere, such as a module-library workflow): its own run directory
    #: and module libraries.
    external_run_dir: str | None = None
    module_library_dirs: list = []

    @property
    def run_dir(self) -> str:
        return self.external_run_dir or run_dir(self.working_dir, self.builder.name)

    def step_dir(self, step_id: str | None = None) -> str:
        return os.path.join(self.run_dir, step_id or self.step)

    def write(self) -> str:
        """Write the workflow. When the run changes a step, the steps it added go just above
        that step, and :attr:`run_steps` is that step, what it now takes from that the run
        added, and every step below it that takes from it, in CA's order."""
        try:
            if self.edit:
                self._drop_unused_points()
            path = self.builder.write()
        except (ValueError, OSError) as exc:
            raise StudyWorkflowError(f"the study workflow was not written: {exc}") from exc
        if self.edit:
            new = [s for s in self.added if s not in self.before]
            if new:
                move_before(path, new, self.edit)
            self._keep_drawn(path)
            raw = read_raw(path)
            wanted = set(self.added) | {self.edit} | descendants(raw, self.edit)
            order = [s.id for s in _checked(raw, path).topological_order()]
            self.run_steps = [s for s in order if s in wanted]
        return path

    #: What a changed step keeps from before when the panel's run does not say it: the arrows
    #: drawn on the chart and what goes with them.
    KEPT_KEYS = ("fixed_from", "priors_from", "emulator_from", "params_for_id_from",
                 "sensitivity_from", "store_distribution")

    def _keep_drawn(self, path: str) -> None:
        """The changed step keeps the arrows drawn to it in the Workflow view (and a prior's
        objective) that the panel's run does not set; CA checks the result."""
        old = self.before.get(self.edit) or {}
        raw = read_raw(path)
        step = next((s for s in raw["steps"] if s["id"] == self.edit), None)
        if step is None:
            return
        ids = {s["id"] for s in raw["steps"]}
        changed = False
        for key in self.KEPT_KEYS:
            if key in old and key not in step:
                value = old[key]
                if key in ("fixed_from", "priors_from"):
                    value = [e for e in value if _list_ref(e) in ids]
                    if not value:
                        continue
                elif isinstance(value, str) and value not in ids:
                    continue
                step[key] = copy.deepcopy(value)
                changed = True
        objective = (old.get("settings") or {}).get("optimiser_options", {}).get("objective_function")
        if objective and step.get("priors_from"):
            opts = step.setdefault("settings", {}).setdefault("optimiser_options", {})
            if "objective_function" not in opts:
                opts["objective_function"] = objective
                changed = True
        if changed:
            _checked(raw, path)
            _write_raw(path, raw)

    def completes(self) -> bool:
        """Whether every step outside this run has a done result, so a run whose own steps
        succeed leaves the workflow complete."""
        running = set(self.run_steps or self.added)
        for step in self.builder.to_dict().get("steps") or []:
            step_id = step.get("id")
            if step_id in running:
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

        A run that changes a step runs :attr:`run_steps`, each ``only`` in turn
        (``run_steps``): the step, then what takes from it, but not the workflow's other
        steps, which ``from_step`` would run too.
        """
        if self.run_steps is not None:
            only, from_step, steps = None, None, list(self.run_steps)
        else:
            only = self.added[0] if len(self.added) == 1 else None
            from_step, steps = (None if only else self.added[0]), list(self.added)
        return {
            # a workflow opened from elsewhere writes its own calibrated values when it runs
            "write_calibrated": not self.external_run_dir and self.completes(),
            "study_step": {"workflow": self.builder.path, "run_dir": self.run_dir,
                           "step": self.step, "steps": steps,
                           "step_dir": self.step_dir(), "edited": self.edit},
            "workflow": self.builder.path,
            "workflow_output_dir": self.run_dir,
            "only": only,
            "from_step": from_step,
            "run_steps": steps if self.run_steps is not None else None,
            "module_library_dirs": list(self.module_library_dirs),
            "markers": markers,
        }


# ---------------------------------------------------------------------------------------
# Editing a workflow file: reorder and delete steps
# ---------------------------------------------------------------------------------------
#: The keys by which a step takes one other step.
_SINGLE_KEYS = ("emulator_from", "result_from", "sensitivity_from", "params_for_id_from",
                "start_from")
#: References that can go with nothing in their place: a calibration with no
#: ``start_from`` starts from the model's own values.
_DROPPABLE = ("start_from",)


def _list_ref(entry) -> str | None:
    if isinstance(entry, str):
        return entry
    if isinstance(entry, dict):
        return entry.get("step") or entry.get("from_step")
    return None


def references(step: dict, ids=None) -> list[tuple[str, str]]:
    """``[(key, step id)]``: every step ``step`` takes something from, by CA's key
    (``scale_from`` for a set entry's ``of``). With ``ids``, only references to those."""
    out = [(k, step[k]) for k in _SINGLE_KEYS if isinstance(step.get(k), str)]
    for key in ("fixed_from", "priors_from", "combine_from"):
        value = step.get(key)
        for entry in (value if isinstance(value, list) else [value] if value else []):
            if _list_ref(entry):
                out.append((key, _list_ref(entry)))
    for entry in step.get("set") or []:
        if isinstance(entry, dict) and isinstance(entry.get("of"), str):
            out.append(("scale_from", entry["of"]))
    return [(k, s) for k, s in out if ids is None or s in ids]


def _replace_ref(step: dict, key: str, old: str, new: str | None) -> None:
    """Point ``step``'s ``key`` reference to ``old`` at ``new`` (None: take it away)."""
    if key in _SINGLE_KEYS:
        if new is None:
            step.pop(key, None)
        else:
            step[key] = new
        return
    if key == "scale_from":
        for entry in step.get("set") or []:
            if isinstance(entry, dict) and entry.get("of") == old:
                entry["of"] = new
        return
    value = step.get(key)
    entries = value if isinstance(value, list) else [value]
    have = {_list_ref(e) for e in entries}
    kept = []
    for entry in entries:
        if _list_ref(entry) != old:
            kept.append(entry)
        elif new is not None and new not in have:
            if isinstance(entry, dict):
                entry = dict(entry, **{"step" if "step" in entry else "from_step": new})
            else:
                entry = new
            kept.append(entry)
    step[key] = kept


def source_kinds() -> dict:
    """CA's ``{key: step kinds it may name}``."""
    ensure_ca_path()
    return ca_from("calibration_workflow.spec", "SOURCE_KINDS")


def read_raw(path: str) -> dict:
    with open(path, encoding="utf-8-sig") as handle:
        return json.load(handle)


def _checked(raw: dict, path: str):
    """CA's parsed workflow of ``raw``; StudyWorkflowError with CA's reason when it refuses."""
    ensure_ca_path()
    parse_workflow = ca_from("calibration_workflow.spec", "parse_workflow")
    try:
        return parse_workflow(copy.deepcopy(raw), path=path)
    except (ValueError, KeyError, TypeError) as exc:
        raise StudyWorkflowError(str(exc)) from exc


def _write_raw(path: str, raw: dict) -> None:
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        handle.write(json.dumps(raw, indent=1, allow_nan=False) + "\n")
    os.replace(tmp, path)


def _order_problem(steps: list[dict]) -> str | None:
    """Why this order cannot be the order the steps run in, or None: a step above a step it
    takes from. CA runs every step after those it takes from, so the file's order is the
    run's only when no step comes before its sources."""
    ids = [s["id"] for s in steps]
    for i, step in enumerate(steps):
        for key, source in references(step, set(ids)):
            if ids.index(source) > i:
                return (f'step "{step["id"]}" takes from "{source}" ({key}), so it has to '
                        f'stay below it')
    return None


def move_step(path: str, step_id: str, index: int) -> dict:
    """Move ``step_id`` to position ``index`` of the workflow file; returns the new file.

    Raises:
        StudyWorkflowError: no such step, or the move would put a step above one it takes from.
    """
    raw = read_raw(path)
    steps = list(raw.get("steps") or [])
    ids = [s["id"] for s in steps]
    if step_id not in ids:
        raise StudyWorkflowError(f"the workflow has no step {step_id!r}")
    step = steps.pop(ids.index(step_id))
    steps.insert(max(0, min(int(index), len(steps))), step)
    problem = _order_problem(steps)
    if problem:
        raise StudyWorkflowError(f"{step_id} cannot go there: {problem}")
    raw["steps"] = steps
    _checked(raw, path)
    _write_raw(path, raw)
    return raw


def move_before(path: str, moved: list[str], before: str) -> None:
    """Move the steps ``moved`` (in their order) to just above ``before``."""
    raw = read_raw(path)
    steps = raw.get("steps") or []
    taken = [s for s in steps if s["id"] in moved]
    rest = [s for s in steps if s["id"] not in moved]
    at = next((i for i, s in enumerate(rest) if s["id"] == before), len(rest))
    raw["steps"] = rest[:at] + taken + rest[at:]
    if raw["steps"] != steps and _order_problem(raw["steps"]) is None:
        _write_raw(path, raw)


def _live_sources(by_id: dict, step_id: str, doomed: set) -> list[str]:
    """The steps ``step_id`` takes from, nearest first, looking through the doomed ones to
    what they take from in turn."""
    out, queue, seen = [], [step_id], {step_id}
    while queue:
        current = queue.pop(0)
        for _, source in references(by_id[current], set(by_id)):
            if source in seen:
                continue
            seen.add(source)
            if source in doomed:
                queue.append(source)
            else:
                out.append(source)
    return out


def delete_plan(raw: dict, step_id: str) -> dict:
    """What deleting ``step_id`` does to the others, without doing it.

    Each reference to a deleted step is reconnected to the nearest step that step took from
    (through further deleted ones) of a kind the key takes (CA's ``SOURCE_KINDS``). A
    ``start_from`` with none goes (the calibration then starts from the model's values); any
    other reference with none deletes its step as well, and so on down.

    Returns:
        ``{"deleted": [ids, file order], "rewired": [{"step", "key", "from", "to"}],
        "dropped": [{"step", "key", "from"}]}``.
    """
    steps = raw.get("steps") or []
    by_id = {s["id"]: s for s in steps}
    if step_id not in by_id:
        raise StudyWorkflowError(f"the workflow has no step {step_id!r}")
    kinds = source_kinds()
    doomed = {step_id}
    while True:
        rewired, dropped, more = [], [], set()
        for step in steps:
            if step["id"] in doomed:
                continue
            for key, source in references(step, doomed):
                wanted = kinds.get(key)
                to = next((c for c in _live_sources(by_id, source, doomed)
                           if c != step["id"] and (not wanted or by_id[c].get(
                               "kind", "calibration") in wanted)), None)
                if to is not None:
                    rewired.append({"step": step["id"], "key": key, "from": source, "to": to})
                elif key in _DROPPABLE:
                    dropped.append({"step": step["id"], "key": key, "from": source})
                else:
                    more.add(step["id"])
        if not more:
            break
        doomed |= more
    return {"deleted": [s["id"] for s in steps if s["id"] in doomed],
            "rewired": rewired, "dropped": dropped}


def delete_step(path: str, step_id: str, *, confirm: bool = False) -> dict:
    """Delete ``step_id`` from the workflow file, reconnecting its neighbours
    (:func:`delete_plan`). When that deletes more than the step itself, nothing changes
    unless ``confirm``. Returns the plan with ``"applied"``. The steps' results stay in the
    run directory.

    Raises:
        StudyWorkflowError: no such step, every step would go, or CA refuses the result.
    """
    raw = read_raw(path)
    plan = delete_plan(raw, step_id)
    if len(plan["deleted"]) > 1 and not confirm:
        return {**plan, "applied": False}
    doomed = set(plan["deleted"])
    steps = [copy.deepcopy(s) for s in raw.get("steps") or [] if s["id"] not in doomed]
    if not steps:
        raise StudyWorkflowError(
            "that would delete every step of the workflow; a workflow keeps at least one")
    by_id = {s["id"]: s for s in steps}
    for change in plan["rewired"]:
        _replace_ref(by_id[change["step"]], change["key"], change["from"], change["to"])
    for change in plan["dropped"]:
        _replace_ref(by_id[change["step"]], change["key"], change["from"], None)
    raw = dict(raw, steps=steps)
    _checked(raw, path)
    _write_raw(path, raw)
    return {**plan, "applied": True}


#: A step id a rename may give: it names the step's results folder and data files.
STEP_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.\-]{0,63}")


def rename_step(path: str, old: str, new: str, run_dir: str | None = None) -> dict:
    """Rename step ``old`` to ``new`` in the workflow file, and every reference to it (the
    ``*_from`` keys, ``fixed_from`` / ``priors_from`` entries, a set entry's ``of``). CA keeps a
    step's results in ``<run_dir>/<id>/`` and finds them by that name, so the folder is moved
    with it and the step keeps its results. Returns the new file.

    Raises:
        StudyWorkflowError: no such step, a name a step id cannot have, a name another step
            has (case aside: the folders would clash on Windows and macOS), or CA refuses the
            file.
    """
    new = (new or "").strip()
    raw = read_raw(path)
    steps = raw.get("steps") or []
    ids = [s["id"] for s in steps]
    if old not in ids:
        raise StudyWorkflowError(f"the workflow has no step {old!r}")
    if new == old:
        return raw
    if not STEP_ID.fullmatch(new):
        raise StudyWorkflowError(
            f"{new!r} cannot name a step: use letters, digits, '_', '.' or '-' (it names the "
            f"step's results folder), starting with a letter or digit, at most 64 characters")
    if any(i.casefold() == new.casefold() for i in ids if i != old):
        raise StudyWorkflowError(f"another step is already called {new!r}")
    folder = os.path.join(run_dir, old) if run_dir else None
    target = os.path.join(run_dir, new) if run_dir else None
    if target and os.path.exists(target) and folder and os.path.exists(folder):
        raise StudyWorkflowError(
            f"{target} already exists, from an earlier step of that name: rename it or the "
            f"folder first")
    raw = copy.deepcopy(raw)
    for step in raw["steps"]:
        if step["id"] == old:
            step["id"] = new
        for key, ref in references(step):
            if ref == old:
                _replace_ref(step, key, old, new)
    _checked(raw, path)
    _write_raw(path, raw)
    if folder and os.path.isdir(folder) and not os.path.exists(target):
        os.rename(folder, target)
    return raw


def descendants(raw: dict, step_id: str) -> set[str]:
    """Every step that takes from ``step_id``, directly or through others."""
    steps = raw.get("steps") or []
    ids = {s["id"] for s in steps}
    found, changed = {step_id}, True
    while changed:
        changed = False
        for step in steps:
            if step["id"] not in found and any(s in found for _, s in references(step, ids)):
                found.add(step["id"])
                changed = True
    return found - {step_id}


def lock():
    """Serialises edits of the workflow file (two panels starting runs at once)."""
    return _lock
