"""A module-library archive opens at the library's calibrated values.

circulatory-autogen-modules ships ``<instance>_calibration.json`` (with
``calibrated_parameters`` and ``cost``) and ``<instance>_calibrated_parameters.csv``
in each instance's archive. Neither is the study's obs_data or params_for_id --
the CSV's name contains "param" and the JSON is a leftover JSON the obs_data
sniff reads -- and their values become the parameters' starting values.
"""

from __future__ import annotations

import io
import json
import zipfile

import pytest
from conftest import RESOURCES_DIR

import library_calibration
import omex_import

MODEL = (RESOURCES_DIR / "Lotka_Volterra_forced.cellml").read_bytes()
PARAMS = (RESOURCES_DIR / "Lotka_Volterra_params_for_id.csv").read_bytes()
OBS = (RESOURCES_DIR / "Lotka_Volterra_obs_data.json").read_bytes()

CALIBRATION = {
    "instance": "lv",
    "date": "2026-10-01",
    "method": "CMA-ES",
    "cost": 0.27193979766414555,
    "calibrated_parameters": {"alpha": 0.4371, "beta": 0.0231, "zeta": 1.0},
    "runs": [{"calibrated_parameters": {"alpha": 9.0}, "cost": 9.0}],
}
CALIBRATED_CSV = (
    "variable_name,units,value,data_reference,sourced\n"
    'alpha,dimensionless,0.5,"calibrated (CMA-ES)",no\n'
    'gamma,dimensionless,2.5,"calibrated (CMA-ES)",no\n'
)
JSON_FORMAT = "http://purl.org/NET/mediatypes/application/json"
CSV_FORMAT = "http://purl.org/NET/mediatypes/text/csv"


def _archive(members: dict, formats: dict | None = None) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, blob in members.items():
            zf.writestr(name, blob)
        if formats is not None:
            lines = [
                '<omexManifest xmlns="http://identifiers.org/combine.specifications/omex-manifest">'
            ]
            for name, fmt in formats.items():
                master = ' master="true"' if name.endswith(".cellml") else ""
                lines.append(f'  <content location="./{name}" format="{fmt}"{master}/>')
            lines.append("</omexManifest>")
            zf.writestr("manifest.xml", "\n".join(lines))
    return buf.getvalue()


def _library_archive(**extra) -> bytes:
    """Laid out as the library writes it, calibration CSV *before* the params_for_id."""
    members = {
        "lv.cellml": MODEL,
        "lv_calibrated_parameters.csv": CALIBRATED_CSV,
        "lv_calibration.json": json.dumps(CALIBRATION),
        "lv_obs_data.json": OBS,
        "lv_params_for_id.csv": PARAMS,
        **extra,
    }
    formats = {
        n: ("http://identifiers.org/combine.specifications/cellml" if n.endswith(".cellml")
            else JSON_FORMAT if n.endswith(".json") else CSV_FORMAT)
        for n in members
    }
    return _archive(members, formats)


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------
def test_calibration_members_are_neither_params_nor_obs():
    parts = omex_import.unpack(_library_archive())
    assert parts["params"][0] == "lv_params_for_id.csv"
    assert parts["obs"][0] == "lv_obs_data.json"
    assert [n for n, _ in parts["calibration"]] == [
        "lv_calibration.json", "lv_calibrated_parameters.csv",
    ]
    assert parts["roles"]["calibration"] == ["lv_calibration.json", "lv_calibrated_parameters.csv"]


def test_only_the_library_names_are_claimed_as_calibration():
    """Other members that mention calibration, such as CA's calibration_workflow.json
    or a notes CSV, are not the library's calibration result."""
    parts = omex_import.unpack(_library_archive(**{
        "calibration_workflow.json": json.dumps({"steps": []}),
        "calibration_notes.csv": "a,b\n1,2\n",
    }))
    assert parts["roles"]["calibration"] == ["lv_calibration.json", "lv_calibrated_parameters.csv"]


