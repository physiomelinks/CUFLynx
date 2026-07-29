"""Load a whole COMBINE archive in one drop (issue #149).

An archive is the study, not any one of its files, so it is accepted on every
import box rather than making the user unzip it and drop three files in order.
"""

from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

import omex_import
import pytest
from conftest import RESOURCES_DIR

EXAMPLE = RESOURCES_DIR / "3compartment.omex"

MANIFEST = """<?xml version="1.0"?>
<omexManifest xmlns="http://identifiers.org/combine.specifications/omex-manifest">
  <content location="./a.cellml" format="cellml"/>
  <content location="./b.cellml" format="cellml" master="true"/>
</omexManifest>
"""


def _zip(members: dict) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in members.items():
            zf.writestr(name, data)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Recognition
# ---------------------------------------------------------------------------
def test_recognises_the_extension_and_the_content():
    assert omex_import.is_omex_filename("study.omex")
    assert omex_import.is_omex_filename("STUDY.OMEX")
    assert not omex_import.is_omex_filename("model.cellml")
    # Archives get handed around as .zip too, so content decides.
    assert omex_import.looks_like_omex(_zip({"m.cellml": "<model/>"}))


def test_a_zip_with_no_model_is_not_an_archive_we_handle():
    assert not omex_import.looks_like_omex(_zip({"notes.txt": "hello"}))


def test_non_zip_input_is_rejected_cleanly():
    assert not omex_import.looks_like_omex(b"<?xml version='1.0'?><model/>")
    assert not omex_import.looks_like_omex(b"")


# ---------------------------------------------------------------------------
# Unpacking
# ---------------------------------------------------------------------------
def test_classifies_the_parts_by_name():
    data = _zip(
        {
            "m.cellml": "<model/>",
            "study_obs_data.json": "{}",
            "study_params_for_id.csv": "a,b\n",
            "module_config.json": "{}",
        }
    )
    parts = omex_import.unpack(data)
    assert list(parts["cellml"]) == ["m.cellml"]
    assert parts["obs"][0] == "study_obs_data.json"
    assert parts["params"][0] == "study_params_for_id.csv"
    assert parts["module_config"][0] == "module_config.json"


def test_module_config_is_never_mistaken_for_obs_data():
    """Both are JSON; loading PhLynx's editor state as observations would be
    nonsense."""
    parts = omex_import.unpack(_zip({"m.cellml": "<model/>", "module_config.json": "{}"}))
    assert parts["obs"] is None
    assert parts["module_config"][0] == "module_config.json"


def test_the_manifest_picks_the_master_model():
    """Which CellML is the main model is the one thing file names cannot say."""
    parts = omex_import.unpack(
        _zip({"manifest.xml": MANIFEST, "a.cellml": "<a/>", "b.cellml": "<b/>"})
    )
    assert parts["master"] == "b.cellml"
    assert list(parts["cellml"])[0] == "b.cellml"


def test_every_cellml_is_kept_for_a_non_flattened_model():
    """A model that imports sisters needs them all; the upload path flattens."""
    parts = omex_import.unpack(_zip({"main.cellml": "<a/>", "units.cellml": "<b/>"}))
    assert set(parts["cellml"]) == {"main.cellml", "units.cellml"}


def test_a_missing_or_broken_manifest_is_not_fatal():
    """Archives in the wild have wrong manifests; the contents are identifiable."""
    parts = omex_import.unpack(_zip({"manifest.xml": "not xml", "m.cellml": "<m/>"}))
    assert list(parts["cellml"]) == ["m.cellml"]


def test_an_archive_with_no_model_is_rejected_with_a_reason():
    with pytest.raises(omex_import.OmexImportError, match="no .cellml"):
        omex_import.unpack(_zip({"obs_data.json": "{}"}))


def test_an_empty_or_corrupt_archive_is_rejected():
    with pytest.raises(omex_import.OmexImportError, match="empty"):
        omex_import.unpack(_zip({}))
    with pytest.raises(omex_import.OmexImportError, match="readable"):
        omex_import.unpack(b"not a zip")


# ---------------------------------------------------------------------------
# module_config
# ---------------------------------------------------------------------------
def test_module_config_is_kept_beside_the_outputs(tmp_path):
    saved = omex_import.save_module_config(b'{"version": 1}', str(tmp_path))
    assert saved and Path(saved).name == "module_config.json"
    assert json.loads(Path(saved).read_text())["version"] == 1


def test_a_corrupt_module_config_is_not_written(tmp_path):
    """Writing a broken file under a name PhLynx will read is worse than none."""
    assert omex_import.save_module_config(b"{not json", str(tmp_path)) is None
    assert not (tmp_path / "module_config.json").exists()


def test_no_output_dir_means_no_copy(tmp_path):
    assert omex_import.save_module_config(b"{}", None) is None


# ---------------------------------------------------------------------------
# Through the route, with the real example archive
# ---------------------------------------------------------------------------
def test_the_example_archive_exists():
    """The issue asks for a 3compartment example to test with."""
    assert EXAMPLE.is_file(), "resources/3compartment.omex is missing"


def test_one_drop_loads_model_obs_and_params(client, tmp_path):
    with open(EXAMPLE, "rb") as fh:
        resp = client.post(
            "/api/omex/upload",
            params={"output_dir": str(tmp_path)},
            files={"file": (EXAMPLE.name, fh, "application/zip")},
        )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["model_id"] and body["variable_count"] > 0
    assert body["model_filename"] == "3compartment_flat.cellml"
    assert len(body["obs_data"]["data_items"]) == 6
    assert len(body["params_for_id"]["params"]) == 4
    # PhLynx's state kept, so the archive round-trips through the editor.
    assert Path(body["module_config_path"]).is_file()


def test_the_loaded_model_behaves_like_any_other(client, tmp_path):
    with open(EXAMPLE, "rb") as fh:
        body = client.post(
            "/api/omex/upload", files={"file": (EXAMPLE.name, fh, "application/zip")}
        ).json()
    assert client.get(f"/api/models/{body['model_id']}/variables").status_code == 200


def test_an_archive_with_only_a_model_still_loads(client):
    """Refusing it would be worse than loading what is there."""
    data = _zip({"m.cellml": (RESOURCES_DIR / "Lotka_Volterra_forced.cellml").read_bytes()})
    resp = client.post("/api/omex/upload", files={"file": ("m.omex", data, "application/zip")})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["model_id"]
    assert body["obs_data"] is None and body["params_for_id"] is None


def test_a_bad_part_is_reported_without_losing_the_model(client):
    data = _zip(
        {
            "m.cellml": (RESOURCES_DIR / "Lotka_Volterra_forced.cellml").read_bytes(),
            "obs_data.json": "{not json",
        }
    )
    resp = client.post("/api/omex/upload", files={"file": ("m.omex", data, "application/zip")})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["model_id"]  # the model still landed
    assert body["obs_data"]["error"]


def test_a_non_archive_is_a_422_not_a_crash(client):
    resp = client.post(
        "/api/omex/upload", files={"file": ("x.omex", b"not a zip", "application/zip")}
    )
    assert resp.status_code == 422
    assert "readable" in resp.json()["detail"]
