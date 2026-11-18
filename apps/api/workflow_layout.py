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


# -- drafts --------------------------------------------------------------------------------

def add_draft(workflow_path: str, col: int | None = None, row: float | None = None) -> str:
    """A new draft box (``new-<n>``); returns its id."""
    layout = read(workflow_path)
    taken = {d.get("id") for d in layout["drafts"]} | set(_step_ids(workflow_path))
    n = 1
    while f"new-{n}" in taken:
        n += 1
    draft = f"new-{n}"
    layout["drafts"].append({"id": draft})
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
