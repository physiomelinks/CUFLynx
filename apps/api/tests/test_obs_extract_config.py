"""The saved decisions, and their round trip.

The whole value of this file is that an afternoon of selections survives a
reload. So the round trip is tested to the byte, and unknown keys are tested to
be *refused* -- a mistyped key that loads cleanly and does nothing produces an
extraction that runs, succeeds, and quietly ignores the setting you came back to
change.
"""

from __future__ import annotations

import json

import pytest

from obs_extract import ObsExtractError, config as C

pytestmark = pytest.mark.unit


def _configured(tmp_path):
    """A config with one group turned on, as the dialog would leave it."""
    cfg = C.new_config("demo", str(tmp_path))
    cfg["subprotocols"]["4AP|Kv-90"] = C.default_subprotocol("voltage")
    cfg["subprotocols"]["4AP|Kv-90"].update(used=True, features=[{
        "operation": "max_in_range", "unit": "picoA", "unit_confirmed": True,
        "operation_kwargs": {}, "range": {"basis": "stimulus_window",
                                          "start_s": 0.1, "end_s": 0.2},
        "std": {"mode": "absolute", "value": 4.0},
    }])
    cfg["datasets"] = [C.default_dataset(
        {"path": "/x/4AP/a.1.Kv-90.1.wcp", "case_name": "4AP_a.1.Kv-90.1.wcp",
         "protocol": "4AP", "subprotocol": "Kv-90", "format": "wcp"})]
    cfg["datasets"][0]["used"] = True
    cfg["data_modifiers"] = [
        {"name": "ljp", "target": "voltage", "modifier": "X - 16.9"}]
    return cfg


def test_a_new_config_validates():
    assert C.validate(C.new_config("demo", "/data")) == []


def test_round_trip_is_stable(tmp_path):
    """Save, load, save again: the second file must equal the first.

    ``updated`` is the one field allowed to move.
    """
    path = str(tmp_path / "obs_extraction_config.json")
    C.save(_configured(tmp_path), path)
    first = json.loads(open(path).read())

    C.save(C.load(path), path)
    second = json.loads(open(path).read())

    first.pop("updated"), second.pop("updated")
    assert first == second


def test_a_saved_config_reloads_with_every_selection(tmp_path):
    path = str(tmp_path / "c.json")
    C.save(_configured(tmp_path), path)
    back = C.load(path)
    group = back["subprotocols"]["4AP|Kv-90"]
    assert group["used"] is True
    assert group["input"] == "voltage"
    assert group["features"][0]["range"]["start_s"] == 0.1
    assert back["datasets"][0]["used"] is True
    assert back["data_modifiers"][0]["modifier"] == "X - 16.9"


@pytest.mark.parametrize(
    "mutate,fragment",
    [
        (lambda c: c.update(sourse={}), "sourse"),
        (lambda c: c["source"].update(rooot="/x"), "rooot"),
        (lambda c: c["subprotocols"]["4AP|Kv-90"].update(sweeplimit=2), "sweeplimit"),
        (lambda c: c["subprotocols"]["4AP|Kv-90"]["features"][0].update(units="mV"),
         "units"),
        (lambda c: c["datasets"][0].update(use=True), "use"),
        (lambda c: c["datasets"][0]["reader"].update(rate=1000), "rate"),
        (lambda c: c["preprocess"].update(clamp_hz=1000), "clamp_hz"),
    ],
)
def test_unknown_keys_are_refused_at_every_level(tmp_path, mutate, fragment):
    cfg = _configured(tmp_path)
    mutate(cfg)
    with pytest.raises(ObsExtractError) as exc:
        C.validate(cfg)
    assert fragment in str(exc.value)


def test_a_near_miss_suggests_the_right_key(tmp_path):
    cfg = _configured(tmp_path)
    cfg["datasets"][0]["case_nam"] = "x"
    with pytest.raises(ObsExtractError, match="Did you mean 'case_name'"):
        C.validate(cfg)


def test_underscore_keys_are_transient_and_not_refused(tmp_path):
    cfg = _configured(tmp_path)
    cfg["_missing"] = ["a.wcp"]
    assert C.validate(cfg) == []


