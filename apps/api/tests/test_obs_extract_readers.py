"""Every supported recording format, read back and checked against what went in.

The four formats are tested through one parametrised contract, because the whole
point of ``readers`` is that extraction downstream cannot tell them apart. Where
a format has a quirk of its own -- a ``.npy`` with no sampling rate, a ``.csv``
whose last sweep the old CLI dropped -- it gets its own test below.
"""

from __future__ import annotations

import json
import os

import numpy as np
import pytest

from obs_extract import (
    CURRENT,
    VOLTAGE,
    ObsExtractError,
    available_formats,
    open_recording,
    probe,
)
from obs_extract.readers import ChannelInfo, resolve_roles
from obs_extract_fixtures import ramp, write_csv, write_npy, write_wcp

pytestmark = pytest.mark.unit

#: 16-bit fixed point over a padded range: ~5e-3 in the units under test.
WCP_QUANTISATION = 0.02


def _sweeps(n_sweeps=3, n=64):
    """Distinct per sweep, so a reader that returns the wrong one is caught."""
    return [[ramp(n, -80 + 5 * s, -20 + 5 * s), ramp(n, 0, 100 * (s + 1))]
            for s in range(n_sweeps)]


@pytest.fixture(params=["wcp", "csv", "npy"])
def recording(request, tmp_path):
    """One synthesised recording per format, plus the values it was built from.

    ``.abf`` is absent here on purpose: nothing can write one, so it is covered
    by :func:`test_abf_reads_a_real_recording` against a real file when one is
    available.
    """
    data = _sweeps()
    fmt = request.param
    if fmt == "wcp":
        path = write_wcp(tmp_path / "cell.1.Currentsteps.1.wcp", data)
        tol = WCP_QUANTISATION
    elif fmt == "csv":
        path = write_csv(tmp_path / "cell.1.Currentsteps.1.csv", data, dt=1e-4)
        tol = 1e-5
    else:
        arr = np.stack([np.stack(sw) for sw in data])  # (sweeps, channels, samples)
        path = write_npy(tmp_path / "cell.1.Currentsteps.1.npy", arr,
                         sample_rate_hz=10000.0, channels=["Vm", "Im"],
                         units=["mV", "pA"])
        tol = 1e-9
    return {"format": fmt, "path": path, "data": data, "tol": tol}


def test_every_format_presents_the_same_recording(recording):
    """The contract extraction relies on, asserted identically for each format."""
    rec = open_recording(recording["path"])
    data = recording["data"]

    assert rec.sweep_count == len(data)
    assert rec.sample_rate_hz == pytest.approx(10000.0, rel=1e-3)
    assert rec.equal_length_sweeps

    # Both roles resolved, and to different channels.
    assert rec.name_for_role(VOLTAGE) is not None
    assert rec.name_for_role(CURRENT) is not None
    assert rec.name_for_role(VOLTAGE) != rec.name_for_role(CURRENT)

    for i in range(rec.sweep_count):
        t, signals = rec.sweep(i)
        assert t.size == len(data[i][0])
        assert np.all(np.diff(t) > 0), "time must increase within a sweep"
        v = signals[rec.name_for_role(VOLTAGE)]
        c = signals[rec.name_for_role(CURRENT)]
        assert np.max(np.abs(v - data[i][0])) < recording["tol"]
        assert np.max(np.abs(c - data[i][1])) < recording["tol"]


def test_probe_reports_without_decoding(recording):
    got = probe(recording["path"])
    assert got["readable"] is True
    assert got["needs"] == []
    assert got["sweep_count"] == len(recording["data"])
    assert {c["role"] for c in got["channels"]} == {VOLTAGE, CURRENT}


def test_probe_never_raises_for_a_bad_file(tmp_path):
    """A scan of hundreds must not fail because one file is corrupt."""
    bad = tmp_path / "junk.1.Currentsteps.1.wcp"
    bad.write_bytes(b"this is not a WCP file at all")
    got = probe(str(bad))
    assert got["readable"] is False
    assert got["error"]
    assert "junk" in got["error"]


