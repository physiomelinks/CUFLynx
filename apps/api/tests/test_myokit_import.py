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
# The shipped .mmt fixture (Beeler-Reuter 1977)
# ---------------------------------------------------------------------------
def _mmt_fixture():
    from conftest import RESOURCES_DIR

    return RESOURCES_DIR / "br-1977.mmt"


def test_the_mmt_fixture_exists_and_has_all_three_sections():
    """It is a whole .mmt -- model, protocol and script -- which is the point:
    only the model must come through."""
    path = _mmt_fixture()
    assert path.is_file(), "resources/br-1977.mmt is missing"
    text = path.read_text()
    for section in ("[[model]]", "[[protocol]]", "[[script]]"):
        assert section in text


@pytest.mark.integration
def test_only_the_model_section_is_imported(requires_simulation, tmp_path):
    """The pacing events belong to the .mmt's [[protocol]], not to the model.

    CUFLynx gets its protocol from obs_data's protocol_info, so importing
    Myokit's own stimulus schedule would give the model two sources of pacing
    that disagree. The stimulus *component* is part of the model and stays; only
    the events that drive it are dropped.
    """
    cellml, _saved = myokit_import.cellml_from_myokit(
        _mmt_fixture().read_bytes(), filename="br-1977.mmt", out_dir=str(tmp_path)
    )
    text = cellml.decode("utf-8")
    # The model's own stimulus component survives -- IStim is part of the model.
    assert "stimulus" in text
    # But `pace` is left a plain constant rather than a schedule: the protocol's
    # events are what would have made it vary with time.
    assert "pace" in text
    assert "piecewise" not in text.lower()


@pytest.mark.integration
def test_the_fixture_imports_and_simulates(client, requires_simulation, tmp_path):
    with open(_mmt_fixture(), "rb") as fh:
        body = client.post(
            "/api/models/upload",
            params={"output_dir": str(tmp_path)},
            files={"file": ("br-1977.mmt", fh, "text/plain")},
        ).json()
    assert body["converted_from"] == "br-1977.mmt"
    # Beeler-Reuter 1977: 8 states (m, h, j, d, f, x1, Cai, V).
    assert len(body["odes"]) == 8
    assert "membrane/V" in body["odes"]

    r = client.post(
        "/api/simulate",
        json={
            "model_id": body["model_id"],
            "params": {},
            "sim_time": 0.5,
            "pre_time": 0.0,
            "outputs": ["membrane/V"],
        },
    )
    assert r.status_code == 200, r.text
    assert len(r.json()["time"]) > 0


@pytest.mark.integration
def test_the_pacing_becomes_a_parameter_to_drive(client, requires_simulation):
    """Without the protocol the model rests, and `pace` is exposed as a
    parameter -- so the stimulus is CUFLynx's to supply, from a slider or from
    obs_data's params_to_change."""
    with open(_mmt_fixture(), "rb") as fh:
        body = client.post(
            "/api/models/upload", files={"file": ("br-1977.mmt", fh, "text/plain")}
        ).json()
    assert "engine/pace" in body["params"]

    def span(pace):
        r = client.post(
            "/api/simulate",
            json={
                "model_id": body["model_id"],
                "params": {"engine/pace": pace},
                "sim_time": 0.5,
                "pre_time": 0.0,
                "outputs": ["membrane/V"],
            },
        )
        v = r.json()["outputs"]["membrane/V"]
        return max(v) - min(v)

    # Unstimulated the model sits at its resting potential (a little numerical
    # drift, not an action potential); driving pace depolarises it by volts.
    assert span(0) < 0.01
    assert span(1) > 1.0


# ---------------------------------------------------------------------------
# Companion fixtures: the .mmt's own protocol, expressed as protocol_info
# ---------------------------------------------------------------------------
def _companions():
    from conftest import RESOURCES_DIR

    return (
        RESOURCES_DIR / "br-1977_obs_data.json",
        RESOURCES_DIR / "br-1977_params_for_id.csv",
    )


def test_the_companion_fixtures_exist():
    for path in _companions():
        assert path.is_file(), f"missing fixture: {path}"


