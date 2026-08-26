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

from myokit_import import NO_PARSER_HINT, _ca_parser, no_parser_hint


def _ca_fill_protocol_info():
    """CA's ``fill_protocol_info``, or None when this CA does not have it.

    Asked for **by name**, not by module: ``utilities.obs_data_helpers`` has
    existed for far longer than this function has lived in it (CA #496), so a
    module-level probe answers "yes" against a CA that would then raise
    AttributeError. ``ca_from`` raises for a missing name, which is the
    distinction that matters.
    """
    try:
        from ca_imports import ca_from, ensure_ca_path  # noqa: PLC0415

        ensure_ca_path()
        return ca_from("utilities.obs_data_helpers", "fill_protocol_info")
    except Exception:  # noqa: BLE001 - CA absent or too old; nothing to ask
        return None

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

    Delegated like the rest of this module, to
    ``libcuflynx.utilities.obs_data_helpers`` -- not to the Myokit reader, because
    it has nothing to do with Myokit: the EasyML reader produces a protocol_info
    too, and so could anything else. Every key it writes (``protocol_info``,
    ``data_items``, ``experiment_labels``, ``experiment_colors``) is that module's
    vocabulary, and that module is where those names get migrated when they change
    (CA #466 renamed several). A copy here would keep writing the old spelling with
    nothing to catch it, which is why the copy that lived here is gone.
    """
    fill = _ca_fill_protocol_info()
    if fill is None:
        raise MmtProtocolError(
            "could not update that obs_data: "
            + no_parser_hint("obs_data", "libcuflynx.utilities.obs_data_helpers")
        )
    try:
        return fill(obs_data, protocol_info)
    except ValueError as exc:
        raise MmtProtocolError(str(exc)) from exc
