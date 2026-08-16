"""Parsing and validation for circulatory_autogen ``obs_data.json`` files.

Two shapes are accepted:

* an **object** with a ``protocol_info`` block (+ ``data_items`` /
  ``prediction_items``) — drives a multi-experiment protocol run; and
* a bare **array** of ``data_items`` (the legacy data-only format, e.g.
  ``3compartment_obs_data.json``) — overlays only, run with manual time.

The structure above is parsed here directly -- enough to load a protocol and draw
overlays without the simulation stack. Whether the document is one
circulatory_autogen can actually *calibrate* is CA's own question, and
:func:`ca_schema_error` asks it rather than reimplementing the answer: CA marks
``variable``, ``data_type``, ``unit``, ``operands``, ``value`` and ``std``
REQUIRED and rejects any key outside its schema, none of which was checked here.
A typo'd ``opperation`` used to upload cleanly, plot, and show a cost, and only
fail when a calibration subprocess started.
"""

from __future__ import annotations

import copy
import sys
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


def data_items_of(obj) -> list:
    """The ``data_items`` of an obs_data document, in either accepted shape.

    The one place the "object or bare array" rule is applied outside
    :func:`parse_obs_data`, so a site that only wants to walk the items does not
    have to re-decide which shape it was handed. Every such site used to assume
    the object form and died with ``'list' object has no attribute 'get'`` on a
    data-only file (the shipped 3compartment / heat_fenics obs_data are bare
    arrays).

    Tolerant on purpose: this is for consumers of an already-accepted document
    (the runners, the exported scripts), not for validation. Anything that is
    not one of the two shapes reads as "no items"; :func:`parse_obs_data` is
    what refuses a malformed document, with a message naming the problem.

    The list returned for the object form is the document's own list, and its
    entries are the document's own dicts in both shapes -- so a caller that
    edits an item in place (e.g. stamping a ``cost_type``) edits the document,
    and can then write the document back out in the shape it arrived in.
    """
    if isinstance(obj, list):
        return obj
    if isinstance(obj, dict):
        items = obj.get("data_items") or []
        return items if isinstance(items, list) else []
    return []


def protocol_info_of(obj) -> dict | None:
    """The ``protocol_info`` block of an obs_data document, or None.

    None for the bare-array (data-only) form, which by definition carries no
    protocol -- CUFLynx runs those with manual time. Same tolerance rule as
    :func:`data_items_of`.
    """
    if isinstance(obj, dict):
        info = obj.get("protocol_info")
        return info if isinstance(info, dict) else None
    return None


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

    # Last, so the structural messages above (which name the offending index)
    # win when both apply.
    ca_error = ca_schema_error(obj)
    if ca_error:
        raise ObsDataError(f"circulatory_autogen rejected this obs_data: {ca_error}")

    return ObsData(
        protocol_info=protocol_info,
        data_items=data_items,
        prediction_items=prediction_items,
    )


def _ca_parser():
    """CA's obs_data parser, or None when CA cannot be reached.

    Imported lazily and through the same sys.path entries the option
    introspection uses, so this module stays importable -- and the unit test tier
    stays runnable -- without CA present. CA's parser itself needs neither Myokit
    nor libCellML, which is why consulting it here does not drag the simulation
    stack into the upload path.
    """
    try:
        from ca_imports import ca_from, ensure_ca_path  # noqa: PLC0415

        ensure_ca_path()
        ObsAndParamDataParser = ca_from(
            "parsers.PrimitiveParsers", "ObsAndParamDataParser")
    except Exception:  # noqa: BLE001 - CA absent or too old; nothing to ask
        return None
    return ObsAndParamDataParser()


def ca_schema_error(obj) -> str | None:
    """circulatory_autogen's verdict on this obs_data, or None if it has none.

    Returns CA's own complaint, so the message the user sees at upload is the
    message the calibration would have failed with. Never invents a verdict:
    when CA cannot be imported -- no clone configured, or one predating the
    schema -- this returns None and the upload proceeds on the structural checks
    alone, exactly as it did before.

    The document is deep-copied first. CA's parser materialises protocol shapes
    and normalises series std in place, and validation must not quietly rewrite
    the obs_data the app then runs and hands back to the editor.
    """
    parser = _ca_parser()
    if parser is None:
        return None

    try:
        # pre_time/sim_time are only consulted when protocol_info omits
        # pre_times/sim_times (the data-only form, which CUFLynx runs with manual
        # time); they exist to satisfy the parser, not to describe the run.
        parser.parse_obs_data_json(
            obs_data_dict=copy.deepcopy(obj), pre_time=0.0, sim_time=1.0
        )
    except ValueError as exc:
        return str(exc)
    except Exception:  # noqa: BLE001
        # Something other than a schema complaint -- a CA bug, a missing optional
        # dependency. Not the user's document to answer for, so don't block them.
        return None
    return None


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
    """A params_to_change string has to name something that exists.

    Two things it can name. ``protocol_traces`` is a point table -- fully
    general, and what circulatory_autogen ultimately runs. ``protocol_shapes``
    declares the same waveform as Myokit-style events and CA expands it into a
    trace when it reads the file, which is what the editor writes because a
    declaration can be read back and a point table cannot. Either resolves.
    """
    params_to_change = protocol_info.get("params_to_change", {}) or {}
    traces = protocol_info.get("protocol_traces", {}) or {}
    shapes = protocol_info.get("protocol_shapes", {}) or {}
    both = sorted(set(traces) & set(shapes))
    if both:
        raise ObsDataError(
            f"{both} defined in both protocol_traces and protocol_shapes; "
            "they are alternatives, so define each name in one or the other"
        )
    for pname, pval in params_to_change.items():
        for key in _string_trace_keys(pval):
            if key not in traces and key not in shapes:
                raise ObsDataError(
                    f"trace key '{key}' for param '{pname}' not found in "
                    f"protocol_traces or protocol_shapes"
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
