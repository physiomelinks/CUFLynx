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


# A PhLynx OMEX as their exporter writes it (services/compress.js): SED-ML is
# the master, the CellML is `model.cellml`, and `simulation.json` rides along as
# plain `application/json` -- exactly the format a real obs_data carries.
PHLYNX_MANIFEST = """<?xml version="1.0" encoding="UTF-8"?>
<omexManifest xmlns="http://identifiers.org/combine.specifications/omex-manifest">
  <content location="." format="http://identifiers.org/combine.specifications/omex"/>
  <content location="./manifest.xml" format="http://identifiers.org/combine.specifications/omex-manifest"/>
  <content location="./document.sedml" format="http://identifiers.org/combine.specifications/sed-ml" master="true"/>
  <content location="./model.cellml" format="http://identifiers.org/combine.specifications/cellml"/>
  <content location="./simulation.json" format="http://purl.org/NET/mediatypes/application/json"/>
  <content location="./flow.json" format="application/x.vnd.phlynx-flow+json"/>
  <content location="./changes.json" format="application/x.vnd.phlynx-changes+json"/>
</omexManifest>
"""


def _phlynx_archive(**extra) -> bytes:
    members = {
        "manifest.xml": PHLYNX_MANIFEST,
        "model.cellml": "<model/>",
        "document.sedml": "<sedML/>",
        "simulation.json": json.dumps({"plots": [], "settings": {}}),
        "flow.json": json.dumps({"id": "phlynx-flow", "nodes": []}),
        "changes.json": json.dumps({"id": "phlynx-changes", "version": "1.0.0", "modified": True}),
    }
    members.update(extra)
    return _zip(members)


def test_a_phlynx_archive_has_no_obs_data_rather_than_a_broken_one():
    """`_classify` used to take *any* leftover .json as observations, so a PhLynx
    archive imported as a parse-error banner: `simulation.json` is declared with
    the same `application/json` a real obs_data carries, and `flow.json` /
    `changes.json` were not known at all (#287/#290)."""
    parts = omex_import.unpack(_phlynx_archive())
    assert parts["obs"] is None
    assert list(parts["cellml"]) == ["model.cellml"]
    # Nothing was dropped on the way in -- an archive that cannot be re-emitted
    # verbatim is one PhLynx's own state does not survive.
    assert set(parts["members"]) == {
        "manifest.xml",
        "model.cellml",
        "document.sedml",
        "simulation.json",
        "flow.json",
        "changes.json",
    }


def test_a_real_obs_data_beside_phlynx_state_is_still_found():
    """The exclusion is of declared non-observations, not of every JSON."""
    obs = json.dumps({"protocol_info": {"sim_times": [1.0]}, "data_items": []})
    parts = omex_import.unpack(_phlynx_archive(**{"study_obs_data.json": obs}))
    assert parts["obs"][0] == "study_obs_data.json"


def test_an_unnamed_obs_data_is_recognised_by_its_contents():
    """With no manifest and no `obs` in the name, the document's own shape is
    what separates observations from a companion file."""
    parts = omex_import.unpack(
        _zip(
            {
                "m.cellml": "<model/>",
                "simulation.json": json.dumps({"plots": []}),
                "measurements.json": json.dumps([{"variable": "a/b"}]),
            }
        )
    )
    assert parts["obs"][0] == "measurements.json"


def test_a_params_for_id_json_is_params_not_obs_data():
    """CUFLynx stores params_for_id as JSON (`_save_params_file`), so an archive
    it writes has to be readable by it -- a params member falling through to the
    obs_data pool means the study comes back without its parameters."""
    parts = omex_import.unpack(
        _zip(
            {
                "m.cellml": "<model/>",
                "study_params_for_id.json": json.dumps({"parameters": []}),
                "study_obs_data.json": json.dumps([{"variable": "a/b"}]),
            }
        )
    )
    assert parts["params"][0] == "study_params_for_id.json"
    assert parts["obs"][0] == "study_obs_data.json"


def test_the_manifest_is_returned_parsed():
    parts = omex_import.unpack(_phlynx_archive())
    entries = parts["manifest"]["entries"]
    flow = [e for e in entries if e["location"].endswith("flow.json")][0]
    assert flow["format"] == "application/x.vnd.phlynx-flow+json"
    assert [e for e in entries if e["master"]][0]["location"].endswith("document.sedml")


