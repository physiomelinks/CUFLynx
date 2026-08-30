"""Where the stimulus is in a sweep, and how a time range becomes a fraction.

Nearly every observable is measured over part of a sweep -- the peak during the
step, the mean over the last fifth of it. Two things follow.

**The stimulus window has to be found.** A sweep is a baseline, a step, and a
return, and the operations are written against the step. It is detected from the
commanded channel: under current clamp the injected current, under voltage clamp
the commanded voltage.

**A range is stored twice.** The user types absolute seconds -- that is what they
can see on the plot. CA's operations take ``start_frac``/``end_frac``, fractions
*of the stimulus window*. Both are kept in the config: the seconds are the
authority and the fractions are derived, per dataset, from that dataset's own
detected window.

That last point is a real difference from the CLI, which converts once against
one example recording and stores only the fractions. Its own comment says the
range is "relative to the stimulus window", but a second recording whose step
starts 5 ms later gets the first recording's fractions applied to a different
window, and measures a slightly different part of the sweep. Re-deriving per
dataset is what makes the stored intent true.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .errors import ObsExtractError

#: Detection thresholds. The current one is in the recording's own units (pA in
#: this corpus); the voltage one is a deviation from the pre-stimulus baseline.
#: Both are config values -- these are the defaults the CLI uses.
DEFAULT_CURRENT_THRESHOLD = 10.0
DEFAULT_VOLTAGE_THRESHOLD = 5.0


@dataclass(frozen=True)
class StimWindow:
    """The stimulus portion of one sweep, as indices and as times."""

    start: int
    stop: int  # exclusive
    t_start: float
    t_stop: float
    #: False when nothing crossed the threshold and the whole sweep was used.
    detected: bool = True

    @property
    def duration(self) -> float:
        return float(self.t_stop - self.t_start)

    def slice(self, values: np.ndarray) -> np.ndarray:
        return np.asarray(values)[self.start:self.stop]


def detect_stim_window(
    t: np.ndarray,
    signal: np.ndarray,
    kind: str,
    *,
    current_threshold: float = DEFAULT_CURRENT_THRESHOLD,
    voltage_threshold: float = DEFAULT_VOLTAGE_THRESHOLD,
) -> StimWindow:
    """Find where the stimulus is applied in one sweep.

    ``kind`` is the *stimulus* kind: ``"current"`` means the cell was current
    clamped and ``signal`` is the recorded current, ``"voltage"`` means voltage
    clamped and ``signal`` is the recorded voltage command.

    Current is judged against zero -- an injected current is zero between steps.
    Voltage is judged against the sweep's own baseline, because a voltage clamp
    holds at some potential and the step is a deviation from it.

    When nothing crosses, the whole sweep is the window and ``detected`` is
    False. That is deliberately not an error: a gap-free recording or an
    unstimulated sweep is still something you can measure, and the caller can
    decide whether an undetected window matters. It is reported, though -- a
    fraction of "the stimulus window" means something quite different when the
    window is the entire sweep.
    """
    t = np.asarray(t, dtype=float)
    values = np.asarray(signal, dtype=float)
    n = values.size
    if n == 0 or t.size != n:
        raise ObsExtractError(
            f"cannot detect a stimulus window: {n} samples against {t.size} times")

    if kind == "current":
        above = np.abs(values) > float(current_threshold)
    elif kind == "voltage":
        # Baseline from the leading samples, before any step. 5% of the sweep,
        # floored at 50 samples so a short sweep still averages something.
        head = max(50, n // 20)
        baseline = float(np.mean(values[:head])) if n else 0.0
        above = np.abs(values - baseline) > float(voltage_threshold)
    else:
        raise ObsExtractError(
            f"unknown stimulus kind {kind!r}; expected 'current' or 'voltage'")

    idx = np.flatnonzero(above)
    if idx.size == 0:
        return StimWindow(0, n, float(t[0]), float(t[-1]), detected=False)
    start, stop = int(idx[0]), int(idx[-1]) + 1
    return StimWindow(start, stop, float(t[start]), float(t[min(stop, n - 1)]))


def seconds_to_fractions(
    window: StimWindow, start_s: float | None, end_s: float | None,
) -> tuple[float, float]:
    """Absolute seconds -> fractions of ``window``.

    Clamped to [0, 1] rather than refused: a range the user drew slightly wide of
    the step is a reasonable thing to have done, and the honest reading of it is
    "to the edge of the window". A start at or past the end is refused, because
    there is no reading of that which produces a measurement.
    """
    if start_s is None and end_s is None:
        return 0.0, 1.0
    span = window.t_stop - window.t_start
    if span <= 0:
        raise ObsExtractError(
            "the detected stimulus window has no duration, so a time range "
            "cannot be expressed as a fraction of it")
    lo = 0.0 if start_s is None else (float(start_s) - window.t_start) / span
    hi = 1.0 if end_s is None else (float(end_s) - window.t_start) / span
    lo, hi = max(0.0, min(1.0, lo)), max(0.0, min(1.0, hi))
    if not lo < hi:
        raise ObsExtractError(
            f"the range {start_s}..{end_s} s is empty within the stimulus window "
            f"{window.t_start:.4f}..{window.t_stop:.4f} s")
    return lo, hi


def fractions_to_seconds(
    window: StimWindow, start_frac: float, end_frac: float,
) -> tuple[float, float]:
    """The inverse, for showing a stored config back to the user."""
    span = window.t_stop - window.t_start
    return (window.t_start + float(start_frac) * span,
            window.t_start + float(end_frac) * span)


def resolve_range(
    window: StimWindow, spec: dict | None, kwargs: dict | None = None,
) -> tuple[float, float] | None:
    """The ``(start_frac, end_frac)`` a feature should be measured over.

    ``spec`` is the config's ``range`` block: ``{"basis", "start_s", "end_s"}``.
    Its seconds win when present, re-derived against *this* dataset's window.
    Falling back to ``kwargs``' stored fractions means a config written before
    the seconds were recorded still works, and a feature with neither simply
    measures the whole window.

    Returns None when the feature declares no range at all, so the caller can
    leave the operation's own defaults alone -- which matters, because several
    operations default to something other than 0..1 (``mean_in_range_minus_initial``
    is 0.8..1.0) and overwriting that would change what they compute.
    """
    kwargs = kwargs or {}
    if spec:
        basis = str(spec.get("basis") or "stimulus_window")
        start_s, end_s = spec.get("start_s"), spec.get("end_s")
        if start_s is not None or end_s is not None:
            if basis == "sweep":
                # Fractions of the whole sweep rather than of the step.
                whole = StimWindow(0, 0, window.t_start, window.t_stop, window.detected)
                return seconds_to_fractions(whole, start_s, end_s)
            return seconds_to_fractions(window, start_s, end_s)
    if "start_frac" in kwargs or "end_frac" in kwargs:
        return (float(kwargs.get("start_frac", 0.0)),
                float(kwargs.get("end_frac", 1.0)))
    return None
