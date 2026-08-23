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


# --- the newest run is not necessarily the run you are looking for -------------------

@pytest.mark.unit
def test_a_newer_calibration_does_not_hide_a_uq_in_the_same_directory(tmp_path):
    """A directory whose newest run is a calibration still reports its UQ.

    The chosen run is the newest of *any* kind. Reading the posterior only from that run
    meant a folder holding a finished calibration and a finished UQ reported "no UQ" --
    with `uq_posterior_samples.npy` sitting one level up, in plain sight. Observed on a
    real outputs directory whose newest run was a GA calibration.
    """
    write_run(tmp_path, "mcmc_run", chain=True, best=False, when=1_000_000)
    write_run(tmp_path, "genetic_algorithm_run", chain=False, best=True, when=3_000_000)

    result = load_outputs.load_outputs(str(tmp_path))

    assert result["run_dir"].endswith("genetic_algorithm_run"), "the newest run is the calibration"
    assert "uq" in result["found"], result["found"]
    assert result["uq"]["params"], "the posterior is in the directory and must be reported"


@pytest.mark.unit
def test_an_explicitly_chosen_run_with_a_chain_is_still_the_one_read(tmp_path):
    """The fallback must not override an explicit choice that has its own answer."""
    a = write_run(tmp_path, "run_a", when=1_000_000)
    write_run(tmp_path, "run_b", when=3_000_000)
    np.save(os.path.join(a, "mcmc_chain.npy"), np.full((10, 2, 3), 7.0))

    result = load_outputs.load_outputs(str(tmp_path), run_dir=a)
    assert result["uq"]["params"][0]["mean"] == pytest.approx(7.0)


# --- the calibrated model -------------------------------------------------------------

@pytest.mark.unit
def test_the_calibrated_model_is_found_without_being_told_the_prefix(tmp_path):
    """Opening a directory before any model is loaded is the whole point of the feature.

    The lookup is ``file_prefix``-driven and the frontend passes the *loaded model's* name,
    which is empty in exactly that case -- so a directory with a calibrated model at its top
    level reported none.
    """
    write_run(tmp_path, "genetic_algorithm_run", chain=False, best=True)
    model = tmp_path / "CardiovascularSystem_calibrated.cellml"
    model.write_text("<model/>", encoding="utf-8")

    result = load_outputs.load_outputs(str(tmp_path))
    assert result["calibration"]["calibrated_model"] == str(model)


@pytest.mark.unit
def test_a_named_prefix_still_wins_over_the_fallback(tmp_path):
    write_run(tmp_path, "genetic_algorithm_run", chain=False, best=True)
    (tmp_path / "wanted_calibrated.cellml").write_text("<model/>", encoding="utf-8")

    result = load_outputs.load_outputs(str(tmp_path), file_prefix="wanted")
    assert result["calibration"]["calibrated_model"].endswith("wanted_calibrated.cellml")


@pytest.mark.unit
def test_two_calibrated_models_are_reported_as_absent_rather_than_guessed(tmp_path):
    """Several studies sharing one directory is ordinary. Picking one of their models
    would attach a model to results it may not belong to, which is worse than saying
    nothing -- the numbers would look right."""
    write_run(tmp_path, "genetic_algorithm_run", chain=False, best=True)
    (tmp_path / "one_calibrated.cellml").write_text("<model/>", encoding="utf-8")
    (tmp_path / "two_calibrated.cellml").write_text("<model/>", encoding="utf-8")

    result = load_outputs.load_outputs(str(tmp_path))
    assert result["calibration"]["calibrated_model"] is None


# --- the emulator the panel found must be the one predictions use ---------------------

