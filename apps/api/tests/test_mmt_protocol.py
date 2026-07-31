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
from conftest import RESOURCES_DIR, all_mmt_fixtures

SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "mmt_to_obs_data.py"

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
def test_a_periodic_stimulus_becomes_alternating_sub_experiments():
    pytest.importorskip("myokit")
    info, _ = _convert(PACED)
    # Two beats of period 1000: rest to 100, stimulus for 2, rest for the
    # remaining 998, then the same again with the tail cut at 2000.
    assert info["sim_times"] == [[100.0, 2.0, 998.0, 2.0, 898.0]]
    assert info["params_to_change"] == {"engine/pace": [[0.0, 1.0, 0.0, 1.0, 0.0]]}
    assert sum(info["sim_times"][0]) == pytest.approx(2000.0)


def test_the_levels_line_up_with_the_durations():
    """Shape, not just content: CA indexes the two in lockstep, so a mismatch
    would drive the wrong parameter value over the wrong stretch of time."""
    pytest.importorskip("myokit")
    info, _ = _convert(PACED)
    for values in info["params_to_change"].values():
        assert len(values) == len(info["sim_times"])
        for row, times in zip(values, info["sim_times"]):
            assert len(row) == len(times)


def test_the_stimulus_lands_where_the_mmt_put_it():
    pytest.importorskip("myokit")
    info, _ = _convert(PACED)
    times, levels = info["sim_times"][0], info["params_to_change"]["engine/pace"][0]
    starts = []
    t = 0.0
    for length, level in zip(times, levels):
        if level:
            starts.append(t)
        t += length
    assert starts == [100.0, 1100.0]  # first at 100, then one period later


def test_beats_controls_how_much_of_an_endless_protocol_is_taken():
    pytest.importorskip("myokit")
    one, _ = _convert(PACED, beats=1)
    three, _ = _convert(PACED, beats=3)
    assert sum(one["sim_times"][0]) == pytest.approx(1000.0)
    assert sum(three["sim_times"][0]) == pytest.approx(3000.0)


def test_an_explicit_duration_wins_over_beats():
    pytest.importorskip("myokit")
    info, _ = _convert(PACED, beats=10, duration=1500)
    assert sum(info["sim_times"][0]) == pytest.approx(1500.0)


def test_the_cut_is_reported_rather_than_left_to_be_inferred():
    """Truncating an indefinite protocol is a choice, not a conversion, so the
    caller has to be told it was made."""
    pytest.importorskip("myokit")
    _, notes = _convert(PACED)
    assert any("repeats indefinitely" in n and "2 beat" in n for n in notes)


def test_pre_time_is_carried_through():
    pytest.importorskip("myokit")
    info, _ = _convert(PACED, pre_time=5000)
    assert info["pre_times"] == [5000.0]


def test_the_parameter_is_named_the_way_ca_names_parameters():
    """Myokit says engine.pace; CA and params_for_id say engine/pace."""
    pytest.importorskip("myokit")
    info, _ = _convert(PACED)
    assert list(info["params_to_change"]) == ["engine/pace"]


# ---------------------------------------------------------------------------
# Refusals -- each has to say what is wrong with the file
# ---------------------------------------------------------------------------
def test_a_model_with_no_pace_binding_is_refused():
    pytest.importorskip("myokit")
    with pytest.raises(mmt_protocol.MmtProtocolError, match="bound to `pace`"):
        _convert(UNPACED)


def test_a_file_with_no_protocol_section_is_refused():
    pytest.importorskip("myokit")
    with pytest.raises(mmt_protocol.MmtProtocolError, match="no \\[\\[protocol\\]\\] events"):
        _convert(NO_PROTOCOL)


def test_a_zero_amplitude_stimulus_is_refused_rather_than_converted():
    """dn-1985-if-gna.mmt declares `0 10 0.5 1000 0` -- a stimulus of amplitude
    zero, because that example is about the model's own currents. Converting it
    yields a protocol_info that looks like pacing and applies none."""
    pytest.importorskip("myokit")
    flat = PACED.replace(b"1.0      100      2        1000     0", b"0 100 2 1000 0")
    with pytest.raises(mmt_protocol.MmtProtocolError, match="amplitude 0"):
        _convert(flat)


def test_unreadable_input_is_refused_with_the_parser_reason():
    pytest.importorskip("myokit")
    with pytest.raises(mmt_protocol.MmtProtocolError, match="could not read"):
        _convert(b"this is not a myokit file at all")


def test_a_nonsense_duration_is_refused():
    pytest.importorskip("myokit")
    with pytest.raises(mmt_protocol.MmtProtocolError, match="greater than zero"):
        _convert(PACED, duration=0)


