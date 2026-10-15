"""A calibration workflow in a COMBINE archive: packed by CA's ``pack_workflow`` under
``workflow/`` on export, unpacked whole on import, so the archive alone reruns the workflow.

The unit tier builds archives by hand; the integration tier exports CA's fixture workflow,
moves the files it was packed from away, imports the archive and runs it.
"""

import io
import json
import os
import shutil
import sys
import time
import zipfile
from pathlib import Path

import pytest

import main
import omex_export
import omex_import
from conftest import LV_MODEL_PATH, LV_OBS_DATA_PATH

PACK = {
    "calibration_workflow.json": json.dumps({
        "schema_version": 1, "workflow_name": "demo", "module_library_dirs": ["modules"],
        "target": {"module_type": "chain", "version": "v1", "parameterisation": "rest"},
        "steps": [{"id": "fit", "model": {"module_type": "chain", "version": "v1",
                                          "parameterisation": "rest"},
                   "obs_data": "inputs/fit/rest_obs_data.json",
                   "params_for_id": "inputs/fit/rest_params_for_id.csv"}]}).encode(),
    "inputs/fit/rest_obs_data.json": LV_OBS_DATA_PATH.read_bytes(),
    "inputs/fit/rest_params_for_id.csv": b"vessel_name,param_name,min,max\nmod,q,0.1,10\n",
    "modules/chain/versions/v1/chain_v1_modules.cellml": b"<model/>",
}


def _members(blob):
    with zipfile.ZipFile(io.BytesIO(blob)) as zf:
        return {n: zf.read(n) for n in zf.namelist()}


def _study_manifest(members):
    name = next(n for n in members if n.endswith("_cuflynx_study.json"))
    return json.loads(members[name])


def _export(**kwargs):
    blob, _ = omex_export.build_archive(
        cellml_text=LV_MODEL_PATH.read_text(), values={}, study={"study_name": "demo"},
        **kwargs)
    return blob


def test_a_packed_workflow_travels_under_workflow_and_is_named_by_the_study():
    blob = _export(workflow_files=PACK)
    members = _members(blob)
    for relative, data in PACK.items():
        assert members[f"workflow/{relative}"] == data
    assert _study_manifest(members)["workflow"] == "workflow/calibration_workflow.json"
    # every packed member is declared in the COMBINE manifest by its full path
    manifest = members["manifest.xml"].decode()
    assert './workflow/inputs/fit/rest_obs_data.json' in manifest

    parts = omex_import.unpack(blob)
    assert parts["workflow_pack"] == PACK
    # the packed steps' files and modules are not the study's
    assert list(parts["cellml"]) == ["model.cellml"]
    assert parts["obs"] is None and parts["params"] is None
    assert parts["calibration_workflow"] is None


def test_a_re_export_keeps_the_archive_s_workflow_or_replaces_it_whole():
    blob = _export(workflow_files=PACK)
    kept = _members(_export(source_archive=blob))
    assert {n for n in kept if n.startswith("workflow/")} == {f"workflow/{n}" for n in PACK}
    assert _study_manifest(kept)["workflow"] == "workflow/calibration_workflow.json"

    smaller = {"calibration_workflow.json": PACK["calibration_workflow.json"]}
    replaced = _members(_export(source_archive=blob, workflow_files=smaller))
    assert [n for n in replaced if n.startswith("workflow/")] == [
        "workflow/calibration_workflow.json"]
    # a study manifest's own workflow block names one machine's files, and is not carried
    plain = _members(omex_export.build_archive(
        cellml_text=LV_MODEL_PATH.read_text(), values={},
        study={"study_name": "demo", "workflow": {"name": "elsewhere"}})[0])
    assert "workflow" not in _study_manifest(plain)


