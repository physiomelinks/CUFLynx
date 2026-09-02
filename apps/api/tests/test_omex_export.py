"""Send the study back out as a COMBINE archive, verbatim where it must be (#290).

The contract #287 fixes is asymmetric: CUFLynx owns the model's constants and
nothing else in the archive. Everything here is a way of pinning "and nothing
else" -- PhLynx's `flow.json` / `changes.json`, its SED-ML and `simulation.json`,
and any member CUFLynx has never heard of, all come back byte-for-byte.
"""

from __future__ import annotations

import base64
import io
import json
import zipfile
from pathlib import Path

import omex_export
import omex_import
import pytest
from conftest import RESOURCES_DIR

EXAMPLE = RESOURCES_DIR / "3compartment.omex"
FLAT_MODEL = RESOURCES_DIR / "3compartment_flat.cellml"

PHLYNX_MANIFEST = """<?xml version="1.0" encoding="UTF-8"?>
<omexManifest xmlns="http://identifiers.org/combine.specifications/omex-manifest">
  <content location="." format="http://identifiers.org/combine.specifications/omex"/>
  <content location="./manifest.xml" format="http://identifiers.org/combine.specifications/omex-manifest"/>
  <content location="./document.sedml" format="http://identifiers.org/combine.specifications/sed-ml" master="true"/>
  <content location="./model.cellml" format="http://identifiers.org/combine.specifications/cellml"/>
  <content location="./simulation.json" format="http://purl.org/NET/mediatypes/application/json"/>
</omexManifest>
"""

FLOW_JSON = json.dumps({"id": "phlynx-flow", "version": "1.0.0", "nodes": [{"id": "heart"}]})
CHANGES_JSON = json.dumps({"id": "phlynx-changes", "version": "1.0.0", "modified": True})
MODULE_CONFIG_JSON = json.dumps({"version": 1, "source": "PhLynx", "modules": []})


def _zip(members: dict) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in members.items():
            zf.writestr(name, data)
    return buf.getvalue()


def _read(blob: bytes) -> dict:
    with zipfile.ZipFile(io.BytesIO(blob)) as zf:
        return {n: zf.read(n) for n in zf.namelist()}


def _manifest_entries(blob: bytes) -> list[dict]:
    with zipfile.ZipFile(io.BytesIO(blob)) as zf:
        return omex_import.read_manifest(zf)["entries"]


def _phlynx_archive(**extra) -> bytes:
    members = {
        "manifest.xml": PHLYNX_MANIFEST,
        "model.cellml": FLAT_MODEL.read_bytes(),
        "document.sedml": "<sedML/>",
        "simulation.json": json.dumps({"plots": [], "settings": {"dt": 0.01}}),
    }
    members.update(extra)
    return _zip(members)


# ---------------------------------------------------------------------------
# Everything CUFLynx did not author comes back untouched
# ---------------------------------------------------------------------------
def test_unknown_members_are_returned_byte_for_byte():
    """The whole point of keeping the source archive: a member CUFLynx has never
    heard of is not a member it may drop."""
    src = _phlynx_archive(**{"notes.txt": b"hand written", "annotations.ttl": b"@prefix x: ."})
    out, _report = omex_export.build_archive(
        cellml_text=FLAT_MODEL.read_text(), source_archive=src
    )
    members = _read(out)
    original = _read(src)
    for name in ("document.sedml", "simulation.json", "notes.txt", "annotations.ttl"):
        assert members[name] == original[name], name


def test_the_manifest_keeps_every_original_entry():
    src = _phlynx_archive()
    out, _ = omex_export.build_archive(cellml_text=FLAT_MODEL.read_text(), source_archive=src)
    by_loc = {Path(e["location"]).name: e for e in _manifest_entries(out)}
    assert by_loc["simulation.json"]["format"] == "http://purl.org/NET/mediatypes/application/json"
    # PhLynx marks its SED-ML master, not its CellML. With one CellML in the
    # archive that file is used regardless (#287 answer 4), so rewriting the flag
    # would edit a member we promised to leave alone in order to restate
    # something already unambiguous.
    assert by_loc["document.sedml"]["master"] is True
    assert by_loc["model.cellml"]["master"] is False


