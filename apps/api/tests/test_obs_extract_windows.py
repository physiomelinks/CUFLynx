"""Stimulus-window detection, and the seconds <-> fractions conversion.

The conversion is the part worth pinning. A user types absolute seconds; CA's
operations take fractions of the stimulus window. Storing only the fractions --
as the CLI does -- means a second recording whose step starts a few milliseconds
later has the first recording's fractions applied to a different window, and
measures a different part of the sweep than the one that was drawn.
"""

from __future__ import annotations

import numpy as np
import pytest

from obs_extract import ObsExtractError, detect_stim_window, resolve_range
from obs_extract.windows import fractions_to_seconds, seconds_to_fractions
from obs_extract_fixtures import step

pytestmark = pytest.mark.unit


def _sweep(n=1000, dt=1e-3, lo=250, hi=750, base=0.0, level=100.0):
    t = np.arange(n) * dt
    return t, step(n, base, level, lo=lo, hi=hi)


def test_current_window_is_where_the_current_is_not_zero():
    t, im = _sweep()
    window = detect_stim_window(t, im, "current")
    assert window.detected
    assert window.start == 250
    assert window.stop == 750
    assert window.t_start == pytest.approx(0.250)
    assert window.duration == pytest.approx(0.499, abs=2e-3)


def test_voltage_window_is_measured_from_the_sweeps_own_baseline():
    """A voltage clamp holds at some potential; the step is a deviation from it,
    not from zero -- so a holding level of -70 mV must not read as 'stimulated
    throughout'."""
    t, vm = _sweep(base=-70.0, level=-20.0)
    window = detect_stim_window(t, vm, "voltage")
    assert window.detected
    assert window.start == 250
    assert window.stop == 750


def test_a_sub_threshold_step_is_not_a_stimulus():
    t, im = _sweep(level=1.0)  # 1 pA, under the 10 pA default
    window = detect_stim_window(t, im, "current")
    assert not window.detected
    assert (window.start, window.stop) == (0, t.size)


def test_thresholds_are_configurable():
    t, im = _sweep(level=1.0)
    window = detect_stim_window(t, im, "current", current_threshold=0.5)
    assert window.detected


def test_an_undetected_window_is_the_whole_sweep_not_an_error():
    """A gap-free or unstimulated sweep is still measurable; the caller decides
    whether an undetected window matters."""
    t = np.arange(100) * 1e-3
    window = detect_stim_window(t, np.zeros(100), "current")
    assert not window.detected
    assert window.duration == pytest.approx(t[-1])


def test_an_unknown_stimulus_kind_is_refused():
    t, im = _sweep()
    with pytest.raises(ObsExtractError, match="unknown stimulus kind"):
        detect_stim_window(t, im, "pressure")


def test_mismatched_lengths_are_refused():
    with pytest.raises(ObsExtractError, match="cannot detect"):
        detect_stim_window(np.arange(10), np.zeros(5), "current")


# ---------------------------------------------------------------------------
def test_seconds_become_fractions_of_the_window_not_of_the_sweep():
    t, im = _sweep()
    window = detect_stim_window(t, im, "current")  # 0.250 .. 0.749 s
    lo, hi = seconds_to_fractions(window, 0.250, 0.500)
    assert lo == pytest.approx(0.0)
    assert hi == pytest.approx(0.5, abs=2e-3)


def test_the_same_seconds_give_different_fractions_for_a_shifted_window():
    """The reason seconds are stored and fractions derived per dataset.

    Two recordings of the same protocol whose steps start 100 ms apart: one
    stored fraction cannot be right for both.
    """
    t_a, im_a = _sweep(lo=250, hi=750)
    t_b, im_b = _sweep(lo=350, hi=850)
    a = detect_stim_window(t_a, im_a, "current")
    b = detect_stim_window(t_b, im_b, "current")

    frac_a = seconds_to_fractions(a, 0.400, 0.500)
    frac_b = seconds_to_fractions(b, 0.400, 0.500)
    assert frac_a != frac_b
    # And re-deriving puts both back on the same absolute seconds.
    assert fractions_to_seconds(a, *frac_a)[0] == pytest.approx(0.400, abs=2e-3)
    assert fractions_to_seconds(b, *frac_b)[0] == pytest.approx(0.400, abs=2e-3)


def test_a_range_outside_the_window_is_clamped_not_refused():
    """Drawing slightly wide of the step is a reasonable thing to have done."""
    t, im = _sweep()
    window = detect_stim_window(t, im, "current")
    lo, hi = seconds_to_fractions(window, 0.0, 10.0)
    assert (lo, hi) == (0.0, 1.0)


def test_an_empty_range_is_refused():
    t, im = _sweep()
    window = detect_stim_window(t, im, "current")
    with pytest.raises(ObsExtractError, match="empty"):
        seconds_to_fractions(window, 0.6, 0.3)


def test_no_range_means_the_whole_window():
    t, im = _sweep()
    window = detect_stim_window(t, im, "current")
    assert seconds_to_fractions(window, None, None) == (0.0, 1.0)


# ---------------------------------------------------------------------------
def test_resolve_range_prefers_the_seconds_the_user_typed():
    t, im = _sweep()
    window = detect_stim_window(t, im, "current")
    got = resolve_range(window, {"basis": "stimulus_window", "start_s": 0.250,
                                 "end_s": 0.500},
                        {"start_frac": 0.9, "end_frac": 1.0})
    assert got[0] == pytest.approx(0.0)
    assert got[1] == pytest.approx(0.5, abs=2e-3)


def test_resolve_range_falls_back_to_stored_fractions():
    """A config written before the seconds were recorded still works."""
    t, im = _sweep()
    window = detect_stim_window(t, im, "current")
    assert resolve_range(window, None, {"start_frac": 0.2, "end_frac": 0.8}) == (0.2, 0.8)


def test_resolve_range_returns_none_when_the_feature_declares_no_range():
    """So the operation's own defaults are left alone.

    Several operations default to something other than 0..1 --
    ``mean_in_range_minus_initial`` is 0.8..1.0 -- and overwriting that would
    change what the operation computes.
    """
    t, im = _sweep()
    window = detect_stim_window(t, im, "current")
    assert resolve_range(window, None, {}) is None
    assert resolve_range(window, {"basis": "stimulus_window"}, {}) is None


def test_a_sweep_basis_measures_against_the_whole_sweep():
    t, im = _sweep()
    window = detect_stim_window(t, im, "current")
    whole = resolve_range(window, {"basis": "sweep", "start_s": 0.0, "end_s": 0.999}, {})
    assert whole[0] == pytest.approx(0.0)
    assert whole[1] == pytest.approx(1.0, abs=1e-2)
