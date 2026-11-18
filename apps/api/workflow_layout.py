"""The Workflow view's chart as the user arranged it, and the arrows the user draws.

Two kinds of thing live here, in ``<workflow stem>.layout.json`` beside the workflow file
(CUFLynx's own file: CA never reads it, and the workflow means the same without it):

* **positions** -- ``{id: {col, row}}``: the column a box is pushed to (never left of what it
  takes from: the chart keeps every arrow pointing right) and its order within the column.
* **drafts** and **pending links** -- a step the user has put on the chart with **New step**
  but not yet run (``new-1``), and every arrow with a draft at either end. A draft has no
  kind until a panel's run makes it a step (the run's kind), so it cannot be in the workflow
  file; the run that makes it (``workflow_target {mode: "new_step", draft}``) gives the new
  step the draft's position, and its arrows join the workflow file as soon as both ends are
  steps.

An arrow between two steps is written straight into the workflow file, as CA's keys
(``fixed_from``, ``priors_from``, ``result_from``, ...), and CA checks it (:func:`set_link`).
"""

from __future__ import annotations

import copy
import json
import os
import re

import study_workflow
from ca_imports import ca_import
from study_workflow import StudyWorkflowError

#: The keys an arrow the user draws can be: one step taking one source.
SINGLE_KEYS = ("result_from", "start_from", "emulator_from", "sensitivity_from",
               "params_for_id_from")
#: The keys a step can list several sources under.
LIST_KEYS = ("fixed_from", "priors_from")
LINK_KEYS = SINGLE_KEYS + LIST_KEYS
#: What a priors_from entry may say besides its step (CA's ``prior``).
PRIOR_OPTIONS = ("kind", "inflate", "parameters")
DRAFT = re.compile(r"new-\d+")


def layout_path(workflow_path: str) -> str:
    stem, _ = os.path.splitext(workflow_path)
    return f"{stem}.layout.json"


def read(workflow_path: str) -> dict:
    """The layout of a workflow: ``{positions, drafts, links}`` (empty when there is none)."""
    out = {"positions": {}, "drafts": [], "links": []}
    try:
        with open(layout_path(workflow_path), encoding="utf-8") as handle:
            raw = json.load(handle)
    except (OSError, ValueError):
        return out
    if isinstance(raw, dict):
        for key, kind in (("positions", dict), ("drafts", list), ("links", list)):
            if isinstance(raw.get(key), kind):
                out[key] = raw[key]
    return out


def write(workflow_path: str, layout: dict) -> None:
    path = layout_path(workflow_path)
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        handle.write(json.dumps(layout, indent=1) + "\n")
    os.replace(tmp, path)


def _step_ids(workflow_path: str) -> list[str]:
    return [s["id"] for s in study_workflow.read_raw(workflow_path).get("steps") or []]


def _is_draft(layout: dict, node: str) -> bool:
    return any(d.get("id") == node for d in layout["drafts"])


def _known(workflow_path: str, layout: dict, node: str) -> bool:
    return node in _step_ids(workflow_path) or _is_draft(layout, node)


# -- positions -----------------------------------------------------------------------------

def set_position(workflow_path: str, node: str, col: int, row: float) -> dict:
    """Put a box at column ``col`` (the column it is pushed to) and order ``row`` there."""
    layout = read(workflow_path)
    if not _known(workflow_path, layout, node):
        raise StudyWorkflowError(f"the workflow has no step {node!r}")
    layout["positions"][node] = {"col": max(0, int(col)), "row": float(row)}
    write(workflow_path, layout)
    return layout


def set_positions(workflow_path: str, positions: dict) -> dict:
    """Put several boxes at once (``{id: {col, row}}``): a drop in the gap between two
    columns makes a new column there, and every box right of it moves one column on."""
    layout = read(workflow_path)
    for node, at in positions.items():
        if not _known(workflow_path, layout, node):
            raise StudyWorkflowError(f"the workflow has no step {node!r}")
        layout["positions"][node] = {"col": max(0, int(at["col"])), "row": float(at["row"])}
    write(workflow_path, layout)
    return layout


# -- drafts --------------------------------------------------------------------------------

#: The boxes of the step prep box's Actions: steps on no model, each made by its own form.
#: ``set``: values set by hand; ``combine``: a parameter set chosen from several
#: calibrations by cost (only where CA has the kind).
ACTIONS = ("set", "combine")