def test_a_renamed_calibration_json_is_recognised_by_content():
    parts = omex_import.unpack(_archive({
        "lv.cellml": MODEL,
        "result.json": json.dumps(CALIBRATION),
    }))
    assert parts["obs"] is None
    assert [n for n, _ in parts["calibration"]] == ["result.json"]
    # Accounted for, so it is not reported as an obs_data that was passed over.
    assert parts["obs_skipped"] == []


def test_the_only_csv_is_not_taken_as_params_when_it_is_the_calibration():
    parts = omex_import.unpack(_archive({
        "lv.cellml": MODEL,
        "lv_calibrated_parameters.csv": CALIBRATED_CSV,
    }))
    assert parts["params"] is None


# ---------------------------------------------------------------------------
# Reading and applying
# ---------------------------------------------------------------------------
def test_the_json_wins_over_the_csv_and_carries_the_cost():
    found = library_calibration.read([
        ("lv_calibrated_parameters.csv", CALIBRATED_CSV.encode()),
        ("lv_calibration.json", json.dumps(CALIBRATION).encode()),
    ])
    assert found["filename"] == "lv_calibration.json"
    assert found["values"] == {"alpha": 0.4371, "beta": 0.0231, "zeta": 1.0}
    assert found["cost"] == pytest.approx(0.27193979766414555)
    assert found["method"] == "CMA-ES"


def test_the_csv_is_used_when_there_is_no_json():
    found = library_calibration.read([("lv_calibrated_parameters.csv", CALIBRATED_CSV.encode())])
    assert found["values"] == {"alpha": 0.5, "gamma": 2.5}
    assert found["cost"] is None


def test_bare_names_resolve_through_the_params_for_id():
    text, report = library_calibration.apply(
        MODEL, [("c.json", json.dumps(CALIBRATION).encode())], PARAMS
    )
    from cellml_meta import parse_cellml

    values = parse_cellml(text).initial_values
    assert values["Lotka_Volterra_module/alpha"] == pytest.approx(0.4371)
    assert values["Lotka_Volterra_module/beta"] == pytest.approx(0.0231)
    assert report["applied"] == ["alpha", "beta"]
    assert report["unresolved"] == ["zeta"]


def test_no_calibration_members_leaves_the_model_alone():
    text, report = library_calibration.apply(MODEL, [], PARAMS)
    assert text == MODEL and report is None


# ---------------------------------------------------------------------------
# Through the importer
# ---------------------------------------------------------------------------
def test_the_imported_study_starts_at_the_calibrated_values(client):
    resp = client.post(
        "/api/omex/upload",
        files={"file": ("lv.omex", _library_archive(), "application/zip")},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()

    cal = body["calibration"]
    assert cal["source"] == "module library"
    assert cal["filename"] == "lv_calibration.json"
    assert cal["cost"] == pytest.approx(0.27193979766414555)
    assert cal["applied"] == ["alpha", "beta"]
    assert cal["unresolved"] == ["zeta"]
    assert any("zeta" in w for w in body["warnings"])

    # The params_for_id is the real one, and its sliders start at the calibration.
    assert body["params_for_id"]["filename"] == "lv_params_for_id.csv"
    start = {p["qname"]: p["initial_value"] for p in body["params_for_id"]["params"]}
    assert start["Lotka_Volterra_module/alpha"] == pytest.approx(0.4371)
    assert start["Lotka_Volterra_module/beta"] == pytest.approx(0.0231)
    assert start["Lotka_Volterra_module/gamma"] == pytest.approx(3.0)  # not calibrated

    model = client.get(f"/api/models/{body['model_id']}/variables").json()
    assert model["initial_values"]["Lotka_Volterra_module/alpha"] == pytest.approx(0.4371)
    # obs_data is the study's own, not the calibration JSON.
    assert body["obs_data"]["filename"] == "lv_obs_data.json"


def test_an_archive_without_a_calibration_reports_none(client):
    blob = _archive({"lv.cellml": MODEL, "lv_params_for_id.csv": PARAMS})
    body = client.post(
        "/api/omex/upload", files={"file": ("lv.omex", blob, "application/zip")}
    ).json()
    assert body["calibration"] is None
    start = {p["qname"]: p["initial_value"] for p in body["params_for_id"]["params"]}
    assert start["Lotka_Volterra_module/alpha"] == pytest.approx(5.0)