# ---------------------------------------------------------------------------
# Merging into an existing obs_data document
# ---------------------------------------------------------------------------
def test_filling_leaves_the_rest_of_the_document_alone():
    doc = {"data_items": [{"variable": "membrane/V"}], "protocol_info": {"sim_times": [[1]]}}
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
def test_it_reproduces_the_hand_written_br_1977_protocol(requires_simulation):
    """resources/br-1977_obs_data.json was written by reading the .mmt by hand.
    If the script disagrees with it, one of the two is wrong -- and this is the
    only test that can tell us which, since both otherwise look reasonable."""
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
def test_every_mmt_fixture_converts_or_is_refused_clearly(path, requires_simulation):
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


@pytest.mark.integration
@pytest.mark.parametrize("path", all_mmt_fixtures(), ids=lambda p: p.name)
def test_the_converted_protocol_matches_the_mmt_stimulus_times(path, requires_simulation):
    """Independently of the conversion: walk the emitted durations to recover
    when the stimulus fires, and check that against the [[protocol]] events. A
    schedule that is internally consistent but shifted in time would pass every
    other assertion here."""
    myokit = pytest.importorskip("myokit")

    try:
        info, _ = mmt_protocol.protocol_info_from_mmt(path.read_bytes(), filename=path.name)
    except mmt_protocol.MmtProtocolError:
        pytest.skip("refused; covered by the test above")

    model, protocol, _ = myokit.load(str(path))
    total = sum(info["sim_times"][0])

    starts = []
    t = 0.0
    (levels,) = info["params_to_change"].values()
    for length, level in zip(info["sim_times"][0], levels[0]):
        if level:
            starts.append(round(t, 6))
        t += length

    expected = []
    for event in protocol.events():
        if not event.level():
            continue
        when, period = event.start(), event.period()
        while when < total:
            expected.append(round(when, 6))
            if not period:
                break
            when += period
    assert starts == sorted(expected)


# ---------------------------------------------------------------------------
# The script
# ---------------------------------------------------------------------------
@pytest.mark.integration
def test_the_script_writes_an_obs_data_file(requires_simulation, tmp_path):
    mmt = tmp_path / "paced.mmt"
    mmt.write_bytes(PACED)
    r = subprocess.run(
        [sys.executable, str(SCRIPT), str(mmt)], capture_output=True, text=True
    )
    assert r.returncode == 0, r.stderr
    out = tmp_path / "paced_obs_data.json"
    assert out.is_file()
    doc = json.loads(out.read_text())
    assert doc["protocol_info"]["sim_times"] == [[100.0, 2.0, 998.0, 2.0, 898.0]]


@pytest.mark.integration
def test_the_script_updates_an_existing_file_without_losing_its_data_items(
    requires_simulation, tmp_path
):
    """The whole point of "fill" rather than "write": data_items are hand-made
    and not reproducible from the .mmt."""
    mmt = tmp_path / "paced.mmt"
    mmt.write_bytes(PACED)
    out = tmp_path / "existing.json"
    out.write_text(json.dumps({"data_items": [{"variable": "membrane/V"}], "protocol_info": {}}))

    r = subprocess.run(
        [sys.executable, str(SCRIPT), str(mmt), "-o", str(out)], capture_output=True, text=True
    )
    assert r.returncode == 0, r.stderr
    doc = json.loads(out.read_text())
    assert doc["data_items"] == [{"variable": "membrane/V"}]
    assert doc["protocol_info"]["sim_times"] == [[100.0, 2.0, 998.0, 2.0, 898.0]]


@pytest.mark.integration
def test_the_script_refuses_a_file_it_cannot_convert(requires_simulation, tmp_path):
    mmt = tmp_path / "unpaced.mmt"
    mmt.write_bytes(UNPACED)
    r = subprocess.run(
        [sys.executable, str(SCRIPT), str(mmt)], capture_output=True, text=True
    )
    assert r.returncode == 1
    assert "bound to `pace`" in r.stderr
    assert not (tmp_path / "unpaced_obs_data.json").exists()


@pytest.mark.integration
def test_the_script_will_not_clobber_an_unreadable_obs_data(requires_simulation, tmp_path):
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
def test_the_script_can_print_instead_of_writing(requires_simulation, tmp_path):
    mmt = tmp_path / "paced.mmt"
    mmt.write_bytes(PACED)
    r = subprocess.run(
        [sys.executable, str(SCRIPT), str(mmt), "--stdout"], capture_output=True, text=True
    )
    assert r.returncode == 0, r.stderr
    assert json.loads(r.stdout)["sim_times"] == [[100.0, 2.0, 998.0, 2.0, 898.0]]
    assert not (tmp_path / "paced_obs_data.json").exists()


# ---------------------------------------------------------------------------
# End to end: the generated protocol_info has to actually run
# ---------------------------------------------------------------------------
@pytest.mark.integration
def test_a_generated_protocol_info_runs_and_paces_the_model(client, requires_simulation):
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
