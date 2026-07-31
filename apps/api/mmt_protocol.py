"""Turn a Myokit ``[[protocol]]`` section into obs_data ``protocol_info``.

``myokit_import`` deliberately imports the ``[[model]]`` section and nothing
else: baking Myokit's stimulus into the exported CellML would give the model two
sources of pacing that disagree, since in CUFLynx the protocol comes from
obs_data. That leaves the protocol the user actually wrote sitting in the .mmt,
unused, to be re-entered by hand -- which is where transcription errors live.
This module reads it back out and writes it where CUFLynx expects it.

The two formats say the same thing in different shapes:

    Myokit   a list of events: (level, start, length, period, multiplier),
             i.e. a stimulus waveform defined by when it fires.
    CA       one sub-experiment, with ``protocol_shapes`` holding those same five
             fields under those same names, which CA expands into the point
             table its solvers want.

So the events copy across unchanged. The alternative -- slicing the run into a
sub-experiment per constant stretch of the waveform -- describes the same
stimulus, but describes it in a form that cannot be read back: five durations
and five levels do not announce that they are a 1 Hz stimulus, so the period
cannot be edited afterwards, only recomputed.

A periodic Myokit protocol usually runs forever (``multiplier=0``), while a CA
experiment has a finite length -- so an indefinite protocol still needs a number
of beats, which is a choice and not a conversion. It defaults to 2: one beat
cannot show that a model returns to its diastolic state, and two can. The events
keep their own ``multiplier``, so it is the sub-experiment's length that decides
how many stimuli land.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

DEFAULT_BEATS = 2


class MmtProtocolError(ValueError):
    """A .mmt whose protocol cannot be expressed as protocol_info (surface as 422)."""


def _load(data: bytes, filename: str):
    try:
        import myokit  # noqa: PLC0415 - optional/heavy, imported on use
    except ImportError as exc:  # pragma: no cover - myokit is present in CI
        raise MmtProtocolError(
            "Myokit is not installed, so a .mmt protocol cannot be read."
        ) from exc

    stem = Path(filename).stem or "model"
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / f"{stem}.mmt"
        path.write_bytes(data)
        try:
            model, protocol, _script = myokit.load(str(path))
        except Exception as exc:  # noqa: BLE001 - myokit raises several types
            raise MmtProtocolError(f"could not read the Myokit file: {exc}") from exc
    return model, protocol


def pace_variable(model) -> str:
    """The CA-style ``component/variable`` name of the paced variable.

    Myokit marks it with ``bind pace``; that binding is the only thing in the
    file that says which variable the protocol drives, so a model without one
    gives the levels nowhere to go.
    """
    if model is None:
        raise MmtProtocolError(
            "that .mmt has no [[model]] section, so there is no way to tell which "
            "variable the protocol drives. Add a variable with `bind pace`, or "
            "write the protocol_info by hand."
        )
    bound = [v for v in model.variables(deep=True) if v.binding() == "pace"]
    if not bound:
        raise MmtProtocolError(
            "no variable in that .mmt is bound to `pace`, so there is nothing for "
            "the protocol to drive. Myokit applies such a protocol through a "
            "simulation's own pacing input rather than through the model, which "
            "CUFLynx has no equivalent of -- add `bind pace` to the stimulus "
            "variable, or write the protocol_info by hand."
        )
    var = bound[0]
    if var.is_state():
        raise MmtProtocolError(
            f"`pace` is bound to {var.qname()}, which is a state variable. CUFLynx "
            "drives the protocol by setting a parameter between sub-experiments, "
            "and a state is integrated rather than set."
        )
    # Myokit qualifies with a dot, CA and Myokit-in-CA with a slash. The CellML
    # export keeps both names, so this is a spelling change, not a mapping.
    return var.qname().replace(".", "/")


def _duration(protocol, beats: int, duration: float | None) -> tuple[float, list[str]]:
    notes: list[str] = []
    if duration is not None:
        if duration <= 0:
            raise MmtProtocolError("duration must be greater than zero.")
        return float(duration), notes

    characteristic = float(protocol.characteristic_time())
    if protocol.is_infinite():
        if beats < 1:
            raise MmtProtocolError("beats must be at least 1.")
        total = characteristic * beats
        notes.append(
            f"the protocol repeats indefinitely, so it was cut to {beats} "
            f"beat(s) of {characteristic:g} = {total:g}. Pass a duration or a "
            f"beat count to change that."
        )
        return total, notes
    if characteristic <= 0:
        raise MmtProtocolError(
            "that protocol has no duration, so there is nothing to simulate."
        )
    return characteristic, notes


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
    model, protocol = _load(data, filename)
    if protocol is None or not protocol.events():
        raise MmtProtocolError(
            "that .mmt has no [[protocol]] events, so there is no protocol to "
            "convert. The model may simply be unpaced."
        )

    name = pace_variable(model)
    total, notes = _duration(protocol, beats, duration)
    return _as_shape(protocol, name, total, notes, pre_time, label)


def _as_shape(protocol, name, total, notes, pre_time, label):
    """One sub-experiment driving ``name`` with the .mmt's own events.

    The alternative -- slicing the run into a sub-experiment per constant
    stretch -- says the same thing, but says it in a form that cannot be read
    back: five durations and five levels do not announce that they are a 1 Hz
    stimulus, so the period cannot be edited, only recomputed. Declaring the
    events keeps the numbers the user wrote in the .mmt.
    """
    events = []
    for event in protocol.events():
        events.append(
            {
                "level": float(event.level()),
                "start": float(event.start()),
                "length": float(event.duration()),
                "period": float(event.period() or 0),
                "multiplier": int(event.multiplier() or 0),
            }
        )

    # Myokit ships examples whose stimulus has amplitude zero because the file is
    # about the model's own currents rather than about pacing -- dn-1985-if-gna
    # declares `0 10 0.5 1000 0`. Converting one gives a protocol_info that looks
    # like a stimulus and applies none.
    if all(e["level"] == 0 for e in events):
        raise MmtProtocolError(
            "that protocol's only stimulus has amplitude 0, so it never changes "
            "anything -- the model is effectively unpaced and there is no "
            "protocol worth converting."
        )
    if all(e["start"] >= total for e in events):
        raise MmtProtocolError(
            f"that protocol fires nothing within the {total:g} it would be run "
            f"over -- its first event starts later than that."
        )

    if label is None:
        period = events[0]["period"]
        label = f"pacing, period {period:g}" if period else "protocol"

    trace_name = name.replace("/", "_")
    return (
        {
            "pre_times": [float(pre_time)],
            "sim_times": [[total]],
            "params_to_change": {name: [[trace_name]]},
            "protocol_shapes": {trace_name: {"events": events}},
            "experiment_labels": [label],
            "experiment_colors": ["r"],
        },
        notes,
    )


def fill_protocol_info(obs_data: dict[str, Any], protocol_info: dict[str, Any]) -> dict[str, Any]:
    """Put ``protocol_info`` into an obs_data document, returning a new dict.

    An existing document's labels and colours are kept when they still fit the
    new schedule: those are the parts a user writes for themselves ("1 Hz
    pacing" reads better than "pacing, period 1000"), and re-deriving the timings
    is no reason to throw them away.
    """
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