def test_the_obs_protocol_matches_the_mmt_protocol():
    """The .mmt says: level 1.0, start 100, length 2, period 1000.

    protocol_info has no notion of a repeating pulse, so the schedule becomes
    sub-experiments holding engine/pace at each level for the right duration.
    This pins the arithmetic: stimuli must begin at t=100 and t=1100.
    """
    import json

    obs_path, _ = _companions()
    pi = json.loads(obs_path.read_text())["protocol_info"]
    durations = pi["sim_times"][0]
    levels = pi["params_to_change"]["engine/pace"][0]
    assert len(durations) == len(levels)

    starts, t = [], 0.0
    for duration, level in zip(durations, levels):
        if level:
            starts.append(t)
        t += duration
    assert starts == [100.0, 1100.0]          # period 1000, first at 100
    assert durations[1] == durations[3] == 2.0  # length 2
    assert set(levels) == {0.0, 1.0}            # level 1.0


@pytest.mark.integration
def test_the_protocol_paces_the_model(client, requires_simulation):
    """Two stimuli, so two action potentials -- the point of replicating it."""
    import json

    obs_path, _ = _companions()
    with open(_mmt_fixture(), "rb") as fh:
        model_id = client.post(
            "/api/models/upload", files={"file": ("br-1977.mmt", fh, "text/plain")}
        ).json()["model_id"]
    client.post(
        "/api/obs_data/upload",
        json={"model_id": model_id, "obs_data": json.loads(obs_path.read_text())},
    )
    r = client.post(
        "/api/protocol/run",
        json={"model_id": model_id, "params": {}, "outputs": ["membrane/V"]},
    )
    assert r.status_code == 200, r.text
    exp = r.json()["experiments"][0]
    t, v = exp["time"], exp["outputs"]["membrane/V"]
    assert t[-1] == pytest.approx(2000, abs=1)

    # Count upstrokes: crossings of 0 mV going up.
    crossings = sum(1 for a, b in zip(v, v[1:]) if a <= 0 < b)
    assert crossings == 2, f"expected two paced APs, saw {crossings}"


@pytest.mark.integration
def test_the_obs_targets_are_the_model_own_features(client, requires_simulation):
    """So the cost sits near zero at the defaults and a calibration over gNaBar
    should recover ~4 -- which makes the fixture self-checking."""
    import json

    obs_path, _ = _companions()
    with open(_mmt_fixture(), "rb") as fh:
        model_id = client.post(
            "/api/models/upload", files={"file": ("br-1977.mmt", fh, "text/plain")}
        ).json()["model_id"]
    items = json.loads(obs_path.read_text())["data_items"]
    client.post(
        "/api/obs_data/upload",
        json={"model_id": model_id, "obs_data": json.loads(obs_path.read_text())},
    )
    exp = client.post(
        "/api/protocol/run",
        json={"model_id": model_id, "params": {}, "outputs": ["membrane/V", "isi/Cai"]},
    ).json()["experiments"][0]

    by_op = {(i["operands"][0], i["operation"]): i["value"] for i in items}
    v = exp["outputs"]["membrane/V"]
    assert max(v) == pytest.approx(by_op[("membrane/V", "max")], abs=0.5)
    assert min(v) == pytest.approx(by_op[("membrane/V", "min")], abs=0.5)


@pytest.mark.integration
def test_the_calibration_parameter_actually_moves_the_observable(client, requires_simulation):
    """A parameter the observables are insensitive to would make the fixture
    useless for testing calibration."""
    import json

    obs_path, params_path = _companions()
    with open(_mmt_fixture(), "rb") as fh:
        model_id = client.post(
            "/api/models/upload", files={"file": ("br-1977.mmt", fh, "text/plain")}
        ).json()["model_id"]
    client.post(
        "/api/obs_data/upload",
        json={"model_id": model_id, "obs_data": json.loads(obs_path.read_text())},
    )
    with open(params_path, "rb") as fh:
        r = client.post(
            "/api/params_for_id/upload",
            params={"model_id": model_id},
            files={"file": ("p.csv", fh, "text/csv")},
        )
    assert r.status_code == 200, r.text
    (param,) = r.json()["params"]
    assert param["qname"] == "ina/gNaBar"
    assert param["min"] < param["initial_value"] < param["max"]

    def peak(g):
        exp = client.post(
            "/api/protocol/run",
            json={"model_id": model_id, "params": {"ina/gNaBar": g}, "outputs": ["membrane/V"]},
        ).json()["experiments"][0]
        return max(exp["outputs"]["membrane/V"])

    # Sodium conductance drives the upstroke, so the peak moves a long way.
    assert peak(param["min"]) < peak(param["initial_value"]) - 10