@pytest.mark.parametrize(
    "state",
    [
        pytest.param({}, id="neither"),
        pytest.param({"flow.json": FLOW_JSON}, id="flow-only"),
        pytest.param({"module_config.json": MODULE_CONFIG_JSON}, id="module-config-only"),
        pytest.param(
            {"flow.json": FLOW_JSON, "module_config.json": MODULE_CONFIG_JSON}, id="both"
        ),
    ],
)
def test_every_editor_state_combination_round_trips(state):
    """#287 has not settled which of `flow.json` / `module_config.json` is
    authoritative. Carrying whichever arrived, and inventing neither, is what
    lets CUFLynx not have to care."""
    src = _phlynx_archive(**state, **{"changes.json": CHANGES_JSON})
    out, _ = omex_export.build_archive(cellml_text=FLAT_MODEL.read_text(), source_archive=src)
    members = _read(out)
    original = _read(src)

    for name in state:
        assert members[name] == original[name], f"{name} not returned verbatim"
    for absent in {"flow.json", "module_config.json"} - set(state):
        assert absent not in members, f"{absent} was fabricated"

    # `changes.json` is PhLynx's outgoing flag, which it does not consult on
    # import. It travels unread: CUFLynx never authors one and never flips it.
    assert members["changes.json"] == original["changes.json"]
    assert json.loads(members["changes.json"])["modified"] is True


# ---------------------------------------------------------------------------
# The two members CUFLynx does replace
# ---------------------------------------------------------------------------
def test_the_model_carries_the_current_values():
    src = _phlynx_archive()
    out, report = omex_export.build_archive(
        cellml_text=FLAT_MODEL.read_text(),
        values={"aortic_root/C": 9.99e-09, "global/E_lv_A": 3.33e8},
        source_archive=src,
    )
    from cellml_meta import parse_cellml

    iv = parse_cellml(_read(out)["model.cellml"]).initial_values
    assert iv["parameters/C_aortic_root"] == pytest.approx(9.99e-09)
    assert iv["parameters_global/E_lv_A"] == pytest.approx(3.33e8)
    assert report["unresolved"] == []
    assert report["outside_parameters"] == []


def test_a_value_that_cannot_be_written_is_named():
    _out, report = omex_export.build_archive(
        cellml_text=FLAT_MODEL.read_text(),
        values={"ghost/param": 1.0},
        source_archive=_phlynx_archive(),
    )
    assert report["unresolved"] == ["ghost/param"]


def test_a_value_written_outside_the_parameter_components_is_flagged():
    """Per #287 PhLynx reads changes back out of `parameters` /
    `parameters_global` only, so a value landing anywhere else travels and is
    then ignored -- which the user has to be told rather than discover."""
    model = """<?xml version="1.0"?>
<model xmlns="http://www.cellml.org/cellml/1.1#" name="m">
  <component name="heart">
    <variable name="E_es" initial_value="1.0" units="dimensionless"/>
  </component>
</model>
"""
    _out, report = omex_export.build_archive(
        cellml_text=model, values={"heart/E_es": 4.2}, source_archive=None
    )
    assert report["outside_parameters"] == ["heart/E_es -> heart/E_es"]


def test_params_for_id_is_refreshed_but_obs_data_is_not_replaced():
    """Range and selection edits are edits to the study and belong in what goes
    back. Observations are the author's ground truth and are not CUFLynx's to
    rewrite -- the first draft of #290 refreshed both, and it should not."""
    obs = json.dumps({"protocol_info": {"sim_times": [1.0]}, "data_items": []}).encode()
    src = _phlynx_archive(
        **{"study_obs_data.json": obs, "study_params_for_id.csv": b"vessel_name,param_name\n"}
    )
    out, _ = omex_export.build_archive(
        cellml_text=FLAT_MODEL.read_text(),
        source_archive=src,
        obs_bytes=b'{"protocol_info": {"sim_times": [99.0]}, "data_items": []}',
        obs_name="study_obs_data.json",
        params_bytes=b'{"parameters": [{"vessel_name": "aortic_root"}]}',
        params_name="study_params_for_id.json",
    )
    members = _read(out)
    assert members["study_obs_data.json"] == obs
    assert json.loads(members["study_params_for_id.json"])["parameters"]
    # The CSV the archive arrived with is retired rather than left beside the
    # JSON: two params files disagreeing about which is current is exactly what
    # `_save_params_file` avoids inside the uploads dir.
    assert "study_params_for_id.csv" not in members
    locations = [Path(e["location"]).name for e in _manifest_entries(out)]
    assert "study_params_for_id.csv" not in locations
    assert "study_params_for_id.json" in locations


