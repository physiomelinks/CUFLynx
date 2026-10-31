"""The study manifest circulatory_autogen writes, read the ways a user hands it over.

circulatory_autogen records every analysis run from one config in
``<study_name>_cuflynx_study.json`` (``libcuflynx.utilities.cuflynx_study``), and the
same document travels inside an OMEX archive. These pin what a reader must get right
for that to be the one handle on a study: finding it among others, resolving a copy
that is not sitting in its study, using the funcs it declares without destroying them,
finding sensitivity results outside the outputs directory, and surviving an archive
round trip with the funcs and solver settings intact.
"""
import io
import json
import os
import zipfile

import pytest

import load_outputs
import omex_export
import omex_import
import study_manifest
import user_funcs
import working_dir
from conftest import RESOURCES_DIR

FLAT_MODEL = RESOURCES_DIR / "3compartment_flat.cellml"

OPS = '''"""Hand-written funcs, outside any CUFLynx directory."""
import numpy as np

SCALE = 2.0


def ratio(a, b, series_output=False):
    return np.mean(a) / np.mean(b)
'''


def _write(path, record):
    path.write_text(json.dumps({"schema": 1, **record}))
    return path


@pytest.fixture
def ca_study(tmp_path):
    """A study laid out the way circulatory_autogen leaves one."""
    out = tmp_path / "calibration" / "RAAS6"
    run = out / "genetic_algorithm_RAAS6_obs"
    run.mkdir(parents=True)
    (run / "best_param_vals.npy").write_bytes(b"")
    sa = tmp_path / "sensitivity_outputs" / "RAAS6_SA_results"
    sa.mkdir(parents=True)
    (sa / "local_sensitivity_relative.csv").write_text("output,p1\ny,0.5\n")
    funcs = tmp_path / "funcs_user" / "operation_funcs_user.py"
    funcs.parent.mkdir()
    funcs.write_text(OPS)
    model = tmp_path / "generated_models" / "RAAS6.cellml"
    model.parent.mkdir()
    model.write_text("<model/>")
    manifest = _write(out / "RAAS6_cuflynx_study.json", {
        "study_name": "RAAS6", "file_prefix": "RAAS6", "study_dir": str(out),
        "model": str(model), "base_model": str(model),
        "user_funcs": {"operation": str(funcs)},
        "solver_info": {"solver": "CVODE_myokit", "MaximumStep": 1e-4, "dt": 0.01},
        "runs": [{"dir": "genetic_algorithm_RAAS6_obs", "method": "genetic_algorithm"}],
        "sensitivity": [{"dir": str(sa), "method": "local"}],
    })
    return {"out": out, "run": run, "sa": sa, "funcs": funcs, "model": model,
            "manifest": manifest}


# --- finding it ------------------------------------------------------------------

def test_the_prefixed_name_is_found(ca_study):
    read = study_manifest.read(str(ca_study["out"]))
    assert read["manifest_path"] == str(ca_study["manifest"])
    assert read["runs"][0]["dir"] == str(ca_study["run"])


def test_the_older_unprefixed_name_still_reads(tmp_path):
    _write(tmp_path / "cuflynx_study.json", {"file_prefix": "SN"})
    assert study_manifest.read(str(tmp_path))["file_prefix"] == "SN"


def test_several_studies_in_one_directory_pick_by_prefix(ca_study):
    _write(ca_study["out"] / "other_cuflynx_study.json", {"file_prefix": "other"})
    os.utime(ca_study["out"] / "other_cuflynx_study.json", (1e10, 1e10))  # the newest
    assert study_manifest.read(str(ca_study["out"]), "RAAS6")["file_prefix"] == "RAAS6"
    assert study_manifest.read(str(ca_study["out"]))["file_prefix"] == "other"


def test_a_manifest_file_can_be_named_directly(ca_study):
    read = study_manifest.read(str(ca_study["manifest"]))
    assert read["study_name"] == "RAAS6"


def test_a_copy_away_from_its_study_resolves_against_study_dir(ca_study, tmp_path):
    elsewhere = tmp_path / "Downloads" / "RAAS6_cuflynx_study.json"
    elsewhere.parent.mkdir()
    elsewhere.write_text(ca_study["manifest"].read_text())
    read = study_manifest.read(str(elsewhere))
    assert read["runs"][0]["dir"] == str(ca_study["run"])
    assert read["missing"] == []


