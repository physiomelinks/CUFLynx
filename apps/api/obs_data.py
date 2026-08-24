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
``data_item_name``, ``data_type``, ``unit``, ``operands``, ``value`` and ``std``
REQUIRED, requires ``data_item_name`` to be unique across ``data_items`` and
``prediction_items`` (CA #466), and rejects any key outside its schema -- none of
which was checked here.
A typo'd ``opperation`` used to upload cleanly, plot, and show a cost, and only
fail when a calibration subprocess started.
"""

from __future__ import annotations

import copy
import sys
from dataclasses import dataclass, field


class ObsDataError(ValueError):
    """Raised for a malformed or invalid obs_data document (maps to HTTP 422)."""


@dataclass
class CaVerdict:
    """What circulatory_autogen said about a document -- including "nothing".

    ``error`` is CA's own schema complaint, the user's to answer for. ``skipped``
    is set when CA could not be *asked* (no clone, an import that failed, a crash
    inside its parser): the document is still accepted, because a missing CA is
    not a fault in the user's file, but the fact that nobody checked it must not
    be indistinguishable from a clean bill of health.
    """

    error: str | None = None
    skipped: str | None = None


@dataclass
class ObsData:
    protocol_info: dict | None
    data_items: list[dict]
    prediction_items: list[dict]
    #: Things worth saying about a document that was nonetheless loaded: the
    #: checks that could not run, the deprecated vocabulary it is written in.
    #: Surfaced by every route that parses one, so nothing loads quietly.
    warnings: list[str] = field(default_factory=list)

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


#: obs_data entry keys that CA #466 replaced. A file still using them was written for
#: a CA that has since changed under it, and CA's own complaint is about the
#: *consequence* -- a duplicate ``data_item_name`` -- rather than the cause, so the
#: cause is said here, along with the migrator that fixes it.
LEGACY_ITEM_KEYS = ("variable", "name_for_plotting")

_MIGRATION_HINT = (
    "This obs_data is written in the vocabulary circulatory_autogen used before its #466 "
    "split ({keys}): 'variable' both named an item and supplied its operand, and one name "
    "was allowed to repeat across the mean/max/min of a trace. 'data_item_name' now has to "
    "be unique. Convert the file with `cuflynx-migrate-obs-data <file>` (it ships with "
    "circulatory_autogen): it qualifies a colliding name by whatever distinguishes the "
    "items, so 'pressure aortic root' becomes 'mean pressure aortic root', 'max pressure "
    "aortic root' and 'min pressure aortic root'."
)


def legacy_vocabulary_hint(obj) -> str | None:
    """How to bring a pre-#466 obs_data up to date, or None if it already is.

    Both an error's second paragraph (when the collision CA reports *is* the
    split) and a warning of its own (when the old keys happen not to collide, so
    the file loads today and fails at some later CA release).
    """
    items = list(data_items_of(obj))
    if isinstance(obj, dict):
        predictions = obj.get("prediction_items")
        if isinstance(predictions, list):
            items = items + predictions
    found = sorted(
        {k for it in items if isinstance(it, dict) for k in LEGACY_ITEM_KEYS if k in it}
    )
    if not found:
        return None
    return _MIGRATION_HINT.format(keys=", ".join(f"'{k}'" for k in found))


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
    verdict = ca_verdict(obj)
    hint = legacy_vocabulary_hint(obj)
    if verdict.error:
        message = f"circulatory_autogen rejected this obs_data: {verdict.error}"
        # The duplicate-name complaint on a pre-#466 file reads as a mistake the
        # author made, when in fact the file was correct when it was written and
        # the schema moved. Say which it is, and how to convert it.
        if hint:
            message = f"{message}\n\n{hint}"
        raise ObsDataError(message)

    warnings = []
    if verdict.skipped:
        warnings.append(verdict.skipped)
    if hint:
        # Accepted -- CA's migration shim still reads the old keys -- but on
        # borrowed time, and silently only until the names happen to collide.
        warnings.append(hint)

    return ObsData(
        protocol_info=protocol_info,
        data_items=data_items,
        prediction_items=prediction_items,
        warnings=warnings,
    )


#: Why the last :func:`_ca_parser` call came back empty-handed, for the message that
#: says the document went unchecked. Module-level because the reason is discovered
#: inside the import and is of no interest to the callers that only want the parser.
_ca_unavailable_reason: str | None = None


def _ca_parser():
    """CA's obs_data parser, or None when CA cannot be reached.

    Imported lazily and through the same sys.path entries the option
    introspection uses, so this module stays importable -- and the unit test tier
    stays runnable -- without CA present. CA's parser itself needs neither Myokit
    nor libCellML, which is why consulting it here does not drag the simulation
    stack into the upload path.
    """
    global _ca_unavailable_reason
    try:
        from ca_imports import ca_from, ensure_ca_path  # noqa: PLC0415

        ensure_ca_path()
        ObsAndParamDataParser = ca_from(
            "parsers.PrimitiveParsers", "ObsAndParamDataParser")
    except Exception as exc:  # noqa: BLE001 - CA absent or too old; nothing to ask
        _ca_unavailable_reason = f"{type(exc).__name__}: {exc}"
        return None
    _ca_unavailable_reason = None
    return ObsAndParamDataParser()


def ca_schema_error(obj) -> str | None:
    """circulatory_autogen's complaint about this obs_data, or None.

    The verdict without the reason it may be missing -- see :func:`ca_verdict`,
    which is what the upload paths use.
    """
    return ca_verdict(obj).error


def ca_verdict(obj) -> CaVerdict:
    """What circulatory_autogen makes of this obs_data.

    Returns CA's own complaint, so the message the user sees at upload is the
    message the calibration would have failed with. Never invents a verdict:
    when CA cannot be imported -- no clone configured, or one predating the
    schema -- the upload proceeds on the structural checks alone, exactly as it
    did before.

    What is new is that "CA said nothing" and "CA was never asked" are no longer
    the same answer. Both let the document through; only the second means the
    schema was not checked, and a user whose typo'd key will fail a calibration
    twenty minutes from now is owed that distinction at upload time.

    The document is deep-copied first. CA's parser materialises protocol shapes
    and normalises series std in place, and validation must not quietly rewrite
    the obs_data the app then runs and hands back to the editor.
    """
    parser = _ca_parser()
    if parser is None:
        detail = f" ({_ca_unavailable_reason})" if _ca_unavailable_reason else ""
        return CaVerdict(
            skipped=(
                f"circulatory_autogen could not be consulted{detail}, so this obs_data was "
                "accepted on CUFLynx's structural checks alone. Anything only CA rejects -- a "
                "typo'd 'opperation', a repeated 'data_item_name', a key outside its schema -- "
                "will not be caught until a run starts."
            )
        )

    try:
        # pre_time/sim_time are only consulted when protocol_info omits
        # pre_times/sim_times (the data-only form, which CUFLynx runs with manual
        # time); they exist to satisfy the parser, not to describe the run.
        parser.parse_obs_data_json(
            obs_data_dict=copy.deepcopy(obj), pre_time=0.0, sim_time=1.0
        )
    except ValueError as exc:
        return CaVerdict(error=str(exc))
    except Exception as exc:  # noqa: BLE001
        # Something other than a schema complaint -- a CA bug, a missing optional
        # dependency. Not the user's document to answer for, so don't block them;
        # but do not pass it off as a clean check either.
        return CaVerdict(
            skipped=(
                f"circulatory_autogen's parser raised {type(exc).__name__}: {exc}. That is a "
                "problem in CA rather than in this obs_data, so the document was loaded -- but "
                "its schema was not checked."
            )
        )
    return CaVerdict()


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