def test_save_strips_the_transient_keys(tmp_path):
    cfg = _configured(tmp_path)
    cfg["_missing"] = ["a.wcp"]
    path = str(tmp_path / "c.json")
    C.save(cfg, path)
    assert "_missing" not in json.loads(open(path).read())


def test_a_future_version_is_refused_with_what_to_do(tmp_path):
    cfg = _configured(tmp_path)
    cfg["obs_extraction_config_version"] = C.SCHEMA_VERSION + 1
    with pytest.raises(ObsExtractError, match="Update CUFLynx"):
        C.validate(cfg)


def test_an_unversioned_document_is_migrated(tmp_path):
    cfg = _configured(tmp_path)
    cfg.pop("obs_extraction_config_version")
    assert C.migrate(cfg)["obs_extraction_config_version"] == C.SCHEMA_VERSION


def test_a_bad_modifier_fails_validation_not_the_extraction(tmp_path):
    cfg = _configured(tmp_path)
    cfg["data_modifiers"][0]["modifier"] = "__import__('os')"
    with pytest.raises(ObsExtractError, match="not allowed"):
        C.validate(cfg)


def test_an_empty_feature_range_is_refused(tmp_path):
    cfg = _configured(tmp_path)
    cfg["subprotocols"]["4AP|Kv-90"]["features"][0]["range"].update(
        start_s=0.5, end_s=0.2)
    with pytest.raises(ObsExtractError, match="empty time range"):
        C.validate(cfg)


def test_an_unconfirmed_unit_is_a_warning_at_validation(tmp_path):
    """Extraction refuses later; validation only says so, because the user is
    still editing."""
    cfg = _configured(tmp_path)
    cfg["subprotocols"]["4AP|Kv-90"]["features"][0]["unit_confirmed"] = False
    assert any("unconfirmed unit" in w for w in C.validate(cfg))


def test_a_dataset_in_a_group_with_no_settings_is_warned_about(tmp_path):
    cfg = _configured(tmp_path)
    cfg["datasets"][0]["protocol"] = "Rilu"
    assert any("no settings for" in w for w in C.validate(cfg))


def test_an_invalid_stimulus_kind_is_refused(tmp_path):
    cfg = _configured(tmp_path)
    cfg["subprotocols"]["4AP|Kv-90"]["input"] = "pressure"
    with pytest.raises(ObsExtractError, match="expected 'current' or 'voltage'"):
        C.validate(cfg)


# ---------------------------------------------------------------------------
def test_merge_scan_keeps_existing_settings(tmp_path):
    """A rescan must not undo the afternoon."""
    cfg = _configured(tmp_path)
    scan = {"datasets": [{"path": "/moved/4AP/a.1.Kv-90.1.wcp",
                          "case_name": "4AP_a.1.Kv-90.1.wcp", "format": "wcp"}],
            "groups": [{"group": "4AP|Kv-90"}]}
    merged = C.merge_scan(cfg, scan)
    row = merged["datasets"][0]
    assert row["used"] is True, "the selection survived"
    assert row["path"] == "/moved/4AP/a.1.Kv-90.1.wcp", "but the path moved with it"
    assert merged["subprotocols"]["4AP|Kv-90"]["used"] is True


def test_merge_scan_does_not_overwrite_hand_edited_labels(tmp_path):
    cfg = _configured(tmp_path)
    cfg["datasets"][0]["subprotocol"] = "RetaggedByHand"
    merged = C.merge_scan(cfg, {"datasets": [
        {"path": "/x/4AP/a.1.Kv-90.1.wcp", "case_name": "4AP_a.1.Kv-90.1.wcp",
         "protocol": "4AP", "subprotocol": "Kv-90", "format": "wcp"}], "groups": []})
    assert merged["datasets"][0]["subprotocol"] == "RetaggedByHand"


def test_merge_scan_adds_new_files_unused(tmp_path):
    cfg = _configured(tmp_path)
    merged = C.merge_scan(cfg, {"datasets": [
        {"path": "/x/4AP/b.1.Kv-90.1.wcp", "case_name": "4AP_b.1.Kv-90.1.wcp",
         "protocol": "4AP", "subprotocol": "Kv-90", "format": "wcp"}], "groups": []})
    new = [d for d in merged["datasets"] if d["case_name"].endswith("b.1.Kv-90.1.wcp")]
    assert len(new) == 1
    assert new[0]["used"] is False