def test_obs_data_is_added_when_the_archive_has_none():
    """Adding what is missing is not replacing what is there."""
    out, _ = omex_export.build_archive(
        cellml_text=FLAT_MODEL.read_text(),
        source_archive=_phlynx_archive(),
        obs_bytes=b"[]",
        obs_name="study_obs_data.json",
    )
    assert _read(out)["study_obs_data.json"] == b"[]"


def test_only_the_flattened_model_leaves_a_multi_cellml_archive():
    """CUFLynx's model is the flattened document that subsumes the imports;
    shipping both would hand PhLynx the same definitions twice."""
    src = _zip(
        {
            "manifest.xml": PHLYNX_MANIFEST,
            "model.cellml": FLAT_MODEL.read_bytes(),
            "units.cellml": b"<units/>",
        }
    )
    out, report = omex_export.build_archive(
        cellml_text=FLAT_MODEL.read_text(), source_archive=src
    )
    members = _read(out)
    assert "units.cellml" not in members
    assert report["model_name"] == "model.cellml"


# ---------------------------------------------------------------------------
# No source archive at all
# ---------------------------------------------------------------------------
def test_a_study_with_no_archive_gets_a_minimal_one():
    """A study assembled from three dropped files is still sendable; refusing it
    would disable the button for exactly the users who never had an archive."""
    out, report = omex_export.build_archive(
        cellml_text=FLAT_MODEL.read_text(),
        obs_bytes=b"[]",
        obs_name="heart_obs_data.json",
        params_bytes=b'{"parameters": []}',
        params_name="heart_params_for_id.json",
    )
    members = _read(out)
    assert set(members) == {
        "manifest.xml",
        "model.cellml",
        "heart_obs_data.json",
        "heart_params_for_id.json",
    }
    # No editor state is invented: CUFLynx has none, and a fabricated one would
    # be a workspace the user never built.
    assert "flow.json" not in members
    assert report["model_name"] == omex_export.DEFAULT_MODEL_NAME
    # Nobody else has claimed master here, so the model takes it -- and it is the
    # name PhLynx's URL loader looks up.
    master = [e for e in _manifest_entries(out) if e["master"]]
    assert [Path(e["location"]).name for e in master] == ["model.cellml"]


def test_a_corrupt_stored_archive_is_reported_not_swallowed():
    with pytest.raises(omex_export.OmexExportError, match="could not be read"):
        omex_export.build_archive(cellml_text="<model/>", source_archive=b"not a zip")


# ---------------------------------------------------------------------------
# The loop closes: what CUFLynx writes, CUFLynx reads
# ---------------------------------------------------------------------------
def test_what_is_written_re_imports_as_the_same_study():
    src = EXAMPLE.read_bytes()
    parts = omex_import.unpack(src)
    out, _ = omex_export.build_archive(
        cellml_text=parts["members"]["3compartment_flat.cellml"].decode(),
        source_archive=src,
        params_bytes=b'{"parameters": [{"vessel_name": "aortic_root", "param_name": "C"}]}',
        params_name="3compartment_params_for_id.json",
    )
    back = omex_import.unpack(out)
    assert list(back["cellml"]) == ["3compartment_flat.cellml"]
    assert back["obs"][0] == "3compartment_obs_data.json"
    # A params_for_id JSON has to be found as params rather than falling through
    # to the obs_data pool, or the study comes back without its parameters.
    assert back["params"][0] == "3compartment_params_for_id.json"
    assert back["module_config"][0] == "module_config.json"


# ---------------------------------------------------------------------------
# Through the route
# ---------------------------------------------------------------------------
def _upload_example(client) -> str:
    with open(EXAMPLE, "rb") as fh:
        resp = client.post(
            "/api/omex/upload", files={"file": (EXAMPLE.name, fh, "application/zip")}
        )
    assert resp.status_code == 200, resp.text
    return resp.json()["model_id"]


