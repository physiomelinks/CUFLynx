"""What the current parameters cost, against the loaded obs_data (issue #159).

Manual exploration had no number attached: you moved a slider, the trace moved,
and whether it moved *towards* the data was left to the eye. A calibration
reports a cost and a per-observable error; the same figures are computable for
whatever the sliders currently say, from a run the app has already done.

Computed with circulatory_autogen's own functions, not reimplementations of
them. The operation that turns a trace into an observable is CA's, and so is
the cost function that scores it -- a `gaussian_MLE` here that disagreed with
the one calibration minimises would be worse than showing nothing, because it
would look authoritative while ranking parameter sets differently.

Both errors mirror what a calibration writes to percent_error_vec.npy and
std_error_vec.npy, so a manual perturbation and a best fit can be put on the
same axes.
"""

from __future__ import annotations

import math

from obs_options import get_cost_funcs, get_operation_funcs

# The cost when a data_item names none. CA's own default for a data_item with no
# cost_type; kept here so a file written before cost_type existed still scores.
DEFAULT_COST_TYPE = "MSE"


def _model_value(item: dict, outputs: dict, op_funcs) -> float | None:
    """The scalar this data_item's operation produces from the run."""
    operands = item.get("operands") or []
    if not operands:
        return None
    series = []
    for name in operands:
        values = outputs.get(name)
        if values is None:
            # `time` is an operand of windowed and peak-timing operations; the
            # caller folds it in beside the recorded variables.
            values = outputs.get("time") if str(name).split("/")[-1] == "time" else None
        if values is None:
            return None
        series.append(values)

    operation = item.get("operation")
    if not operation:
        # No operation: the observable *is* the operand, so the last value is
        # the only reading that makes sense for a constant.
        return float(series[0][-1]) if len(series[0]) else None

    func = (op_funcs or {}).get(operation)
    if func is None:
        return None
    kwargs = item.get("operation_kwargs")
    kwargs = kwargs if isinstance(kwargs, dict) else {}
    try:
        value = func(*series, **kwargs)
    except Exception:  # noqa: BLE001 - a bad operand shape is not our failure
        return None
    try:
        scalar = float(value)
    except (TypeError, ValueError):
        # A series-returning operation has no single value to score.
        return None
    return scalar if math.isfinite(scalar) else None


def evaluate(data_items, outputs_by_experiment, output_dir: str | None = None) -> dict | None:
    """Score the current run against the data_items.

    ``outputs_by_experiment`` is ``{experiment_idx: {variable: series}}``; a
    single-experiment run passes ``{0: outputs}``.

    Returns ``{"cost", "items": [...]}`` or None when nothing could be scored --
    no CA, no data_items, or a run that recorded none of the operands. None
    rather than a cost of zero: "perfect fit" and "could not tell" must not look
    the same.
    """
    items = [it for it in (data_items or []) if isinstance(it, dict)]
    if not items:
        return None

    op_funcs = get_operation_funcs(output_dir)
    cost_funcs = get_cost_funcs(output_dir)
    if cost_funcs is None:
        return None

    scored = []
    total = 0.0
    any_scored = False
    for item in items:
        exp = int(item.get("experiment_idx", 0) or 0)
        outputs = outputs_by_experiment.get(exp) or {}
        label = item.get("name_for_plotting") or item.get("variable") or ""
        entry = {
            "label": label,
            "operation": item.get("operation") or "",
            "experiment_idx": exp,
            "observed": item.get("value"),
            "model": None,
            "percent_error": None,
            "std_error": None,
            "cost": None,
        }

        model = _model_value(item, outputs, op_funcs)
        observed = item.get("value")
        if model is not None and isinstance(observed, (int, float)):
            entry["model"] = model
            # The same two error measures a calibration saves, so a manual
            # perturbation can be compared with a best fit on one axis.
            if observed:
                entry["percent_error"] = (model - observed) / abs(observed) * 100.0
            std = item.get("std")
            if isinstance(std, (int, float)) and std:
                entry["std_error"] = (model - observed) / std

            func = cost_funcs.get(item.get("cost_type") or DEFAULT_COST_TYPE)
            if func is not None:
                try:
                    cost = float(
                        func(model, observed, item.get("std") or 1.0, item.get("weight") or 1.0)
                    )
                except Exception:  # noqa: BLE001 - a cost func that cannot score this item
                    cost = None
                if cost is not None and math.isfinite(cost):
                    entry["cost"] = cost
                    total += cost
                    any_scored = True
        scored.append(entry)

    if not any_scored:
        return None
    return {"cost": total, "items": scored}
