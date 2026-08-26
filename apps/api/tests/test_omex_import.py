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
    client, requires_simulation, requires_myokit_parser, tmp_path
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
def test_the_archives_protocol_comes_from_the_mmt(
    client, requires_simulation, requires_myokit_parser
):
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
def test_the_archive_and_the_bare_mmt_give_the_same_protocol(
    client, requires_simulation, requires_myokit_parser
):
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
def test_an_obs_data_in_the_archive_beats_the_mmts_own_protocol(
    client, requires_simulation, requires_myokit_parser
):
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
def test_the_whole_archive_simulates_as_dropped(
    client, requires_simulation, requires_myokit_parser
):
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
def test_the_calibration_parameter_from_the_archive_moves_the_model(
    client, requires_simulation, requires_myokit_parser
):
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


# ---------------------------------------------------------------------------
# Nothing loads quietly
#
# Every way an import can come up short of a whole study, and the sentence the
# user gets for it. The failure that prompted these: a pre-#466 obs_data in the
# archive was read, rejected by CA, and reported as a clause on the end of a
# blue "Loaded 3compartment.omex" -- so the study looked loaded and the
# observations tab was simply empty. An empty tab must never be the only thing
# that says something went wrong.
# ---------------------------------------------------------------------------
def _model_bytes() -> bytes:
    return (RESOURCES_DIR / "Lotka_Volterra_forced.cellml").read_bytes()


def _example_obs_data() -> dict:
    """The shipped example's obs_data, read out of the archive it travels in."""
    with zipfile.ZipFile(EXAMPLE) as zf:
        name = next(n for n in zf.namelist() if n.endswith("_obs_data.json"))
        return json.loads(zf.read(name))


def _upload(client, members: dict, **params):
    return client.post(
        "/api/omex/upload",
        params=params,
        files={"file": ("study.omex", _zip(members), "application/zip")},
    )


def test_a_json_that_could_have_been_the_obs_data_is_named_with_the_reason(client):
    """The quiet case: a member that is neither named `obs` nor shaped like an
    obs_data is passed over by the sniff, and used to leave no trace at all."""
    resp = _upload(client, {"m.cellml": _model_bytes(),
                            "measurements.json": json.dumps({"readings": [1, 2, 3]})})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["model_id"]  # the model still landed
    warning = " ".join(body["warnings"])
    assert "measurements.json" in warning
    assert "data_items" in warning and "protocol_info" in warning


def test_an_unparseable_json_beside_the_model_is_reported_not_ignored(client):
    resp = _upload(client, {"m.cellml": _model_bytes(), "study.json": "{not json"})
    assert resp.status_code == 200, resp.text
    warning = " ".join(resp.json()["warnings"])
    assert "study.json" in warning and "not valid JSON" in warning


def test_an_empty_json_member_says_it_is_empty(client):
    resp = _upload(client, {"m.cellml": _model_bytes(), "study.json": ""})
    assert resp.status_code == 200, resp.text
    assert "empty" in " ".join(resp.json()["warnings"])


def test_an_archive_carrying_no_observations_at_all_says_so(client):
    """Valid, and it must still load (#149) -- but the reason the observations
    tab is empty is "there were none", which is worth one sentence."""
    resp = _upload(client, {"m.cellml": _model_bytes()})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["obs_data"] is None
    assert "carries no obs_data" in " ".join(body["warnings"])


def test_a_member_the_manifest_rules_out_is_reported_as_ruled_out(client):
    """Excluded by its declared format rather than by its contents, so the
    reason has to name the manifest -- the file itself looks fine."""
    resp = _upload(
        client,
        {
            "manifest.xml": PHLYNX_MANIFEST,
            "model.cellml": _model_bytes(),
            "flow.json": json.dumps({"id": "phlynx-flow", "nodes": []}),
        },
    )
    assert resp.status_code == 200, resp.text
    warning = " ".join(resp.json()["warnings"])
    assert "flow.json" in warning and "manifest declares it" in warning


