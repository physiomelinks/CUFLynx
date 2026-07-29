"""Saved runs: the model outputs that go with a saved parameter vector (#126).

"Save current" already writes the slider values (see :mod:`param_io`). On its own
that only lets a user *return* to a parameter set — to compare against it they had
to re-run it, which loses the point of saving. So the outputs are saved alongside,
under the same prefix::

    manual_params.npy            <- param_io, the slider values
    manual_params_outputs.json   <- here, the traces those values produced

The sibling naming is what ties the two together: the UI lists saved runs by
prefix, and one prefix means one (parameters, outputs) pair.

The file carries its own ``params`` copy as well. The npy form is a bare array
that only makes sense against the current qname order, and the UI needs the
values to mark each slider — reading them from here keeps that independent of
whether the npy still matches the loaded model.

JSON rather than npy: this is a small, self-describing record read by the
browser, and it must be loadable without numpy (which lives only in the analysis
environment).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

OUTPUTS_SUFFIX = "_outputs.json"


class SavedRunError(ValueError):
    """Bad path or unreadable/foreign run file (surface as HTTP 422)."""


def prefix_for(params_filename: str) -> str:
    """The shared prefix of a saved pair: ``manual_params.npy`` -> ``manual_params``."""
    return Path(str(params_filename)).stem


def outputs_path_for(params_path: str | Path) -> Path:
    """The outputs file that pairs with ``params_path``."""
    p = Path(params_path)
    return p.with_name(p.stem + OUTPUTS_SUFFIX)


def _series(values) -> list[float]:
    # `values or []` would raise on a numpy array ("truth value is ambiguous"),
    # and the engine hands back numpy for some backends.
    return [] if values is None else [float(v) for v in values]


def _outputs(mapping) -> dict[str, list[float]]:
    return {} if mapping is None else {str(k): _series(v) for k, v in mapping.items()}


def build_record(prefix: str, params: dict, result: dict) -> dict:
    """The on-disk record for one saved run.

    ``result`` is whatever the client is currently plotting: a single run
    (``time`` + ``outputs``) or a protocol run (``experiments``). Both shapes are
    kept as-is so a saved run overlays onto the same plot layout it came from —
    a protocol run's traces belong to particular experiments, and flattening them
    would put experiment 1's trace on experiment 0's axes.
    """
    experiments = result.get("experiments")
    record = {
        "prefix": prefix,
        "saved_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "params": {str(k): float(v) for k, v in (params or {}).items()},
    }
    if isinstance(experiments, list) and experiments:
        record["experiments"] = [
            {"time": _series(e.get("time")), "outputs": _outputs(e.get("outputs"))}
            for e in experiments
        ]
    else:
        record["time"] = _series(result.get("time"))
        record["outputs"] = _outputs(result.get("outputs"))
    return record


def variables_in(record: dict) -> list[str]:
    """Every variable the record has a trace for, in first-seen order."""
    seen: dict[str, None] = {}
    for exp in record.get("experiments") or []:
        for q in (exp.get("outputs") or {}):
            seen.setdefault(q, None)
    for q in record.get("outputs") or {}:
        seen.setdefault(q, None)
    return list(seen)


def save_run(params_path: str | Path, params: dict, result: dict) -> str:
    """Write the outputs file beside ``params_path``; return its path."""
    path = outputs_path_for(params_path)
    record = build_record(prefix_for(Path(params_path).name), params, result)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record), encoding="utf-8")
    return str(path)


def load_run(path: str | Path) -> dict:
    """Read one saved run."""
    p = Path(path)
    if not p.is_file():
        raise SavedRunError(f"file not found: {path}")
    try:
        record = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise SavedRunError(f"could not read saved run: {exc}") from exc
    if not isinstance(record, dict):
        raise SavedRunError(f"not a saved run file: {path}")
    record.setdefault("prefix", p.name[: -len(OUTPUTS_SUFFIX)] if p.name.endswith(OUTPUTS_SUFFIX) else p.stem)
    record.setdefault("params", {})
    return record


def list_runs(directory: str | Path) -> list[dict]:
    """Saved runs in ``directory``, newest first, without their series.

    Deliberately metadata-only: a run holds every plotted trace, so listing ten
    of them in full would ship megabytes to populate a list of checkboxes. The
    series are fetched by :func:`load_run` when one is actually shown.

    A missing directory lists nothing rather than erroring — it just means
    nothing has been saved there yet.
    """
    d = Path(directory)
    if not d.is_dir():
        return []
    runs = []
    for p in sorted(d.glob(f"*{OUTPUTS_SUFFIX}")):
        try:
            record = load_run(p)
        except SavedRunError:
            continue  # a foreign/corrupt file must not break the whole list
        runs.append(
            {
                "prefix": record.get("prefix") or p.stem,
                "path": str(p),
                "saved_at": record.get("saved_at", ""),
                "params": record.get("params", {}),
                "variables": variables_in(record),
                "n_experiments": len(record.get("experiments") or []),
            }
        )
    runs.sort(key=lambda r: (r["saved_at"], r["prefix"]), reverse=True)
    return runs