def add_draft(workflow_path: str, col: int | None = None, row: float | None = None,
              on_target: bool = False, model=None, action: str | None = None) -> str:
    """A new draft box (``new-<n>``); returns its id.

    ``model``: a model box (a step's model as the workflow file writes it): what it becomes
    is what is chosen for it (a panel's run, or its Simulation form). ``action``: an action
    box (:data:`ACTIONS`), made by its own form. ``on_target``: dropped after the target, a
    sweep run on the merged target, made from its Simulation form (:func:`make_sweep`)."""
    if action is not None and action not in ACTIONS:
        raise StudyWorkflowError(f"an action box is one of {ACTIONS}")
    layout = read(workflow_path)
    taken = {d.get("id") for d in layout["drafts"]} | set(_step_ids(workflow_path))
    n = 1
    while f"new-{n}" in taken:
        n += 1
    draft = f"new-{n}"
    entry = {"id": draft}
    if on_target:
        entry["on_target"] = True
    if model:
        entry["model"] = copy.deepcopy(model)
    if action:
        entry["action"] = action
    layout["drafts"].append(entry)
    if col is not None:
        layout["positions"][draft] = {"col": max(0, int(col)), "row": float(row or 0)}
    write(workflow_path, layout)
    return draft


def delete_draft(workflow_path: str, draft: str) -> dict:
    layout = read(workflow_path)
    if not _is_draft(layout, draft):
        raise StudyWorkflowError(f"there is no new step {draft!r}")
    layout["drafts"] = [d for d in layout["drafts"] if d.get("id") != draft]
    layout["positions"].pop(draft, None)
    layout["links"] = [k for k in layout["links"] if draft not in (k["source"], k["target"])]
    write(workflow_path, layout)
    return layout


def draft_became(workflow_path: str, draft: str, step_id: str) -> None:
    """The draft ``draft`` is now the step ``step_id`` (a run made it): the step takes its
    position, and every arrow of the draft whose other end is a step joins the workflow file.

    Raises:
        StudyWorkflowError: CA refuses an arrow (a key this kind of step does not take, or a
            source of the wrong kind); the step stays, and so do the arrows not yet written,
            so the user can change them.
    """
    layout = read(workflow_path)
    if not _is_draft(layout, draft):
        return
    layout["drafts"] = [d for d in layout["drafts"] if d.get("id") != draft]
    if draft in layout["positions"]:
        layout["positions"][step_id] = layout["positions"].pop(draft)
    for link in layout["links"]:
        for end in ("source", "target"):
            if link[end] == draft:
                link[end] = step_id
    write(workflow_path, layout)
    apply_pending(workflow_path)


def apply_pending(workflow_path: str) -> None:
    """Write every pending arrow whose ends are both steps into the workflow file."""
    layout = read(workflow_path)
    ids = set(_step_ids(workflow_path))
    ready = [k for k in layout["links"] if k["source"] in ids and k["target"] in ids]
    if not ready:
        return
    raw = study_workflow.read_raw(workflow_path)
    for link in ready:
        raw = _with_link(raw, link["source"], link["target"], link["key"], link.get("options"))
    _write_checked(workflow_path, raw)
    layout["links"] = [k for k in layout["links"] if k not in ready]
    write(workflow_path, layout)


def rename(workflow_path: str, old: str, new: str) -> None:
    """A step renamed: its position and its pending arrows follow it."""
    layout = read(workflow_path)
    if old in layout["positions"]:
        layout["positions"][new] = layout["positions"].pop(old)
    for link in layout["links"]:
        for end in ("source", "target"):
            if link[end] == old:
                link[end] = new
    write(workflow_path, layout)


def forget(workflow_path: str, step_ids) -> None:
    """Steps deleted: their positions and pending arrows go too."""
    layout = read(workflow_path)
    gone = set(step_ids)
    for step_id in gone:
        layout["positions"].pop(step_id, None)
    layout["links"] = [k for k in layout["links"] if not ({k["source"], k["target"]} & gone)]
    write(workflow_path, layout)


SWEEP_DESIGNS = ("grid", "one_at_a_time")


def make_sweep(workflow_path: str, draft: str, *, obs_data: str | None, outputs: list,
               parameters: dict | None = None, design: str | None = None,
               zip_groups: list | None = None, settings: dict | None = None) -> str:
    """Make the after-target draft ``draft`` a sweep step run on the merged target (CA's
    ``kind: sweep, on_target: true``): the protocol of ``obs_data`` (a path, written relative
    to the workflow file), the variables ``outputs`` recorded over time, and the parameters
    swept (``{"<vessel>/<param>": range}``, CA's sweep_range; none: one run as merged).
    Checked by CA. Returns the step id (the draft's)."""
    layout = read(workflow_path)
    entry = next((d for d in layout["drafts"] if d.get("id") == draft), None)
    if entry is None:
        raise StudyWorkflowError(f"there is no new step {draft!r}")
    raw = copy.deepcopy(study_workflow.read_raw(workflow_path))
    sweep: dict = {"parameters": dict(parameters or {})}
    if outputs:
        sweep["outputs"] = [str(o) for o in outputs]
    if design:
        if design not in SWEEP_DESIGNS:
            raise StudyWorkflowError(f"a sweep's design is one of {SWEEP_DESIGNS}")
        sweep["design"] = design
    if zip_groups:
        sweep["zip"] = zip_groups
    # after the target it runs on the merged target; a model box runs on its own model
    step = {"id": draft, "kind": "sweep", "sweep": sweep}
    if entry.get("model"):
        step["model"] = copy.deepcopy(entry["model"])
    else:
        step["on_target"] = True
    if settings:
        step["settings"] = dict(settings)
    if obs_data:
        base = os.path.dirname(os.path.abspath(workflow_path))
        step["obs_data"] = os.path.relpath(os.path.abspath(obs_data), base)
    raw.setdefault("steps", []).append(step)
    _write_checked(workflow_path, raw)
    layout["drafts"] = [d for d in layout["drafts"] if d.get("id") != draft]
    write(workflow_path, layout)
    return draft