def test_the_send_route_returns_a_loadable_archive(client):
    model_id = _upload_example(client)
    resp = client.post(
        "/api/phlynx/send",
        json={"model_id": model_id, "source": "current", "values": {"aortic_root/C": 1e-8}},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    import base64

    blob = base64.b64decode(body["base64"])
    members = _read(blob)
    assert body["member_count"] == len(body["members"])
    assert body["too_large"] is False
    # PhLynx's own state survived the round trip through the server.
    assert "module_config.json" in members
    from cellml_meta import parse_cellml

    iv = parse_cellml(members["3compartment_flat.cellml"]).initial_values
    assert iv["parameters/C_aortic_root"] == pytest.approx(1e-8)


def test_as_imported_writes_nothing_into_the_model(client):
    model_id = _upload_example(client)
    resp = client.post("/api/phlynx/send", json={"model_id": model_id, "source": "as_imported"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["updated"] == []
    import base64

    members = _read(base64.b64decode(resp.json()["base64"]))
    assert members["3compartment_flat.cellml"] == _read(EXAMPLE.read_bytes())[
        "3compartment_flat.cellml"
    ]


def test_best_fit_needs_a_run_to_read(client, tmp_path):
    model_id = _upload_example(client)
    resp = client.post(
        "/api/phlynx/send",
        json={"model_id": model_id, "source": "best_fit", "output_dir": str(tmp_path)},
    )
    assert resp.status_code == 422
    assert "best fit" in resp.json()["detail"]


def test_an_unknown_source_is_refused(client):
    model_id = _upload_example(client)
    resp = client.post("/api/phlynx/send", json={"model_id": model_id, "source": "guess"})
    assert resp.status_code == 422


def test_every_send_offers_the_archive_as_a_downloadable_url(client):
    """The download is a plain GET so the frontend can use a real `<a download>`.

    It replaced a `download: true` variant of the POST that handed back a blob
    for the browser to save by script. That could not work in the packaged app:
    pywebview's macOS download path cancels the navigation and re-fetches the URL
    with `NSURLSession`, which cannot read a `blob:` URL (#340). And it is
    offered on *every* send, not only an over-long one, because the packaged
    macOS link path has its own unmeasured URL ceiling.
    """
    model_id = _upload_example(client)
    sent = client.post(
        "/api/phlynx/send", json={"model_id": model_id, "source": "as_imported"}
    )
    assert sent.status_code == 200, sent.text
    url = sent.json()["download_url"]
    assert url.startswith("/api/phlynx/archive/")
    assert url.endswith(".omex")

    resp = client.get(url)
    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"] == "application/zip"
    assert ".omex" in resp.headers["content-disposition"]
    assert zipfile.is_zipfile(io.BytesIO(resp.content))
    # The same bytes the link form carries, not a second build of the study.
    assert resp.content == base64.b64decode(sent.json()["base64"])


def test_an_unknown_archive_token_is_a_404_not_a_traversal(client):
    """The filename in the URL is decoration: what is served is the name recorded
    when the archive was built, so a crafted segment has nothing to reach."""
    assert client.get("/api/phlynx/archive/nope/x.omex").status_code == 404
    assert client.get("/api/phlynx/archive/..%2f..%2fetc/passwd").status_code in (404, 422)


def test_a_study_that_never_came_from_an_archive_can_still_be_sent(client):
    """The Edit button is never disabled -- a dropped CellML is a study too."""
    model = (RESOURCES_DIR / "Lotka_Volterra_forced.cellml").read_bytes()
    up = client.post(
        "/api/models/upload", files={"file": ("lv.cellml", model, "application/xml")}
    )
    assert up.status_code == 200, up.text
    resp = client.post(
        "/api/phlynx/send", json={"model_id": up.json()["model_id"], "source": "as_imported"}
    )
    assert resp.status_code == 200, resp.text
    import base64

    members = _read(base64.b64decode(resp.json()["base64"]))
    assert set(members) == {"manifest.xml", "model.cellml"}


def test_the_archive_survives_losing_the_in_memory_registry(client):
    """A dev-server reload wipes `_models`; the study is re-derived from the
    uploads dir. Without the archive coming back with it the send would silently
    degrade to a synthesised archive and drop PhLynx's editor state."""
    import main as main_mod

    model_id = _upload_example(client)
    main_mod._models.clear()

    record = main_mod._get_model(model_id)
    assert record.archive_path is not None and record.archive_path.is_file()

    resp = client.post("/api/phlynx/send", json={"model_id": model_id, "source": "as_imported"})
    assert resp.status_code == 200, resp.text
    import base64

    assert "module_config.json" in _read(base64.b64decode(resp.json()["base64"]))
