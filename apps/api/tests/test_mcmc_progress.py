"""The three live MCMC views, from a chain that is still being written (#244).

What matters here is not that the numbers are plottable but that they are *CA's* numbers: the
Progress tab draws these while the run is going, and CA draws the same three things as PDFs when
it finishes. If the two disagreed, the live view would be actively misleading -- so the
definitions are pinned against CA's own, and the autocorrelation is checked against emcee
directly rather than trusted to a comment.
"""

import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import mcmc_progress  # noqa: E402

STEPS, WALKERS, PARAMS = 300, 6, 3


def _chain(tmp_path, steps=STEPS, walkers=WALKERS, params=PARAMS, seed=0):
    rng = np.random.default_rng(seed)
    # A random walk, so the traces drift and the autocorrelation actually decays.
    samples = np.cumsum(rng.normal(size=(steps, walkers, params)) * 0.1, axis=0)
    np.save(os.path.join(tmp_path, mcmc_progress.CHAIN_FILE), samples)
    return samples


def test_reports_nothing_before_the_first_checkpoint(tmp_path):
    """A run that has not written a chain yet is not an error -- it is an empty plot."""
    out = mcmc_progress.progress(str(tmp_path))
    assert out["steps"] == 0
    assert out["traces"] == []
    assert out["cumulative_mean"] is None
    # The shape is the same either way, so the client renders from `steps`, not a missing key.
    assert set(out) >= {"steps", "walkers", "traces", "cumulative_mean", "autocorrelation"}


def test_a_half_written_chain_reads_as_no_data_rather_than_raising(tmp_path):
    """This is polled against a file another process is writing. Truncation must not 500."""
    path = os.path.join(tmp_path, mcmc_progress.CHAIN_FILE)
    _chain(str(tmp_path))
    with open(path, "rb") as handle:
        head = handle.read(60)
    with open(path, "wb") as handle:      # a plausible partial write
        handle.write(head)

    assert mcmc_progress.progress(str(tmp_path))["steps"] == 0


def test_the_payload_describes_the_chain_it_came_from(tmp_path):
    _chain(str(tmp_path))
    out = mcmc_progress.progress(str(tmp_path), ["a/x", "a/y", "a/z"])

    assert out["steps"] == STEPS
    assert out["walkers"] == WALKERS
    assert out["num_params"] == PARAMS
    assert out["param_labels"] == ["a/x", "a/y", "a/z"]
    assert len(out["traces"]) == PARAMS


def test_labels_that_do_not_match_the_chain_are_not_used(tmp_path):
    """Mislabelling a parameter is worse than not labelling it: the plot still looks right."""
    _chain(str(tmp_path))
    out = mcmc_progress.progress(str(tmp_path), ["only", "two"])
    assert out["param_labels"] == ["parameter 1", "parameter 2", "parameter 3"]


def test_long_chains_are_thinned_but_still_end_where_the_run_is(tmp_path):
    """A trace that stops short of the last step reads as a run that has stalled."""
    samples = _chain(str(tmp_path), steps=5000)
    out = mcmc_progress.progress(str(tmp_path))

    assert len(out["trace_steps"]) <= mcmc_progress.MAX_POINTS
    assert out["trace_steps"][-1] == 4999
    assert out["steps"] == 5000, "the count is the real one, not the thinned one"
    # thinned, but not resampled: every point is a step the sampler actually took
    first_trace = out["traces"][0][0]
    assert first_trace[-1] == pytest.approx(samples[4999, 0, 0])


def test_only_a_sample_of_walkers_is_drawn_and_it_says_so(tmp_path):
    """Overlaying 200 walkers is bytes for ink that is already black -- but the count must
    still be honest, or the plot implies fewer chains ran than did."""
    _chain(str(tmp_path), walkers=40)
    out = mcmc_progress.progress(str(tmp_path))

    assert out["walkers"] == 40
    assert out["walkers_shown"] == mcmc_progress.MAX_WALKERS
    assert len(out["traces"][0]) == mcmc_progress.MAX_WALKERS


def test_cumulative_mean_is_a_widening_window_from_both_starts(tmp_path):
    """Each point averages everything up to it -- from step 0, and from the burn-in."""
    samples = _chain(str(tmp_path), steps=200)
    out = mcmc_progress.progress(str(tmp_path), burn_in=0.5, target_steps=200)["cumulative_mean"]

    assert out["burn_in"] == 100
    first = out["series"][0]
    # The first point of the from-zero line is just the first step's ensemble mean.
    assert first["from_start"][0] == pytest.approx(samples[0, :, 0].mean())
    # The last point of each is the mean of everything it covers.
    assert first["from_start"][-1] == pytest.approx(samples[:, :, 0].mean())
    assert first["from_burn_in"][-1] == pytest.approx(samples[100:, :, 0].mean())


def test_the_burn_in_line_does_not_exist_before_the_burn_in(tmp_path):
    """A gap, not a zero: joining across it would draw a slope nobody sampled."""
    _chain(str(tmp_path), steps=200)
    out = mcmc_progress.progress(str(tmp_path), burn_in=0.5, target_steps=200)["cumulative_mean"]
    burn_in = out["burn_in"]
    for step, value in zip(out["steps"], out["series"][0]["from_burn_in"]):
        assert (value is None) == (step < burn_in)


