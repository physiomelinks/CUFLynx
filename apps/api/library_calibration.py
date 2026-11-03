"""Apply a module library's calibration result to an imported study.

The circulatory-autogen-modules library ships each calibrated instance's result
in its COMBINE archive, beside the model it was calibrated on:

* ``<instance>_calibration.json`` -- an object with ``calibrated_parameters``
  (``{name: value}``), ``cost``, ``method``, ``date`` and per-run detail;
* ``<instance>_calibrated_parameters.csv`` -- ``variable_name,units,value,...``,
  the same values in the library's parameters-file layout.

:func:`apply` writes those values into the model's ``initial_value`` attributes
through :func:`calibrated_model.calibrated_cellml` -- the writer a CUFLynx
calibration's saved model uses -- so the study opens with the calibrated values
as the parameters' starting values, and the sliders, the params_for_id entries'
``initial_value`` and a later send to PhLynx all read them from the one model.

Names are the library's: bare parameter names (``alpha``), resolved against the
archive's params_for_id rows (``mod/alpha``) and then against the model's own
constants. A name that resolves to nothing is reported, never dropped silently.
"""

from __future__ import annotations

import csv
import io
import json
import math

from calibrated_model import calibrated_cellml
from cellml_meta import parse_cellml
from params_for_id import ParamsForIdError, parse_params_for_id

#: What the UI calls the source of these values.
SOURCE = "module library"


def read(members: list[tuple[str, bytes]]) -> dict | None:
    """``{"filename", "values", "cost", "method", "date"}`` from the members, or None.

    The JSON wins when both are present: it carries the cost. The CSV is read
    only when no JSON gave any values.
    """
    for name, blob in members:
        if not name.lower().endswith(".json"):
            continue
        try:
            doc = json.loads(blob.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            continue
        values = _numbers((doc or {}).get("calibrated_parameters") if isinstance(doc, dict) else None)
        if values:
            cost = doc.get("cost")
            return {
                "filename": name,
                "values": values,
                "cost": float(cost) if _is_number(cost) else None,
                "method": str(doc["method"]) if doc.get("method") else None,
                "date": str(doc["date"]) if doc.get("date") else None,
            }
    for name, blob in members:
        if not name.lower().endswith(".csv"):
            continue
        values = _csv_values(blob)
        if values:
            return {"filename": name, "values": values, "cost": None, "method": None, "date": None}
    return None


def apply(cellml: bytes, members: list[tuple[str, bytes]],
          params_blob: bytes | None) -> tuple[bytes, dict | None]:
    """``(cellml, report)`` with the calibrated values written into the model.

    ``report`` is None when the members hold no values; otherwise it is what
    :func:`read` returns (without ``values``) plus ``applied`` (the library's
    names that were written) and ``unresolved`` (those that were not), and
    ``source``.
    """
    found = read(members)
    if found is None:
        return cellml, None
    text = cellml.decode("utf-8")
    meta = parse_cellml(text)
    entries = []
    if params_blob:
        try:
            entries = parse_params_for_id(params_blob, meta.initial_values)
        except ParamsForIdError:
            entries = []

    targets: dict[str, float] = {}
    applied: list[str] = []
    unresolved: list[str] = []
    for name, value in found["values"].items():
        qnames = _resolve(name, entries, meta.initial_values)
        if not qnames:
            unresolved.append(name)
            continue
        for q in qnames:
            targets[q] = value
        applied.append(name)

    new_text, written = calibrated_cellml(text, targets)
    # A target the writer could not place: report the library name it came from.
    missed = set(written["unresolved"])
    if missed:
        for name in list(applied):
            if any(q in missed for q in _resolve(name, entries, meta.initial_values)):
                applied.remove(name)
                unresolved.append(name)

    report = {k: v for k, v in found.items() if k != "values"}
    report.update({"source": SOURCE, "applied": applied, "unresolved": unresolved})
    return new_text.encode("utf-8"), report


def _resolve(name: str, entries, initial_values: dict) -> list[str]:
    """The ``vessel/param`` names one library parameter name stands for."""
    if "/" in name:
        return [name]
    for entry in entries:
        if entry.modifies:
            # A modifier's slider is a dimensionless factor, not a model value.
            continue
        if entry.name == name or any(q.rpartition("/")[2] == name for q in entry.qnames):
            return list(entry.qnames)
    owners = [k for k in initial_values if k.rpartition("/")[2] == name]
    return owners if len(owners) == 1 else []


def _csv_values(blob: bytes) -> dict[str, float]:
    try:
        rows = list(csv.DictReader(io.StringIO(blob.decode("utf-8-sig"))))
    except (UnicodeDecodeError, csv.Error):
        return {}
    out: dict[str, float] = {}
    for row in rows:
        row = {str(k or "").strip(): v for k, v in row.items()}
        name = str(row.get("variable_name") or "").strip()
        try:
            value = float(str(row.get("value") or "").strip())
        except ValueError:
            continue
        if name and math.isfinite(value):
            out[name] = value
    return out


def _numbers(raw) -> dict[str, float]:
    if not isinstance(raw, dict):
        return {}
    return {str(k): float(v) for k, v in raw.items() if _is_number(v)}


def _is_number(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)
