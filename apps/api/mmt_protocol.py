"""Turn a Myokit ``[[protocol]]`` section into obs_data ``protocol_info``.

``myokit_import`` deliberately imports the ``[[model]]`` section and nothing
else: baking Myokit's stimulus into the exported CellML would give the model two
sources of pacing that disagree, since in CUFLynx the protocol comes from
obs_data. That leaves the protocol the user actually wrote sitting in the .mmt,
unused, to be re-entered by hand -- which is where transcription errors live.
This module reads it back out and writes it where CUFLynx expects it.

The two formats say the same thing in different shapes:

    Myokit   a list of events: (level, start, duration, period, multiplier),
             i.e. a stimulus waveform defined by when it fires.
    CA       sim_times[exp][sub] -- the *duration* of each sub-experiment -- with
             params_to_change[name][exp][sub] holding the value of each changed
             parameter over that sub-experiment.

So the conversion is: flatten the events into the piecewise-constant function of
time they describe, then emit one sub-experiment per constant segment. Myokit's
own ``log_for_interval`` does the flattening, so overlapping or oddly-phased
events resolve exactly as they would in a Myokit simulation rather than as this
module's re-reading of the rules.

A periodic Myokit protocol usually runs forever (``multiplier=0``), while a CA
experiment is a finite list of durations -- so an indefinite protocol needs a
number of beats, which is a choice and not a conversion. It defaults to 2: one
beat cannot show that a model returns to its diastolic state, and two can.
"""

from __future__ import annotations

import math
import tempfile
from pathlib import Path
from typing import Any

# Sub-experiments shorter than this are dropped rather than emitted. Myokit
# protocols are written in whole units of time, so a segment this short is
# floating-point debris from the event arithmetic, not something the user asked
# to simulate -- and CA turns each one into at least one solver step.
MIN_SEGMENT = 1e-9

# Enough to hold what a .mmt actually contains without inventing precision.
ROUND_TO = 9

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


def _segments(protocol, duration: float) -> list[tuple[float, float]]:
    """``(length, level)`` for each constant stretch of the protocol in [0, duration]."""
    # log_for_interval is the current name; create_log_for_interval is the same
    # method under the name older Myokit used, and warns on newer ones.
    log_for_interval = getattr(protocol, "log_for_interval", None) or protocol.create_log_for_interval
    log = log_for_interval(0, duration, for_drawing=False)
    times = list(log["time"])
    levels = list(log["pace"])

    segments: list[tuple[float, float]] = []
    for start, end, level in zip(times, times[1:], levels):
        length = round(end - start, ROUND_TO)
        if length <= MIN_SEGMENT:
            continue
        level = round(float(level), ROUND_TO)
        # Myokit emits a change point per event edge; two adjacent stretches at
        # the same level are one sub-experiment, not two.
        if segments and math.isclose(segments[-1][1], level, rel_tol=0, abs_tol=10**-ROUND_TO):
            segments[-1] = (round(segments[-1][0] + length, ROUND_TO), segments[-1][1])
        else:
            segments.append((length, level))
    return segments


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
    segments = _segments(protocol, total)
    if not segments:
        raise MmtProtocolError(
            "that protocol covers no time at all, so there are no sub-experiments "
            "to make from it."
        )
    if len(segments) == 1:
        # One segment means the level never changes. At zero that is a protocol
        # that does nothing -- dn-1985-if-gna.mmt declares `0 10 0.5 1000 0`,
        # a stimulus of amplitude zero, because the example is about the model's
        # own currents rather than about pacing. Converting it would produce a
        # protocol_info that looks like a stimulus and applies none.
        level = segments[0][1]
        if level == 0:
            raise MmtProtocolError(
                "that protocol's only stimulus has amplitude 0, so it never "
                "changes anything -- the model is effectively unpaced and there "
                "is no protocol worth converting."
            )
        notes.append(
            f"the protocol holds {name} at {level:g} for the whole run, so the "
            "experiment has a single sub-experiment."
        )

    if label is None:
        first = protocol.events()[0]
        period = float(first.period() or 0)
        label = f"pacing, period {period:g}" if period else "protocol"

    info: dict[str, Any] = {
        "pre_times": [float(pre_time)],
        "sim_times": [[length for length, _ in segments]],
        "params_to_change": {name: [[level for _, level in segments]]},
        "experiment_labels": [label],
        "experiment_colors": ["r"],
    }
    return info, notes


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
