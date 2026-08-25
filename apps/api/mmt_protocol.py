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
    """
    parser = _parser_or_raise()
    try:
        return parser.fill_protocol_info(obs_data, protocol_info)
    except ValueError as exc:
        raise MmtProtocolError(str(exc)) from exc