@pytest.mark.unit
def test_the_predict_route_finds_the_same_emulator_the_loader_does(tmp_path, monkeypatch):
    """The reported case: results load, then the panel says there is no emulator.

    ``_emulator_dir_for`` asked the *convention* -- where CA would put an emulator for this
    model name and obs file -- while the loader searched for where one actually is. For a
    directory this app did not produce those differ, because ``emulator_settings.emulator_dir``
    is a setting and the conventional name embeds the obs file. So the Analysis panel listed a
    trained emulator while ``/api/emulator/predict`` answered 404 "no trained emulator for this
    study" about that same bundle.
    """
    import main

    # An emulator that is present, but not where the convention would put it.
    elsewhere = tmp_path / "emulators" / "somewhere_else"
    elsewhere.mkdir(parents=True)
    (elsewhere / "emulator_metadata.json").write_text(
        json.dumps({"model": "RadialBasisFunctions", "feature_labels": ["u"]}), encoding="utf-8"
    )

    monkeypatch.setattr(
        ca_run_history, "emulator_dir", lambda *a, **k: str(tmp_path / "emulators" / "guessed")
    )
    monkeypatch.setattr(ca_run_history, "find_emulator_dir", lambda *a, **k: str(elsewhere))

    class _Meta:
        name = "CardiovascularSystem"

    class _Record:
        meta = _Meta()
        obs_path = None

    monkeypatch.setattr(main, "_get_model", lambda _id: _Record())

    resolved = main._emulator_dir_for("any-model", {"config_outputs_dir": str(tmp_path)})
    assert resolved == str(elsewhere), "predictions must use the emulator that was found"
    assert ca_run_history.emulator_metadata(resolved) is not None


@pytest.mark.unit
def test_a_genuinely_absent_emulator_still_names_where_it_was_expected(tmp_path, monkeypatch):
    """The fallback must not turn a real absence into a confusing one: the message is only
    actionable if it points at the place the emulator was looked for."""
    import main

    guessed = str(tmp_path / "emulators" / "guessed")
    monkeypatch.setattr(ca_run_history, "emulator_dir", lambda *a, **k: guessed)
    monkeypatch.setattr(ca_run_history, "find_emulator_dir", lambda *a, **k: None)

    class _Meta:
        name = "m"

    class _Record:
        meta = _Meta()
        obs_path = None

    monkeypatch.setattr(main, "_get_model", lambda _id: _Record())

    assert main._emulator_dir_for("any-model", {"config_outputs_dir": str(tmp_path)}) == guessed


# --- the loaded best fit must be the numbers on disk ----------------------------------

@pytest.mark.unit
def test_the_loaded_best_fit_is_exactly_what_the_run_recorded(tmp_path):
    """Every parameter, by name, at full precision, with the cost that goes with it.

    A best fit that loads *approximately* is worse than one that does not load: it seeds
    "start from the best fit", the SA nominal and the UQ prior, so a silent transcription
    error propagates into every analysis that follows and none of them look wrong.
    """
    run = os.path.join(str(tmp_path), "genetic_algorithm_run")
    os.makedirs(run)
    values = np.array([0.12345678901234, 2.5, -3.75, 1e-9])
    np.save(os.path.join(run, "best_param_vals.npy"), values)
    np.save(os.path.join(run, "best_cost.npy"), np.array(0.0070613052722848565))
    with open(os.path.join(run, "param_names.csv"), "w", newline="") as handle:
        csv.writer(handle).writerows(
            [["heart/a"], ["heart/b"], ["aortic_root/c"], ["aortic_root/d"]])

    best = load_outputs.load_outputs(str(tmp_path))["calibration"]["best"]

    assert list(best["params"]) == ["heart/a", "heart/b", "aortic_root/c", "aortic_root/d"]
    for name, expected in zip(best["params"], values):
        assert best["params"][name] == pytest.approx(expected, rel=0, abs=0), name
    assert best["cost"] == pytest.approx(0.0070613052722848565, rel=0, abs=0)


# --- sensitivity ----------------------------------------------------------------------