def test_unsupported_suffix_names_what_is_supported(tmp_path):
    p = tmp_path / "notes.docx"
    p.write_text("x")
    with pytest.raises(ObsExtractError, match=r"\.wcp"):
        open_recording(str(p))


def test_sweep_index_out_of_range(recording):
    rec = open_recording(recording["path"])
    with pytest.raises(ObsExtractError, match="out of range"):
        rec.sweep(rec.sweep_count)


# ---------------------------------------------------------------------------
# Role resolution
# ---------------------------------------------------------------------------
def test_wcp_channel_order_does_not_decide_the_role(tmp_path):
    """Units decide, not position.

    The real corpus records ``Im0`` first and ``Vm0`` second. A positional rule
    -- which is what the CLI this replaces uses -- labels those backwards, and
    every extracted observable then reads the wrong signal.
    """
    data = _sweeps(1)
    current_first = [[sw[1], sw[0]] for sw in data]
    path = write_wcp(tmp_path / "a.1.Currentsteps.1.wcp", current_first,
                     channels=(("Im0", "pA"), ("Vm0", "mV")))
    rec = open_recording(path)
    assert rec.channels[0].role == CURRENT
    assert rec.channels[1].role == VOLTAGE
    assert all(c.role_source == "unit" for c in rec.channels)

    _, signals = rec.sweep(0)
    assert np.max(np.abs(signals["Vm0"] - data[0][0])) < WCP_QUANTISATION


def test_roles_fall_through_unit_then_name_then_position():
    unit_decided, warns = resolve_roles(
        [ChannelInfo(0, "ch0", "[pA]"), ChannelInfo(1, "ch1", "[mV]")],
        unit_objects=_units(["pA", "mV"]))
    assert [c.role for c in unit_decided] == [CURRENT, VOLTAGE]
    assert [c.role_source for c in unit_decided] == ["unit", "unit"]
    assert warns == []

    name_decided, warns = resolve_roles(
        [ChannelInfo(0, "Vm trace", ""), ChannelInfo(1, "Im trace", "")])
    assert [c.role for c in name_decided] == [VOLTAGE, CURRENT]
    assert [c.role_source for c in name_decided] == ["name", "name"]
    assert warns == []

    positional, warns = resolve_roles(
        [ChannelInfo(0, "A", ""), ChannelInfo(1, "B", "")])
    assert [c.role for c in positional] == [VOLTAGE, CURRENT]
    assert [c.role_source for c in positional] == ["position", "position"]
    assert warns, "a positional guess must say so"
    assert "current-first" in warns[0]


def test_an_explicit_role_beats_the_units():
    got, _ = resolve_roles(
        [ChannelInfo(0, "ch0", "[pA]"), ChannelInfo(1, "ch1", "[mV]")],
        unit_objects=_units(["pA", "mV"]), explicit={0: VOLTAGE, 1: CURRENT})
    assert [c.role for c in got] == [VOLTAGE, CURRENT]
    assert [c.role_source for c in got] == ["explicit", "explicit"]


def test_two_channels_claiming_one_role_keeps_the_first_and_warns():
    got, warns = resolve_roles(
        [ChannelInfo(0, "Vm1", ""), ChannelInfo(1, "Vm2", "")])
    assert got[0].role == VOLTAGE
    assert got[1].role is None, "the duplicate must not silently win"
    assert any("both look like" in w for w in warns)


def _units(names):
    import myokit

    return [myokit.parse_unit(n) for n in names]


# ---------------------------------------------------------------------------
# .csv
# ---------------------------------------------------------------------------
def test_csv_keeps_the_last_sweep(tmp_path):
    """Regression: the CLI this replaces drops it.

    ``ProcessData.read_data_onestep`` sets ``num_exps = len(split_idxs)`` and
    slices ``[split[i-1]:split[i]]``, so the rows after the final time reset are
    never emitted -- the last sweep of every multi-sweep file.
    """
    data = _sweeps(3, n=8)
    path = write_csv(tmp_path / "c.1.Currentsteps.1.csv", data)
    rec = open_recording(path)
    assert rec.sweep_count == 3
    _, last = rec.sweep(2)
    assert np.allclose(last["Vm"], data[2][0])


