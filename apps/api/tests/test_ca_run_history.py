"""Reading a circulatory_autogen run directory (issue #210).

CUFLynx used to have each runner serialise its own ``results.json`` summary and
hand it back to the manager. That put a file in the user's outputs directory
that is no part of the study, and made CUFLynx the author of a format
duplicating CA's -- so the two could disagree, and the same history parsing
existed twice with different rules.

Everything is now read from the files CA itself writes. These tests build a run
directory by hand, in CA's shapes, and check that what the managers report comes
back out of it. A consequence worth stating: a run directory produced by CA's
own scripts is now just as readable as one produced through the GUI.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path

import ca_run_history as crh
import numpy as np
import pytest


def _ca_run(dir_path: Path, values=(1.5, 2.0), cost=0.25, names=(["a/x"], ["a/y"])):
    dir_path.mkdir(parents=True, exist_ok=True)
    np.save(dir_path / "best_param_vals.npy", np.array(values, dtype=float))
    np.save(dir_path / "best_cost.npy", np.array([cost], dtype=float))
    with open(dir_path / "param_names.csv", "w", newline="") as fh:
        csv.writer(fh).writerows(names)
    return dir_path


# ---------------------------------------------------------------------------
# Finding the run
# ---------------------------------------------------------------------------
def test_a_run_directory_is_found_in_cas_case_subdir(tmp_path):
    """CA names it ``<method>_<prefix>_<obs_prefix>``, so a caller cannot know it
    without repeating CA's naming rule."""
    _ca_run(tmp_path / "genetic_algorithm_model_obs")
    assert crh.find_run_dir(str(tmp_path)) == str(tmp_path / "genetic_algorithm_model_obs")
    assert crh.has_results(str(tmp_path))


def test_the_output_dir_itself_counts_as_the_run_dir(tmp_path):
    _ca_run(tmp_path)
    assert crh.find_run_dir(str(tmp_path)) == str(tmp_path)


def test_a_previous_runs_results_do_not_count_as_this_runs(tmp_path):
    """Searching for a ``<case_type>`` subdirectory can reach an *earlier* run's
    outputs, which the old direct read of a single file could not do. A run whose
    own results are missing must fail, not be reported with someone else's
    numbers -- so results older than the run that is asking are not its own.
    """
    import time

    _ca_run(tmp_path / "genetic_algorithm_model_obs")
    started_later = time.time() + 60

    assert crh.has_results(str(tmp_path)) is True  # they are somebody's
    assert crh.has_results(str(tmp_path), newer_than=started_later) is False
    # A run that did write its own results is unaffected.
    assert crh.has_results(str(tmp_path), newer_than=time.time() - 60) is True


def test_an_empty_directory_has_no_results(tmp_path):
    """``has_results`` is the gate, not ``find_run_dir``.

    CA's own find_run_dir falls back to the directory it was given when it
    recognises nothing in it, so "a directory was returned" says nothing about
    whether a run happened there.
    """
    assert crh.has_results(str(tmp_path)) is False
    assert crh.best_param_values(str(tmp_path)) == {"params": {}, "cost": None}
    assert crh.modifiers(str(tmp_path)) == []


# ---------------------------------------------------------------------------
# The best fit
# ---------------------------------------------------------------------------
def test_the_best_fit_pairs_values_with_every_member_qname(tmp_path):
    """One row is one calibrated variable, which may name several model
    constants (#193). Every member gets that row's value, which is the same
    expansion the runner used to do before handing back a summary."""
    _ca_run(tmp_path, values=(1.5, 2.0), names=(["a/x", "b/x"], ["a/y"]))
    best = crh.best_param_values(str(tmp_path))
    assert best["params"] == {"a/x": 1.5, "b/x": 1.5, "a/y": 2.0}
    assert best["cost"] == 0.25


def test_a_half_written_run_reads_as_empty_rather_than_raising(tmp_path):
    """A run directory may be read while it is still being written."""
    np.save(tmp_path / "best_param_vals.npy", np.array([1.0]))
    best = crh.best_param_values(str(tmp_path))
    assert best["params"] == {}  # no param_names.csv yet
    assert best["cost"] is None


