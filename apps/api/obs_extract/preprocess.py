"""Turn a recorded sweep into a clamp command trace the solver can follow.

A ``protocol_traces`` entry is a point table CA interpolates during a run. Handing
it raw instrument samples is wrong twice over: the recording carries measurement
noise the model would be asked to reproduce, and at 10-20 kHz over a second it is
tens of thousands of points per experiment, which bloats the obs_data file and the
solver's interpolation for no gain.

So: interpolate onto a uniform grid at the recording's own median spacing,
Savitzky-Golay smooth, resample to ``clamp_output_hz`` (1 kHz by default).

**The peak-preservation guard.** A voltage command is often an action-potential
waveform, whose whole character is a sharp peak. A smoothing window wide enough to
be useful on a current step flattens that peak by several millivolts -- and the
model is then clamped to a waveform that never reaches the voltage the cell did.
So for voltage commands the window is shrunk (x0.6 at a time) until the smoothed
extrema stay within ``voltage_peak_preserve_ratio`` of the raw ones, or the window
reaches a floor. Current commands are steps and need no such care, which is why
they get a window an order of magnitude wider.

Ported from ``SN_full/trace_signal_preprocess.py`` and the guard beside it in
``pre_process_data.py``. The constants that were module-level literals there are
config values here (see ``config.DEFAULT_PREPROCESS``), because the right window
depends on the recording rather than on the pipeline.
"""

from __future__ import annotations

from typing import Tuple

import numpy as np

from .errors import ObsExtractError

# scipy is imported inside the two functions that use it, not here.
#
# `main.py` imports obs_extract at module scope, so anything this package pulls
# in at import time is pulled in by the whole app -- and the backend unit CI tier
# deliberately installs a minimal set (fastapi, numpy, pandas, matplotlib) with
# no scipy, to prove the app's routes work without the simulation stack. A
# module-level scipy import here stops `import main` dead and takes every backend
# test with it. No other module in apps/api imports scipy at module level either.

#: How far the window shrinks each attempt, and how small it may get.
PEAK_GUARD_SHRINK = 0.6
PEAK_GUARD_MIN_WINDOW_S = 1e-7
#: Below this peak-to-peak span a trace is flat enough that the guard is moot.
PEAK_GUARD_FLAT_SPAN = 1.0


