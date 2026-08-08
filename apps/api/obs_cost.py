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

The *aggregation* is CA's too, and has to be (#181): CA takes the mean
contribution per weighted observable, not the sum --

    paramID.get_cost_obs_and_pred_from_params:
        cost += sub_cost                       # over experiments x subexperiments
        cost = cost / float(weighted_obs_denominator)

-- so summing here reported a number larger by exactly the observable count.
Same parameters, same data, two different costs, with nothing on screen to say
which was which.
"""

from __future__ import annotations

import math
import sys
import tempfile

from obs_options import get_cost_funcs, get_operation_funcs

# The cost when a data_item names none. CA's own default for a data_item with no
# cost_type; kept here so a file written before cost_type existed still scores.
DEFAULT_COST_TYPE = "MSE"


def _ca_call_cost_func():
    """CA's ``call_cost_func``, or None on a CA that predates it (CA #370).

    Puts CA on ``sys.path`` itself rather than assuming an earlier call did: this
    is reached from both the CA path and the local-walk fallback, and a silent
    ImportError here would look exactly like an older CA and quietly go back to
    calling every cost func positionally.
    """
    try:
        from obs_options import _ca_paths  # noqa: PLC0415

        for path in _ca_paths():
            if path not in sys.path:
                sys.path.insert(0, path)
        from param_id.cost_kwargs import call_cost_func  # noqa: PLC0415

        return call_cost_func
    except Exception:  # noqa: BLE001 - older CA without the contract, or no CA
        return None


def _call_cost(func, *positional, std=None, weight=None, cost_kwargs=None):
    """Call a cost func the way circulatory_autogen calls it (CA #370).

    A cost func no longer has one fixed ``(output, ground_truth, std, weight)``
    signature: it is handed ``std``/``weight`` only if it declares them, plus the
    data_item's own ``cost_kwargs``. Routing through CA's own ``call_cost_func``
    keeps this panel calling the func exactly as the calibration will -- calling it
    positionally here would silently score a kwarg-taking cost with its defaults,
    or blow up on a cost that has no ``std`` (``multimodal_gaussian``), while
    looking authoritative, which is the failure this module exists to avoid (#159).

    Falls back to the old positional call on a pre-#370 CA, where every cost func
    still had the fixed signature and no cost_kwargs existed to pass.
    """
    call = _ca_call_cost_func()
    if call is None:
        return func(*positional, std, weight)
    return call(func, *positional, std=std, weight=weight, cost_kwargs=cost_kwargs)


def _weight_of(item: dict) -> float:
    """The item's weight, with a *deliberate* zero preserved.

    ``item.get("weight") or 1.0`` reads a 0 as "unset" and substitutes 1.0 --
    the one coercion that reverses what the user asked for, since 0 is how an
    observable is switched off.
    """
    weight = item.get("weight")
    if not isinstance(weight, (int, float)):
        return 1.0
    return float(weight)


def _std_of(item: dict) -> float:
    """The item's std; 1.0 when absent. A std of 0 would divide by zero, so it
    is treated as absent rather than passed to the cost func."""
    std = item.get("std")
    if not isinstance(std, (int, float)) or not std:
        return 1.0
    return float(std)


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


def _ca_engine(obs_data: dict, output_dir: str | None, dt: float):
    """A cost-only ``OpencorParamID`` built from an in-memory obs_data.

    ``__init__`` is bypassed deliberately: it instantiates a solver for a model
    path we do not have and would compile it, while the cost path touches none of
    that -- it needs the parsed obs_info/protocol_info, the func registries and
    dt. This is the same seam CA reuses itself for gradients (fsa_backend calls
    get_cost_from_operands on externally perturbed operands).

    Returns None when CA cannot be reached or the document cannot be parsed, so
    the caller falls back rather than losing the panel.
    """
    try:
        from obs_options import _ca_paths  # noqa: PLC0415

        for path in _ca_paths():
            if path not in sys.path:
                sys.path.insert(0, path)
        from param_id.paramID import OpencorParamID  # noqa: PLC0415
        from parsers.PrimitiveParsers import (  # noqa: PLC0415
            ObsAndParamDataParser,
            scriptFunctionParser,
        )
    except Exception:  # noqa: BLE001 - no CA, or one too old
        return None

    try:
        parser = ObsAndParamDataParser()
        parsed = parser.parse_obs_data_json(obs_data_dict=obs_data, pre_time=0.0, sim_time=1.0)
        # get_ground_truth_values writes its .npy copies unconditionally; a temp
        # dir keeps a cost evaluation from depositing files in the user's outputs.
        with tempfile.TemporaryDirectory() as scratch:
            obs_info = parser.process_obs_info(
                gt_df=parsed["gt_df"], output_dir=scratch, dt=dt)
        protocol_info = parser.process_protocol_and_weights(
            gt_df=parsed["gt_df"], protocol_info=parsed["protocol_info"], dt=dt)

        sfp = scriptFunctionParser(
            operation_funcs_external_path=_external(output_dir, "operation"),
            cost_funcs_external_path=_external(output_dir, "cost"),
        )
        pid = OpencorParamID.__new__(OpencorParamID)
        pid.obs_info = obs_info
        pid.protocol_info = protocol_info
        pid.cost_type = obs_info["cost_type"]
        pid.dt = float(dt)
        pid.model_type = "cellml_only"  # only decides symbolic vs numeric; this path is numeric
        pid.cost_funcs_dict = sfp.get_cost_funcs_dict("numpy")
        pid.cost_funcs_dict_symbolic = pid.cost_funcs_dict
        pid.operation_funcs_dict = sfp.get_operation_funcs_dict("numpy")
        pid.operation_funcs_dict_symbolic = pid.operation_funcs_dict
        pid._num_weighted_obs_by_exp_sub = None
        pid._refresh_num_weighted_obs_tables()
    except Exception:  # noqa: BLE001 - an obs_data CA cannot use
        return None
    return pid


def _external(output_dir: str | None, kind: str):
    """The user's external func file for ``kind``, if there is one (CA #303)."""
    try:
        from user_funcs import external_path  # noqa: PLC0415

        return external_path(kind, output_dir or None)
    except Exception:  # noqa: BLE001
        return None


def _operands_for(pid, segment: dict):
    """CUFLynx's ``{variable: series}`` for one segment, in CA's operand layout.

    ``operands_outputs[JJ][k]`` is operand k of data_item JJ -- the shape
    get_obs_output_dict indexes.
    """
    import numpy as np  # noqa: PLC0415

    return [
        [np.asarray(segment.get(name, []), dtype=float)
         for name in pid.obs_info["operands"][JJ]]
        for JJ in range(pid.obs_info["num_obs"])
    ]


def _ca_evaluate(obs_data, outputs_by_experiment, output_dir, dt) -> dict | None:
    """The cost, computed by circulatory_autogen rather than reproduced here.

    Every part of the number is CA's: the operation that turns a trace into an
    observable (with its operation_kwargs resolved, including a kwarg naming
    another observable), the cost func, which data_types are scorable at all, and
    the mean-per-weighted-observable aggregation. Walking the data_items here
    scored only `constant` items and left series, frequency and prob_dist counted
    but never evaluated -- so the panel could differ from the calibration it is
    meant to mirror while looking authoritative.
    """
    import numpy as np  # noqa: PLC0415

    pid = _ca_engine(obs_data, output_dir, dt)
    if pid is None:
        return None

    obs_info = pid.obs_info
    # Absent on a CA that predates cost_kwargs; then no data_item can carry any.
    kwargs_for = getattr(pid, "_cost_kwargs_for", None)
    num_sub = pid.protocol_info["num_sub_per_exp"]
    total = 0.0
    denom = 0
    models: dict[int, float] = {}
    costs: dict[int, float] = {}

    try:
        for exp in range(len(pid.protocol_info["sim_times"])):
            for sub in range(num_sub[exp]):
                segment = outputs_by_experiment.get((exp, sub))
                if segment is None:
                    segment = outputs_by_experiment.get(exp)
                if segment is None:
                    continue
                operands = _operands_for(pid, segment)
                total += float(pid.get_cost_from_operands(operands, exp_idx=exp, sub_idx=sub))
                denom += int(pid._num_weighted_obs_by_exp_sub[exp][sub])
                # The scalar observables this segment produced, for the per-item
                # rows; taken from CA's own evaluation rather than recomputed.
                const = pid.get_obs_output_dict(operands).get("const")
                if const is not None:
                    weights = pid.protocol_info[
                        "scaled_weight_const_from_exp_sub"][exp][sub]
                    for k, obs_idx in enumerate(obs_info["const_idx_to_obs_idx"]):
                        obs_idx = int(obs_idx)
                        if int(obs_info["experiment_idxs"][obs_idx]) != exp or \
                                int(obs_info["subexperiment_idxs"][obs_idx]) != sub:
                            continue
                        models[obs_idx] = float(const[k])
                        # This item's own contribution, from the same call
                        # cost_calc makes -- CA's cost func, CA's ground truth,
                        # CA's weight (indexed by data_item row, CA #349). Not a
                        # second formula: the per-item column and the total have
                        # to be the same arithmetic or they will disagree.
                        weight = float(weights[obs_idx])
                        if weight == 0:
                            continue
                        func = pid.cost_funcs_dict.get(pid.cost_type[obs_idx])
                        if func is None:
                            continue
                        try:
                            value = float(_call_cost(
                                func,
                                float(const[k]),
                                float(obs_info["ground_truth_const"][k]),
                                std=float(obs_info["std_const_vec"][k]),
                                weight=weight,
                                # The data_item's own cost_kwargs, read through CA's
                                # accessor so the per-item column indexes them the
                                # same way cost_calc does (by observable, CA #370).
                                cost_kwargs=(kwargs_for(obs_idx) if kwargs_for else None),
                            ))
                        except Exception:  # noqa: BLE001 - a func that cannot score it
                            continue
                        if math.isfinite(value):
                            costs[obs_idx] = value
    except Exception:  # noqa: BLE001 - a run CA cannot score; fall back
        return None

    if not denom:
        return None
    return {
        "cost": total / float(denom),
        "items": _ca_items(obs_info, models, costs),
        "n_weighted": denom,
        "incomplete": False,
        "computed_by": "circulatory_autogen",
    }


def _ca_items(obs_info, models: dict, costs: dict) -> list:
    """Per-observable rows for the panel, from CA's own obs_info."""
    items = []
    gt = {int(o): float(v) for o, v in
          zip(obs_info["const_idx_to_obs_idx"], obs_info["ground_truth_const"])}
    std = {int(o): float(v) for o, v in
           zip(obs_info["const_idx_to_obs_idx"], obs_info["std_const_vec"])}
    for obs_idx in range(obs_info["num_obs"]):
        model = models.get(obs_idx)
        observed = gt.get(obs_idx)
        entry = {
            "label": str(obs_info["names_for_plotting"][obs_idx]),
            "operation": str(obs_info["operations"][obs_idx] or ""),
            "experiment_idx": int(obs_info["experiment_idxs"][obs_idx]),
            "subexperiment_idx": int(obs_info["subexperiment_idxs"][obs_idx]),
            "observed": observed,
            "model": model,
            "percent_error": None,
            "std_error": None,
            "cost": costs.get(obs_idx),
        }
        if model is not None and observed is not None:
            if observed:
                entry["percent_error"] = (model - observed) / abs(observed) * 100.0
            sigma = std.get(obs_idx)
            if sigma:
                entry["std_error"] = (model - observed) / sigma
        items.append(entry)
    return items


def evaluate(data_items, outputs_by_experiment, output_dir: str | None = None,
             obs_data: dict | None = None, dt: float = 0.01) -> dict | None:
    """Score the current run against the data_items.

    ``outputs_by_experiment`` is keyed by ``(experiment_idx, subexperiment_idx)``
    or, for a run with no subexperiments, by ``experiment_idx`` alone; a single
    run passes ``{0: outputs}``. A data_item names both, and CA scores it against
    that subexperiment's own segment (#181) -- keying on the experiment alone put
    every item past the first subexperiment against the wrong trace.

    Returns ``{"cost", "items": [...]}`` or None when nothing could be scored --
    no CA, no data_items, or a run that recorded none of the operands. None
    rather than a cost of zero: "perfect fit" and "could not tell" must not look
    the same.
    """
    items = [it for it in (data_items or []) if isinstance(it, dict)]
    if not items:
        return None

    # CA first: it owns the cost the calibration minimises, and reproducing that
    # here is what made the two disagree (#181, #182). The walk below stays as the
    # fallback for a CA that cannot be reached or an obs_data it cannot parse --
    # losing the panel entirely would be worse than an approximate number, but it
    # is approximate, and `computed_by` says which one you are looking at.
    if obs_data is not None:
        via_ca = _ca_evaluate(obs_data, outputs_by_experiment, output_dir, dt)
        if via_ca is not None:
            return via_ca

    op_funcs = get_operation_funcs(output_dir)
    cost_funcs = get_cost_funcs(output_dir)
    if cost_funcs is None:
        return None

    scored = []
    total = 0.0
    any_scored = False
    # CA's denominator: every observable with a non-zero weight, across all
    # experiments and subexperiments. Counted even when we cannot score it --
    # CA can, so leaving it out would report a *better* cost than the
    # calibration for the same parameters.
    weighted = 0
    unscored = 0
    for item in items:
        exp = int(item.get("experiment_idx", 0) or 0)
        sub = int(item.get("subexperiment_idx", 0) or 0)
        weight = _weight_of(item)
        # (exp, sub) when the run kept its segments; the experiment alone when
        # it did not, so a plain simulate still scores.
        outputs = (
            outputs_by_experiment.get((exp, sub))
            if (exp, sub) in outputs_by_experiment
            else outputs_by_experiment.get(exp)
        ) or {}
        label = item.get("name_for_plotting") or item.get("variable") or ""
        entry = {
            "label": label,
            "operation": item.get("operation") or "",
            "experiment_idx": exp,
            "subexperiment_idx": sub,
            "observed": item.get("value"),
            "model": None,
            "percent_error": None,
            "std_error": None,
            "cost": None,
        }

        if weight == 0.0:
            # CA skips these (`if weight_entry != 0`), and so must the
            # denominator: switching an observable off would otherwise change
            # the cost of the ones left on.
            scored.append(entry)
            continue
        weighted += 1

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
                item_kwargs = item.get("cost_kwargs")
                try:
                    cost = float(_call_cost(
                        func, model, observed,
                        std=_std_of(item), weight=weight,
                        cost_kwargs=item_kwargs if isinstance(item_kwargs, dict) else None,
                    ))
                except Exception:  # noqa: BLE001 - a cost func that cannot score this item
                    cost = None
                if cost is not None and math.isfinite(cost):
                    entry["cost"] = cost
                    total += cost
                    any_scored = True
        if entry["cost"] is None:
            unscored += 1
        scored.append(entry)

    if not any_scored:
        return None
    return {
        "cost": total / float(weighted or 1),
        "items": scored,
        "n_weighted": weighted,
        "computed_by": "cuflynx",
        # Some weighted observable had no number behind it, so this is the mean
        # over a numerator CA would have filled in -- lower than CA's, and worth
        # saying so rather than presenting it as the same figure.
        "incomplete": unscored > 0,
    }
