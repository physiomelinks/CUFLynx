"""Accept a Myokit model by converting it to CellML on the way in (issue #27).

Everything downstream assumes CellML -- the metadata parser, params_for_id's
`component/variable` naming, the exported pipeline, CA itself -- so a dropped
.mmt is converted once at the door and the rest of the app never knows.
"""

from __future__ import annotations

import glob
import os
from pathlib import Path

import myokit_import
import pytest

MMT = b"""[[model]]
name: tiny
membrane.V = -80

[membrane]
time = 0 bind time
dot(V) = 0.1
"""


# ---------------------------------------------------------------------------
# Recognition
# ---------------------------------------------------------------------------
def test_recognises_the_extension():
    assert myokit_import.is_myokit_filename("model.mmt")
    assert myokit_import.is_myokit_filename("MODEL.MMT")
    assert not myokit_import.is_myokit_filename("model.cellml")


def test_recognises_the_content_whatever_it_is_called():
    """A model dropped with the wrong name should still work."""
    assert myokit_import.looks_like_myokit(MMT)


def test_xml_is_never_taken_for_a_myokit_model():
    """An .mmt-named XML file must not reach the Myokit parser."""
    assert not myokit_import.looks_like_myokit(b'<?xml version="1.0"?><model/>')
    assert not myokit_import.looks_like_myokit(b"   <model/>")


def test_arbitrary_text_is_not_a_model():
    assert not myokit_import.looks_like_myokit(b"just some notes about a model")
    assert not myokit_import.looks_like_myokit(b"")


# ---------------------------------------------------------------------------
# Conversion
# ---------------------------------------------------------------------------
@pytest.mark.integration
def test_converts_a_real_myokit_model_to_cellml(requires_simulation, tmp_path):
    import myokit

    src = sorted(
        glob.glob(os.path.join(os.path.dirname(myokit.__file__), "**", "*.mmt"), recursive=True)
    )
    if not src:
        pytest.skip("no myokit sample models available")
    data = Path(src[0]).read_bytes()

    cellml, saved = myokit_import.cellml_from_myokit(
        data, filename=Path(src[0]).name, out_dir=str(tmp_path)
    )
    assert cellml.lstrip().startswith(b"<?xml")
    assert b"cellml" in cellml[:400].lower()
    # Kept for the user, so the conversion is inspectable and re-importable.
    assert saved and Path(saved).is_file()
    assert Path(saved).suffix == ".cellml"


@pytest.mark.integration
def test_the_converted_model_parses_as_cellml(requires_simulation, tmp_path):
    """The point of converting is that the rest of the pipeline can read it."""
    from cellml_meta import parse_cellml

    cellml, _ = myokit_import.cellml_from_myokit(MMT, filename="tiny.mmt", out_dir=str(tmp_path))
    meta = parse_cellml(cellml)
    assert meta.variable_count > 0


@pytest.mark.integration
def test_conversion_still_returns_without_an_output_dir(requires_simulation):
    """Keeping a copy is a convenience, not a precondition."""
    cellml, saved = myokit_import.cellml_from_myokit(MMT, filename="tiny.mmt", out_dir=None)
    assert cellml
    assert saved is None


@pytest.mark.integration
def test_an_unwritable_output_dir_does_not_fail_the_conversion(requires_simulation, tmp_path):
    import stat

    ro = tmp_path / "ro"
    ro.mkdir()
    ro.chmod(stat.S_IRUSR | stat.S_IXUSR)
    try:
        cellml, saved = myokit_import.cellml_from_myokit(MMT, filename="t.mmt", out_dir=str(ro))
        assert cellml
        assert saved is None  # copy skipped, model still imported
    finally:
        ro.chmod(stat.S_IRWXU)


def test_a_file_with_no_model_section_is_rejected_clearly(tmp_path):
    pytest.importorskip("myokit")
    with pytest.raises(myokit_import.MyokitImportError):
        myokit_import.cellml_from_myokit(b"[[protocol]]\n", filename="x.mmt", out_dir=None)


def test_unreadable_myokit_source_is_a_clear_error():
    pytest.importorskip("myokit")
    with pytest.raises(myokit_import.MyokitImportError, match="could not read"):
        myokit_import.cellml_from_myokit(b"[[model]]\n!!! nonsense", filename="x.mmt", out_dir=None)


