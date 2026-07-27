"""Save / load manual parameter-value vectors for the sliders (issue #106).

The Parameters panel can save the current slider values to a file the user names
(default ``manual_params.npy``) and load them back later. Two formats:

* ``.npy`` — a bare 1-D array in the given qname order, matching
  circulatory_autogen's ``best_param_vals.npy`` convention, so a calibration's
  ``best_param_vals.npy`` can be loaded straight onto the sliders (same order).
* ``.csv`` — self-describing ``vessel_name,param_name,value`` rows, so it round-
  trips without depending on the parameter order.

npy needs numpy (only in the analysis env / bundle), so it's imported lazily.
"""

from __future__ import annotations

import csv
import io
from pathlib import Path


class ParamIOError(ValueError):
    """Bad path / format / shape (surface as HTTP 422)."""


def format_for(filename: str) -> str:
    """``'csv'`` for a ``.csv`` name, else ``'npy'`` (the default)."""
    return "csv" if str(filename).lower().endswith(".csv") else "npy"


def save_param_values(
    values: dict[str, float], order: list[str], out_dir: str, filename: str
) -> str:
    """Write ``values`` (``{qname: value}``) to ``<out_dir>/<filename>``; return the
    path. Format is taken from the filename extension (``.csv`` else npy). ``order``
    is the qname order for the npy array (and the row order for csv)."""
    filename = (filename or "").strip()
    if not filename or "/" in filename or "\\" in filename:
        raise ParamIOError("a bare file name is required (no path separators)")
    missing = [q for q in order if q not in values]
    if missing:
        raise ParamIOError(f"missing values for: {missing[:5]}")

    d = Path(out_dir)
    d.mkdir(parents=True, exist_ok=True)
    path = d / filename

    if format_for(filename) == "npy":
        import numpy as np  # noqa: E402 - heavy, analysis env only

        arr = np.array([float(values[q]) for q in order], dtype=float)
        with open(path, "wb") as fh:  # file handle => np.save won't re-append .npy
            np.save(fh, arr)
    else:
        with open(path, "w", newline="", encoding="utf-8") as fh:
            writer = csv.writer(fh)
            writer.writerow(["vessel_name", "param_name", "value"])
            for q in order:
                vessel, _, param = q.partition("/")
                writer.writerow([vessel, param, values[q]])
    return str(path)


def load_param_values(path: str, order: list[str] | None = None) -> dict[str, float]:
    """Read ``path`` (``.npy`` or ``.csv``) into ``{qname: value}``.

    For ``.npy`` (a bare array), ``order`` supplies the qnames to zip with, and its
    length must match the array — otherwise the file is from a different parameter
    set and there's no way to name the values (use a CSV, which carries names).
    """
    p = Path(path)
    if not p.is_file():
        raise ParamIOError(f"file not found: {path}")

    if p.suffix.lower() == ".csv":
        return _load_csv(p)

    import numpy as np  # noqa: E402

    try:
        arr = np.asarray(np.load(p, allow_pickle=False), dtype=float).ravel()
    except Exception as exc:  # noqa: BLE001 - bad/foreign npy
        raise ParamIOError(f"could not read npy: {exc}") from exc
    order = order or []
    if len(order) != len(arr):
        raise ParamIOError(
            f"the file has {len(arr)} values but there are {len(order)} current "
            "parameters — load the matching model's params first, or use a CSV "
            "(which stores parameter names)."
        )
    return {q: float(v) for q, v in zip(order, arr)}


def _load_csv(p: Path) -> dict[str, float]:
    reader = csv.DictReader(io.StringIO(p.read_text(encoding="utf-8-sig")))
    cols = {(c or "").strip(): c for c in (reader.fieldnames or [])}
    for req in ("vessel_name", "param_name", "value"):
        if req not in cols:
            raise ParamIOError(f"CSV missing required column '{req}'")
    out: dict[str, float] = {}
    for i, row in enumerate(reader):
        vessel = str(row[cols["vessel_name"]]).strip()
        param = str(row[cols["param_name"]]).strip()
        try:
            val = float(row[cols["value"]])
        except (TypeError, ValueError) as exc:
            raise ParamIOError(f"row {i}: value must be numeric") from exc
        if vessel and param:
            out[f"{vessel}/{param}"] = val
    if not out:
        raise ParamIOError("no parameter rows found")
    return out