def test_csv_splits_on_any_time_reset_not_only_zero(tmp_path):
    """Sweeps that restart at something other than exactly 0.0 still split."""
    rows = ["0.5\t-80\t0", "0.6\t-79\t1", "0.5\t-70\t2", "0.6\t-69\t3"]
    p = tmp_path / "c.1.Currentsteps.1.csv"
    p.write_text("\n".join(rows) + "\n")
    rec = open_recording(str(p))
    assert rec.sweep_count == 2


def test_csv_reads_named_columns_in_any_order(tmp_path):
    p = tmp_path / "c.1.Currentsteps.1.csv"
    p.write_text("current,time,voltage\n0,0.0,-80\n5,0.1,-70\n")
    rec = open_recording(str(p))
    assert rec.sweep_count == 1
    _, sig = rec.sweep(0)
    assert np.allclose(sig["Vm"], [-80, -70])
    assert np.allclose(sig["Im"], [0, 5])


def test_csv_without_names_is_read_positionally_and_says_so(tmp_path):
    data = _sweeps(1, n=4)
    path = write_csv(tmp_path / "c.1.Currentsteps.1.csv", data)
    rec = open_recording(path)
    assert any("positionally" in w for w in rec.warnings)


def test_csv_honours_an_explicit_sweep_column(tmp_path):
    p = tmp_path / "c.1.Currentsteps.1.csv"
    p.write_text("time,voltage,sweep\n0.0,-80,0\n0.1,-79,0\n0.2,-70,1\n0.3,-69,1\n")
    rec = open_recording(str(p), sweep_column="sweep")
    assert rec.sweep_count == 2, "time increases throughout; only the column splits it"


# ---------------------------------------------------------------------------
# .npy
# ---------------------------------------------------------------------------
def test_npy_without_a_rate_asks_for_one(tmp_path):
    path = write_npy(tmp_path / "x.npy", np.zeros((2, 4)))
    got = probe(path)
    assert got["readable"] is False
    assert got["needs"] == ["sample_rate_hz"]
    assert "sample_rate_hz" in got["error"]


def test_npy_rate_from_the_dataset_settings_when_there_is_no_sidecar(tmp_path):
    path = write_npy(tmp_path / "x.npy", np.zeros((2, 4)))
    rec = open_recording(path, sample_rate_hz=500.0)
    assert rec.sweep_count == 2
    assert rec.sample_rate_hz == 500.0
    t, _ = rec.sweep(0)
    assert t[1] - t[0] == pytest.approx(1 / 500.0)


@pytest.mark.parametrize(
    "shape,sweeps,channels",
    [((6,), 1, 1), ((3, 6), 3, 1), ((3, 2, 6), 3, 2)],
)
def test_npy_shapes(tmp_path, shape, sweeps, channels):
    path = write_npy(tmp_path / "x.npy", np.zeros(shape), sample_rate_hz=1000.0)
    rec = open_recording(path)
    assert rec.sweep_count == sweeps
    assert len(rec.channels) == channels


def test_npy_warns_when_the_array_looks_transposed(tmp_path):
    path = write_npy(tmp_path / "x.npy", np.zeros((100, 2)), sample_rate_hz=1000.0)
    rec = open_recording(path)
    assert any("transpose" in w for w in rec.warnings)
    rec2 = open_recording(path, transpose=True)
    assert rec2.sweep_count == 2


def test_npy_refuses_a_pickled_array_and_names_the_flag(tmp_path):
    """An untrusted .npy must never be loaded with allow_pickle.

    The user browses to these files; ``allow_pickle=True`` on one is arbitrary
    code execution at load time. Refusing must be explicit rather than a bare
    "could not load", so nobody reaches for the flag to make it work.
    """
    path = tmp_path / "obj.npy"
    np.save(path, np.array([{"a": 1}], dtype=object), allow_pickle=True)
    with pytest.raises(ObsExtractError, match="allow_pickle"):
        open_recording(str(path))