# --- what load_outputs does with it ----------------------------------------------

def test_the_model_outside_the_outputs_directory_is_opened(ca_study):
    found = load_outputs.load_outputs(str(ca_study["out"]))
    assert found["study"]["model"] == str(ca_study["model"])
    assert found["run_dir"] == str(ca_study["run"])


def test_sensitivity_results_outside_the_outputs_directory_are_found(ca_study):
    found = load_outputs.load_outputs(str(ca_study["out"]))
    assert found["sensitivity"]["local"]["param_names"] == ["p1"]
    assert "sensitivity" in found["found"]


def test_a_calibrated_model_written_after_the_manifest_wins(ca_study):
    calibrated = ca_study["out"] / "RAAS6_calibrated.cellml"
    calibrated.write_text("<model/>")
    found = load_outputs.load_outputs(str(ca_study["out"]), "RAAS6")
    assert found["study"]["model"] == str(calibrated)


# --- the funcs it declares ---------------------------------------------------------

def test_declared_funcs_are_the_ones_used(ca_study):
    path = user_funcs.external_path("operation", str(ca_study["out"]))
    assert path == str(ca_study["funcs"])
    listed = user_funcs.read_user_funcs("operation", str(ca_study["out"]))
    assert [f["name"] for f in listed["functions"]] == ["ratio"]


def test_editing_a_declared_file_keeps_everything_else_in_it(ca_study, monkeypatch):
    monkeypatch.setattr(user_funcs, "_refresh_options", lambda: None)
    base = str(ca_study["out"])
    user_funcs.save_user_func(
        "operation", "ratio",
        "def ratio(a, b, series_output=False):\n    return SCALE * np.mean(a) / np.mean(b)\n",
        base)
    user_funcs.save_user_func(
        "operation", None, "def double(x, series_output=False):\n    return 2 * x\n", base)
    # each edit is a version stored in the working dir; the declared file is left as it was
    edited = user_funcs.external_path("operation", base)
    assert working_dir.is_inside(base, edited) and ca_study["funcs"].read_text() == OPS
    text = open(edited).read()
    # the docstring, the import and the module constant are the user's, and stay
    assert text.startswith('"""Hand-written funcs')
    assert "import numpy as np" in text and "SCALE = 2.0" in text
    assert "return SCALE * np.mean(a)" in text and "def double(" in text

    user_funcs.delete_user_func("operation", "double", base)
    text = open(user_funcs.external_path("operation", base)).read()
    assert "def double(" not in text and "SCALE = 2.0" in text


def test_without_a_manifest_funcs_stay_in_the_outputs_directory(tmp_path):
    (tmp_path / "user_funcs").mkdir()
    (tmp_path / "user_funcs" / "operation_funcs_user.py").write_text("def f(x):\n    return x\n")
    assert user_funcs.external_path("operation", str(tmp_path)) == str(
        tmp_path / "user_funcs" / "operation_funcs_user.py")


# --- dropping it on the app ----------------------------------------------------------

def test_a_dropped_manifest_names_its_study_directory(client, ca_study):
    resp = client.post("/api/outputs/manifest",
                       json={"content": ca_study["manifest"].read_text()})
    assert resp.status_code == 200, resp.text
    assert resp.json()["dir"] == str(ca_study["out"])
    assert resp.json()["study_name"] == "RAAS6"


def test_a_dropped_manifest_with_a_path_uses_where_it_is(client, ca_study):
    resp = client.post("/api/outputs/manifest",
                       json={"content": {"schema": 1}, "path": str(ca_study["manifest"])})
    assert resp.json()["dir"] == str(ca_study["out"])


@pytest.mark.parametrize("content, words", [
    ({"schema": 1}, "relative to its own folder"),
    ({"schema": 1, "study_dir": "/no/such/study"}, "not on this machine"),
    ({"schema": 99, "study_dir": "/tmp"}, "Update CUFLynx"),
    ("{not json", "not JSON"),
])
def test_a_manifest_that_cannot_be_opened_says_why(client, content, words):
    resp = client.post("/api/outputs/manifest", json={"content": content})
    assert resp.status_code == 422
    assert words in resp.json()["detail"]


