"""What passes along one arrow of the Workflow view's chart, from CA's records.

An arrow is ``(source, target, key)``: a step's reference to another (``fixed_from``,
``priors_from``, ``result_from``, ...), the model a step runs (``source == "model"``), a
step's values merged into the target (``target == "target"``) or a step run on the merged
target (``source == "target"``, a sweep ``on_target``). Everything comes from what CA wrote
-- the receiving step's ``step_result.json`` (``fixed`` rows, ``priors``), the sending step's
(``calibrated``, ``set``, ``distribution_dir``, ``emulator_dir``), ``workflow_result.json``
(``merged_parameters``) -- or, for a step that has not run, from the workflow file, said so
with ``recorded: false``.
"""

from __future__ import annotations

import json
import os

import ca_run_history

MODEL = "model"
TARGET = "target"


def _result(run_dir: str, step_id: str) -> dict:
    if not run_dir:
        return {}
    return ca_run_history.step_result(os.path.join(run_dir, step_id)) or {}


def _row(name, value=None, units=None, note=None) -> dict:
    return {"name": name, "value": value, "units": units or "", "note": note or ""}


def _calibrated_rows(result: dict) -> list[dict]:
    return [_row(r.get("target_parameterisation_name") or r.get("target_host_name")
                 or r.get("model_name"), r.get("value"), r.get("units"), r.get("note"))
            for r in result.get("calibrated") or []]


def _set_rows(result: dict, file_step: dict) -> list[dict]:
    if result.get("set"):
        return [_row(r.get("target_parameterisation_name") or r.get("target_host_name"),
                     r.get("value"), r.get("units"), r.get("reason"))
                for r in result["set"]]
    return [_row(e.get("parameter"), e.get("value"), e.get("units"),
                 e.get("reason") or (f"{e.get('scale')} x {e.get('of')}" if e.get("of") else ""))
            for e in (file_step or {}).get("set") or []]


def _values_of(run_dir: str, step: dict) -> tuple[list[dict], bool]:
    """A calibration's fitted values, or a set step's values; whether CA recorded them."""
    result = _result(run_dir, step["id"])
    if (step.get("kind") or "calibration") == "set":
        return _set_rows(result, step), bool(result.get("set"))
    rows = _calibrated_rows(result)
    return rows, bool(rows)


def _step_folder(run_dir: str, source: str, key: str, name: str) -> str | None:
    """A folder ``source``'s step_result records under ``key``, else the step's own
    ``<run>/<source>/<name>``: CA records absolute paths, which a moved or unzipped working
    dir no longer has."""
    recorded = _result(run_dir, source).get(key)
    if isinstance(recorded, str) and os.path.isdir(recorded):
        return recorded
    own = os.path.join(run_dir or "", source, name)
    return own if run_dir and os.path.isdir(own) else None


def _distribution(run_dir: str, source: str) -> dict | None:
    """``{parameter: {mean, sd, q025, q975}}`` of the distribution ``source`` stored."""
    directory = _step_folder(run_dir, source, "distribution_dir", "distribution")
    if not directory or not os.path.isfile(os.path.join(directory, "stats.json")):
        return None
    try:
        with open(os.path.join(directory, "stats.json"), encoding="utf-8") as handle:
            stats = json.load(handle)
    except (OSError, ValueError):
        return None
    out = {}
    for name, row in (stats.get("parameters") or {}).items():
        q = row.get("quantiles") or {}
        out[name] = {"mean": row.get("mean"), "sd": row.get("sd"),
                     "q025": q.get("0.025"), "q975": q.get("0.975")}
    return out


#: CA's provenance graph (``reporting/provenance.py``): the schema versions read here, and the
#: node of the model source whose counts say how many of the target's values nothing classifies
GRAPH_VERSIONS = (3,)
SOURCE_NODE = "__source__"
TARGET_NODE = "__target__"