def test_merge_scan_reports_rather_than_drops_a_vanished_file(tmp_path):
    """A config that quietly forgets a dataset when a drive is unmounted is
    worse than one that says so."""
    cfg = _configured(tmp_path)
    merged = C.merge_scan(cfg, {"datasets": [], "groups": []})
    assert merged["_missing"] == ["4AP_a.1.Kv-90.1.wcp"]
    assert len(merged["datasets"]) == 1, "still there, still selected"


# ---------------------------------------------------------------------------
def test_a_dataset_override_beats_its_group(tmp_path):
    cfg = _configured(tmp_path)
    cfg["subprotocols"]["4AP|Kv-90"]["sweep_limit"] = 5
    assert C.sweep_limit_for(cfg, cfg["datasets"][0]) == 5
    cfg["datasets"][0]["sweep_limit"] = 2
    assert C.sweep_limit_for(cfg, cfg["datasets"][0]) == 2

    assert len(C.features_for(cfg, cfg["datasets"][0])) == 1
    cfg["datasets"][0]["features_override"] = []
    assert C.features_for(cfg, cfg["datasets"][0]) == []


def test_timeline_defaults_differ_by_clamp_direction():
    """Voltage clamp pins the membrane from the first sample and needs no settle;
    current clamp must not begin at the holding current, so it gets one -- and
    that settle is a sub-experiment, which is why the stimulus is index 1."""
    voltage = C.timeline_for({"input": "voltage"})
    assert voltage["pre_time_s"] == 0.0
    assert voltage["settle_time_s"] is None
    assert voltage["stim_subexperiment_index"] == 0

    current = C.timeline_for({"input": "current"})
    assert current["pre_time_s"] == 1.0
    assert current["settle_time_s"] == 1.0
    assert current["stim_subexperiment_index"] == 1


def test_an_explicit_timeline_overrides_the_default():
    got = C.timeline_for({"input": "current", "timeline": {"pre_time_s": 0.25}})
    assert got["pre_time_s"] == 0.25
    assert got["settle_time_s"] == 1.0, "the rest still defaults"


def test_preprocess_settings_fill_in_defaults():
    got = C.preprocess_settings({"preprocess": {"clamp_output_hz": 2000.0}})
    assert got["clamp_output_hz"] == 2000.0
    assert got["voltage_peak_preserve_ratio"] == 0.95
    assert "voltage" in got["savgol_window_seconds"]


def test_load_reports_a_missing_or_malformed_file(tmp_path):
    with pytest.raises(ObsExtractError, match="no such config"):
        C.load(str(tmp_path / "nope.json"))
    bad = tmp_path / "bad.json"
    bad.write_text("{not json")
    with pytest.raises(ObsExtractError, match="not valid JSON"):
        C.load(str(bad))


def test_the_allow_list_and_the_defaults_agree():
    """Adding a preprocess key means editing two places; this is the reminder."""
    from obs_extract.config import _PREPROCESS

    assert set(C.DEFAULT_PREPROCESS) == set(_PREPROCESS)


def test_switching_a_group_to_voltage_clamp_switches_its_timeline():
    """Regression, found against a real recording.

    A group created as current clamp and later switched to voltage kept the
    settling sub-experiment and went on measuring in sub-experiment 1 -- which
    for a voltage clamp does not exist. The clamp direction is the authority.
    """
    group = C.default_subprotocol("current")
    assert C.timeline_for(group)["stim_subexperiment_index"] == 1

    group["input"] = "voltage"
    switched = C.timeline_for(group)
    assert switched["pre_time_s"] == 0.0
    assert switched["settle_time_s"] is None
    assert switched["stim_subexperiment_index"] == 0


def test_an_explicit_timeline_still_survives_a_switch():
    group = C.default_subprotocol("current")
    group["timeline"] = {"pre_time_s": 0.25}
    group["input"] = "voltage"
    got = C.timeline_for(group)
    assert got["pre_time_s"] == 0.25, "what the user set is kept"
    assert got["stim_subexperiment_index"] == 0, "the rest follows the direction"