# ---------------------------------------------------------------------------
# Through the upload route
# ---------------------------------------------------------------------------
@pytest.mark.integration
def test_dropping_a_myokit_model_yields_a_usable_model(client, requires_simulation, tmp_path):
    resp = client.post(
        "/api/models/upload",
        params={"output_dir": str(tmp_path)},
        files={"file": ("tiny.mmt", MMT, "text/plain")},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["model_id"]
    assert body["variable_count"] > 0
    # The UI needs to be able to say the model is not the file that was dropped.
    assert body["converted_from"] == "tiny.mmt"
    assert body["converted_cellml_path"].endswith("tiny.cellml")

    # ...and the model behaves like any other from here on.
    variables = client.get(f"/api/models/{body['model_id']}/variables")
    assert variables.status_code == 200


def test_a_cellml_upload_is_untouched_by_the_myokit_path(client):
    from conftest import LV_MODEL_PATH, upload_model

    body = upload_model(client, LV_MODEL_PATH)
    assert body["converted_from"] is None
    assert body["converted_cellml_path"] is None


# ---------------------------------------------------------------------------
# The shipped .mmt fixture
# ---------------------------------------------------------------------------
MMT_FIXTURE = None  # resolved lazily so a CA-less run can still collect


def _fixture_paths():
    from conftest import RESOURCES_DIR

    return (
        RESOURCES_DIR / "3compartment.mmt",
        RESOURCES_DIR / "3compartment_mmt_params_for_id.csv",
        RESOURCES_DIR / "3compartment_mmt_obs_data.json",
    )


def test_the_mmt_fixture_and_its_companions_exist():
    for path in _fixture_paths():
        assert path.is_file(), f"missing fixture: {path}"


def test_the_mmt_fixture_is_our_own_model_not_a_vendored_one():
    """It is derived from this repo's 3compartment_flat.cellml, so it carries our
    licence. Myokit's own sample models embed third-party (GPL) notices that
    would not be compatible with this repository's Apache-2.0 licence."""
    mmt, _params, _obs = _fixture_paths()
    text = mmt.read_text()
    assert "3compartment_flat.cellml" in text
    assert "GNU General Public License" not in text


def test_the_obs_fixture_is_the_data_only_shape():
    """A bare list, which is what "no protocol" means -- the dict form requires
    protocol_info and would be rejected."""
    import json

    _mmt, _params, obs = _fixture_paths()
    items = json.loads(obs.read_text())
    assert isinstance(items, list) and items
    assert all("operands" in item for item in items)


@pytest.mark.integration
def test_the_fixture_set_loads_and_runs_together(client, requires_simulation, tmp_path):
    """Dropping the .mmt gives the same model as the CellML, and the companion
    params / obs attach to it."""
    import json

    mmt, params, obs = _fixture_paths()
    with open(mmt, "rb") as fh:
        body = client.post(
            "/api/models/upload",
            params={"output_dir": str(tmp_path)},
            files={"file": (mmt.name, fh, "text/plain")},
        ).json()
    model_id = body["model_id"]
    assert body["converted_from"] == "3compartment.mmt"
    # Same model as the CellML fixture: 27 states.
    assert len(body["odes"]) == 27

    with open(params, "rb") as fh:
        r = client.post(
            "/api/params_for_id/upload",
            params={"model_id": model_id},
            files={"file": (params.name, fh, "text/csv")},
        )
    assert r.status_code == 200, r.text
    assert [p["qname"] for p in r.json()["params"]] == ["aortic_root/C", "global/E_lv_A"]

    r = client.post(
        "/api/obs_data/upload",
        json={"model_id": model_id, "obs_data": json.loads(obs.read_text())},
    )
    assert r.status_code == 200, r.text
    assert len(r.json()["data_items"]) == 2
    assert r.json()["has_protocol"] is False

    r = client.post(
        "/api/simulate",
        json={
            "model_id": model_id,
            "params": {},
            "sim_time": 2.0,
            "pre_time": 5.0,
            "outputs": ["aortic_root/u", "aortic_root/v"],
        },
    )
    assert r.status_code == 200, r.text
    assert len(r.json()["time"]) > 0