def make_set(workflow_path: str, draft: str, entries: list, point: bool = False) -> str:
    """Make the action box ``draft`` a set step: each entry a value set by hand, with its
    reason (``{parameter, value, reason}``, CA's set entry). ``point``: only a point for a
    step to start from or be analysed at, not merged into the target. Checked by CA."""
    layout = read(workflow_path)
    entry = next((d for d in layout["drafts"] if d.get("id") == draft), None)
    if entry is None:
        raise StudyWorkflowError(f"there is no new step {draft!r}")
    rows = []
    for e in entries:
        if not str(e.get("parameter") or "").strip():
            continue
        if not str(e.get("reason") or "").strip():
            raise StudyWorkflowError(
                f"{e['parameter']}: say why it has this value (a value set by hand needs a reason)")
        rows.append({"parameter": str(e["parameter"]).strip(), "value": float(e["value"]),
                     "reason": str(e["reason"]).strip()})
    if not rows:
        raise StudyWorkflowError("give at least one parameter and its value")
    raw = copy.deepcopy(study_workflow.read_raw(workflow_path))
    step = {"id": draft, "kind": "set", "set": rows}
    if point:
        step["point"] = True
    raw.setdefault("steps", []).append(step)
    _write_checked(workflow_path, raw)
    layout["drafts"] = [d for d in layout["drafts"] if d.get("id") != draft]
    write(workflow_path, layout)
    return draft


def step_kinds() -> list:
    """The step kinds this CA has (its schema's ``kind`` enum), so an action box is offered
    only where CA can make it (``combine``)."""
    try:
        spec = ca_import("calibration_workflow.spec")
        root = os.path.dirname(os.path.dirname(os.path.abspath(spec.__file__)))
        with open(os.path.join(root, "schemas", "calibration_workflow.schema.json")) as fh:
            schema = json.load(fh)
        return list(schema["$defs"]["step"]["properties"]["kind"]["enum"])
    except (ImportError, OSError, ValueError, KeyError, TypeError, AttributeError):
        return []


# -- arrows --------------------------------------------------------------------------------

def fixed_parameters_supported() -> bool:
    """Whether this CA takes ``{"step", "parameters"}`` in ``fixed_from`` (only those values
    fixed), read from its workflow schema; else a fixed arrow fixes all of the source's."""
    try:
        spec = ca_import("calibration_workflow.spec")
        root = os.path.dirname(os.path.dirname(os.path.abspath(spec.__file__)))
        with open(os.path.join(root, "schemas", "calibration_workflow.schema.json")) as fh:
            schema = json.load(fh)
        items = schema["$defs"]["step"]["properties"]["fixed_from"]["items"]
    except (ImportError, OSError, ValueError, KeyError, TypeError, AttributeError):
        return False
    shapes = items.get("oneOf") or items.get("anyOf") or [items]
    return any(s.get("type") == "object" or "$ref" in s for s in shapes)


def _entry(source: str, key: str, options: dict | None):
    """A list key's entry for ``source``: the bare id, or an object with its options."""
    options = {k: v for k, v in (options or {}).items() if v not in (None, "", [])}
    if key == "priors_from":
        options = {k: v for k, v in options.items() if k in PRIOR_OPTIONS}
    elif key == "fixed_from":
        options = {k: v for k, v in options.items() if k == "parameters"}
        if options and not fixed_parameters_supported():
            raise StudyWorkflowError(
                "this circulatory_autogen fixes every value of a fixed_from step: it cannot "
                "fix only some of them yet")
    return {"step": source, **options} if options else source