def test_a_phlynx_archive_imports_through_the_route_without_an_error(client):
    """The user-visible half of the classify fix: no obs_data, no banner."""
    model = (RESOURCES_DIR / "Lotka_Volterra_forced.cellml").read_bytes()
    data = _phlynx_archive(**{"model.cellml": model})
    resp = client.post(
        "/api/omex/upload", files={"file": ("phlynx.omex", data, "application/zip")}
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["obs_data"] is None
    assert body["params_for_id"] is None


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


def test_module_config_lands_beside_the_model_not_among_the_outputs(client, tmp_path):
    """It is the model's editor state, not a result, so it goes into
    ``generated_models/<prefix>/`` -- the layout the export bundle uses and CA
    resolves a model path against. It used to sit at the top of the outputs
    directory, among files the user had asked a calibration to produce."""
    with open(EXAMPLE, "rb") as fh:
        resp = client.post(
            "/api/omex/upload",
            params={"output_dir": str(tmp_path)},
            files={"file": (EXAMPLE.name, fh, "application/zip")},
        )
    assert resp.status_code == 200, resp.text
    saved = Path(resp.json()["module_config_path"])
    assert saved == tmp_path / "generated_models" / "3compartment_flat" / "module_config.json"
    assert saved.is_file()
    assert not (tmp_path / "module_config.json").exists()


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


# ---------------------------------------------------------------------------
# An archive whose model is a Myokit .mmt (#27 + #149)
# ---------------------------------------------------------------------------
MMT_EXAMPLE = RESOURCES_DIR / "br-1977.omex"


def test_the_myokit_example_archive_exists():
    assert MMT_EXAMPLE.is_file(), "resources/br-1977.omex is missing"


def test_the_myokit_archive_holds_a_model_and_a_params_file():
    """No obs_data in it, deliberately: the protocol comes from the .mmt itself,
    which is the case this archive exists to cover."""
    with zipfile.ZipFile(MMT_EXAMPLE) as zf:
        names = sorted(Path(n).name for n in zf.namelist())
    assert names == ["br-1977.mmt", "br-1977_params_for_id.csv", "manifest.xml"]


def test_an_mmt_is_recognised_as_an_archives_model():
    data = _zip({"manifest.xml": MANIFEST, "model.mmt": b"[[model]]\n"})
    assert omex_import.looks_like_omex(data)
    assert omex_import.unpack(data)["master"] == "model.mmt"


def test_a_cellml_wins_when_an_archive_carries_both():
    """An archive shipping both has presumably already been converted, and the
    CellML is the copy its author chose to ship."""
    data = _zip({"model.mmt": b"[[model]]\n", "model.cellml": b"<model/>"})
    assert omex_import.unpack(data)["master"] == "model.cellml"


def test_an_archive_with_neither_says_what_it_wanted():
    data = _zip({"notes.txt": b"hello"})
    with pytest.raises(omex_import.OmexImportError, match=r"\.cellml or \.mmt"):
        omex_import.unpack(data)


@pytest.mark.integration
def test_the_myokit_archive_loads_model_and_params_in_one_drop(
    client, requires_simulation, tmp_path
):
    with open(MMT_EXAMPLE, "rb") as fh:
        resp = client.post(
            "/api/omex/upload",
            params={"output_dir": str(tmp_path)},
            files={"file": (MMT_EXAMPLE.name, fh, "application/zip")},
        )
    assert resp.status_code == 200, resp.text
    body = resp.json()

    # Converted on the way in, so the rest of the app sees CellML as usual.
    assert body["converted_from"] == "br-1977.mmt"
    assert body["model_filename"] == "br-1977.cellml"
    assert len(body["odes"]) == 8  # Beeler-Reuter: m, h, j, d, f, x1, Cai, V
    assert [p["qname"] for p in body["params_for_id"]["params"]] == ["ina/gNaBar"]


@pytest.mark.integration
def test_the_archives_protocol_comes_from_the_mmt(client, requires_simulation):
    """The archive carries no obs_data. Without this the study would load unpaced
    -- a model and a parameter to fit, and nothing driving it."""
    with open(MMT_EXAMPLE, "rb") as fh:
        body = client.post(
            "/api/omex/upload", files={"file": (MMT_EXAMPLE.name, fh, "application/zip")}
        ).json()

    obs = body["obs_data"]
    assert obs is not None and obs.get("derived_from_mmt") is True
    assert obs["filename"] == "br-1977_obs_data.json"
    protocol = obs["protocol_info"]
    assert protocol["experiment_labels"] == ["pacing, period 1000"]
    # No data_items: what to measure is not in a .mmt.
    assert obs["data_items"] == []

    # As a declared pacing shape, not as sub-experiments holding levels: the
    # archive has to produce what a dropped .mmt produces, and an expansion
    # would still run correctly while losing the period the .mmt stated.
    assert protocol["sim_times"] == [[2000.0]]
    assert protocol["params_to_change"] == {"engine/pace": [["engine_pace"]]}
    assert protocol["protocol_shapes"]["engine_pace"]["events"] == [
        {"level": 1.0, "start": 100.0, "length": 2.0, "period": 1000.0, "multiplier": 0}
    ]


@pytest.mark.integration
def test_the_archive_and_the_bare_mmt_give_the_same_protocol(client, requires_simulation):
    """Two routes into the same conversion, so they must not drift apart. The
    archive path is easy to leave behind: it has its own upload route, and this
    is the assertion that notices."""
    with open(MMT_EXAMPLE, "rb") as fh:
        from_archive = client.post(
            "/api/omex/upload", files={"file": (MMT_EXAMPLE.name, fh, "application/zip")}
        ).json()["obs_data"]["protocol_info"]

    with open(RESOURCES_DIR / "br-1977.mmt", "rb") as fh:
        from_file = client.post(
            "/api/models/upload", files={"file": ("br-1977.mmt", fh, "text/plain")}
        ).json()["protocol_obs_data"]["obs_data"]["protocol_info"]

    assert from_archive == from_file


@pytest.mark.integration
def test_an_obs_data_in_the_archive_beats_the_mmts_own_protocol(client, requires_simulation):
    """The author's file is the author's intent; a derived protocol must not
    displace one they shipped."""
    mine = {
        "protocol_info": {
            "pre_times": [0.0],
            "sim_times": [[500.0]],
            "params_to_change": {"engine/pace": [[0.0]]},
            "experiment_labels": ["mine"],
        },
        "data_items": [],
    }
    with zipfile.ZipFile(MMT_EXAMPLE) as src:
        members = {n: src.read(n) for n in src.namelist()}
    members["br-1977_obs_data.json"] = json.dumps(mine).encode()

    resp = client.post(
        "/api/omex/upload", files={"file": ("mixed.omex", _zip(members), "application/zip")}
    )
    assert resp.status_code == 200, resp.text
    obs = resp.json()["obs_data"]
    assert obs.get("derived_from_mmt") is not True
    assert obs["protocol_info"]["experiment_labels"] == ["mine"]


@pytest.mark.integration
def test_the_whole_archive_simulates_as_dropped(client, requires_simulation):
    """The point of the archive: drop it, and the study runs. Two stimuli in the
    .mmt's protocol, so two action potentials."""
    with open(MMT_EXAMPLE, "rb") as fh:
        body = client.post(
            "/api/omex/upload", files={"file": (MMT_EXAMPLE.name, fh, "application/zip")}
        ).json()

    resp = client.post(
        "/api/protocol/run",
        json={"model_id": body["model_id"], "params": {}, "outputs": ["membrane/V"]},
    )
    assert resp.status_code == 200, resp.text
    exp = resp.json()["experiments"][0]
    v = exp["outputs"]["membrane/V"]
    assert exp["time"][-1] == pytest.approx(2000, abs=1)
    assert sum(1 for a, b in zip(v, v[1:]) if a <= 0 < b) == 2


@pytest.mark.integration
def test_the_calibration_parameter_from_the_archive_moves_the_model(client, requires_simulation):
    """params_for_id and the protocol have to reach the same model: a parameter
    loaded against a model the protocol does not drive would look fine and fit
    nothing."""
    with open(MMT_EXAMPLE, "rb") as fh:
        body = client.post(
            "/api/omex/upload", files={"file": (MMT_EXAMPLE.name, fh, "application/zip")}
        ).json()
    model_id = body["model_id"]

    def peak(gna):
        r = client.post(
            "/api/protocol/run",
            json={
                "model_id": model_id,
                "params": {"ina/gNaBar": gna},
                "outputs": ["membrane/V"],
            },
        )
        assert r.status_code == 200, r.text
        return max(r.json()["experiments"][0]["outputs"]["membrane/V"])

    assert peak(1.0) != pytest.approx(peak(8.0), abs=1e-3)