def test_npy_sidecar_units_resolve_the_roles(tmp_path):
    path = write_npy(tmp_path / "x.npy", np.zeros((2, 2, 4)), sample_rate_hz=1000.0,
                     channels=["a", "b"], units=["pA", "mV"])
    rec = open_recording(path)
    assert rec.channels[0].role == CURRENT
    assert rec.channels[1].role == VOLTAGE


def test_a_malformed_sidecar_is_ignored_not_fatal(tmp_path):
    path = write_npy(tmp_path / "x.npy", np.zeros((2, 4)))
    (tmp_path / "x.json").write_text("{ not json")
    got = probe(path)
    assert got["needs"] == ["sample_rate_hz"], "falls through to asking for a rate"


# ---------------------------------------------------------------------------
# .abf -- nothing can write one, so this runs against a real file if present
# ---------------------------------------------------------------------------
ABF_CORPUS = os.path.expanduser("~/Documents/data/Sympathetic_Neuron")


@pytest.mark.integration
def test_abf_reads_a_real_recording():
    import glob

    files = sorted(glob.glob(os.path.join(ABF_CORPUS, "**", "*.abf"), recursive=True))
    if not files:
        pytest.skip("no .abf recordings on this machine")
    rec = open_recording(files[0])
    assert rec.sweep_count >= 1
    assert rec.sample_rate_hz > 0
    t, signals = rec.sweep(0)
    assert t.size > 1
    assert signals
    assert np.all(np.diff(t) > 0)


def test_available_formats_covers_every_supported_suffix():
    got = {f["suffix"]: f for f in available_formats()}
    assert set(got) == {".wcp", ".abf", ".csv", ".npy"}
    assert all(f["available"] for f in got.values()), "all four read on a bare install"
    # .abf is myokit's, and myokit is a core dependency -- pyabf is not needed.
    assert got[".abf"]["needs"] is None


def test_npy_sidecar_is_read_from_beside_the_file(tmp_path):
    arr = np.zeros((2, 4))
    np.save(tmp_path / "y.npy", arr)
    (tmp_path / "y.json").write_text(json.dumps({"sample_rate_hz": 250.0}))
    rec = open_recording(str(tmp_path / "y.npy"))
    assert rec.sample_rate_hz == 250.0


def test_a_recording_with_no_sweeps_is_refused(tmp_path):
    """A header with no records behind it is not a usable recording.

    Two files in the reference corpus parse cleanly and report zero sweeps.
    Reporting them readable puts a row in the GUI that can never contribute an
    observable, so the reader calls it what it is.
    """
    path = write_npy(tmp_path / "empty.npy", np.zeros((0, 4)), sample_rate_hz=1000.0)
    got = probe(path)
    assert got["readable"] is False
    assert "no sweeps" in got["error"]


def test_when_both_wcp_readers_fail_the_error_names_both(tmp_path, monkeypatch):
    """A one-sided message misleads: which reader was even tried depends on the
    machine, so a file that neither can open must say so about both."""
    from obs_extract import readers

    bad = tmp_path / "x.1.Currentsteps.1.wcp"
    bad.write_bytes(b"not a wcp file")

    def fake_neo(path, opts):
        raise ObsExtractError(f"{os.path.basename(path)}: neo could not read it ('RTIME')")

    monkeypatch.setattr(readers, "_read_wcp_neo", fake_neo)
    with pytest.raises(ObsExtractError) as exc:
        open_recording(str(bad))
    assert "neo:" in str(exc.value)
    assert "myokit:" in str(exc.value)
    assert "RTIME" in str(exc.value)


def test_myokit_is_tried_after_neo_fails_not_only_when_neo_is_absent(tmp_path, monkeypatch):
    """The two libraries reject different files, so the second chance is real."""
    from obs_extract import readers

    path = write_wcp(tmp_path / "x.1.Currentsteps.1.wcp", _sweeps(2))

    def failing_neo(_path, _opts):
        raise ObsExtractError("x: neo could not read it ('RTIME')")

    monkeypatch.setattr(readers, "_read_wcp_neo", failing_neo)
    rec = open_recording(path)
    assert rec.sweep_count == 2, "myokit should have picked it up"
    assert rec.meta["reader"] == "myokit.formats.wcp"
