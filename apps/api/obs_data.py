"""Parsing and validation for circulatory_autogen ``obs_data.json`` files.

We implement the focused subset the API needs (protocol summary + data-item
validation) directly, rather than importing
``parsers.PrimitiveParsers.parse_obs_data_json``.  The PrimitiveParsers module
imports libCellML at import time, which would drag the heavy simulation
dependencies into the obs-data *upload* path and its unit tests.  The qname and
``protocol_info`` conventions here match circulatory_autogen exactly.
"""

from __future__ import annotations

from dataclasses import dataclass


class ObsDataError(ValueError):
    """Raised for a malformed or invalid obs_data document (maps to HTTP 422)."""


@dataclass
class ObsData:
    protocol_info: dict
    data_items: list[dict]
    prediction_items: list[dict]

    @property
    def n_experiments(self) -> int:
        return len(self.protocol_info.get("sim_times", []))

    def summary(self) -> dict:
        pi = self.protocol_info
        labels = pi.get("experiment_labels")
        if not labels:
            labels = [f"experiment_{i}" for i in range(self.n_experiments)]
        return {
            "n_experiments": self.n_experiments,
            "n_data_items": len(self.data_items),
            "n_prediction_items": len(self.prediction_items),
            "experiment_labels": labels,
        }


def parse_obs_data(obj: dict) -> ObsData:
    """Validate and structure a parsed obs_data JSON object.

    Raises :class:`ObsDataError` with a user-facing message for every documented
    invalid case.
    """
    if not isinstance(obj, dict):
        raise ObsDataError("obs_data must be a JSON object")

    protocol_info = obj.get("protocol_info")
    if not isinstance(protocol_info, dict):
        raise ObsDataError("protocol_info is required")

    sim_times = protocol_info.get("sim_times")
    pre_times = protocol_info.get("pre_times")
    if sim_times is None or pre_times is None:
        raise ObsDataError("protocol_info must contain 'pre_times' and 'sim_times'")
    if not isinstance(sim_times, list) or not isinstance(pre_times, list):
        raise ObsDataError("'pre_times' and 'sim_times' must be lists")
    if len(sim_times) != len(pre_times):
        raise ObsDataError("'pre_times' and 'sim_times' must have the same length")

    n_experiments = len(sim_times)
    data_items = obj.get("data_items", []) or []
    prediction_items = obj.get("prediction_items", []) or []
    if not isinstance(data_items, list):
        raise ObsDataError("'data_items' must be a list")

    for i, item in enumerate(data_items):
        if not isinstance(item, dict):
            raise ObsDataError(f"data_items[{i}] must be an object")
        dtype = item.get("data_type")
        if dtype == "series" and item.get("obs_dt") is None:
            raise ObsDataError("obs_dt is required for series entries")
        exp_idx = item.get("experiment_idx", 0)
        if not isinstance(exp_idx, int) or not (0 <= exp_idx < n_experiments):
            raise ObsDataError(
                f"experiment_idx {exp_idx} out of range "
                f"(0..{n_experiments - 1}) in data_items[{i}]"
            )

    # params_to_change string values reference protocol_traces keys.
    params_to_change = protocol_info.get("params_to_change", {}) or {}
    traces = protocol_info.get("protocol_traces", {}) or {}
    for pname, pval in params_to_change.items():
        keys = _string_trace_keys(pval)
        for key in keys:
            if key not in traces:
                raise ObsDataError(
                    f"trace key '{key}' for param '{pname}' not found in protocol_traces"
                )

    return ObsData(
        protocol_info=protocol_info,
        data_items=data_items,
        prediction_items=prediction_items,
    )


def _string_trace_keys(value) -> list[str]:
    """Collect string (trace-key) leaves from a params_to_change value."""
    keys: list[str] = []
    if isinstance(value, str):
        keys.append(value)
    elif isinstance(value, list):
        for v in value:
            keys.extend(_string_trace_keys(v))
    return keys