# ---------------------------------------------------------------------------
# Every .mmt in resources/, so a model dropped in later is covered without
# anyone remembering to write a test for it.
# ---------------------------------------------------------------------------
def _all_mmt():
    """Every .mmt fixture: the two kept at the top of resources/ plus the
    third-party example set. Shared with the protocol-conversion tests so the
    two sweeps cannot end up covering different model sets."""
    from conftest import all_mmt_fixtures

    return all_mmt_fixtures()


def test_there_is_at_least_one_mmt_fixture():
    """Otherwise the parametrised tests below would silently cover nothing."""
    assert _all_mmt()


@pytest.mark.integration
@pytest.mark.parametrize("path", _all_mmt(), ids=lambda p: p.name)
def test_every_mmt_fixture_converts_or_is_refused_clearly(path, requires_simulation, tmp_path):
    """Either it yields a usable model, or it is refused with a reason.

    Myokit's example set includes files whose [[model]] is a stub, because they
    exist to demonstrate a protocol or a script. Silently accepting one gives a
    model with nothing to integrate, so refusal is the correct outcome -- but it
    has to say why.
    """
    from cellml_meta import parse_cellml

    try:
        cellml, saved = myokit_import.cellml_from_myokit(
            path.read_bytes(), filename=path.name, out_dir=str(tmp_path)
        )
    except myokit_import.MyokitImportError as exc:
        assert "no state variables" in str(exc)
        return
    meta = parse_cellml(cellml)
    assert meta.variable_count > 0
    assert meta.odes
    assert Path(saved).is_file()


@pytest.mark.integration
@pytest.mark.parametrize("path", _all_mmt(), ids=lambda p: p.name)
def test_every_mmt_fixture_loads_through_the_upload_route(path, client, requires_simulation):
    with open(path, "rb") as fh:
        resp = client.post(
            "/api/models/upload", files={"file": (path.name, fh, "text/plain")}
        )
    if resp.status_code == 422:
        # A stub model, refused at the door -- see the test above.
        assert "no state variables" in resp.json()["detail"]
        return
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["converted_from"] == path.name
    assert body["odes"]
    # ...and the model is usable, not merely parseable.
    assert client.get(f"/api/models/{body['model_id']}/variables").status_code == 200


@pytest.mark.integration
def test_a_stub_model_is_refused_rather_than_imported_empty(client, requires_simulation):
    """It used to import with zero ODEs, so the emptiness only surfaced later as
    a simulation with no outputs."""
    from conftest import RESOURCES_DIR

    path = RESOURCES_DIR / "models" / "third_party" / "fink-2009-protocol.mmt"
    if not path.is_file():
        pytest.skip("fink-2009-protocol.mmt not present")
    with open(path, "rb") as fh:
        resp = client.post(
            "/api/models/upload", files={"file": (path.name, fh, "text/plain")}
        )
    assert resp.status_code == 422
    assert "nothing to simulate" in resp.json()["detail"]


@pytest.mark.integration
def test_a_voltage_clamp_model_converts_as_well_as_a_stimulus_one(requires_simulation, tmp_path):
    """hh-1952d drives its membrane with a voltage clamp gated on `pace` rather
    than a stimulus current, so it exercises a different shape of protocol
    binding from br-1977."""
    from conftest import RESOURCES_DIR

    path = RESOURCES_DIR / "hh-1952d.mmt"
    if not path.is_file():
        pytest.skip("hh-1952d.mmt not present")
    cellml, _ = myokit_import.cellml_from_myokit(
        path.read_bytes(), filename=path.name, out_dir=str(tmp_path)
    )
    from cellml_meta import parse_cellml

    meta = parse_cellml(cellml)
    # Hodgkin-Huxley 1952: membrane potential plus the m/h/n gates.
    assert len(meta.odes) == 4
