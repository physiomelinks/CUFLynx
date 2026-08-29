"""Scanning a directory of recordings, and the labels the scan infers.

The inference is a convenience, not a contract: it is right for the corpus it
was written against and will be wrong somewhere else, so these tests pin the
rule *and* the fact that nothing downstream is forced to accept it.
"""

from __future__ import annotations

import numpy as np
import pytest

from obs_extract import ObsExtractError, discover, group_key, split_group_key
from obs_extract.discovery import case_name, date_from_filename, subprotocol_from_filename
from obs_extract_fixtures import ramp, write_csv, write_npy, write_wcp

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    "filename,expected",
    [
        # The corpus's real shape: date_cell, index numbers, waveform, index.
        ("200926_005.1.1..1.1.1.UniqueAp.1.wcp", "UniqueAp"),
        ("200926_005.1.1.1.Kv-90.1.wcp", "Kv-90"),
        ("200926_006.2.AP2editwaveform.1.wcp", "AP2editwaveform"),
        ("200110_002.1.Currentsteps.1.wcp", "Currentsteps"),
        # No waveform token at all: fall back to the stem rather than to "".
        ("200112_003.wcp", "200112_003"),
        ("24122004.abf", "24122004"),
        # A plain name with no dots.
        ("recording.csv", "recording"),
    ],
)
def test_subprotocol_inference(filename, expected):
    assert subprotocol_from_filename(filename) == expected


@pytest.mark.parametrize(
    "filename,expected",
    [("200926_005.1.Kv-90.1.wcp", "2020-09-26"), ("991231_001.wcp", "2099-12-31"),
     ("recording.csv", None), ("209926_005.wcp", None)],  # month 99 is not a date
)
def test_date_inference(filename, expected):
    assert date_from_filename(filename) == expected


def test_group_key_round_trips():
    assert split_group_key(group_key("4AP", "Kv-90")) == ("4AP", "Kv-90")
    # A subprotocol containing a dash or a space must survive.
    assert split_group_key(group_key("Other", "AP W SHR")) == ("Other", "AP W SHR")


def _corpus(root):
    """A miniature of the real layout: protocol dirs, plus a file at the root."""
    (root / "4AP").mkdir()
    (root / "Rilu").mkdir()
    data = [[ramp(16, -80, -20), ramp(16, 0, 100)]]
    write_wcp(root / "4AP" / "200926_005.1.1.1.Kv-90.1.wcp", data)
    write_wcp(root / "4AP" / "200926_006.1.1.1.Kv-90.1.wcp", data)
    write_wcp(root / "4AP" / "200926_007.2.UniqueAp.1.wcp", data)
    write_wcp(root / "Rilu" / "200927_001.1.Currentsteps.1.wcp", data)
    write_wcp(root / "200928_002.1.Currentsteps.1.wcp", data)
    return root


def test_discover_groups_by_protocol_and_subprotocol(tmp_path):
    got = discover(str(_corpus(tmp_path)))
    assert len(got["datasets"]) == 5
    groups = {g["group"]: g for g in got["groups"]}
    assert set(groups) == {
        "4AP|Kv-90", "4AP|UniqueAp", "Rilu|Currentsteps",
        # A file at the root has no directory to name its protocol, so it takes
        # its own subprotocol -- the group is still meaningful and still unique.
        "Currentsteps|Currentsteps",
    }
    assert groups["4AP|Kv-90"]["n_datasets"] == 2
    assert groups["4AP|Kv-90"]["n_readable"] == 2
    assert groups["4AP|Kv-90"]["formats"] == ["wcp"]


def test_group_order_follows_the_walk_not_the_alphabet(tmp_path):
    """A rescan must not move a group the user had scrolled to."""
    root = str(_corpus(tmp_path))
    first = [g["group"] for g in discover(root)["groups"]]
    again = [g["group"] for g in discover(root)["groups"]]
    assert first == again


def test_case_name_disambiguates_across_protocol_directories(tmp_path):
    root = _corpus(tmp_path)
    got = discover(str(root))
    names = {d["case_name"] for d in got["datasets"]}
    assert "4AP_200926_005.1.1.1.Kv-90.1.wcp" in names
    assert "200928_002.1.Currentsteps.1.wcp" in names, "root files keep a bare name"
    assert len(names) == len(got["datasets"]), "case names must be unique"


