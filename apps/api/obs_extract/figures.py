"""Thumbnails of a recording, for the dialog and the report.

Choosing which of several hundred recordings to include is a visual judgement --
this sweep looks like the others, that one is an artefact -- so the dialog needs
a picture per dataset and the report needs the same picture beside the decision
that was made about it.

**No ``pyplot``, anywhere in this module.** ``pyplot`` keeps global figure state
and is not thread-safe, and extraction runs on a worker thread while the user is
still clicking Preview on other rows. Building ``Figure`` and ``FigureCanvasAgg``
directly has no global state to race over, and needs no ``plt.close`` discipline
to avoid leaking figures. The cost is that ``solver_plots.save_figures`` cannot
be reused -- it is a pyplot path -- so only its *path arithmetic* is borrowed and
the writing is done here.

matplotlib itself is an optional import, guarded the way ``solver_plots.force_agg``
guards it: no matplotlib means no thumbnails, an ``[info]`` line, and an
extraction that still succeeds. A picture is a convenience; the numbers are not.
"""

from __future__ import annotations

import os

import numpy as np

#: Enough to judge a sweep at a glance without making a 488-row list heavy.
FIGURE_SIZE = (4.0, 2.4)
DPI = 96
#: Above this many samples a trace is decimated before drawing. Beyond a few
#: thousand points nothing is visible that a decimated line does not show, and
#: the PNG stops getting better while it keeps getting slower.
MAX_POINTS = 4000


def available() -> bool:
    """Whether thumbnails can be drawn at all in this install."""
    try:
        import matplotlib  # noqa: F401,PLC0415

        return True
    except Exception:  # noqa: BLE001 - a broken matplotlib is the same as none
        return False


def _decimate(x: np.ndarray, y: np.ndarray):
    n = int(np.asarray(x).size)
    if n <= MAX_POINTS:
        return x, y
    stride = max(1, n // MAX_POINTS)
    return x[::stride], y[::stride]


def sweep_figure(
    recording,
    sweep_indices,
    *,
    title: str = "",
    window=None,
    ranges=(),
    time_window=None,
):
    """A two-panel figure of one recording, or None when matplotlib is absent.

    Both recorded channels are drawn, one panel each, sharing an x-axis, with the
    detected stimulus window shaded and each feature's measurement range marked.
    That is the picture a selection decision is actually made from: whether the
    sweep is typical, and whether the range lands where it was meant to.
    """
    try:
        from matplotlib.backends.backend_agg import FigureCanvasAgg  # noqa: PLC0415
        from matplotlib.figure import Figure  # noqa: PLC0415
    except Exception:  # noqa: BLE001
        return None

    channels = [c for c in recording.channels]
    n_panels = max(1, min(len(channels), 2))
    fig = Figure(figsize=FIGURE_SIZE, dpi=DPI)
    FigureCanvasAgg(fig)
    axes = fig.subplots(n_panels, 1, sharex=True, squeeze=False)[:, 0]

    for panel, channel in enumerate(channels[:n_panels]):
        ax = axes[panel]
        for sweep_index in sweep_indices:
            try:
                t, signals = recording.sweep(sweep_index)
            except Exception:  # noqa: BLE001 - a bad sweep must not kill the figure
                continue
            values = signals.get(channel.name)
            if values is None:
                continue
            ax.plot(*_decimate(np.asarray(t), np.asarray(values)),
                    linewidth=0.6, alpha=0.85)
        label = channel.name + (f" {channel.unit}" if channel.unit else "")
        ax.set_ylabel(label, fontsize=6)
        ax.tick_params(labelsize=5)
        for spine in ("top", "right"):
            ax.spines[spine].set_visible(False)

        if window is not None and getattr(window, "detected", False):
            ax.axvspan(window.t_start, window.t_stop, color="0.85", zorder=0)
        for start, stop in ranges or ():
            ax.axvline(start, color="tab:red", linestyle="--", linewidth=0.6)
            ax.axvline(stop, color="tab:blue", linestyle="--", linewidth=0.6)
        if time_window:
            lo, hi = time_window
            if lo is not None or hi is not None:
                ax.set_xlim(lo, hi)

    axes[-1].set_xlabel("time (s)", fontsize=6)
    if title:
        fig.suptitle(title, fontsize=7)
    fig.tight_layout()
    return fig


def save(fig, path: str) -> str | None:
    """Write a figure to ``path``. Returns the path, or None if there is none."""
    if fig is None:
        return None
    os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
    fig.savefig(path, dpi=DPI, bbox_inches="tight")
    return path