# --- inside an OMEX archive -----------------------------------------------------------

def _read(blob):
    with zipfile.ZipFile(io.BytesIO(blob)) as zf:
        return {n: zf.read(n) for n in zf.namelist()}


def test_an_archive_carries_the_funcs_and_solver_settings():
    study = {"study_name": "RAAS6", "file_prefix": "RAAS6",
             "solver_info": {"solver": "CVODE_myokit", "MaximumStep": 1e-4, "dt": 1e-3},
             "runs": [{"dir": "/somewhere/on/one/machine"}]}
    blob, _ = omex_export.build_archive(
        cellml_text=FLAT_MODEL.read_text(), study=study,
        funcs={"operation": ("operation_funcs_user.py", OPS.encode())})
    members = _read(blob)
    manifest = json.loads(members["RAAS6_cuflynx_study.json"])
    assert manifest["model"] == "model.cellml"
    assert manifest["user_funcs"] == {"operation": "user_funcs/operation_funcs_user.py"}
    assert manifest["solver_info"]["MaximumStep"] == 1e-4
    assert "runs" not in manifest  # results do not travel
    assert members["user_funcs/operation_funcs_user.py"] == OPS.encode()

    parts = omex_import.unpack(blob)
    assert parts["study"]["funcs"]["operation"][1] == OPS.encode()
    assert parts["study"]["manifest"]["solver_info"]["dt"] == 1e-3
    # declared by format, so it is neither the obs_data nor the params_for_id
    assert parts["obs"] is None and parts["params"] is None
    assert parts["obs_skipped"] == []


def test_phlynx_members_still_come_back_untouched_beside_the_study():
    src_members = {
        "manifest.xml": """<?xml version="1.0" encoding="UTF-8"?>
<omexManifest xmlns="http://identifiers.org/combine.specifications/omex-manifest">
  <content location="./model.cellml" format="http://identifiers.org/combine.specifications/cellml"/>
  <content location="./flow.json" format="application/x.vnd.phlynx-flow+json"/>
</omexManifest>""",
        "model.cellml": FLAT_MODEL.read_bytes(),
        "flow.json": b'{"id": "phlynx-flow"}',
    }
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in src_members.items():
            zf.writestr(name, data)
    once, _ = omex_export.build_archive(
        cellml_text=FLAT_MODEL.read_text(), source_archive=buf.getvalue(),
        study={"file_prefix": "s"}, funcs={"operation": ("operation_funcs_user.py", b"v1")})
    # sent again with the funcs edited: replaced, not duplicated
    twice, _ = omex_export.build_archive(
        cellml_text=FLAT_MODEL.read_text(), source_archive=once,
        study={"file_prefix": "s"}, funcs={"operation": ("operation_funcs_user.py", b"v2")})
    members = _read(twice)
    assert members["flow.json"] == b'{"id": "phlynx-flow"}'
    assert members["user_funcs/operation_funcs_user.py"] == b"v2"
    assert sorted(n for n in members if n.endswith("_cuflynx_study.json")) == [
        "s_cuflynx_study.json"]


def test_importing_an_archive_keeps_its_funcs_and_existing_ones(client, tmp_path):
    out = tmp_path / "outputs"
    (out / "user_funcs").mkdir(parents=True)
    mine = out / "user_funcs" / "operation_funcs_user.py"
    mine.write_text("def mine(x):\n    return x\n")
    blob, _ = omex_export.build_archive(
        cellml_text=FLAT_MODEL.read_text(),
        study={"file_prefix": "s", "solver_info": {"dt": 0.001}},
        funcs={"operation": ("operation_funcs_user.py", OPS.encode())})
    resp = client.post("/api/omex/upload", params={"output_dir": str(out)},
                       files={"file": ("s.omex", blob, "application/zip")})
    assert resp.status_code == 200, resp.text
    # the archive's funcs are a new version; the older folder's file is left as it was
    assert mine.read_text() == "def mine(x):\n    return x\n"
    [stored] = (out / "inputs" / "funcs").glob("*/operation_funcs_user.py")
    assert stored.read_text() == OPS
    assert any(str(mine) in w for w in resp.json()["warnings"])
    assert any("dt" in w for w in resp.json()["warnings"])
