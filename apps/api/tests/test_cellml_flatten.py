"""Flattening a non-flattened / CellML 1.1 multi-file model to CellML 2.0.

Fixture: ``resources/3compartment_unflattened/`` -- the main ``3compartment.cellml``
(CellML 1.1, imports parameters/modules/units) plus its sister files. Flattening
it must reproduce the self-contained CellML 2.0 model that ``3compartment_flat.cellml``
already is.
"""

import os
import tempfile
from pathlib import Path

import pytest

import cellml_flatten as cf
import cellml_meta
from conftest import RESOURCES_DIR, upload_bundle

UNFLAT_DIR = RESOURCES_DIR / "3compartment_unflattened"
FLAT_FIXTURE = RESOURCES_DIR / "3compartment_flat.cellml"
MAIN = UNFLAT_DIR / "3compartment.cellml"


def _bundle() -> dict:
    return {p.name: p.read_bytes() for p in UNFLAT_DIR.glob("*.cellml")}


def test_pick_main_cellml_selects_the_import_root():
    """The main model imports its sisters but is imported by none, so it is the
    root of the import graph."""
    assert cf.pick_main_cellml(_bundle()) == "3compartment.cellml"
    # A single file is trivially its own main.
    assert cf.pick_main_cellml({"only.cellml": b"<model/>"}) == "only.cellml"


def test_flatten_produces_self_contained_cellml_2_0(requires_simulation):
    flat = cf.flatten_cellml(str(MAIN))
    assert "cellml/2.0" in flat  # libCellML's Printer always emits 2.0
    assert "xlink:href" not in flat  # no imports left -> self-contained
    assert "CardiovascularSystem" in flat


def test_flattened_metadata_matches_the_reference_flat_model(requires_simulation):
    """The flattened output must be equivalent to the hand-checked flat fixture:
    same states/params, so it drives the pipeline identically."""
    flat_meta = cellml_meta.parse_cellml(cf.flatten_cellml(str(MAIN)).encode())
    ref_meta = cellml_meta.parse_cellml(FLAT_FIXTURE.read_bytes())
    assert flat_meta.name == ref_meta.name == "CardiovascularSystem"
    assert flat_meta.variable_count == ref_meta.variable_count
    assert sorted(flat_meta.odes) == sorted(ref_meta.odes)
    assert sorted(flat_meta.params) == sorted(ref_meta.params)


def test_flatten_reports_missing_sister_files(requires_simulation):
    """Uploading the main model without its sisters is a clear error, not a crash
    or a silently-broken model."""
    with tempfile.TemporaryDirectory() as td:
        (Path(td) / MAIN.name).write_bytes(MAIN.read_bytes())
        with pytest.raises(cf.CellMLFlattenError):
            cf.flatten_cellml(str(Path(td) / MAIN.name), td)


# --- through the upload endpoint ---


def test_upload_unflattened_bundle_flattens_and_registers(client, requires_simulation):
    """Dragging the non-flattened model + sisters uploads a single flattened,
    self-contained CellML 2.0 model usable by the standard pipeline."""
    resp = upload_bundle(client, sorted(UNFLAT_DIR.glob("*.cellml")))
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["name"] == "CardiovascularSystem"
    assert data["variable_count"] == 456
    assert len(data["odes"]) == 27
    # what was persisted is the flattened, self-contained CellML 2.0.
    import main
    saved = (Path(main.UPLOAD_DIR) / f"{data['model_id']}.cellml").read_text()
    assert "cellml/2.0" in saved and "xlink:href" not in saved


def test_upload_bundle_missing_sisters_returns_422(client, requires_simulation):
    resp = upload_bundle(client, [MAIN])  # main only, no sisters
    assert resp.status_code == 422
    assert "import" in resp.json()["detail"].lower()


def test_upload_single_flat_file_still_saved_as_is(client):
    """Back-compat: a self-contained single file is not routed through flattening."""
    before = FLAT_FIXTURE.read_bytes()
    resp = client.post(
        "/api/models/upload",
        files={"file": (FLAT_FIXTURE.name, before, "application/xml")},
    )
    assert resp.status_code == 200
    import main
    saved = (Path(main.UPLOAD_DIR) / f"{resp.json()['model_id']}.cellml").read_bytes()
    assert saved == before  # byte-for-byte, no re-serialisation