def test_an_imported_archive_s_workflow_is_unpacked_whole_and_offered(client):
    r = client.post("/api/omex/upload",
                    files={"file": ("w.omex", _export(workflow_files=PACK), "application/zip")})
    assert r.status_code == 200, r.text
    offered = r.json()["calibration_workflow"]
    assert offered["packed"] and offered["files"] == len(PACK)
    root = Path(offered["path"]).parent
    for relative, data in PACK.items():
        assert (root / relative).read_bytes() == data
    # the study is the archive's own model, not a packed step's files
    assert r.json()["obs_data"] is None


def test_a_packed_workflow_cannot_write_outside_its_directory(client):
    escaping = dict(PACK, **{"../../escaped.txt": b"x"})
    buf = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(_export(workflow_files=PACK))) as src, \
            zipfile.ZipFile(buf, "w") as out:
        for name in src.namelist():
            out.writestr(name, src.read(name))
        out.writestr("workflow/../../escaped.txt", escaping["../../escaped.txt"])
    r = client.post("/api/omex/upload",
                    files={"file": ("w.omex", buf.getvalue(), "application/zip")})
    assert r.status_code == 422 and "outside it" in r.text


# ---------------------------------------------------------------------------
# integration: CA's own fixture workflow, exported, moved away, imported, run
# ---------------------------------------------------------------------------
def _ca_or_skip(name):
    try:
        from ca_imports import ca_from, ensure_ca_path

        ensure_ca_path()
        return ca_from(*name)
    except ImportError:
        if os.environ.get("CUFLYNX_REQUIRE_CA_RUN"):
            raise
        pytest.skip("this circulatory_autogen cannot pack workflows (CA #585)")


def _wait(client, job_id, timeout):
    deadline = time.time() + timeout
    while time.time() < deadline:
        s = client.get(f"/api/workflow/{job_id}/status").json()
        if s["state"] != "running":
            return s
        time.sleep(0.2)
    raise AssertionError("the workflow did not finish")


@pytest.mark.integration
@pytest.mark.slow
def test_an_exported_workflow_reruns_from_the_archive_alone(
        client, requires_simulation, tmp_path):
    build = _ca_or_skip(("external_testing.workflow_library", "build_workflow_library"))
    _ca_or_skip(("calibration_workflow", "pack_workflow"))
    paths = build(str(tmp_path / "library"))
    assert client.post("/api/config", json={"module_library_dirs": [],
                                            "python_path": sys.executable}).status_code == 200
    loaded = client.post("/api/workflow/load", json={
        "path": paths["chain_analysed"], "config_outputs_dir": str(tmp_path / "outputs")})
    assert loaded.status_code == 200, loaded.text

    exported = client.post("/api/workflow/export", json={})
    assert exported.status_code == 200, exported.text
    archive = client.get(exported.json()["download_url"]).content
    members = _members(archive)
    assert "workflow/calibration_workflow.json" in members
    assert not any(n.endswith("step_result.json") for n in members)   # no results
    client.post("/api/workflow/close")

    shutil.move(str(tmp_path / "library"), str(tmp_path / "moved"))  # the originals are gone
    r = client.post("/api/omex/upload", files={"file": ("w.omex", archive, "application/zip")})
    assert r.status_code == 200, r.text
    offered = r.json()["calibration_workflow"]
    assert offered["packed"]
    body = client.post("/api/workflow/load", json={
        "path": offered["path"], "config_outputs_dir": str(tmp_path / "outputs2")}).json()
    assert [(s["id"], s["kind"]) for s in body["plan"]["steps"]] == [
        ("fit_a", "calibration"), ("screen", "sensitivity"), ("reduce", "reduction"),
        ("fit", "calibration"), ("ia", "identifiability")]

    job = client.post("/api/workflow/run", json={}).json()["job_id"]
    status = _wait(client, job, timeout=300)
    assert status["state"] == "done", "\n".join(status["lines"][-40:])
    target = client.post("/api/workflow/view", json={"view": "target"}).json()
    merged = {c["model_name"]: c["value"] for c in target["workflow_view"]["calibrated"]}
    assert merged["p_mod_A"] == pytest.approx(2.0, rel=1e-4)
    assert merged["q_mod_C"] == pytest.approx(3.0, rel=1e-4)