class SmoothDownsampleSignal:
    """
    Smooth with Savitzky-Golay on a uniform time base, then downsample to
    ``output_hz`` (default 1000 Hz) via linear interpolation.

    Parameters
    ----------
    output_hz
        Target sampling rate after downsampling [Hz].
    savgol_polyorder
        Polynomial order for Savitzky-Golay.
    savgol_window_seconds
        Target smoothing window duration [s], converted to an odd sample
        count from the interpolated sample rate; shrunk if segments are short.
    """

    def __init__(
        self,
        output_hz: float = 1000.0,
        savgol_polyorder: int = 3,
        savgol_window_seconds: float = 5e-3,
    ):
        self.output_hz = float(output_hz)
        self.savgol_polyorder = int(max(1, savgol_polyorder))
        self.savgol_window_seconds = float(max(1e-6, savgol_window_seconds))

        if self.output_hz <= 0:
            raise ValueError("output_hz must be positive")

    @staticmethod
    def _nearest_odd(n: int) -> int:
        n = int(max(5, n))
        return n if (n % 2 == 1) else n + 1

    def _uniform_dense_grid(
        self, t: np.ndarray, y: np.ndarray,
    ) -> Tuple[np.ndarray, np.ndarray]:
        """Interpolates ``y`` onto a uniform grid at median sample spacing."""
        from scipy.interpolate import interp1d  # noqa: PLC0415

        t = np.asarray(t, dtype=np.float64).ravel()
        y = np.asarray(y, dtype=np.float64).ravel()

        if t.size != y.size:
            raise ValueError("t and y must have the same length")
        if t.size < 2:
            raise ValueError("need at least two samples to preprocess")

        order = np.argsort(t)
        t = t[order]
        y = y[order]

        dup = np.concatenate([[False], np.abs(np.diff(t)) < 1e-18])
        t = t[~dup]
        y = y[~dup]

        if t.size < 2:
            raise ValueError("too few distinct time points after deduplication")

        dt_med = float(np.clip(np.median(np.diff(t)), 1e-9, np.inf))

        tn = np.arange(t[0], t[-1] + 0.5 * dt_med, dt_med)
        if tn.size < 2:
            tn = np.array([t[0], t[-1]], dtype=np.float64)

        yi = interp1d(
            t, y, kind="linear",
            bounds_error=False,
            fill_value=(float(y[0]), float(y[-1])),
        )(tn)
        return tn, yi.astype(np.float64)

    def smooth_on_uniform_times(
        self, t_uni: np.ndarray, y_uni: np.ndarray,
    ) -> np.ndarray:
        """Savitzky-Golay on uniform ``y_uni``."""
        from scipy.signal import savgol_filter  # noqa: PLC0415

        n = len(y_uni)
        if n < 5:
            return np.asarray(y_uni, dtype=np.float64)

        dt = float(np.median(np.diff(t_uni))) if len(t_uni) > 1 else 1e-9
        target_wins = max(
            self.savgol_polyorder + 2,
            int(round(self.savgol_window_seconds / dt)),
        )
        win = min(self._nearest_odd(target_wins), n)

        poly = min(self.savgol_polyorder, win - 1)
        if win <= poly:
            poly = max(1, win - 2)

        if win < 5 or poly < 1 or win > n:
            return np.asarray(y_uni, dtype=np.float64)

        return savgol_filter(
            y_uni, window_length=win, polyorder=poly, mode="nearest",
        ).astype(np.float64)

    def downsample_to_output_hz(
        self,
        t_start: float,
        t_end: float,
        t_src: np.ndarray,
        y_smooth: np.ndarray,
    ) -> Tuple[np.ndarray, np.ndarray]:
        """Linear interpolation onto spacing ``1/output_hz`` from ``t_start``."""
        from scipy.interpolate import interp1d  # noqa: PLC0415

        dt_out = 1.0 / self.output_hz

        duration = float(t_end - t_start)
        if duration <= 0:
            raise ValueError("non-positive segment duration")

        n_out = int(np.floor(duration * self.output_hz)) + 1
        t_out = t_start + np.arange(n_out, dtype=np.float64) * dt_out
        mask = t_out <= t_end + 1e-15
        t_out = t_out[mask]

        if t_out.size < 2:
            t_out = np.array([t_start, t_end], dtype=np.float64)

        yi = interp1d(
            t_src, y_smooth, kind="linear",
            bounds_error=False,
            fill_value=(float(y_smooth[0]), float(y_smooth[-1])),
        )(t_out)
        return t_out, yi.astype(np.float64)

    def run_with_intermediates(
        self, t: np.ndarray, y: np.ndarray,
    ) -> Tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
        """
        Same pipeline as ``run``, but returns the uniform interpolated series
        *before* Savitzky-Golay as ``(t_uni, y_pre_smooth)``, and the final
        downsampled output as ``(t_out, y_final)``.

        Raw input samples ``(t, y)`` are not returned here; callers that need
        them should pass the stimulus-window arrays separately for plotting.
        """
        t_input = np.asarray(t, dtype=np.float64).ravel()
        y_input = np.asarray(y, dtype=np.float64).ravel()
        if t_input.size < 2 or y_input.size < 2:
            return t_input, y_input, t_input, y_input

        t0 = float(np.min(t_input))
        t1 = float(np.max(t_input))

        t_uni, y_uni = self._uniform_dense_grid(t_input, y_input)
        y_sm = self.smooth_on_uniform_times(t_uni, y_uni)
        t_out, y_out = self.downsample_to_output_hz(t0, t1, t_uni, y_sm)
        return t_uni, y_uni, t_out, y_out

    def run(self, t: np.ndarray, y: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
        """
        Interpolate uniformly, smooth, then downsample to ``output_hz``.

        Returns uniform ``t_out`` from original minimum to maximum span.
        """
        _tu, _yu, t_out, y_out = self.run_with_intermediates(t, y)
        return t_out, y_out


def _peak_guard_passes(raw: np.ndarray, smoothed: np.ndarray, ratio: float) -> bool:
    """Whether smoothing kept the extrema that matter.

    Only extrema meaningfully away from zero are checked -- a trace whose maximum
    is 0.001 mV does not have a peak to preserve, and demanding a ratio of it
    would shrink the window to nothing chasing noise.
    """
    raw = np.asarray(raw, dtype=float)
    smoothed = np.asarray(smoothed, dtype=float)
    if raw.size == 0 or smoothed.size == 0:
        return True
    r_max, r_min = float(np.nanmax(raw)), float(np.nanmin(raw))
    span = r_max - r_min
    if span <= PEAK_GUARD_FLAT_SPAN:
        return True
    eps = max(5e-2 * max(span, 1.0), 1e-3)
    ok = True
    if r_max > eps:
        ok = ok and float(np.nanmax(smoothed)) >= ratio * r_max - 1e-9
    if r_min < -eps:
        ok = ok and float(np.nanmin(smoothed)) <= ratio * r_min + 1e-9
    return ok


def command_trace(
    t: np.ndarray,
    values: np.ndarray,
    *,
    kind: str,
    output_hz: float = 1000.0,
    window_seconds: float = 5e-3,
    peak_preserve_ratio: float = 0.95,
) -> tuple[np.ndarray, np.ndarray, list[str]]:
    """Smooth and resample one sweep into a clamp command.

    Returns ``(t_out, values_out, notes)``. ``notes`` records a guard that had to
    shrink the window, or one that hit the floor still short of the ratio -- the
    second is worth saying out loud, because it means the command the model
    follows does not quite reach the voltage the cell was held at.

    **A known characteristic, inherited deliberately.** Savitzky-Golay rings at a
    discontinuity, so a rectangular current step comes out with roughly 8%
    overshoot at its edges with the default 30 ms window. The peak guard is not
    applied to current commands, because that is what the CLI this replaces does
    and matching its numbers is worth more here than smoothing the corner --
    a step's amplitude is set by its plateau, which is unaffected. Narrow
    ``savgol_window_seconds.current`` if the edges matter for your protocol.
    """
    t = np.asarray(t, dtype=float).ravel()
    values = np.asarray(values, dtype=float).ravel()
    notes: list[str] = []
    if t.size < 2:
        return t, values, notes
    try:
        import scipy  # noqa: F401,PLC0415
    except ImportError as exc:
        # scipy is a declared core dependency, so this only happens on an
        # install thinner than the declaration. Say which package, rather than
        # letting a ModuleNotFoundError surface from inside the smoother.
        raise ObsExtractError(
            "building a clamp command trace needs scipy, which is not installed "
            "here.") from exc

    if kind != "voltage":
        pre = SmoothDownsampleSignal(output_hz=output_hz,
                                     savgol_window_seconds=window_seconds)
        t_out, y_out = pre.run(t, values)
        return t_out, y_out, notes

    window = float(window_seconds)
    shrunk = 0
    while True:
        pre = SmoothDownsampleSignal(output_hz=output_hz,
                                     savgol_window_seconds=window)
        _tu, _yu, t_out, y_out = pre.run_with_intermediates(t, values)
        if _peak_guard_passes(values, y_out, peak_preserve_ratio):
            if shrunk:
                notes.append(
                    f"voltage command smoothing narrowed {shrunk}x to "
                    f"{window:.3g} s to preserve the waveform's peak")
            return t_out, y_out, notes
        if window <= PEAK_GUARD_MIN_WINDOW_S:
            notes.append(
                f"voltage command: smoothing reached its minimum window "
                f"({window:.2g} s) and the peak is still below "
                f"{peak_preserve_ratio:.0%} of the recording's -- the model will "
                f"be clamped slightly short of the measured extreme")
            return t_out, y_out, notes
        window *= PEAK_GUARD_SHRINK
        shrunk += 1