@pytest.mark.unit
def test_a_sobol_run_is_loaded_with_its_indices_and_names(tmp_path):
    """SA results are read from a directory this app did not produce, like the rest."""
    run = os.path.join(str(tmp_path), "sobol_run")
    os.makedirs(run)
    np.save(os.path.join(run, "best_param_vals.npy"), np.array([1.0, 2.0]))
    np.save(os.path.join(run, "best_cost.npy"), np.array(0.5))
    with open(os.path.join(run, "param_names.csv"), "w", newline="") as handle:
        csv.writer(handle).writerows([["a/x"], ["a/y"]])
    np.save(os.path.join(str(tmp_path), "all_outputs_n1_Sobol_indices.npy"),
            np.array([[0.6, 0.4], [0.3, 0.7]]))

    result = load_outputs.load_outputs(str(tmp_path))
    sobol = result["sensitivity"]["sobol"]
    local = result["sensitivity"]["local"]

    # Whichever form this CA wrote, one of them must carry indices rather than both
    # being silently empty -- that is the state the panel cannot distinguish from
    # "no SA was run".
    assert (sobol and sobol.get("indices")) or (local and local.get("indices")) or \
        "sensitivity" not in result["found"], (
            "an SA that produced indices must either load them or be reported absent, "
            "never load as an empty panel"
        )


@pytest.mark.unit
def test_a_directory_with_no_sensitivity_says_so_rather_than_showing_an_empty_panel(tmp_path):
    write_run(tmp_path, "genetic_algorithm_run", chain=False, best=True)
    result = load_outputs.load_outputs(str(tmp_path))
    assert "sensitivity" not in result["found"]


# --- UQ, reloaded ----------------------------------------------------------------------

@pytest.mark.unit
def test_a_reloaded_uq_carries_its_posterior_coverage_and_artefacts(tmp_path):
    """What the UQ panel needs to render a run it did not watch happen: a posterior per
    parameter, the coverage numbers, and whether the predictive artefacts are on disk."""
    run = write_run(tmp_path, "mcmc_run", chain=True, best=False)
    write_coverage(run)
    open(os.path.join(run, load_outputs.PREDICTIVE_FILE), "wb").close()
    open(os.path.join(run, load_outputs.SERIES_FILE), "wb").close()

    result = load_outputs.load_outputs(str(tmp_path))
    uq = result["uq"]

    assert "uq" in result["found"]
    assert len(uq["params"]) == 3, "one posterior per parameter"
    assert all("mean" in p for p in uq["params"])
    assert uq["coverage"] is not None, "the coverage numbers are what the panel reports"
    assert uq["has_posterior_predictive"] is True
    assert uq["has_sample_traces"] is True
    assert uq["run_dir"] == run, "the panel must be able to say which run answered"


# --- the inputs the run was made from -------------------------------------------------

def write_snapshots(run, stamps, *, obs=True, params=True):
    """CA's timestamped snapshots, all sharing one mtime as they do on a real run."""
    made = []
    for stamp in stamps:
        if obs:
            q = os.path.join(run, f"abc_obs_data_{stamp}.json")
            with open(q, "w") as h:
                json.dump({"stamp": stamp}, h)
            made.append(q)
        if params:
            q = os.path.join(run, f"abc_params_for_id_{stamp}.json")
            with open(q, "w") as h:
                json.dump({"stamp": stamp}, h)
            made.append(q)
    for q in made:                      # one write, one mtime -- as CA leaves them
        os.utime(q, (1_000_000, 1_000_000))


@pytest.mark.unit
def test_the_study_inputs_are_reported_so_a_directory_can_be_reopened(tmp_path):
    run = write_run(tmp_path, "genetic_algorithm_run", chain=False, best=True)
    write_snapshots(run, ["260824_091622"])
    (tmp_path / "CardiovascularSystem_calibrated.cellml").write_text("<model/>", encoding="utf-8")

    study = load_outputs.load_outputs(str(tmp_path))["study"]

    assert study["file_prefix"] == "CardiovascularSystem"
    assert study["model"].endswith("CardiovascularSystem_calibrated.cellml")
    assert study["model_is_calibrated"] is True
    assert study["obs_data"].endswith("abc_obs_data_260824_091622.json")
    assert study["params_for_id"].endswith("abc_params_for_id_260824_091622.json")