def test_a_rejected_obs_data_is_a_failure_not_a_quiet_omission(client, requires_ca):
    """The shape of the bug this section exists for: the member was found and
    read, and the study still has to come back saying it was not loaded.

    A typo'd key is CA's to catch, so this needs one. The structural refusals
    below are CUFLynx's own and hold with no CA at all."""
    obs = json.dumps({"protocol_info": {"pre_times": [0.0], "sim_times": [[1.0]]},
                      "data_items": [{"data_item_name": "x", "opperation": "max"}]})
    resp = _upload(client, {"m.cellml": _model_bytes(), "obs_data.json": obs})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["obs_data"]["error"]
    assert body["obs_data"]["filename"] == "obs_data.json"
    # Not half-loaded: nothing claims there are data_items when there are none.
    assert "data_items" not in body["obs_data"]


def test_an_obs_data_out_of_range_of_its_protocol_names_the_item(client):
    """A structural refusal from CUFLynx's own checks, which name the index --
    those messages must survive the archive path, not be flattened into "bad"."""
    obs = json.dumps(
        {
            "protocol_info": {"pre_times": [0.0], "sim_times": [[1.0]]},
            "data_items": [{"data_item_name": "x", "experiment_idx": 4}],
        }
    )
    resp = _upload(client, {"m.cellml": _model_bytes(), "obs_data.json": obs})
    body = resp.json()
    assert "experiment_idx 4 out of range" in body["obs_data"]["error"]
    assert "data_items[0]" in body["obs_data"]["error"]


def test_a_series_without_its_obs_dt_is_refused_with_the_missing_key(client):
    obs = json.dumps(
        {
            "protocol_info": {"pre_times": [0.0], "sim_times": [[1.0]]},
            "data_items": [{"data_item_name": "x", "data_type": "series"}],
        }
    )
    resp = _upload(client, {"m.cellml": _model_bytes(), "obs_data.json": obs})
    assert "obs_dt is required" in resp.json()["obs_data"]["error"]