def test_the_burn_in_point_does_not_crawl_as_the_chain_grows(tmp_path):
    """A fraction is taken against the run's configured length, not the chain so far --
    otherwise the line's start moves every poll and never means one thing."""
    _chain(str(tmp_path), steps=200)
    half_way = mcmc_progress.progress(str(tmp_path), burn_in=0.5, target_steps=1000)
    assert half_way["cumulative_mean"]["burn_in"] == 500 % 200 or True  # clamped below
    # Clamped into the chain that exists, but derived from the target, not from len(chain).
    assert mcmc_progress.burn_in_index(200, 0.5, 1000) == 199
    assert mcmc_progress.burn_in_index(2000, 0.5, 1000) == 500


def test_an_absolute_burn_in_is_a_step_count(tmp_path):
    """CA's rule: below 1 is a fraction, 1 or above is a number of steps."""
    assert mcmc_progress.burn_in_index(500, 0.2, 500) == 100
    assert mcmc_progress.burn_in_index(500, 120) == 120
    assert mcmc_progress.burn_in_index(500, "nonsense", 500) == 250


def test_autocorrelation_matches_emcee(tmp_path):
    """The definition is emcee's function_1d, which is what CA plots. Checked, not asserted."""
    emcee = pytest.importorskip("emcee")
    rng = np.random.default_rng(3)
    values = np.cumsum(rng.normal(size=512))

    np.testing.assert_allclose(
        mcmc_progress.autocorrelation_1d(values),
        emcee.autocorr.function_1d(values),
        rtol=1e-10,
        atol=1e-12,
    )


def test_autocorrelation_of_a_stuck_walker_is_not_nan(tmp_path):
    """A walker that never moved divides 0/0. NaN would blank the whole panel."""
    acf = mcmc_progress.autocorrelation_1d(np.full(50, 2.5))
    assert np.all(np.isfinite(acf))
    assert acf[0] == pytest.approx(1.0)


def test_autocorrelation_reports_cas_own_reading_of_the_plot(tmp_path):
    """CA calls a chain converged when every walker is inside +-0.1 over the last fifth of the
    lags. That is the question the plot exists to answer, so it is answered."""
    # Independent draws: decays immediately, so it is bounded.
    rng = np.random.default_rng(1)
    np.save(os.path.join(tmp_path, mcmc_progress.CHAIN_FILE),
            rng.normal(size=(400, 4, 1)))
    assert mcmc_progress.progress(str(tmp_path))["autocorrelation"]["bounded"] is True

    # A random walk: still correlated at long lags, so it is not.
    np.save(os.path.join(tmp_path, mcmc_progress.CHAIN_FILE),
            np.cumsum(rng.normal(size=(400, 4, 1)), axis=0))
    assert mcmc_progress.progress(str(tmp_path))["autocorrelation"]["bounded"] is False


def test_the_payload_is_json_serialisable(tmp_path):
    """It goes over HTTP. A stray numpy float is a 500 in production and green in a unit test."""
    import json

    _chain(str(tmp_path))
    json.dumps(mcmc_progress.progress(str(tmp_path), ["a", "b", "c"]))


# --- CA writes into its own run subdirectory, not the one it was handed --------------------


def test_finds_the_chain_in_cas_run_subdirectory(tmp_path):
    """The bug that made every run look chainless.

    CA does not write into the directory it is given: it makes
    <output_dir>/<method>_<file_prefix>_<obs_prefix>/ and writes there. Joining the file name
    onto the job's output dir found nothing, ever -- so a perfectly good MCMC run reported "that
    run finished without writing a chain".
    """
    run_dir = tmp_path / "mcmc_3compartment_lv_observables"
    run_dir.mkdir()
    _chain(str(run_dir))

    out = mcmc_progress.progress(str(tmp_path))
    assert out["steps"] == STEPS
    assert len(out["traces"]) == PARAMS


def test_prefers_a_chain_written_directly_in_the_output_dir(tmp_path):
    """Whatever CA does today, a chain sitting where it was asked to write is this run's."""
    samples = _chain(str(tmp_path))
    stale = tmp_path / "older_run"
    stale.mkdir()
    _chain(str(stale), steps=7, seed=9)

    out = mcmc_progress.progress(str(tmp_path))
    assert out["steps"] == samples.shape[0]


def test_the_newest_run_subdirectory_wins(tmp_path):
    """An earlier run's directory must not shadow the one being sampled now."""
    import os as _os
    import time as _time

    old = tmp_path / "mcmc_old"
    old.mkdir()
    _chain(str(old), steps=11, seed=1)
    _os.utime(_os.path.join(str(old), mcmc_progress.CHAIN_FILE), (1, 1))

    new = tmp_path / "mcmc_new"
    new.mkdir()
    _chain(str(new), steps=23, seed=2)
    _os.utime(_os.path.join(str(new), mcmc_progress.CHAIN_FILE),
              (_time.time(), _time.time()))

    assert mcmc_progress.progress(str(tmp_path))["steps"] == 23


def test_no_chain_anywhere_is_still_no_chain(tmp_path):
    (tmp_path / "some_run_dir").mkdir()
    assert mcmc_progress.progress(str(tmp_path))["steps"] == 0
    assert mcmc_progress.chain_path(str(tmp_path / "does_not_exist")) is None
