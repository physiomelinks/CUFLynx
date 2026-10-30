"""A working dir read after it has been moved, copied or unzipped elsewhere.

Relative paths resolve against the folder holding the manifest, whatever ``study_dir``
it records. A manifest written before inputs were copied into the working dir records
them by absolute path; those are found again under the moved folder or under its
``inputs/<kind>/``.
"""
import json
import shutil

import pytest

import study_manifest

pytestmark = pytest.mark.unit


def _write(path, body):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"schema": 1, **body}), encoding="utf-8")
    return path


def _touch(path, text="x"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def test_relative_paths_follow_the_manifest_even_when_the_original_still_exists(tmp_path):
    """A copy must read its own files, not the original's, though both are on disk."""
    original = tmp_path / "original"
    _touch(original / "inputs" / "model" / "S.cellml", "original")
    _write(original / "S_cuflynx_study.json", {
        "study_dir": str(original), "model": "inputs/model/S.cellml"})
    copy = tmp_path / "copy"
    shutil.copytree(original, copy)
    (copy / "inputs" / "model" / "S.cellml").write_text("copy")

    read = study_manifest.read(str(copy))

    assert read["model"] == str(copy / "inputs" / "model" / "S.cellml")
    assert read["study_dir"] == str(copy)
    assert read["missing"] == []


def test_a_manifest_with_no_study_dir_reads_in_its_folder(tmp_path):
    """What libcuflynx writes now: only relative paths."""
    work = tmp_path / "W"
    _touch(work / "inputs" / "obs_data" / "S_obs_data.json")
    _touch(work / "inputs" / "funcs" / "operation_funcs_user.py")
    (work / "genetic_algorithm_S_S_obs_data").mkdir(parents=True)
    _write(work / "S_cuflynx_study.json", {
        "obs_data": "inputs/obs_data/S_obs_data.json",
        "user_funcs": {"operation": "inputs/funcs/operation_funcs_user.py"},
        "runs": [{"dir": "genetic_algorithm_S_S_obs_data"}]})

    read = study_manifest.read(str(work))

    assert read["obs_data"] == str(work / "inputs" / "obs_data" / "S_obs_data.json")
    assert read["user_funcs"]["operation"] == str(
        work / "inputs" / "funcs" / "operation_funcs_user.py")
    assert read["runs"][0]["dir"] == str(work / "genetic_algorithm_S_S_obs_data")
    assert read["missing"] == []


def test_an_old_manifests_absolute_paths_follow_a_moved_folder(tmp_path):
    """Written before the change: absolute paths under study_dir, top-level user_funcs/."""
    old = tmp_path / "old" / "W"
    _touch(old / "user_funcs" / "operation_funcs_user.py")
    _touch(old / "S_calibrated.cellml")
    (old / "genetic_algorithm_S_S_obs_data").mkdir(parents=True)
    _write(old / "S_cuflynx_study.json", {
        "study_dir": str(old),
        "model": str(old / "S_calibrated.cellml"),
        "user_funcs": {"operation": str(old / "user_funcs" / "operation_funcs_user.py")},
        "runs": [{"dir": str(old / "genetic_algorithm_S_S_obs_data")}]})
    moved = tmp_path / "moved" / "W"
    shutil.move(str(old), str(moved))

    read = study_manifest.read(str(moved))

    assert read["model"] == str(moved / "S_calibrated.cellml")
    assert read["user_funcs"]["operation"] == str(
        moved / "user_funcs" / "operation_funcs_user.py")
    assert read["runs"][0]["dir"] == str(moved / "genetic_algorithm_S_S_obs_data")
    assert read["missing"] == []


def test_an_old_absolute_input_outside_the_folder_is_found_in_inputs(tmp_path):
    """An input recorded where it was uploaded from, since copied into inputs/<kind>/."""
    work = tmp_path / "W"
    _touch(work / "inputs" / "params_for_id" / "S_params_for_id.json")
    _write(work / "S_cuflynx_study.json", {
        "study_dir": str(work),
        "params_for_id": str(tmp_path / "gone" / "S_params_for_id.json")})

    read = study_manifest.read(str(work))

    assert read["params_for_id"] == str(
        work / "inputs" / "params_for_id" / "S_params_for_id.json")
    assert read["missing"] == []


def test_a_path_found_nowhere_is_still_reported_missing(tmp_path):
    work = tmp_path / "W"
    _write(work / "S_cuflynx_study.json", {
        "study_dir": str(tmp_path / "elsewhere"),
        "obs_data": str(tmp_path / "gone" / "S_obs_data.json")})

    read = study_manifest.read(str(work))

    assert read["missing"] and "obs_data" in read["missing"][0]


def test_a_manifest_copied_alone_still_uses_its_study_dir_hint(tmp_path):
    """The one case study_dir is for: the manifest read away from its folder."""
    work = tmp_path / "W"
    _touch(work / "inputs" / "model" / "S.cellml")
    manifest = _write(work / "S_cuflynx_study.json", {
        "study_dir": str(work), "model": "inputs/model/S.cellml"})
    alone = tmp_path / "Downloads" / manifest.name
    alone.parent.mkdir()
    shutil.copy(manifest, alone)

    read = study_manifest.read(str(alone))

    assert read["model"] == str(work / "inputs" / "model" / "S.cellml")