def value_origins(graph_path: str | None) -> dict:
    """The boxes of where the model's values come from, from CA's ``provenance_graph.json``:
    ``{"boxes": [{id, title, mark, values: [{name, value, units, note}], to: [chart ids]}],
    "model_counts": {mark: n}}``. A box is one of CA's ``values`` nodes (values set by hand,
    measured, derived, calibrated before the run, the derivations' fixed constants), with the
    chart boxes CA draws it giving values to (``__target__`` is the chart's ``target``,
    ``__source__`` its ``model``). Empty
    when the run has no graph, or one of a schema this does not read."""
    empty = {"boxes": [], "model_counts": {}}
    if not graph_path or not os.path.isfile(graph_path):
        return empty
    try:
        with open(graph_path, encoding="utf-8") as handle:
            graph = json.load(handle)
    except (OSError, ValueError):
        return empty
    if graph.get("schema_version") not in GRAPH_VERSIONS:
        return empty
    edges = graph.get("edges") or []
    boxes = []
    model_counts = {}
    for node in graph.get("nodes") or []:
        if node.get("id") == SOURCE_NODE:
            model_counts = {k: v for k, v in (node.get("counts") or {}).items() if v}
        if node.get("kind") != "values":
            continue
        # CA ends a values box's edges at the model source when a step is generated from it
        # (CA #692: the values are in that source, so every step's model has them), and at
        # the target only when none is
        ends = {TARGET_NODE: TARGET, SOURCE_NODE: MODEL}
        to = [ends.get(e.get("to"), e.get("to"))
              for e in edges if e.get("from") == node.get("id")]
        boxes.append({
            "id": node.get("id"), "title": node.get("title"), "mark": node.get("mark"),
            "values": [_row(p.get("name"), p.get("value"), p.get("units"),
                            p.get("reason") or p.get("data_reference"))
                       for p in node.get("parameters") or []],
            "to": [t for t in to if t],
        })
    return {"boxes": boxes, "model_counts": model_counts}


WORDS = {
    "fixed_from": "values fixed in the receiving step's model",
    "priors_from": "a distribution, used as the prior",
    "result_from": "the point the analysis is at",
    "start_from": "the point the calibration starts from",
    "scale_from": "values the set step scales",
    "emulator_from": "the emulator it runs on",
    "emulator_dir": "the emulator it runs on",
    "sensitivity_from": "the Sobol indices the reduction ranks",
    "params_for_id_from": "the parameters the reduction kept",
    "combine_from": "values the combine step chooses from",
}