@pytest.mark.unit
def test_the_obs_data_and_params_come_from_the_same_run(tmp_path):
    """The pairing bug, from a real directory.

    CA writes both snapshots in one go, so on disk all of them carry the *same* mtime --
    to the microsecond. Picking "the newest file" therefore came down to whatever order the
    glob returned, and it paired one run's obs_data with another run's params_for_id: two
    files that were never used together, presented as the study.
    """
    run = write_run(tmp_path, "genetic_algorithm_run", chain=False, best=True)
    write_snapshots(run, ["260824_091622", "260824_092846"])

    study = load_outputs.load_outputs(str(tmp_path))["study"]

    assert study["obs_data"].endswith("abc_obs_data_260824_092846.json"), "the newest run"
    assert study["params_for_id"].endswith("abc_params_for_id_260824_092846.json")
    # ...and above all, the same stamp on both.
    assert _stamp_of(study["obs_data"]) == _stamp_of(study["params_for_id"])


def _stamp_of(path):
    return os.path.basename(path).rsplit("_", 2)[-2:]


@pytest.mark.unit
def test_a_run_whose_newest_stamp_has_only_one_half_still_reports_it(tmp_path):
    """Half a study is worth reporting; the caller can see the stamps differ."""
    run = write_run(tmp_path, "genetic_algorithm_run", chain=False, best=True)
    write_snapshots(run, ["260824_091622"])
    write_snapshots(run, ["260824_092846"], params=False)

    study = load_outputs.load_outputs(str(tmp_path))["study"]
    # The complete pair wins over a newer half-pair -- that is the run that finished.
    assert study["obs_data"].endswith("abc_obs_data_260824_091622.json")
    assert study["params_for_id"].endswith("abc_params_for_id_260824_091622.json")


@pytest.mark.unit
def test_a_generated_model_is_preferred_over_the_calibrated_one(tmp_path):
    """`generated_models/<prefix>` is the source model; the calibrated file is the output."""
    write_run(tmp_path, "genetic_algorithm_run", chain=False, best=True)
    (tmp_path / "Heart_calibrated.cellml").write_text("<model/>", encoding="utf-8")
    generated = tmp_path / "generated_models" / "Heart"
    generated.mkdir(parents=True)
    (generated / "Heart.cellml").write_text("<model/>", encoding="utf-8")

    study = load_outputs.load_outputs(str(tmp_path))["study"]
    assert study["model"] == str(generated / "Heart.cellml")
    assert study["model_is_calibrated"] is False


@pytest.mark.unit
def test_a_generated_model_for_another_study_is_not_used(tmp_path):
    """An outputs directory reused across studies is ordinary. A `generated_models` entry
    whose prefix does not match these runs belongs to a different study, and attaching it
    would produce numbers that look right."""
    write_run(tmp_path, "genetic_algorithm_run", chain=False, best=True)
    (tmp_path / "Heart_calibrated.cellml").write_text("<model/>", encoding="utf-8")
    stray = tmp_path / "generated_models" / "3compartment_flat"
    stray.mkdir(parents=True)
    (stray / "3compartment_flat.cellml").write_text("<model/>", encoding="utf-8")

    study = load_outputs.load_outputs(str(tmp_path))["study"]
    assert study["model"].endswith("Heart_calibrated.cellml"), study["model"]


@pytest.mark.unit
def test_a_missing_user_inputs_is_reported_rather_than_treated_as_a_fault(tmp_path):
    write_run(tmp_path, "genetic_algorithm_run", chain=False, best=True)
    result = load_outputs.load_outputs(str(tmp_path))
    assert result["study"]["user_inputs"] is None
    assert not [m for m in result["missing"] if "user_inputs" in m]


@pytest.mark.unit
def test_a_generated_user_inputs_is_picked_up_when_the_directory_has_one(tmp_path):
    write_run(tmp_path, "genetic_algorithm_run", chain=False, best=True)
    (tmp_path / "user_inputs_260824.yaml").write_text("do_generation: false\n", encoding="utf-8")

    study = load_outputs.load_outputs(str(tmp_path))["study"]
    assert study["user_inputs"].endswith("user_inputs_260824.yaml")
