"""Read a .mmt's [[protocol]] back out as obs_data protocol_info.

The import path takes the [[model]] section and nothing else, so the pacing the
user wrote in Myokit is left behind and has to be retyped as sim_times. This
converts it instead. The tests below are mostly about the two places that can go
quietly wrong: the schedule arithmetic (a segment in the wrong place still looks
like a plausible protocol) and the choice of how long to run something Myokit
declares as running forever.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

import mmt_protocol
import myokit_import
from conftest import RESOURCES_DIR, all_mmt_fixtures

SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "mmt_to_obs_data.py"


@pytest.fixture
def requires_protocol_shapes(requires_simulation, requires_myokit_parser):
    """protocol_shapes is expanded by circulatory_autogen, so running one needs a
    CA that has it (physiomelinks/circulatory_autogen#339)."""
    # Checked on disk rather than by import: CA is put on sys.path lazily, by the
    # engine's first simulation, so an import here can fail for the wrong reason.
    from engine import _circulatory_autogen_src  # noqa: PLC0415

    src = Path(_circulatory_autogen_src() or "")
    if not (src / "utilities" / "protocol_shapes.py").is_file():
        pytest.skip("this circulatory_autogen has no protocol_shapes support yet")

def _requires_engine_reader():
    """Myokit, and a circulatory_autogen new enough to carry the reader.

    The conversion lives in ``libcuflynx.parsers.MyokitParsers`` and this app
    delegates to it with no local fallback, so a CA predating it cannot convert
    anything. That is a skip, not a failure: CI pins the unit tier at a commit
    on purpose, and the integration tier tracks CA's master, so both trail this
    app by however long the engine side takes to land.
    """
    pytest.importorskip("myokit")
    if myokit_import._ca_parser() is None:
        pytest.skip("this circulatory_autogen has no libcuflynx.parsers.MyokitParsers")


# A model whose only purpose is to carry a binding and a protocol.
PACED = b"""[[model]]
name: paced
membrane.V = -80

[engine]
time = 0 bind time
pace = 0 bind pace

[membrane]
dot(V) = 0.1 + engine.pace

[[protocol]]
# Level  Start    Length   Period   Multiplier
1.0      100      2        1000     0
"""

UNPACED = PACED.replace(b"pace = 0 bind pace", b"pace = 0")
NO_PROTOCOL = PACED.split(b"[[protocol]]")[0]


def _convert(data: bytes, **kw):
    return mmt_protocol.protocol_info_from_mmt(data, filename="test.mmt", **kw)


# ---------------------------------------------------------------------------
# The schedule
# ---------------------------------------------------------------------------
def test_the_events_cross_over_verbatim():
    """The five numbers in the .mmt appear as the five numbers in the file. The
    alternative -- expanding them into durations and levels -- describes the same
    stimulus in a form nobody can read back as "1 Hz"."""
    _requires_engine_reader()
    info, _ = _convert(PACED)
    (shape,) = info["protocol_shapes"].values()
    assert shape["events"] == [
        {"level": 1.0, "start": 100.0, "length": 2.0, "period": 1000.0, "multiplier": 0}
    ]


def test_it_is_one_sub_experiment_named_by_the_shape():
    _requires_engine_reader()
    info, _ = _convert(PACED)
    assert info["sim_times"] == [[2000.0]]
    (rows,) = info["params_to_change"].values()
    assert rows == [[next(iter(info["protocol_shapes"]))]]


def test_the_shape_name_is_the_parameter_it_drives():
    """So a file with several controlled parameters stays readable."""
    _requires_engine_reader()
    info, _ = _convert(PACED)
    assert list(info["protocol_shapes"]) == ["engine_pace"]


def test_beats_controls_how_much_of_an_endless_protocol_is_taken():
    """The events are unchanged -- multiplier stays 0 -- so it is the length of
    the sub-experiment that decides how many stimuli land."""
    _requires_engine_reader()
    one, _ = _convert(PACED, beats=1)
    three, _ = _convert(PACED, beats=3)
    assert one["sim_times"] == [[1000.0]]
    assert three["sim_times"] == [[3000.0]]
    assert one["protocol_shapes"] == three["protocol_shapes"]


def test_an_explicit_duration_wins_over_beats():
    _requires_engine_reader()
    info, _ = _convert(PACED, beats=10, duration=1500)
    assert info["sim_times"] == [[1500.0]]


def test_the_cut_is_reported_rather_than_left_to_be_inferred():
    """Truncating an indefinite protocol is a choice, not a conversion, so the
    caller has to be told it was made."""
    _requires_engine_reader()
    _, notes = _convert(PACED)
    assert any("repeats indefinitely" in n and "2 beat" in n for n in notes)


def test_pre_time_is_carried_through():
    _requires_engine_reader()
    info, _ = _convert(PACED, pre_time=5000)
    assert info["pre_times"] == [5000.0]


def test_the_parameter_is_named_the_way_ca_names_parameters():
    """Myokit says engine.pace; CA and params_for_id say engine/pace."""
    _requires_engine_reader()
    info, _ = _convert(PACED)
    assert list(info["params_to_change"]) == ["engine/pace"]


# ---------------------------------------------------------------------------
# Refusals -- each has to say what is wrong with the file
# ---------------------------------------------------------------------------
def test_a_model_with_no_pace_binding_is_refused():
    _requires_engine_reader()
    with pytest.raises(mmt_protocol.MmtProtocolError, match="bound to `pace`"):
        _convert(UNPACED)


def test_a_file_with_no_protocol_section_is_refused():
    _requires_engine_reader()
    with pytest.raises(mmt_protocol.MmtProtocolError, match="no \\[\\[protocol\\]\\] events"):
        _convert(NO_PROTOCOL)


def test_a_zero_amplitude_stimulus_is_refused_rather_than_converted():
    """dn-1985-if-gna.mmt declares `0 10 0.5 1000 0` -- a stimulus of amplitude
    zero, because that example is about the model's own currents. Converting it
    yields a protocol_info that looks like pacing and applies none."""
    _requires_engine_reader()
    flat = PACED.replace(b"1.0      100      2        1000     0", b"0 100 2 1000 0")
    with pytest.raises(mmt_protocol.MmtProtocolError, match="amplitude 0"):
        _convert(flat)


def test_unreadable_input_is_refused_with_the_parser_reason():
    _requires_engine_reader()
    with pytest.raises(mmt_protocol.MmtProtocolError, match="could not read"):
        _convert(b"this is not a myokit file at all")


def test_a_nonsense_duration_is_refused():
    _requires_engine_reader()
    with pytest.raises(mmt_protocol.MmtProtocolError, match="greater than zero"):
        _convert(PACED, duration=0)


# ---------------------------------------------------------------------------
# Merging into an existing obs_data document
# ---------------------------------------------------------------------------
def test_filling_leaves_the_rest_of_the_document_alone():
    doc = {"data_items": [{"data_item_name": "membrane/V"}], "protocol_info": {"sim_times": [[1]]}}
    out = mmt_protocol.fill_protocol_info(doc, {"sim_times": [[2.0]]})
    assert out["data_items"] == doc["data_items"]
    assert out["protocol_info"]["sim_times"] == [[2.0]]


def test_filling_does_not_mutate_the_document_it_was_given():
    doc = {"protocol_info": {"sim_times": [[1]]}}
    mmt_protocol.fill_protocol_info(doc, {"sim_times": [[2.0]]})
    assert doc["protocol_info"]["sim_times"] == [[1]]


def test_a_hand_written_label_survives_a_re_derivation():
    """"1 Hz pacing" is worth more than "pacing, period 1000", and re-deriving
    the timings is no reason to lose it."""
    doc = {"protocol_info": {"experiment_labels": ["1 Hz pacing"], "experiment_colors": ["b"]}}
    out = mmt_protocol.fill_protocol_info(
        doc, {"sim_times": [[1.0]], "experiment_labels": ["pacing, period 1000"], "experiment_colors": ["r"]}
    )
    assert out["protocol_info"]["experiment_labels"] == ["1 Hz pacing"]
    assert out["protocol_info"]["experiment_colors"] == ["b"]


def test_a_label_that_no_longer_fits_is_replaced():
    """Keeping one label against two experiments would fail CA's own shape check."""
    doc = {"protocol_info": {"experiment_labels": ["only one"]}}
    out = mmt_protocol.fill_protocol_info(
        doc, {"sim_times": [[1.0], [2.0]], "experiment_labels": ["a", "b"]}
    )
    assert out["protocol_info"]["experiment_labels"] == ["a", "b"]


def test_filling_an_empty_document_works():
    out = mmt_protocol.fill_protocol_info({}, {"sim_times": [[1.0]]})
    assert out["protocol_info"]["sim_times"] == [[1.0]]


# ---------------------------------------------------------------------------
# The fixture: the generated protocol must equal the hand-written one
# ---------------------------------------------------------------------------
@pytest.mark.integration
def test_it_reproduces_the_hand_written_br_1977_protocol(requires_simulation, requires_myokit_parser):
    """The fixture is now generated by the script rather than hand-written, so
    this is a regression lock on the file rather than an independent check --
    the independent one is the Myokit comparison below. It still earns its
    place: the fixture is what the integration tests simulate, so a change in
    the conversion that nobody meant shows up here first."""
    mmt = RESOURCES_DIR / "br-1977.mmt"
    fixture = json.loads((RESOURCES_DIR / "br-1977_obs_data.json").read_text())["protocol_info"]
    info, _ = mmt_protocol.protocol_info_from_mmt(mmt.read_bytes(), filename=mmt.name)
    for key in ("pre_times", "sim_times", "params_to_change"):
        assert info[key] == fixture[key], key


# ---------------------------------------------------------------------------
# Every .mmt in resources/, so a model added later is covered automatically
# ---------------------------------------------------------------------------
@pytest.mark.integration
@pytest.mark.parametrize("path", all_mmt_fixtures(), ids=lambda p: p.name)
def test_every_mmt_fixture_converts_or_is_refused_clearly(path, requires_simulation, requires_myokit_parser):
    """Either the protocol comes out as a usable schedule, or the file is
    refused for a reason a user can act on. A conversion that produced an empty
    or misaligned schedule would be worse than either."""
    try:
        info, notes = mmt_protocol.protocol_info_from_mmt(path.read_bytes(), filename=path.name)
    except mmt_protocol.MmtProtocolError as exc:
        # The three known refusals in the example set: no events at all, no pace
        # binding, and a zero-amplitude stimulus.
        assert any(
            reason in str(exc)
            for reason in ("no [[protocol]] events", "bound to `pace`", "amplitude 0")
        ), str(exc)
        return

    assert info["sim_times"] and info["sim_times"][0]
    assert all(t > 0 for t in info["sim_times"][0]), "a sub-experiment must last some time"
    assert len(info["params_to_change"]) == 1
    (values,) = info["params_to_change"].values()
    assert len(values[0]) == len(info["sim_times"][0])
    assert len(info["experiment_labels"]) == len(info["sim_times"])
    assert isinstance(notes, list)
    # Every string leaf has to name a shape, or CA rejects the file.
    (shape_name,) = info["protocol_shapes"]
    assert values == [[shape_name]]
    assert info["protocol_shapes"][shape_name]["events"]


@pytest.mark.integration
@pytest.mark.parametrize("path", all_mmt_fixtures(), ids=lambda p: p.name)
def test_the_converted_protocol_paces_at_the_same_instants_as_the_mmt(path, requires_simulation, requires_myokit_parser):
    """Rebuild a Myokit protocol from the emitted events and check it against the
    one in the file, over the length the conversion chose.

    Copying five fields across looks too simple to get wrong, which is exactly
    why it is worth checking: `duration()` is the .mmt's `Length` column, and a
    field read from the wrong accessor still produces a plausible protocol.
    """
    myokit = _requires_engine_reader()

    try:
        info, _ = mmt_protocol.protocol_info_from_mmt(path.read_bytes(), filename=path.name)
    except mmt_protocol.MmtProtocolError:
        pytest.skip("refused; covered by the test above")

    _model, original, _script = myokit.load(str(path))
    total = info["sim_times"][0][0]

    rebuilt = myokit.Protocol()
    (shape,) = info["protocol_shapes"].values()
    for event in shape["events"]:
        rebuilt.schedule(
            event["level"], event["start"], event["length"], event["period"], event["multiplier"]
        )

    def waveform(protocol):
        log = getattr(protocol, "log_for_interval", None) or protocol.create_log_for_interval
        out = log(0, total, for_drawing=False)
        return list(zip(out["time"], out["pace"]))

    assert waveform(rebuilt) == waveform(original)


# ---------------------------------------------------------------------------
# The script
# ---------------------------------------------------------------------------
@pytest.mark.integration
def test_the_script_writes_an_obs_data_file(requires_simulation, requires_myokit_parser, tmp_path):
    mmt = tmp_path / "paced.mmt"
    mmt.write_bytes(PACED)
    r = subprocess.run(
        [sys.executable, str(SCRIPT), str(mmt)], capture_output=True, text=True
    )
    assert r.returncode == 0, r.stderr
    out = tmp_path / "paced_obs_data.json"
    assert out.is_file()
    doc = json.loads(out.read_text())
    assert doc["protocol_info"]["sim_times"] == [[2000.0]]
    assert doc["protocol_info"]["protocol_shapes"]["engine_pace"]["events"][0]["period"] == 1000.0


@pytest.mark.integration
def test_the_script_updates_an_existing_file_without_losing_its_data_items(
    requires_simulation, requires_myokit_parser, tmp_path
):
    """The whole point of "fill" rather than "write": data_items are hand-made
    and not reproducible from the .mmt."""
    mmt = tmp_path / "paced.mmt"
    mmt.write_bytes(PACED)
    out = tmp_path / "existing.json"
    out.write_text(json.dumps({"data_items": [{"data_item_name": "membrane/V"}], "protocol_info": {}}))

    r = subprocess.run(
        [sys.executable, str(SCRIPT), str(mmt), "-o", str(out)], capture_output=True, text=True
    )
    assert r.returncode == 0, r.stderr
    doc = json.loads(out.read_text())
    assert doc["data_items"] == [{"data_item_name": "membrane/V"}]
    assert doc["protocol_info"]["sim_times"] == [[2000.0]]
    assert doc["protocol_info"]["protocol_shapes"]["engine_pace"]["events"][0]["period"] == 1000.0


@pytest.mark.integration
def test_the_script_refuses_a_file_it_cannot_convert(requires_simulation, requires_myokit_parser, tmp_path):
    mmt = tmp_path / "unpaced.mmt"
    mmt.write_bytes(UNPACED)
    r = subprocess.run(
        [sys.executable, str(SCRIPT), str(mmt)], capture_output=True, text=True
    )
    assert r.returncode == 1
    assert "bound to `pace`" in r.stderr
    assert not (tmp_path / "unpaced_obs_data.json").exists()


@pytest.mark.integration
def test_the_script_will_not_clobber_an_unreadable_obs_data(requires_simulation, requires_myokit_parser, tmp_path):
    mmt = tmp_path / "paced.mmt"
    mmt.write_bytes(PACED)
    out = tmp_path / "broken.json"
    out.write_text("{ this is not json")

    r = subprocess.run(
        [sys.executable, str(SCRIPT), str(mmt), "-o", str(out)], capture_output=True, text=True
    )
    assert r.returncode == 2
    assert out.read_text() == "{ this is not json"


@pytest.mark.integration
def test_the_script_can_print_instead_of_writing(requires_simulation, requires_myokit_parser, tmp_path):
    mmt = tmp_path / "paced.mmt"
    mmt.write_bytes(PACED)
    r = subprocess.run(
        [sys.executable, str(SCRIPT), str(mmt), "--stdout"], capture_output=True, text=True
    )
    assert r.returncode == 0, r.stderr
    assert json.loads(r.stdout)["sim_times"] == [[2000.0]]
    assert not (tmp_path / "paced_obs_data.json").exists()


# ---------------------------------------------------------------------------
# End to end: the generated protocol_info has to actually run
# ---------------------------------------------------------------------------
@pytest.mark.integration
def test_a_generated_protocol_info_runs_and_paces_the_model(client, requires_protocol_shapes):
    """The real check. A protocol_info that CA accepts but that produces no
    stimulus would satisfy every structural assertion above."""
    mmt = RESOURCES_DIR / "br-1977.mmt"
    info, _ = mmt_protocol.protocol_info_from_mmt(mmt.read_bytes(), filename=mmt.name)

    with open(mmt, "rb") as fh:
        model_id = client.post(
            "/api/models/upload", files={"file": (mmt.name, fh, "text/plain")}
        ).json()["model_id"]

    obs = json.loads((RESOURCES_DIR / "br-1977_obs_data.json").read_text())
    obs = mmt_protocol.fill_protocol_info(obs, info)
    r = client.post("/api/obs_data/upload", json={"model_id": model_id, "obs_data": obs})
    assert r.status_code == 200, r.text

    r = client.post(
        "/api/protocol/run",
        json={"model_id": model_id, "params": {}, "outputs": ["membrane/V"]},
    )
    assert r.status_code == 200, r.text
    exp = r.json()["experiments"][0]
    assert exp["time"][-1] == pytest.approx(2000, abs=1)
    upstrokes = sum(1 for a, b in zip(exp["outputs"]["membrane/V"], exp["outputs"]["membrane/V"][1:]) if a <= 0 < b)
    assert upstrokes == 2, f"expected two paced action potentials, saw {upstrokes}"


# ---------------------------------------------------------------------------
# The in-app hook: dropping a .mmt offers its protocol as a new obs_data
# ---------------------------------------------------------------------------
def _upload_mmt(client, path: Path, out_dir: str | None = None):
    url = "/api/models/upload" + (f"?output_dir={out_dir}" if out_dir else "")
    with open(path, "rb") as fh:
        return client.post(url, files={"file": (path.name, fh, "text/plain")})


@pytest.mark.integration
def test_uploading_a_mmt_offers_its_protocol_as_obs_data(client, requires_simulation, requires_myokit_parser, tmp_path):
    r = _upload_mmt(client, RESOURCES_DIR / "br-1977.mmt", str(tmp_path))
    assert r.status_code == 200, r.text
    offered = r.json()["protocol_obs_data"]
    assert offered["reason"] is None
    assert offered["filename"] == "br-1977_obs_data.json"
    assert offered["obs_data"]["protocol_info"]["sim_times"] == [[2000.0]]
    assert offered["obs_data"]["protocol_info"]["protocol_shapes"]["engine_pace"]["events"]
    # data_items are the user's to write: what to measure is not in the .mmt.
    assert offered["obs_data"]["data_items"] == []


@pytest.mark.integration
def test_the_offered_obs_data_is_written_beside_the_converted_cellml(
    client, requires_simulation, requires_myokit_parser, tmp_path
):
    r = _upload_mmt(client, RESOURCES_DIR / "br-1977.mmt", str(tmp_path))
    offered = r.json()["protocol_obs_data"]
    written = Path(offered["path"])
    assert written.is_file()
    assert json.loads(written.read_text())["protocol_info"]["sim_times"]


@pytest.mark.integration
def test_an_existing_obs_data_on_disk_is_never_overwritten(client, requires_simulation, requires_myokit_parser, tmp_path):
    """It may hold hand-written data_items that nothing here could reconstruct."""
    existing = tmp_path / "br-1977_obs_data.json"
    existing.write_text('{"mine": true}')

    r = _upload_mmt(client, RESOURCES_DIR / "br-1977.mmt", str(tmp_path))
    offered = r.json()["protocol_obs_data"]
    assert existing.read_text() == '{"mine": true}'
    assert offered["path"] is None
    assert any("left alone" in n for n in offered["notes"])
    # Still offered in the response, so the user can adopt it if they want to.
    assert offered["obs_data"]["protocol_info"]["sim_times"]


@pytest.mark.integration
def test_the_offered_obs_data_is_accepted_and_paces_the_model(client, requires_protocol_shapes):
    """End to end through the routes a drop actually goes through."""
    r = _upload_mmt(client, RESOURCES_DIR / "br-1977.mmt")
    body = r.json()
    model_id, offered = body["model_id"], body["protocol_obs_data"]

    r = client.post(
        "/api/obs_data/upload", json={"model_id": model_id, "obs_data": offered["obs_data"]}
    )
    assert r.status_code == 200, r.text

    r = client.post(
        "/api/protocol/run",
        json={"model_id": model_id, "params": {}, "outputs": ["membrane/V"]},
    )
    assert r.status_code == 200, r.text
    v = r.json()["experiments"][0]["outputs"]["membrane/V"]
    assert sum(1 for a, b in zip(v, v[1:]) if a <= 0 < b) == 2


@pytest.mark.integration
def test_a_model_whose_protocol_cannot_be_converted_still_uploads(client, requires_simulation, requires_myokit_parser):
    """The protocol is a bonus. Losing it must not cost the user the model."""
    path = next(p for p in all_mmt_fixtures() if p.name == "stewart-2009.mmt")
    r = _upload_mmt(client, path)
    assert r.status_code == 200, r.text
    offered = r.json()["protocol_obs_data"]
    assert offered["obs_data"] is None
    assert "no [[protocol]] events" in offered["reason"]


@pytest.mark.integration
def test_a_cellml_upload_offers_nothing(client, requires_simulation, requires_myokit_parser):
    from conftest import LV_MODEL_PATH

    with open(LV_MODEL_PATH, "rb") as fh:
        r = client.post(
            "/api/models/upload", files={"file": (LV_MODEL_PATH.name, fh, "text/xml")}
        )
    assert r.status_code == 200, r.text
    assert r.json()["protocol_obs_data"] is None


@pytest.mark.integration
@pytest.mark.parametrize("path", all_mmt_fixtures(), ids=lambda p: p.name)
def test_every_mmt_upload_either_offers_a_usable_protocol_or_says_why(
    path, client, requires_simulation, requires_myokit_parser
):
    upload = _upload_mmt(client, path)
    if upload.status_code == 422:
        pytest.skip("stub model, refused by the importer")
    offered = upload.json()["protocol_obs_data"]
    assert offered is not None
    if offered["obs_data"] is None:
        assert offered["reason"]
        return
    # Offered protocols must survive the obs_data validator, or the UI would
    # adopt something the next request rejects.
    r = client.post(
        "/api/obs_data/upload",
        json={"model_id": upload.json()["model_id"], "obs_data": offered["obs_data"]},
    )
    assert r.status_code == 200, r.text