# ---------------------------------------------------------------------------
# Modifiers
# ---------------------------------------------------------------------------
def test_modifiers_come_back_with_their_resolved_baselines(tmp_path):
    """CA writes param_modifiers.json twice -- once at parse time with baselines
    still None, then again once resolved -- so a finished run's copy carries the
    values the frontend needs to expand theta."""
    _ca_run(tmp_path, values=(1.2, 2.0), names=(["heart/C"], ["a/y"]))
    (tmp_path / "param_modifiers.json").write_text(json.dumps([
        {"index": 0, "name": "C_scale", "modifier": "scale",
         "targets": ["heart/C", "aorta/C"], "baselines": [1e-8, 2e-8]},
    ]))
    mods = crh.modifiers(str(tmp_path))
    assert len(mods) == 1
    assert mods[0] == {
        "name": "C_scale",
        "anchor": "heart/C",
        "targets": ["heart/C", "aorta/C"],
        "operation": "scale",
        "baselines": [1e-8, 2e-8],
        "theta": 1.2,
    }


def test_the_pre_rename_operation_key_is_still_read(tmp_path):
    """CA #385 renamed `operation` to `modifier`. A run directory written by an
    older CA is still a run directory someone may open."""
    _ca_run(tmp_path, values=(1.2,), names=(["heart/C"],))
    (tmp_path / "param_modifiers.json").write_text(json.dumps([
        {"index": 0, "name": "C_scale", "operation": "scale", "targets": ["heart/C"]},
    ]))
    assert crh.modifiers(str(tmp_path))[0]["operation"] == "scale"


def test_no_modifier_file_is_not_an_error(tmp_path):
    _ca_run(tmp_path)
    assert crh.modifiers(str(tmp_path)) == []


# ---------------------------------------------------------------------------
# Error vectors
# ---------------------------------------------------------------------------
def test_error_vectors_are_labelled_by_cas_own_names_file(tmp_path):
    """CA #341 added error_vec_names.npy precisely so the vectors self-identify;
    the labels used to be guessed from obs_data, which is how a mislabelled bar
    chart happened."""
    _ca_run(tmp_path)
    np.save(tmp_path / "percent_error_vec.npy", np.array([0.96, 4.34, -2.87]))
    np.save(tmp_path / "std_error_vec.npy", np.array([0.09, 0.43, -0.28]))
    np.save(tmp_path / "error_vec_names.npy", np.array(["p_ao", "q_lv", "v_ar"]))

    errors = crh.error_vectors(str(tmp_path))
    assert errors["percent_error"] == pytest.approx([0.96, 4.34, -2.87])
    assert errors["std_error"] == pytest.approx([0.09, 0.43, -0.28])
    assert errors["error_labels"] == ["p_ao", "q_lv", "v_ar"]


def test_missing_error_vectors_are_absent_not_zero(tmp_path):
    """They are written by plot_outputs(), not by run(), so a run that never
    plotted has none -- which is different from having errors of zero."""
    _ca_run(tmp_path)
    errors = crh.error_vectors(str(tmp_path))
    assert errors["percent_error"] is None
    assert errors["std_error"] is None
    assert errors["error_labels"] == []


# ---------------------------------------------------------------------------
# The calibrated model
# ---------------------------------------------------------------------------
def test_the_calibrated_model_path_is_derived_not_round_tripped(tmp_path):
    """Both halves of the name are known to the manager, so it never needed the
    runner to report the path back."""
    assert crh.calibrated_model_path(str(tmp_path), "lv") is None
    (tmp_path / "lv_calibrated.cellml").write_text("<model/>")
    assert crh.calibrated_model_path(str(tmp_path), "lv") == str(
        tmp_path / "lv_calibrated.cellml"
    )