def _with_link(raw: dict, source: str, target: str, key: str, options: dict | None,
               old_key: str | None = None) -> dict:
    """``raw`` with ``target`` taking ``source`` by ``key`` (replacing its ``old_key`` arrow
    from ``source`` when given)."""
    if key not in LINK_KEYS:
        raise StudyWorkflowError(f"an arrow cannot be {key!r}: it is one of {LINK_KEYS}")
    raw = copy.deepcopy(raw)
    step = next((s for s in raw.get("steps") or [] if s["id"] == target), None)
    if step is None:
        raise StudyWorkflowError(f"the workflow has no step {target!r}")
    if old_key:
        study_workflow._replace_ref(step, old_key, source, None)
        if step.get(old_key) == []:
            step.pop(old_key)
    if key in SINGLE_KEYS:
        step[key] = source
    else:
        if key == "priors_from" and (options or {}).get("objective") == "likelihood":
            # asked for with the arrow: a calibration uses a prior only when it optimises
            # the log posterior (or samples one), CA's rule
            settings = step.setdefault("settings", {})
            settings.setdefault("optimiser_options", {})["objective_function"] = "likelihood"
        entries = [e for e in step.get(key) or [] if study_workflow._list_ref(e) != source]
        entries.append(_entry(source, key, options))
        step[key] = entries
    return raw


def _write_checked(workflow_path: str, raw: dict) -> None:
    """Check ``raw`` with CA and write it, in an order CA runs it in when a new arrow put a
    step above one it takes from."""
    parsed = study_workflow._checked(raw, workflow_path)
    if study_workflow._order_problem(raw["steps"]):
        by_id = {s["id"]: s for s in raw["steps"]}
        raw["steps"] = [by_id[s.id] for s in parsed.topological_order()]
    study_workflow._write_raw(workflow_path, raw)


def set_link(workflow_path: str, source: str, target: str, key: str,
             options: dict | None = None, old_key: str | None = None) -> None:
    """Draw (or change) the arrow ``source`` -> ``target``: ``target`` takes ``source`` by
    ``key`` (CA's key), with a list key's ``options`` (a prior's ``kind`` / ``inflate`` /
    ``parameters``; a fixed arrow's ``parameters`` where CA takes them). Between two steps
    it is written into the workflow file and checked by CA; with a draft at either end it
    waits in the layout until the draft is a step.

    Raises:
        StudyWorkflowError: an unknown end or key, an arrow to itself or into the model, or
            CA's reason for refusing it.
    """
    layout = read(workflow_path)
    for end in (source, target):
        if not _known(workflow_path, layout, end):
            raise StudyWorkflowError(f"the workflow has no step {end!r}")
    if source == target:
        raise StudyWorkflowError("a step cannot take from itself")
    if key not in LINK_KEYS:
        raise StudyWorkflowError(f"an arrow cannot be {key!r}: it is one of {LINK_KEYS}")
    if _is_draft(layout, source) or _is_draft(layout, target):
        _entry(source, key, options)  # refuses options this CA cannot write, now
        links = [k for k in layout["links"] if not (
            k["source"] == source and k["target"] == target and k["key"] in (key, old_key))]
        if key in SINGLE_KEYS:  # one source per single key
            links = [k for k in links if not (k["target"] == target and k["key"] == key)]
        links.append({"source": source, "target": target, "key": key,
                      **({"options": options} if options else {})})
        layout["links"] = links
        write(workflow_path, layout)
        return
    raw = _with_link(study_workflow.read_raw(workflow_path), source, target, key, options,
                     old_key)
    _write_checked(workflow_path, raw)


def remove_link(workflow_path: str, source: str, target: str, key: str) -> None:
    """Take the arrow ``source`` -> ``target`` (``key``) away."""
    layout = read(workflow_path)
    pending = [k for k in layout["links"]
               if k["source"] == source and k["target"] == target and k["key"] == key]
    if pending:
        layout["links"] = [k for k in layout["links"] if k not in pending]
        write(workflow_path, layout)
        return
    raw = copy.deepcopy(study_workflow.read_raw(workflow_path))
    step = next((s for s in raw.get("steps") or [] if s["id"] == target), None)
    if step is None or (key, source) not in study_workflow.references(step):
        raise StudyWorkflowError(f"{target!r} does not take {key} from {source!r}")
    study_workflow._replace_ref(step, key, source, None)
    if step.get(key) == []:
        step.pop(key)
    _write_checked(workflow_path, raw)


def link_kinds() -> dict:
    """What an arrow can be, for the chart's chooser: ``{key: {sources, users}}`` -- the step
    kinds CA lets a key name (``SOURCE_KINDS``) and the kinds that take it."""
    out = {}
    sources = study_workflow.source_kinds()
    try:
        users = ca_import("calibration_workflow.spec")._SOURCE_USERS
    except (ImportError, AttributeError):
        users = {}
    for key in LINK_KEYS:
        if key in sources:
            out[key] = {"sources": list(sources[key]), "users": list(users.get(key) or [])}
    return out
