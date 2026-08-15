"""``GET /api/models/{id}/source``: the file the user actually wrote.

PhLynx builds CellML, so "Edit" only means PhLynx for a CellML model. An
external_python model and a Myokit model both have a source of their own, and
this route is what the button opens instead. A ``.mmt`` only has one because the
upload now keeps it: the conversion to CellML happens at the door (#27), and
until this the bytes the user dropped survived nowhere.

Unit tier — the Myokit conversion is stubbed, so nothing here needs Myokit.
"""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

import pytest

import main
import myokit_import
from conftest import BG_MODEL_PATH

PY_FIXTURE = Path(__file__).resolve().parent / "data" / "heat1d_external_model.py"
MMT = b"[[model]]\nname: tiny\n# what the user wrote\n"
CELLML = (
    b'<?xml version="1.0"?><model xmlns="http://www.cellml.org/cellml/2.0#" '
    b'name="tiny"><component name="c"><variable name="v" units="dimensionless" '
    b'initial_value="1"/></component></model>'
)


@pytest.fixture
def stub_myokit(monkeypatch):
    """Convert a .mmt without Myokit: the route's job here is what it *keeps*."""

    def fake_convert(data, filename="", out_dir=None):
        return CELLML, None

    monkeypatch.setattr(myokit_import, "cellml_from_myokit", fake_convert)
    monkeypatch.setattr(
        main,
        "_protocol_from_mmt",
        lambda data, filename, out_dir: {
            "filename": "tiny_obs_data.json",
            "obs_data": None,
            "notes": [],
            "reason": "stubbed",
        },
    )


def _zip(members: dict) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in members.items():
            zf.writestr(name, data)
    return buf.getvalue()


def upload_mmt(client):
    resp = client.post(
        "/api/models/upload", files={"file": ("tiny.mmt", MMT, "text/plain")}
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def upload_mmt_archive(client):
    data = _zip({"tiny.mmt": MMT})
    resp = client.post(
        "/api/omex/upload", files={"file": ("tiny.omex", data, "application/zip")}
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def upload_py(client):
    resp = client.post(
        "/api/models/upload",
        files={"file": ("heat1d_external_model.py", PY_FIXTURE.read_bytes(), "text/x-python")},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def upload_cellml(client):
    with open(BG_MODEL_PATH, "rb") as fh:
        resp = client.post(
            "/api/models/upload",
            files={"file": (BG_MODEL_PATH.name, fh, "application/xml")},
        )
    assert resp.status_code == 200, resp.text
    return resp.json()


# ---------------------------------------------------------------------------
# The .mmt is kept
# ---------------------------------------------------------------------------
def test_a_dropped_mmt_is_kept_beside_the_cellml_it_became(client, stub_myokit):
    body = upload_mmt(client)
    model_id = body["model_id"]
    assert body["converted_from"] == "tiny.mmt"
    # Unchanged: the converted CellML is still what every simulation path uses.
    assert (main.UPLOAD_DIR / f"{model_id}.cellml").read_bytes() == CELLML
    # New: the original, verbatim.
    assert (main.UPLOAD_DIR / f"{model_id}.mmt").read_bytes() == MMT


def test_an_archives_mmt_is_kept_too(client, stub_myokit):
    """The archive route converts the same way, so it must keep the same thing --
    it is the path that is easy to leave behind (#149)."""
    body = upload_mmt_archive(client)
    model_id = body["model_id"]
    assert body["converted_from"] == "tiny.mmt"
    assert (main.UPLOAD_DIR / f"{model_id}.cellml").read_bytes() == CELLML
    assert (main.UPLOAD_DIR / f"{model_id}.mmt").read_bytes() == MMT


def test_a_plain_cellml_upload_writes_no_source_copy(client):
    model_id = upload_cellml(client)["model_id"]
    assert not (main.UPLOAD_DIR / f"{model_id}.mmt").exists()


def test_the_model_still_recovers_as_cellml_after_the_registry_is_lost(client, stub_myokit):
    """The recovery arm reads .cellml first, so the new .mmt beside it must not
    become the file a reloaded server tries to parse as a model."""
    model_id = upload_mmt(client)["model_id"]
    main._models.pop(model_id, None)
    record = main._get_model(model_id)
    assert record.path.suffix == ".cellml"


def test_the_ttl_prune_ages_the_new_suffix_out(tmp_path):
    """A plain iterdir(), so a .mmt is pruned like everything else -- asserted
    rather than assumed, since it is a file type the prune never saw before."""
    old = tmp_path / "abc.mmt"
    old.write_bytes(MMT)
    import os

    os.utime(old, (0, 0))
    assert main.prune_upload_dir(tmp_path, ttl_days=1) == 1
    assert not old.exists()


# ---------------------------------------------------------------------------
# The route
# ---------------------------------------------------------------------------
def test_the_mmt_is_served_as_inline_text(client, stub_myokit):
    model_id = upload_mmt(client)["model_id"]
    resp = client.get(f"/api/models/{model_id}/source")
    assert resp.status_code == 200, resp.text
    assert resp.content == MMT
    assert resp.headers["content-type"].startswith("text/plain")
    # Inline: a browser tab that shows the model, not a download the user then
    # has to go and find.
    assert "inline" in resp.headers["content-disposition"]
    assert ".mmt" in resp.headers["content-disposition"]


def test_an_archives_mmt_is_served_as_well(client, stub_myokit):
    model_id = upload_mmt_archive(client)["model_id"]
    resp = client.get(f"/api/models/{model_id}/source")
    assert resp.status_code == 200, resp.text
    assert resp.content == MMT


def test_an_external_python_model_serves_its_own_py(client):
    model_id = upload_py(client)["model_id"]
    resp = client.get(f"/api/models/{model_id}/source")
    assert resp.status_code == 200, resp.text
    assert resp.content == PY_FIXTURE.read_bytes()
    assert resp.headers["content-type"].startswith("text/plain")
    assert ".py" in resp.headers["content-disposition"]


def test_a_plain_cellml_model_has_no_source_to_show(client):
    """404, not the flattened document CUFLynx generated: that is not a file the
    user has ever seen, and a CellML model is edited in PhLynx."""
    model_id = upload_cellml(client)["model_id"]
    resp = client.get(f"/api/models/{model_id}/source")
    assert resp.status_code == 404
    assert "PhLynx" in resp.json()["detail"]


def test_an_unknown_model_is_a_404(client):
    assert client.get("/api/models/deadbeef/source").status_code == 404


@pytest.mark.parametrize(
    "bad",
    [
        "..%2F..%2Fetc%2Fpasswd",
        "..",
        ".%2E%2F.mmt",
        "%2Fetc%2Fpasswd",
    ],
)
def test_a_model_id_is_never_joined_onto_a_path(client, bad):
    """The same discipline solver_plots applies: a client string is validated as
    an id before anything is joined onto a path."""
    resp = client.get(f"/api/models/{bad}/source")
    assert resp.status_code == 404


def test_the_handler_itself_rejects_a_traversing_id():
    """Asserted on the handler, not only through the URL: whether a client's
    ``..`` survives routing is the http stack's business, and the guard has to
    hold either way."""
    from fastapi import HTTPException

    assert main._model_source_path("../../etc/passwd") is None
    with pytest.raises(HTTPException) as exc:
        main.get_model_source("../../etc/passwd")
    assert exc.value.status_code == 404
