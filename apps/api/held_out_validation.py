"""Validating a calibrated model against the held-out data in its obs_data.

A ``prediction_item`` may carry measured data -- ``value`` with ``data_type``,
``std`` and, for a series, ``obs_dt`` (circulatory_autogen #535). It is never
scored in the calibration; after it, the best fit's prediction is compared with
that data. circulatory_autogen owns the comparison
(``param_id.validation.validation_results``) and this module calls it whenever
the CA in reach has it.

Against a CA that predates #535 the same scores come from
:func:`local_validation_results`, a copy with CA's rules exactly -- a series
compared at ``k * obs_dt`` within the run with the model linearly interpolated
onto those times, a constant compared with the model at the end of the
experiment -- and the same result shape, so the reader, the API and the panel
cannot tell which side produced it. **Keep the two in step**; the tests pin the
copy against CA's own test cases.

Ships into ``runners/``: the calibration runner is what calls it.
"""

from __future__ import annotations


def prediction_info(prediction_items: list) -> dict:
    """The parser's ``prediction_info`` columns, built from the raw items.

    The CA that ran the calibration may have been handed the document without
    its held-out keys (see ``obs_data.for_ca``), so its own ``prediction_info``
    has no values in it. The item order is the document's in both, which is what
    lets the saved prediction traces be matched to these rows.
    """
    info = {k: [] for k in ("data_item_names", "operands", "units", "experiment_idxs",
                            "data_types", "values", "stds", "obs_dts")}
    for i, item in enumerate(prediction_items):
        item = item if isinstance(item, dict) else {}
        info["data_item_names"].append(str(item.get("data_item_name", f"prediction_{i}")))
        operands = item.get("operands") or []
        info["operands"].append([str(o) for o in operands])
        info["units"].append(str(item.get("unit", "")))
        info["experiment_idxs"].append(int(item.get("experiment_idx", 0) or 0))
        info["data_types"].append(item.get("data_type"))
        info["values"].append(item.get("value"))
        info["stds"].append(item.get("std"))
        info["obs_dts"].append(item.get("obs_dt"))
    return info


def _as_array(x):
    import numpy as np  # noqa: PLC0415

    return None if x is None else np.atleast_1d(np.asarray(x, dtype=float))


def _item_result(name, operand, unit, data_type, value, std, obs_dt, t_sim, model):
    """One item's scores and plotted series -- CA's ``_item_result``, line for line."""
    import numpy as np  # noqa: PLC0415

    data = _as_array(value)
    std = _as_array(std)
    t_sim = np.asarray(t_sim, dtype=float)
    model = np.asarray(model, dtype=float).ravel()
    if data_type == "series":
        t_obs = np.arange(data.size) * float(obs_dt)
        keep = t_obs <= t_sim[-1] + 1e-12 * max(1.0, abs(t_sim[-1]))
        t_obs, data = t_obs[keep], data[keep]
        if std is not None and std.size > 1:
            std = std[:keep.size][keep]
        model_at = np.interp(t_obs, t_sim, model)
    else:
        t_obs = np.array([t_sim[-1]])
        data = data[:1]
        model_at = model[-1:]
    if std is not None and std.size == 1 and data.size > 1:
        std = np.full(data.shape, float(std[0]))
    diff = model_at - data
    rmse = float(np.sqrt(np.mean(diff ** 2))) if data.size else None
    span = float(np.ptp(data)) if data.size > 1 else float(np.max(np.abs(data))) if data.size else 0.0
    nrmse = rmse / span if rmse is not None and span > 0 else None
    z = np.abs(diff) / std if std is not None and np.all(std > 0) else None
    return {
        "data_item_name": name,
        "operand": operand,
        "unit": unit,
        "data_type": data_type,
        "n_points": int(data.size),
        "rmse": rmse,
        "nrmse": nrmse,
        "mean_abs_z": float(np.mean(z)) if z is not None and z.size else None,
        "within_2std": float(np.mean(z <= 2.0)) if z is not None and z.size else None,
        "t": t_obs.tolist(),
        "data": data.tolist(),
        "std": std.tolist() if std is not None else None,
        "model": model_at.tolist(),
    }


def local_validation_results(info: dict, time_per_exp: dict, prediction_per_item: list) -> dict:
    """``{'items': [...]}`` for every item with data -- CA's ``validation_results``."""
    values = info.get("values") or []
    n = len(values)
    stds = info.get("stds") or [None] * n
    obs_dts = info.get("obs_dts") or [None] * n
    items = []
    for i, value in enumerate(values):
        if value is None:
            continue
        exp_idx = int(info["experiment_idxs"][i])
        operands = info["operands"][i]
        items.append(_item_result(
            info["data_item_names"][i], str(operands[0]) if len(operands) else "",
            info["units"][i], info["data_types"][i], value, stds[i], obs_dts[i],
            time_per_exp[exp_idx], prediction_per_item[i]))
    return {"items": items}


def validation_results(info: dict, time_per_exp: dict, prediction_per_item: list) -> dict:
    """CA's ``validation_results`` when it has one, else the local copy."""
    try:
        from ca_imports import ca_from  # noqa: PLC0415

        ca_validation_results = ca_from("param_id.validation", "validation_results")
    except ImportError:
        return local_validation_results(info, time_per_exp, prediction_per_item)
    return ca_validation_results(info, time_per_exp, prediction_per_item)