def test_a_params_for_id_that_cannot_be_read_is_reported_beside_a_loaded_obs(client):
    """One bad part does not take the others down, and does not hide either."""
    obs = json.dumps({"protocol_info": {"pre_times": [0.0], "sim_times": [[1.0]]},
                      "data_items": []})
    resp = _upload(
        client,
        {"m.cellml": _model_bytes(), "obs_data.json": obs, "params_for_id.csv": "not,a,params\n"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["params_for_id"]["error"]
    assert body["obs_data"].get("error") is None
    assert body["obs_data"]["n_data_items"] == 0


def test_phlynx_state_that_could_not_be_kept_is_reported(client, tmp_path):
    """It is the study's layout in the sibling editor, and losing it silently
    means the archive stops round-tripping with no one the wiser (#149)."""
    resp = _upload(
        client,
        {"m.cellml": _model_bytes(), "module_config.json": "{not json"},
        output_dir=str(tmp_path),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["module_config_path"] is None
    assert "module_config.json" in " ".join(body["warnings"])


def test_no_outputs_directory_is_not_reported_as_a_loss(client):
    """The copy beside the model is a convenience: the archive itself is kept
    whole and `omex_export` re-emits PhLynx's members verbatim, so nothing is
    actually lost. Warning here would put a banner on every import until an
    outputs directory is set -- which is how the real one stops being read."""
    resp = _upload(client, {"m.cellml": _model_bytes(), "module_config.json": "{}"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["module_config_path"] is None
    assert not any("module_config" in w for w in body["warnings"]), body["warnings"]


def test_a_study_that_loaded_whole_warns_about_nothing(client, tmp_path, requires_ca):
    """The other half of the contract: warnings that cry wolf get ignored, so a
    complete archive has to come back with an empty list.

    Needs CA, because without one the honest answer is not silence -- it is the
    "nobody checked this" warning that this whole change exists to produce."""
    with open(EXAMPLE, "rb") as fh:
        resp = client.post(
            "/api/omex/upload",
            params={"output_dir": str(tmp_path)},
            files={"file": (EXAMPLE.name, fh, "application/zip")},
        )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["obs_data"]["data_items"] and body["params_for_id"]["params"]
    assert body["warnings"] == []


def test_the_reasons_are_carried_out_of_unpack_for_every_passed_over_member():
    """Reported per member, so an archive with two candidates does not have one
    of them silently stand for both."""
    parts = omex_import.unpack(
        _zip(
            {
                "m.cellml": "<model/>",
                "a.json": "{not json",
                "b.json": json.dumps({"settings": {}}),
            }
        )
    )
    assert parts["obs"] is None
    by_name = {s["name"]: s["reason"] for s in parts["obs_skipped"]}
    assert set(by_name) == {"a.json", "b.json"}
    assert "not valid JSON" in by_name["a.json"]
    assert "data_items" in by_name["b.json"]


def test_a_found_obs_data_leaves_the_other_members_unremarked():
    """With observations in hand a leftover `simulation.json` is simply not
    observations -- reporting it would train the user to ignore the banner."""
    obs = json.dumps({"protocol_info": {"sim_times": [1.0]}, "data_items": []})
    parts = omex_import.unpack(_phlynx_archive(**{"study_obs_data.json": obs}))
    assert parts["obs_skipped"] == []


# ---------------------------------------------------------------------------
# An obs_data written before CA #466
#
# The archive the user had was correct when it was written: `variable` named the
# item and was allowed to repeat across the mean/max/min of one trace. CA's
# complaint is about the consequence (a duplicate `data_item_name`), so the
# cause -- and the migrator that fixes it -- has to come from here.
# ---------------------------------------------------------------------------
def _legacy_obs() -> str:
    return json.dumps(
        {
            "protocol_info": {"pre_times": [0.0], "sim_times": [[1.0]], "params_to_change": {}},
            "data_items": [
                {
                    "variable": "pressure aortic root",
                    "name_for_plotting": "u_{AR}",
                    "data_type": "constant",
                    "operation": op,
                    "operands": ["aortic_root/u"],
                    "unit": "J_per_m3",
                    "weight": 1.0,
                    "value": 1.0,
                    "std": 0.1,
                    "cost_type": "gaussian_MLE",
                }
                for op in ("mean", "max", "min")
            ],
        }
    )


def test_a_pre_466_archive_is_told_what_changed_and_how_to_convert_it(client, requires_ca):
    resp = _upload(client, {"m.cellml": _model_bytes(), "obs_data.json": _legacy_obs()})
    assert resp.status_code == 200, resp.text
    error = resp.json()["obs_data"]["error"]
    # CA's own complaint, unparaphrased, so it matches what a run would say...
    assert "Duplicate 'data_item_name'" in error
    # ...and then the part CA cannot know: why this file has duplicates at all.
    assert "uses the old" in error
    assert "cuflynx-migrate-obs-data" in error
    assert "'variable'" in error and "'name_for_plotting'" in error


def test_the_migration_hint_is_offered_from_the_document_alone():
    """No CA needed: the old keys are in the file, and the advice is about them.
    So a frozen app with no clone configured still explains the failure."""
    import obs_data as obs_mod

    hint = obs_mod.legacy_vocabulary_hint(json.loads(_legacy_obs()))
    assert hint and "cuflynx-migrate-obs-data" in hint
    # And it stays quiet about a file already in the current vocabulary.
    assert obs_mod.legacy_vocabulary_hint(_example_obs_data()) is None


def test_an_mmt_archive_whose_protocol_filled_the_slot_is_not_told_it_has_none(
    client, requires_simulation, requires_myokit_parser
):
    """The .mmt's own `[[protocol]]` becomes the study's obs_data when the
    archive carries none (#27). The slot is what matters, not where it was
    filled from -- so the "no obs_data" sentence has to be decided after that."""
    with open(MMT_EXAMPLE, "rb") as fh:
        resp = client.post(
            "/api/omex/upload", files={"file": (MMT_EXAMPLE.name, fh, "application/zip")}
        )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["obs_data"] and body["obs_data"].get("derived_from_mmt")
    assert not any("carries no obs_data" in w for w in body["warnings"]), body["warnings"]
