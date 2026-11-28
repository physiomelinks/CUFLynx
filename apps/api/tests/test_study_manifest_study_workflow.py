"""A study made by 0.8.0 opens at its working dir, not at its study workflow's run folder.

Every panel run is a step of the study workflow, so libcuflynx writes the study manifest in
the run folder ``<W>/workflows/study/``, beside the workflow file
``<W>/workflow/study/calibration_workflow.json``. Opening that manifest (a dropped file, a
``cuflynx://open?study=`` link, a zipped working dir) has to open ``<W>``.
"""

import io
import json
import zipfile

import open_link
import study_manifest

MANIFEST = {"schema": 1, "study_name": "study", "file_prefix": None}


def _study(work, *, workflow_file=True):
    """A working dir as a 0.8.0 panel run leaves it; returns the manifest's path."""
    run = work / "workflows" / "study"
    run.mkdir(parents=True)
    manifest = run / "study_cuflynx_study.json"
    manifest.write_text(json.dumps(MANIFEST))
    if workflow_file:
        wf = work / "workflow" / "study"
        wf.mkdir(parents=True)
        (wf / "calibration_workflow.json").write_text("{}")
    return manifest


def test_a_manifest_in_the_study_workflow_run_opens_its_working_dir(tmp_path):
    manifest = _study(tmp_path / "W")
    opened = study_manifest.study_location(MANIFEST, str(manifest))
    assert opened["dir"] == str(tmp_path / "W")


def test_a_workflows_folder_without_the_workflow_file_is_its_own_study(tmp_path):
    manifest = _study(tmp_path / "W", workflow_file=False)
    opened = study_manifest.study_location(MANIFEST, str(manifest))
    assert opened["dir"] == str(tmp_path / "W" / "workflows" / "study")


def test_a_manifest_at_the_top_still_opens_its_own_folder(tmp_path):
    (tmp_path / "W").mkdir()
    manifest = tmp_path / "W" / "LV_cuflynx_study.json"
    manifest.write_text(json.dumps(MANIFEST))
    assert study_manifest.study_location(MANIFEST, str(manifest))["dir"] == str(tmp_path / "W")


def test_the_working_dir_finds_the_manifest_in_its_study_workflow_run(tmp_path):
    manifest = _study(tmp_path / "W")
    assert study_manifest.find(str(tmp_path / "W")) is None  # what the other readers keep
    assert study_manifest.find_study(str(tmp_path / "W")) == str(manifest)


def test_a_manifest_at_the_top_wins_over_the_run_folder(tmp_path):
    _study(tmp_path / "W")
    top = tmp_path / "W" / "LV_cuflynx_study.json"
    top.write_text(json.dumps(MANIFEST))
    assert study_manifest.find_study(str(tmp_path / "W")) == str(top)


def test_a_study_link_to_the_working_dir_or_its_manifest_opens_the_working_dir(tmp_path):
    manifest = _study(tmp_path / "W")
    for target in (tmp_path / "W", manifest):
        path, _, raw = open_link.read_study(target.as_uri())
        assert path == str(manifest)
        assert study_manifest.study_location(raw, path)["dir"] == str(tmp_path / "W")


def _zip(members):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in members.items():
            zf.writestr(name, data)
    buf.seek(0)
    return zipfile.ZipFile(buf)


def test_a_zipped_0_8_working_dir_is_a_working_dir_rooted_above_its_run():
    for prefix in ("", "W/"):
        found = study_manifest.zip_study(_zip({
            f"{prefix}workflows/study/study_cuflynx_study.json": json.dumps(MANIFEST),
            f"{prefix}workflow/study/calibration_workflow.json": "{}",
            f"{prefix}inputs/index.json": "{}",
        }))
        assert found["kind"] == "working_dir"
        assert found["root"] == prefix
        assert found["manifest"] == f"{prefix}workflows/study/study_cuflynx_study.json"


def test_a_zip_with_a_run_folder_but_no_workflow_file_is_not_a_working_dir():
    assert study_manifest.zip_study(_zip({
        "workflows/study/study_cuflynx_study.json": json.dumps(MANIFEST)})) is None