def edge_detail(run_dir: str, workflow: dict, source: str, target: str, key: str) -> dict:
    """What passes from ``source`` to ``target`` along ``key`` (see the module docstring):
    ``{source, target, key, what, recorded, rows, distribution, prior, note}``."""
    steps = {s["id"]: s for s in workflow.get("steps") or []}
    out = {"source": source, "target": target, "key": key, "what": WORDS.get(key, key),
           "recorded": False, "rows": [], "distribution": None, "prior": None, "note": ""}

    if source == MODEL:
        model = (steps.get(target) or {}).get("model") or workflow.get("target")
        out["what"] = "the model the step runs"
        out["model"] = model
        out["note"] = ("Every value no earlier step fixes or sets comes from this model "
                       "(inherited).")
        fixed = _result(run_dir, target).get("fixed") or []
        out["recorded"] = bool(_result(run_dir, target))
        if fixed:
            out["note"] += f" {len(fixed)} values in it are fixed from earlier steps."
        return out

    if target == TARGET:
        out["what"] = "values merged into the target"
        step = steps.get(source) or {}
        out["rows"], out["recorded"] = _values_of(run_dir, step)
        # what the step carries on from earlier steps: the values fixed in its model, so a
        # step whose values all pass through it needs no arrow of its own to the target
        for f in _result(run_dir, source).get("fixed") or []:
            out["rows"].append({"name": f.get("host_name") or f.get("model_name"),
                                "value": f.get("value"), "units": f.get("units"),
                                "note": f"from {f.get('from_step')}, fixed in {source}"})
        return out

    if source == TARGET:
        out["what"] = "the target with every merged value (the step runs on it)"
        merged = []
        path = os.path.join(run_dir or "", "workflow_result.json")
        if os.path.isfile(path):
            try:
                with open(path, encoding="utf-8") as handle:
                    merged = json.load(handle).get("merged_parameters") or []
            except (OSError, ValueError):
                merged = []
        out["rows"] = [_row(m.get("parameterisation_name") or m.get("host_name"), m.get("value"),
                            m.get("units"), f"from {m.get('from_step')}") for m in merged]
        out["recorded"] = bool(merged)
        return out

    receiving = _result(run_dir, target)
    sending = steps.get(source) or {"id": source}
    if key == "fixed_from":
        rows = [_row(r.get("host_name") or r.get("model_name"), r.get("value"), r.get("units"))
                for r in receiving.get("fixed") or [] if r.get("from_step") == source]
        if rows:
            out["rows"], out["recorded"] = rows, True
        else:
            out["rows"], _ = _values_of(run_dir, sending)
            out["note"] = (f"{target} has not run with them yet: these are {source}'s current "
                           f"values, fixed where {target}'s model has them.")
        return out

    if key == "priors_from":
        prior = next((p for p in receiving.get("priors") or [] if p.get("step") == source), None)
        if prior is None:
            prior = next((p if isinstance(p, dict) else {"step": p}
                          for p in (steps.get(target) or {}).get("priors_from") or []
                          if (p.get("step") if isinstance(p, dict) else p) == source), {})
        else:
            out["recorded"] = True
        out["prior"] = {"kind": prior.get("kind") or "mvnormal", "inflate": prior.get("inflate"),
                        "parameters": prior.get("parameters")}
        out["distribution"] = _distribution(run_dir, source)
        if out["distribution"] is None:
            out["note"] = f"{source} has stored no distribution yet."
        return out

    if key in ("emulator_from", "emulator_dir"):
        bundle = _step_folder(run_dir, source, "emulator_dir", "emulator")
        summary = ca_run_history.emulator_summary(bundle) if bundle else None
        out["recorded"] = bool(summary)
        out["emulator"] = {"dir": bundle, **(summary or {})}
        if not summary:
            out["note"] = f"{source} has not trained its emulator yet."
        return out

    if key == "params_for_id_from":
        result = _result(run_dir, source)
        kept = (result.get("kept") or result.get("selected_parameters")
                or [r.get("parameter") for r in (result.get("reduction") or {}).get("selected") or []])
        out["rows"] = [_row(p if isinstance(p, str) else p.get("parameter")) for p in kept or []]
        out["recorded"] = bool(kept)
        if not kept:
            out["note"] = f"{source} has not run yet."
        return out

    if key == "combine_from":
        out["rows"], out["recorded"] = _values_of(run_dir, sending)
        method = ((steps.get(target) or {}).get("combine") or {}).get("method") or "best_fit"
        chosen, costs = receiving.get("chosen_from"), receiving.get("costs") or {}
        cost = costs.get(source)
        at = f" (cost {cost:.4g})" if isinstance(cost, (int, float)) else ""
        if method == "mean":
            out["note"] = f"{target} takes each parameter's mean over its inputs, these included."
        elif chosen == source:
            out["note"] = f"{target} chose these: {source} has the lowest cost{at}."
        elif chosen:
            out["note"] = f"{target} chose {chosen}'s values; {source}'s cost{at} is higher."
        else:
            out["note"] = (f"{target} has not run yet: it will take the values of whichever "
                           "input has the lowest cost.")
        return out

    if key == "sensitivity_from":
        out["recorded"] = bool(_result(run_dir, source))
        out["note"] = f"The reduction ranks parameters by {source}'s Sobol indices."
        return out

    if key == "scale_from":
        target_step = steps.get(target) or {}
        values, out["recorded"] = _values_of(run_dir, sending)
        by_name = {r["name"]: r["value"] for r in values}
        out["rows"] = [_row(e.get("parameter"), e.get("scale"),
                            note=f"x {source}'s value"
                                 + (f" ({by_name[e['parameter']]})" if e.get("parameter") in by_name
                                    else ""))
                       for e in target_step.get("set") or [] if e.get("of") == source]
        return out

    # result_from, start_from: the point
    out["rows"], out["recorded"] = _values_of(run_dir, sending)
    kind = sending.get("kind") or "calibration"
    out["note"] = (f"{source}'s best fit." if kind == "calibration"
                   else f"The values {source} sets" + (" (a point only)." if sending.get("point")
                                                       else "."))
    if not out["recorded"]:
        out["note"] += f" {source} has not run yet."
    return out
