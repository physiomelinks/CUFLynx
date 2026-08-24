"""A study that says where its files are must be believed, and a broken one must not.

The conventions these replace do not fail loudly. Looking for the emulator under
``emulators/``, the model at ``<prefix>_calibrated.cellml``, and "the newest run when
several share a folder" are all guesses that return *something* -- a folder holding nine
runs answered with an eighteen-parameter fit belonging to none of them. So the tests
that matter here are about precedence and about refusing, not about parsing.
"""
import json
import os

import pytest

import load_outputs
import study_manifest


def _study(tmp_path, **overrides):
    """A study directory with a run in it, and a manifest describing it."""
    output = tmp_path / "param_id_output_ox1"
    run = output / "genetic_algorithm_SN_full_joint_ox1_obs_data"
    run.mkdir(parents=True)
    (run / "best_param_vals.npy").write_bytes(b"")
    (output / "SN_full_calibrated.cellml").write_text("<model/>")
    emulator = tmp_path / "shared" / "emulators" / "joint"
    emulator.mkdir(parents=True)
    (emulator / "emulator_metadata.json").write_text("{}")

    record = {
        "file_prefix": "SN_full",
        "model": str(output / "SN_full_calibrated.cellml"),
        "emulator": str(emulator),
        "runs": [{"dir": str(run), "obs_data": "joint_ox1_obs_data.json"}],
    }
    record.update(overrides)
    study_manifest.write(str(output), record)
    return output, run, emulator


def test_a_shared_emulator_outside_the_study_is_reachable(tmp_path):
    """The case conventions cannot express at all.

    One emulator trained on a joint dataset serves several obs_data -- that is why it was
    trained jointly. The conventional search only looks under the selected directory, so
    without a manifest each study needs its own copy of the bundle.
    """
    output, _, emulator = _study(tmp_path)
    read = study_manifest.read(str(output))
    assert read["emulator"] == str(emulator)
    assert os.path.isdir(read["emulator"])


def test_the_manifest_beats_the_convention(tmp_path):
    """Two runs in the folder, and the study names the one it means.

    Without this the newest wins, which is only the right answer by luck.
    """
    output, run, _ = _study(tmp_path)
    other = output / "genetic_algorithm_SN_full_someone_elses_obs_data"
    other.mkdir()
    (other / "best_param_vals.npy").write_bytes(b"")
    os.utime(other / "best_param_vals.npy", (2 ** 31, 2 ** 31))  # newest by far

    result = load_outputs.load_outputs(str(output))
    assert result["run_dir"] == str(run), (
        "the newest run was chosen over the one the study declares")
    assert [entry["path"] for entry in result["run_dirs"]] == [str(run)]


def test_a_directory_with_no_manifest_still_loads(tmp_path):
    """Every directory that loads today has to keep loading; the file is optional."""
    output = tmp_path / "plain"
    run = output / "genetic_algorithm_SN_full_obs_data"
    run.mkdir(parents=True)
    (run / "best_param_vals.npy").write_bytes(b"")

    result = load_outputs.load_outputs(str(output))
    assert result["manifest"] is None
    assert result["manifest_error"] is None
    assert result["run_dir"] == str(run)


def test_a_manifest_that_cannot_be_read_is_reported_not_ignored(tmp_path):
    """Falling back to guesses here would hide a corrupt study behind a plausible answer."""
    output = tmp_path / "broken"
    run = output / "genetic_algorithm_SN_full_obs_data"
    run.mkdir(parents=True)
    (run / "best_param_vals.npy").write_bytes(b"")
    (output / study_manifest.MANIFEST_NAME).write_text("{not json")

    result = load_outputs.load_outputs(str(output))
    assert result["manifest"] is None
    assert "could not be read" in (result["manifest_error"] or "")


def test_a_newer_schema_is_refused_rather_than_half_read(tmp_path):
    """The file exists to be believed; understanding part of it is worse than none."""
    output, _, _ = _study(tmp_path)
    location = output / study_manifest.MANIFEST_NAME
    record = json.loads(location.read_text())
    record["schema"] = study_manifest.SCHEMA + 1
    location.write_text(json.dumps(record))

    with pytest.raises(study_manifest.ManifestError, match="Update CUFLynx"):
        study_manifest.read(str(output))


def test_a_declared_file_that_is_not_there_is_named(tmp_path):
    """A manifest pointing at a missing file is a broken study, and should read as one."""
    output, _, _ = _study(tmp_path, obs_data="/nowhere/joint_ox1_obs_data.json")
    read = study_manifest.read(str(output))
    assert any("obs_data" in entry for entry in read["missing"])

    result = load_outputs.load_outputs(str(output))
    assert any("obs_data" in entry for entry in result["missing"])


def test_a_study_directory_survives_being_moved(tmp_path):
    """Paths inside the study are stored relative, so copying the folder keeps it whole."""
    output, _, _ = _study(tmp_path)
    moved = tmp_path / "moved_elsewhere"
    os.rename(output, moved)

    read = study_manifest.read(str(moved))
    assert read["model"] == str(moved / "SN_full_calibrated.cellml")
    assert os.path.isfile(read["model"])
    assert read["runs"][0]["dir"].startswith(str(moved))


def test_a_shared_artefact_is_stored_absolutely(tmp_path):
    """Relative would only survive while the study stays put; the emulator is elsewhere."""
    output, _, emulator = _study(tmp_path)
    record = json.loads((output / study_manifest.MANIFEST_NAME).read_text())
    assert os.path.isabs(record["emulator"])
    # ...while the study's own files stay relative, which is what makes it movable.
    assert not os.path.isabs(record["model"])
    assert not os.path.isabs(record["runs"][0]["dir"])
