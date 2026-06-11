"""Parsing and validation for circulatory_autogen ``obs_data.json`` files.

Two shapes are accepted:

* an **object** with a ``protocol_info`` block (+ ``data_items`` /
  ``prediction_items``) — drives a multi-experiment protocol run; and
* a bare **array** of ``data_items`` (the legacy data-only format, e.g.
  ``3compartment_obs_data.json``) — overlays only, run with manual time.

We implement the focused subset the API needs directly rather than importing
``parsers.PrimitiveParsers.parse_obs_data_json`` (which pulls libCellML at import
time and would drag the simulation deps into this path).
"""

from __future__ import annotations

from dataclasses import dataclass


class ObsDataError(ValueError):
    """Raised for a malformed or invalid obs_data document (maps to HTTP 422)."""


@dataclass
class ObsData:
    protocol_info: dict | None
    data_items: list[dict]
    prediction_items: list[dict]

    @property
    def has_protocol(self) -> bool:
        return self.protocol_info is not None

    @property
    def n_experiments(self) -> int:
        if self.protocol_info is not None:
            return len(self.protocol_info.get("sim_times", []))
        # Data-only: infer from the data_items' experiment indices.
        idxs = [it.get("experiment_idx", 0) for it in self.data_items if isinstance(it, dict)]
        return (max(idxs) + 1) if idxs else 1

    def summary(self) -> dict:
        labels = None
        if self.protocol_info is not None:
            labels = self.protocol_info.get("experiment_labels")
        if not labels:
            labels = [f"experiment_{i}" for i in range(self.n_experiments)]
        return {
            "has_protocol": self.has_protocol,
            "n_experiments": self.n_experiments,
            "n_data_items": len(self.data_items),
            "n_prediction_items": len(self.prediction_items),
            "experiment_labels": labels,
        }


def parse_obs_data(obj) -> ObsData:
    """Validate and structure a parsed obs_data JSON value (object or array).

    Raises :class:`ObsDataError` with a user-facing message for every documented
    invalid case.
    """
    if isinstance(obj, list):
        protocol_info = None
        data_items = obj
        prediction_items: list = []
    elif isinstance(obj, dict):
        protocol_info = obj.get("protocol_info")
        if not isinstance(protocol_info, dict):
            raise ObsDataError("protocol_info is required")
        _validate_protocol_info(protocol_info)
        data_items = obj.get("data_items", []) or []
        prediction_items = obj.get("prediction_items", []) or []
    else:
        raise ObsDataError("obs_data must be a JSON object or array")

    if not isinstance(data_items, list):
        raise ObsDataError("'data_items' must be a list")

    if protocol_info is not None:
        n_experiments = len(protocol_info["sim_times"])
    else:
        idxs = [it.get("experiment_idx", 0) for it in data_items if isinstance(it, dict)]
        n_experiments = (max(idxs) + 1) if idxs else 1

    for i, item in enumerate(data_items):
        if not isinstance(item, dict):
            raise ObsDataError(f"data_items[{i}] must be an object")
        if item.get("data_type") == "series" and item.get("obs_dt") is None:
            raise ObsDataError("obs_dt is required for series entries")
        exp_idx = item.get("experiment_idx", 0)
        if not isinstance(exp_idx, int) or not (0 <= exp_idx < n_experiments):
            raise ObsDataError(
                f"experiment_idx {exp_idx} out of range "
                f"(0..{n_experiments - 1}) in data_items[{i}]"
            )

    if protocol_info is not None:
        _validate_traces(protocol_info)

    return ObsData(
        protocol_info=protocol_info,
        data_items=data_items,
        prediction_items=prediction_items,
    )


def _validate_protocol_info(protocol_info: dict) -> None:
    sim_times = protocol_info.get("sim_times")
    pre_times = protocol_info.get("pre_times")
    if sim_times is None or pre_times is None:
        raise ObsDataError("protocol_info must contain 'pre_times' and 'sim_times'")
    if not isinstance(sim_times, list) or not isinstance(pre_times, list):
        raise ObsDataError("'pre_times' and 'sim_times' must be lists")
    if len(sim_times) != len(pre_times):
        raise ObsDataError("'pre_times' and 'sim_times' must have the same length")


def _validate_traces(protocol_info: dict) -> None:
    params_to_change = protocol_info.get("params_to_change", {}) or {}
    traces = protocol_info.get("protocol_traces", {}) or {}
    for pname, pval in params_to_change.items():
        for key in _string_trace_keys(pval):
            if key not in traces:
                raise ObsDataError(
                    f"trace key '{key}' for param '{pname}' not found in protocol_traces"
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
