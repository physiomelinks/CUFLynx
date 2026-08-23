"""Reading a run this app did not produce (#255, #256).

The panels are filled by job polls, so a run made by ``cuflynx-param-id``, by a
generated ``run_pipeline.py``, or by this app yesterday is invisible even though
every file is sitting there. These cover the three things that made such a
directory unreadable rather than merely unread.
"""
import csv
import json
import os

import numpy as np
import pytest

import ca_run_history
import load_outputs


def write_run(directory, name, *, chain=True, best=True, when=None):
    run = os.path.join(str(directory), name)
    os.makedirs(run, exist_ok=True)
    if best:
        np.save(os.path.join(run, "best_param_vals.npy"), np.array([1.0, 2.0, 3.0]))
        np.save(os.path.join(run, "best_cost.npy"), np.array(0.5))
    if chain:
        rng = np.random.default_rng(0)
        block = rng.normal(size=(40, 4, 3))
        block[:20] += 100.0          # a burn-in that is obvious if it survives
        np.save(os.path.join(run, "mcmc_chain.npy"), block)
    with open(os.path.join(run, "param_names.csv"), "w", newline="") as handle:
        csv.writer(handle).writerows([["a/x"], ["a/y"], ["a/z"]])
    if when is not None:
        for entry in os.scandir(run):
            os.utime(entry.path, (when, when))
    return run


def write_coverage(run, predictive=0.42):
    with open(os.path.join(run, load_outputs.COVERAGE_FILE), "w") as handle:
        json.dump({"coverage": {"num_observables": 5, "levels": {
            "0.8": {"predictive_coverage": predictive,
                    "sample_interval_coverage": 0.3, "z": 1.2816}}},
            "used_emulator": False, "num_samples": 100,
            "samples_that_failed_to_simulate": 0}, handle)


# --- the posterior of a run we did not produce --------------------------------

@pytest.mark.unit
def test_the_chain_is_read_when_our_own_samples_file_is_absent(tmp_path):
    """uq_posterior_samples.npy is written by our runner. circulatory_autogen
    writes the chain instead, so without a fallback the UQ panel was empty for
    every run made outside the app."""
    write_run(tmp_path, "genetic_algorithm_demo_obs_data")

    params = ca_run_history.uq_distributions(str(tmp_path))

    assert params is not None
    assert [p["qname"] for p in params] == ["a/x", "a/y", "a/z"]
    # The +100 burn-in is dropped, so the summary describes the posterior.
    assert abs(params[0]["mean"]) < 10


@pytest.mark.unit
def test_a_directory_with_no_chain_and_no_samples_reads_as_no_uq(tmp_path):
    write_run(tmp_path, "genetic_algorithm_demo_obs_data", chain=False)
    assert ca_run_history.uq_distributions(str(tmp_path)) is None


@pytest.mark.unit
def test_parameters_are_named_even_when_the_names_file_is_short(tmp_path):
    run = write_run(tmp_path, "genetic_algorithm_demo_obs_data")
    with open(os.path.join(run, "param_names.csv"), "w", newline="") as handle:
        csv.writer(handle).writerows([["a/x"]])

    params = ca_run_history.uq_distributions(str(tmp_path))
    assert [p["qname"] for p in params] == ["a/x", "parameter 2", "parameter 3"]


# --- several runs in one directory --------------------------------------------

@pytest.mark.unit
def test_every_run_in_the_directory_is_listed_newest_first(tmp_path):
    """A study fitted to three datasets writes three sibling run directories."""
    write_run(tmp_path, "genetic_algorithm_demo_a_obs_data", when=1_000_000)
    write_run(tmp_path, "genetic_algorithm_demo_b_obs_data", when=3_000_000)
    write_run(tmp_path, "genetic_algorithm_demo_c_obs_data", when=2_000_000)

    runs = load_outputs.list_run_dirs(str(tmp_path))

    assert [run["name"] for run in runs] == [
        "genetic_algorithm_demo_b_obs_data",
        "genetic_algorithm_demo_c_obs_data",
        "genetic_algorithm_demo_a_obs_data",
    ]


@pytest.mark.unit
def test_an_explicitly_chosen_run_is_the_one_read(tmp_path):
    """Loading the newest silently is how a panel ends up describing a different
    dataset than the user thinks they are looking at."""
    write_run(tmp_path, "run_a", when=1_000_000)
    newest = write_run(tmp_path, "run_b", when=3_000_000)
    chosen = os.path.join(str(tmp_path), "run_a")
    write_coverage(chosen, predictive=0.11)
    write_coverage(newest, predictive=0.99)

    result = load_outputs.load_outputs(str(tmp_path), run_dir=chosen)

    assert result["run_dir"] == chosen
    levels = result["uq"]["coverage"]["coverage"]["levels"]["0.8"]
    assert levels["predictive_coverage"] == 0.11


@pytest.mark.unit
def test_the_posterior_comes_from_the_chosen_run_too(tmp_path):
    """The coverage and the posterior have to describe the same run."""
    a = write_run(tmp_path, "run_a", when=1_000_000)
    write_run(tmp_path, "run_b", when=3_000_000)
    # Make run_a's chain unmistakable.
    np.save(os.path.join(a, "mcmc_chain.npy"), np.full((10, 2, 3), 7.0))

    result = load_outputs.load_outputs(str(tmp_path), run_dir=a)
    assert result["uq"]["params"][0]["mean"] == pytest.approx(7.0)


# --- tolerance ----------------------------------------------------------------