def test_case_name_of_a_root_file_is_the_filename(tmp_path):
    assert case_name(str(tmp_path), str(tmp_path / "a.wcp")) == "a.wcp"
    assert case_name(str(tmp_path), str(tmp_path / "4AP" / "a.wcp")) == "4AP_a.wcp"


def test_exclude_matches_basename_or_case_name(tmp_path):
    root = _corpus(tmp_path)
    by_base = discover(str(root), exclude=["200926_007.2.UniqueAp.1.wcp"])
    assert len(by_base["datasets"]) == 4
    by_case = discover(str(root), exclude=["4AP_200926_007.2.UniqueAp.1.wcp"])
    assert len(by_case["datasets"]) == 4


def test_recurse_false_stays_at_the_root(tmp_path):
    got = discover(str(_corpus(tmp_path)), recurse=False)
    assert [d["case_name"] for d in got["datasets"]] == ["200928_002.1.Currentsteps.1.wcp"]


def test_suffix_filter(tmp_path):
    root = _corpus(tmp_path)
    write_npy(root / "extra.npy", np.zeros((2, 4)), sample_rate_hz=1000.0)
    assert len(discover(str(root), suffixes=[".npy"])["datasets"]) == 1
    assert len(discover(str(root), suffixes=[".wcp"])["datasets"]) == 5


def test_mixed_formats_in_one_directory(tmp_path):
    root = tmp_path
    data = [[ramp(16, -80, -20), ramp(16, 0, 100)]]
    write_wcp(root / "a.1.Currentsteps.1.wcp", data)
    write_csv(root / "b.1.Currentsteps.1.csv", data)
    write_npy(root / "c.1.Currentsteps.1.npy", np.zeros((1, 2, 16)),
              sample_rate_hz=10000.0, channels=["Vm", "Im"], units=["mV", "pA"])
    got = discover(str(root))
    assert len(got["datasets"]) == 3
    assert got["groups"][0]["formats"] == ["csv", "npy", "wcp"]


def test_one_unreadable_file_does_not_fail_the_scan(tmp_path):
    root = _corpus(tmp_path)
    (root / "4AP" / "broken.1.Kv-90.1.wcp").write_bytes(b"not a wcp")
    got = discover(str(root))
    assert len(got["datasets"]) == 6
    bad = [d for d in got["datasets"] if not d["readable"]]
    assert len(bad) == 1
    assert bad[0]["error"]
    assert got["warnings"] and "could not be read" in got["warnings"][0]
    # The group still counts it, but distinguishes readable from present.
    kv = next(g for g in got["groups"] if g["group"] == "4AP|Kv-90")
    assert (kv["n_datasets"], kv["n_readable"]) == (3, 2)


def test_group_reports_the_smallest_sweep_count(tmp_path):
    """A sweep limit above the smallest count silently takes fewer from some."""
    root = tmp_path
    write_wcp(root / "a.1.Currentsteps.1.wcp",
              [[ramp(8, -80, -20), ramp(8, 0, 100)] for _ in range(5)])
    write_wcp(root / "b.1.Currentsteps.1.wcp",
              [[ramp(8, -80, -20), ramp(8, 0, 100)] for _ in range(2)])
    group = discover(str(root))["groups"][0]
    assert (group["min_sweeps"], group["max_sweeps"]) == (2, 5)


def test_reader_options_reach_the_probe(tmp_path):
    """A .npy with no sidecar becomes readable once its rate is supplied."""
    write_npy(tmp_path / "x.1.Currentsteps.1.npy", np.zeros((2, 4)))
    plain = discover(str(tmp_path))
    assert plain["datasets"][0]["readable"] is False
    assert plain["datasets"][0]["needs"] == ["sample_rate_hz"]

    fixed = discover(str(tmp_path), reader_opts={
        "x.1.Currentsteps.1.npy": {"sample_rate_hz": 1000.0}})
    assert fixed["datasets"][0]["readable"] is True


def test_probe_files_false_skips_decoding(tmp_path):
    got = discover(str(_corpus(tmp_path)), probe_files=False)
    assert len(got["datasets"]) == 5
    assert all("readable" not in d for d in got["datasets"])
    assert all(d["group"] for d in got["datasets"]), "labels still inferred"


def test_a_missing_root_is_the_users_error(tmp_path):
    with pytest.raises(ObsExtractError, match="not a directory"):
        discover(str(tmp_path / "nope"))
