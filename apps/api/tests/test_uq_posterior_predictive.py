"""The coverage and predictive data a finished UQ run offers the analysis tab.

A chain says what the parameters could be; it does not say whether the model at
those parameters reproduces what was measured. These are the two things the UI
needs to show that: a coverage summary, and per-observable intervals scaled so
observables on different scales can share one axis.
"""
import json
import os

import numpy as np
import pytest

import uq as uq_module


@pytest.fixture
def job(tmp_path):
    """A finished job whose run directory is the job directory."""
    manager = uq_module.UQManager()
    finished = uq_module.UQJob("job-1", str(tmp_path))
    finished.state = "done"
    manager._job = finished
    return manager, finished, tmp_path


def write_predictive(directory, n_samples=50, n_obs=4, offset=0.0, spread=1.0):
    rng = np.random.default_rng(0)
    truth = np.arange(n_obs, dtype=float) * 10 + 1
    std = np.full(n_obs, 2.0)
    preds = (truth[None, :] + offset * std[None, :]
             + rng.normal(size=(n_samples, n_obs)) * std[None, :] * spread)
    np.savez(os.path.join(str(directory), "posterior_predictive.npz"),
             thetas=np.zeros((n_samples, 2)), predictions=preds,
             ground_truth=truth, std=std,
             labels=np.array(["obs%d" % i for i in range(n_obs)], dtype=object))
    return preds, truth, std


def write_coverage(directory, predictive=0.81, data_interval=0.79):
    payload = {
        "coverage": {
            "num_observables": 4, "num_observables_skipped": 0,
            "levels": {"0.8": {"predictive_coverage": predictive,
                               "data_interval_coverage": data_interval,
                               "z": 1.2816}},
        },
        "num_samples": 50, "used_emulator": False,
        "samples_that_failed_to_simulate": 0,
    }
    with open(os.path.join(str(directory), "posterior_predictive_coverage.json"),
              "w") as handle:
        json.dump(payload, handle)
    return payload


@pytest.mark.unit
def test_an_unknown_job_is_not_found(job):
    manager, _, _ = job
    assert manager.posterior_predictive("no-such-job") is None


@pytest.mark.unit
def test_a_run_without_the_check_is_unavailable_not_an_error(job):
    """A UQ run that produced a posterior has not failed because it was not
    also scored."""
    manager, _, _ = job
    payload = manager.posterior_predictive("job-1")

    assert payload["available"] is False
    assert "error" not in payload


@pytest.mark.unit
def test_intervals_are_returned_in_units_of_the_measurement_std(job):
    """Scaled on the server, so two clients cannot draw two different figures
    from one run."""
    manager, _, tmp_path = job
    write_predictive(tmp_path, offset=0.0, spread=0.1)
    payload = manager.posterior_predictive("job-1")

    assert payload["available"] is True
    assert payload["labels"] == ["obs0", "obs1", "obs2", "obs3"]
    # Centred on the measurement and one std wide, so a well-fitted observable
    # sits near zero whatever its raw scale.
    assert max(abs(v) for v in payload["median"]) < 0.5
    for lo, mid, hi in zip(payload["lo"], payload["median"], payload["hi"]):
        assert lo <= mid <= hi


@pytest.mark.unit
def test_a_biased_fit_shows_up_as_a_shift(job):
    manager, _, tmp_path = job
    write_predictive(tmp_path, offset=3.0, spread=0.1)
    payload = manager.posterior_predictive("job-1")

    assert min(payload["median"]) > 2.0, payload["median"]


@pytest.mark.unit
def test_observables_that_never_simulated_are_left_out(job):
    manager, _, tmp_path = job
    preds, truth, std = write_predictive(tmp_path)
    preds[:, 1] = np.nan
    np.savez(os.path.join(str(tmp_path), "posterior_predictive.npz"),
             thetas=np.zeros((preds.shape[0], 2)), predictions=preds,
             ground_truth=truth, std=std,
             labels=np.array(["obs0", "obs1", "obs2", "obs3"], dtype=object))

    payload = manager.posterior_predictive("job-1")
    assert payload["labels"] == ["obs0", "obs2", "obs3"]


@pytest.mark.unit
def test_a_zero_std_does_not_divide_the_observable_away(job):
    manager, _, tmp_path = job
    preds, truth, std = write_predictive(tmp_path)
    std[0] = 0.0
    np.savez(os.path.join(str(tmp_path), "posterior_predictive.npz"),
             thetas=np.zeros((preds.shape[0], 2)), predictions=preds,
             ground_truth=truth, std=std,
             labels=np.array(["obs0", "obs1", "obs2", "obs3"], dtype=object))

    payload = manager.posterior_predictive("job-1")
    assert len(payload["labels"]) == 4
    assert all(np.isfinite(payload["median"]))


@pytest.mark.unit
def test_a_corrupt_file_is_reported_rather_than_raised(job):
    manager, _, tmp_path = job
    with open(os.path.join(str(tmp_path), "posterior_predictive.npz"), "w") as f:
        f.write("not an npz")

    payload = manager.posterior_predictive("job-1")
    assert payload["available"] is False
    assert payload["error"]


@pytest.mark.unit
def test_coverage_is_read_from_the_run_directory(job):
    """From the file, not the pipe: a run that was salvaged or reattached to has
    the file but not the pipe."""
    manager, finished, tmp_path = job
    expected = write_coverage(tmp_path)

    read = manager._read_coverage(str(tmp_path))
    assert read == expected
    assert read["coverage"]["levels"]["0.8"]["predictive_coverage"] == 0.81


@pytest.mark.unit
def test_missing_coverage_reads_as_none(job):
    manager, _, tmp_path = job
    assert manager._read_coverage(str(tmp_path)) is None


@pytest.mark.unit
def test_coverage_travels_on_the_status_payload(job):
    manager, finished, tmp_path = job
    finished.coverage = write_coverage(tmp_path)

    status = manager.status("job-1")
    assert status["coverage"]["coverage"]["levels"]["0.8"][
        "data_interval_coverage"] == 0.79