@pytest.mark.unit
def test_a_missing_directory_is_reported_not_raised(tmp_path):
    result = load_outputs.load_outputs(str(tmp_path / "nope"))
    assert result["found"] == []
    assert "no such directory" in result["error"]


@pytest.mark.unit
def test_a_half_full_directory_loads_what_it_has(tmp_path):
    """A folder with a calibration and no UQ is an ordinary folder, not an error."""
    write_run(tmp_path, "genetic_algorithm_demo_obs_data", chain=False)

    result = load_outputs.load_outputs(str(tmp_path))

    assert "calibration" in result["found"]
    assert "uq" not in result["found"]
    assert result["uq"]["params"] is None


@pytest.mark.unit
def test_a_broken_artefact_is_named_rather_than_losing_the_rest(tmp_path):
    run = write_run(tmp_path, "genetic_algorithm_demo_obs_data")
    with open(os.path.join(run, load_outputs.COVERAGE_FILE), "w") as handle:
        handle.write("{ not json")

    result = load_outputs.load_outputs(str(tmp_path))

    assert "uq" in result["found"]          # the posterior still loaded
    assert any("coverage" in item for item in result["missing"])


@pytest.mark.unit
def test_the_predictive_artefacts_are_reported_when_present(tmp_path):
    run = write_run(tmp_path, "genetic_algorithm_demo_obs_data")
    np.savez(os.path.join(run, load_outputs.PREDICTIVE_FILE), predictions=np.zeros((2, 2)))
    np.savez(os.path.join(run, load_outputs.SERIES_FILE), **{"y|0|a": np.zeros((2, 3))})

    result = load_outputs.load_outputs(str(tmp_path))
    assert result["uq"]["has_posterior_predictive"] is True
    assert result["uq"]["has_sample_traces"] is True


# --- finding an emulator that is not where the convention says --------------------

class TestFindingTheEmulator:
    """``emulator_dir`` is a setting, not only a convention.

    A study that trains one emulator and reuses it across several obs_data has to
    set it: the conventional name embeds the obs file, so the runs would otherwise
    each look in a different directory and only the first would find a bundle.
    Reading such a run means finding the bundle that is there, not the one the
    convention predicts.
    """

    def _bundle(self, path, mtime=None):
        os.makedirs(path, exist_ok=True)
        with open(os.path.join(path, "emulator_metadata.json"), "w") as handle:
            json.dump({"feature_r2": [0.9]}, handle)
        with open(os.path.join(path, "training_data.npz"), "wb") as handle:
            handle.write(b"not really an npz")
        if mtime is not None:
            os.utime(os.path.join(path, "emulator_metadata.json"), (mtime, mtime))
        return path

    def test_the_conventional_place_is_still_preferred(self, tmp_path):
        out = str(tmp_path)
        wanted = self._bundle(os.path.join(out, "emulators", "3compartment_obs"))
        self._bundle(os.path.join(out, "emulators", "something_else"))
        assert ca_run_history.find_emulator_dir(out, "3compartment", "obs.json") == wanted

    def test_a_bundle_under_another_name_is_found(self, tmp_path):
        """The case that made this necessary: emulator_dir set to a shared path."""
        out = str(tmp_path)
        wanted = self._bundle(os.path.join(out, "emulators", "SN_full_joint"))
        assert ca_run_history.find_emulator_dir(out, "SN_full", "cpvt_obs_data.json") == wanted

    def test_a_directory_that_only_half_holds_a_bundle_is_not_offered(self, tmp_path):
        """CA's trainer needs both files; metadata alone loads and then refuses."""
        out = str(tmp_path)
        half = os.path.join(out, "emulators", "SN_full_joint")
        os.makedirs(half)
        with open(os.path.join(half, "emulator_metadata.json"), "w") as handle:
            json.dump({}, handle)
        assert ca_run_history.find_emulator_dir(out, "SN_full", None) is None

    def test_the_model_prefix_breaks_a_tie(self, tmp_path):
        out = str(tmp_path)
        self._bundle(os.path.join(out, "emulators", "other_study"), mtime=2_000_000_000)
        wanted = self._bundle(os.path.join(out, "emulators", "SN_full_joint"),
                              mtime=1_000_000_000)
        assert ca_run_history.find_emulator_dir(out, "SN_full", None) == wanted

    def test_the_newest_wins_among_equals(self, tmp_path):
        out = str(tmp_path)
        self._bundle(os.path.join(out, "emulators", "a_study"), mtime=1_000_000_000)
        wanted = self._bundle(os.path.join(out, "emulators", "b_study"),
                              mtime=2_000_000_000)
        assert ca_run_history.find_emulator_dir(out, None, None) == wanted

    def test_no_emulators_directory_is_not_an_error(self, tmp_path):
        assert ca_run_history.find_emulator_dir(str(tmp_path), "SN_full", None) is None

    def test_a_directory_that_does_not_exist_is_not_an_error(self):
        assert ca_run_history.find_emulator_dir("/no/such/place", "SN_full", None) is None

    def test_the_loader_reports_the_metadata_it_found_off_convention(self, tmp_path):
        out = str(tmp_path)
        self._bundle(os.path.join(out, "emulators", "SN_full_joint"))
        loaded = load_outputs.load_outputs(out, file_prefix="SN_full")
        assert loaded["emulator"]["dir"].endswith("SN_full_joint")
        assert loaded["emulator"]["metadata"]["feature_r2"] == [0.9]
        assert "emulator" in loaded["found"]
