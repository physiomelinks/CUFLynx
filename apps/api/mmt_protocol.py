"""Turn a Myokit ``[[protocol]]`` section into obs_data ``protocol_info``.

``myokit_import`` deliberately imports the ``[[model]]`` section and nothing
else: baking Myokit's stimulus into the exported CellML would give the model two
sources of pacing that disagree, since in CUFLynx the protocol comes from
obs_data. That leaves the protocol the user actually wrote sitting in the .mmt,
unused, to be re-entered by hand -- which is where transcription errors live.

**The conversion lives in circulatory_autogen**, in
``libcuflynx.parsers.MyokitParsers``. It belongs there rather than here because
what it produces is CA's vocabulary: the five event fields (``level``, ``start``,
``length``, ``period``, ``multiplier``) are ``protocol_shapes``' own, and this
module used to reproduce them by convention from another repository -- an
agreement with nothing enforcing it. Engine-side, the conversion validates
through ``utilities.protocol_shapes`` itself, so the two can no longer drift.

What stays here is the CUFLynx-facing surface: a stable
:class:`MmtProtocolError` (the CA directory can be re-pointed at runtime, so an
error class imported from CA would change identity mid-session), the default
beat count, and the delegation.
"""

from __future__ import annotations

from typing import Any

from myokit_import import NO_PARSER_HINT, _ca_parser

#: Beats an indefinite protocol is cut to when the caller names no duration. One
#: beat cannot show that a model returns to its diastolic state, and two can.
DEFAULT_BEATS = 2


class MmtProtocolError(ValueError):
    """A .mmt whose protocol cannot be expressed as protocol_info (surface as 422)."""


def _parser_or_raise():
    parser = _ca_parser()
    if parser is None:
        raise MmtProtocolError(f"could not read that protocol: {NO_PARSER_HINT}")
    return parser


def pace_variable(model) -> str:
    """The CA-style ``component/variable`` name of the paced variable."""
    try:
        return _parser_or_raise().pace_variable(model)
    except ValueError as exc:
        raise MmtProtocolError(str(exc)) from exc


def protocol_info_from_mmt(
    data: bytes,
    *,
    filename: str = "model.mmt",
    beats: int = DEFAULT_BEATS,
    duration: float | None = None,
    pre_time: float = 0.0,
    label: str | None = None,
) -> tuple[dict[str, Any], list[str]]:
    """Build a one-experiment ``protocol_info`` from a .mmt's ``[[protocol]]``.

    Returns ``(protocol_info, notes)``. ``notes`` records the choices the
    conversion had to make -- how long an indefinite protocol was run for, above
    all -- because those are the parts a user may want to overrule and would
    otherwise have to infer from the numbers.
    """
    parser = _parser_or_raise()
    try:
        return parser.protocol_info_from_mmt(
            data,
            filename=filename,
            beats=beats,
            duration=duration,
            pre_time=pre_time,
            label=label,
        )
    except ValueError as exc:
        raise MmtProtocolError(str(exc)) from exc


def fill_protocol_info(
    obs_data: dict[str, Any] | list | None, protocol_info: dict[str, Any]
) -> dict[str, Any]:
    """Put ``protocol_info`` into an obs_data document, returning a new dict.

    An existing document's labels and colours are kept when they still fit the
    new schedule: those are the parts a user writes for themselves ("1 Hz
    pacing" reads better than "pacing, period 1000"), and re-deriving the timings
    is no reason to throw them away.

    A bare array of data_items -- CA's other accepted shape, and what the
    3compartment / heat_fenics studies ship -- becomes the object form carrying
    those same items, which is the only shape that can hold a protocol_info at
    all. ``dict(obs_data or {})`` used to raise on one, so
    ``scripts/mmt_to_obs_data.py`` died rather than updating a data-only file.

    **Local, unlike the rest of this module.** Everything else here delegates to
    the engine because it needs Myokit or CA's protocol vocabulary; this needs
    neither. It is document plumbing -- put a key in a dict, keep the labels --
    so routing it through circulatory_autogen would make a pure function fail
    when the engine is old or Myokit is absent, which is precisely what it did.
    """
    if isinstance(obs_data, list):
        obs_data = {"data_items": obs_data}
    out = dict(obs_data or {})
    existing = out.get("protocol_info") or {}
    merged = dict(protocol_info)
    n = len(protocol_info.get("sim_times", []))
    for key in ("experiment_labels", "experiment_colors"):
        kept = existing.get(key)
        if isinstance(kept, list) and len(kept) == n:
            merged[key] = kept
    out["protocol_info"] = merged
    return out