# ---------------------------------------------------------------------------
# Sensitivity
# ---------------------------------------------------------------------------
def test_sobol_indices_are_read_from_cas_csv(tmp_path):
    """CA writes all_outputs_n<N>_Sobol_indices.csv: a Parameter column, then
    S1_<output> / ST_<output> per output. The sample count is in the filename,
    so it is globbed rather than reconstructed."""
    (tmp_path / "all_outputs_n64_Sobol_indices.csv").write_text(
        "Parameter,S1_max/m/x,ST_max/m/x,S1_min/m/y,ST_min/m/y\n"
        "a/b,0.6,0.7,0.1,0.2\n"
        "c/d,0.2,0.3,0.8,0.9\n"
    )
    data = crh.sobol_indices(str(tmp_path))
    assert data["param_names"] == ["a/b", "c/d"]
    assert data["output_names"] == ["max/m/x", "min/m/y"]
    assert data["indices"]["ST"]["max/m/x"] == {"a/b": 0.7, "c/d": 0.3}
    assert data["indices"]["S1"]["min/m/y"] == {"a/b": 0.1, "c/d": 0.8}


def test_the_second_order_csv_is_not_mistaken_for_the_indices(tmp_path):
    """CA writes both; they share a suffix and only one has the S1/ST columns."""
    (tmp_path / "all_outputs_n64_Sobol_2nd_order_indices.csv").write_text(
        "Parameter,max/m/x__a/b\na/b,0.1\n"
    )
    assert crh.sobol_indices(str(tmp_path)) is None


def test_local_sensitivities_round_trip_through_cas_csv_format(tmp_path):
    """CUFLynx's own local-SA arm writes CA's format, so the outputs directory
    holds one format whichever arm produced the numbers."""
    local = {"max/m/x": {"a/b": 0.5, "c/d": -0.2}, "min/m/y": {"a/b": None, "c/d": 1.0}}
    crh.write_local_sensitivity(str(tmp_path), "relative", local, ["max/m/x", "min/m/y"])

    data = crh.local_sensitivity(str(tmp_path))
    assert data["param_names"] == ["a/b", "c/d"]
    assert data["output_names"] == ["max/m/x", "min/m/y"]
    assert data["indices"]["local"]["max/m/x"] == {"a/b": 0.5, "c/d": -0.2}
    # A failed evaluation stays distinguishable from a real zero.
    assert data["indices"]["local"]["min/m/y"]["a/b"] is None


def test_no_sensitivity_files_reads_as_none(tmp_path):
    assert crh.sobol_indices(str(tmp_path)) is None
    assert crh.local_sensitivity(str(tmp_path)) is None


# ---------------------------------------------------------------------------
# UQ
# ---------------------------------------------------------------------------
def test_posteriors_are_binned_from_the_samples_the_run_persisted(tmp_path):
    """The samples are the result; the histogram is derived, so the bundle holds
    no summary format of CUFLynx's own devising."""
    rng = np.random.default_rng(0)
    flat = np.column_stack([rng.normal(1.0, 0.1, 2000), rng.normal(-3.0, 0.5, 2000)])
    crh.write_uq_samples(str(tmp_path), flat, ["a/x", "a/y"])

    params = crh.uq_distributions(str(tmp_path))
    assert [p["qname"] for p in params] == ["a/x", "a/y"]
    assert params[0]["mean"] == pytest.approx(1.0, abs=0.02)
    assert params[1]["std"] == pytest.approx(0.5, abs=0.05)
    # Edges = counts + 1, which is what the posterior plot assumes.
    assert len(params[0]["bins"]) == len(params[0]["counts"]) + 1


def test_no_samples_reads_as_none(tmp_path):
    assert crh.uq_distributions(str(tmp_path)) is None


# ---------------------------------------------------------------------------
# Nothing writes results.json any more
# ---------------------------------------------------------------------------
def test_no_runner_or_manager_names_results_json_in_code():
    """The whole point of #210. A grep, because the failure mode is one of the
    four writers quietly coming back rather than any single call misbehaving.

    Matches the *quoted* name, so the prose explaining why it is gone does not
    trip it -- the history is worth keeping in the comments.
    """
    api_dir = Path(__file__).resolve().parent.parent
    offenders = []
    for name in (
        "calibration.py", "calibration_runner.py",
        "sensitivity.py", "sensitivity_runner.py",
        "uq.py", "uq_runner.py",
        "export_pipeline.py",
    ):
        text = (api_dir / name).read_text(encoding="utf-8")
        if '"results.json"' in text or "'results.json'" in text:
            offenders.append(name)
    assert not offenders, f"results.json is back in {offenders}"
