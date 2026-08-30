"""A config becomes an obs_data document.

This is where the schema has to be right. The two things most worth pinning are
the **clamp conventions** -- current clamp carries a settling sub-experiment that
voltage clamp does not, and the stimulus is measured in a different
sub-experiment as a result -- and the **shape of params_to_change**, which CA
indexes positionally and which is therefore the one place this pipeline can
corrupt a run silently.

The corpus is CSV: what is under test is the obs_data a config produces, not
the format it was read from, and CSV needs no reader dependency.
"""

from __future__ import annotations

import numpy as np
import pytest

from obs_extract import ObsExtractError, build_obs_data, config as C, discover
from obs_extract_fixtures import ramp, step, write_csv

# Every extraction here builds a clamp command trace, which needs scipy.
pytestmark = [pytest.mark.unit, pytest.mark.usefixtures("requires_scipy")]


def _ops():
    """Stand-ins with CA's signatures, so these tests need no CA."""

    def max_in_range(x, start_frac=0.0, end_frac=1.0, series_output=False):
        n = len(x)
        return float(np.max(x[int(n * start_frac):max(int(n * end_frac), 1)]))

    def first_time(t, V, series_output=False, spike_min_thresh=-10):
        return float(t[0])

    def always_nan(x):
        return float("nan")

    def explodes(x):
        raise RuntimeError("boom")

    return {"max_in_range": max_in_range, "first_time": first_time,
            "always_nan": always_nan, "explodes": explodes}


VARIABLES = {
    "params": ["soma/set_V", "soma/V_set", "soma/I_in", "soma/alpha"],
    "odes": ["soma/V"],
    "algebraic": ["soma/I_tot"],
    "all_names": ["soma/set_V", "soma/V_set", "soma/I_in", "soma/alpha",
                  "soma/V", "soma/I_tot"],
    "units": {},
}


def _corpus(root, n_sweeps=3, n=400):
    (root / "4AP").mkdir(exist_ok=True)
    sweeps = []
    for s in range(n_sweeps):
        im = step(n, 0.0, 20.0 * (s + 1), lo=100, hi=300)
        vm = step(n, -70.0, -70.0 + 8.0 * (s + 1), lo=100, hi=300)
        sweeps.append([vm, im])
    write_csv(root / "4AP" / "200926_001.1.Currentsteps.1.csv", sweeps, dt=1e-4)
    return root


def _config(root, *, stimulus="current", features=None, **group):
    cfg = C.new_config("demo", str(root))
    cfg = C.merge_scan(cfg, discover(str(root)))
    key = "4AP|Currentsteps"
    cfg["subprotocols"][key] = C.default_subprotocol(stimulus)
    cfg["subprotocols"][key].update(
        used=True,
        features=features if features is not None else [{
            "operation": "max_in_range", "unit": "milliV", "unit_confirmed": True,
            "operation_kwargs": {}, "std": {"mode": "absolute", "value": 4.0},
            "name_suffix": "vmax"}],
        **group)
    for d in cfg["datasets"]:
        d["used"] = True
    cfg["model_binding"].update(
        current_command_param="soma/I_in", voltage_command_param="soma/V_set",
        measured_voltage_variable="soma/V", measured_current_variable="soma/I_tot")
    cfg["model_binding"]["clamp_mode_param"] = {
        "qname": "soma/set_V", "voltage_value": 1.0, "current_value": 0.0}
    return cfg


def _build(cfg, **kw):
    return build_obs_data(cfg, operation_funcs=_ops(), variables=VARIABLES, **kw)


# ---------------------------------------------------------------------------
def test_one_sweep_becomes_one_experiment(tmp_path):
    doc, outcome = _build(_config(_corpus(tmp_path)))
    assert outcome.n_experiments == 3
    assert len(doc["protocol_info"]["sim_times"]) == 3
    assert len(doc["data_items"]) == 3
    assert outcome.datasets_used == 1


def test_a_sweep_limit_is_honoured(tmp_path):
    doc, outcome = _build(_config(_corpus(tmp_path), sweep_limit=2))
    assert outcome.n_experiments == 2


def test_current_clamp_carries_a_settling_subexperiment(tmp_path):
    """pre_times 1.0 **and** a 1.0 s settle inside sim_times -- the settle is a
    sub-experiment as well as a pre-time, which is why the stimulus is index 1."""
    doc, _ = _build(_config(_corpus(tmp_path), stimulus="current"))
    info = doc["protocol_info"]
    assert info["pre_times"] == [1.0, 1.0, 1.0]
    assert all(len(s) == 2 and s[0] == 1.0 for s in info["sim_times"])
    assert all(item["subexperiment_idx"] == 1 for item in doc["data_items"])


def test_voltage_clamp_has_one_subexperiment_and_no_pre_time(tmp_path):
    """A voltage clamp pins the membrane from the first sample, so there is
    nothing to settle."""
    cfg = _config(_corpus(tmp_path), stimulus="voltage", features=[{
        "operation": "max_in_range", "unit": "picoA", "unit_confirmed": True,
        "operation_kwargs": {}, "name_suffix": "imax"}])
    doc, _ = _build(cfg)
    info = doc["protocol_info"]
    assert info["pre_times"] == [0.0, 0.0, 0.0]
    assert all(len(s) == 1 for s in info["sim_times"])
    assert all(item["subexperiment_idx"] == 0 for item in doc["data_items"])


def test_the_command_trace_is_registered_and_pointed_at(tmp_path):
    doc, _ = _build(_config(_corpus(tmp_path)))
    info = doc["protocol_info"]
    traces = info["protocol_traces"]
    assert len(traces) == 3
    row = info["params_to_change"]["soma/I_in"][0]
    assert row[0] == 0.0, "no stimulus during the settle"
    assert row[1] in traces, "the stimulus sub-experiment names a trace"
    assert set(traces[row[1]]) == {"t", "values"}
    assert len(traces[row[1]]["t"]) == len(traces[row[1]]["values"])


def test_the_clamp_mode_switch_is_set_for_the_direction(tmp_path):
    current, _ = _build(_config(_corpus(tmp_path), stimulus="current"))
    assert current["protocol_info"]["params_to_change"]["soma/set_V"][0] == [0.0, 0.0]

    cfg = _config(_corpus(tmp_path), stimulus="voltage", features=[{
        "operation": "max_in_range", "unit": "picoA", "unit_confirmed": True,
        "operation_kwargs": {}}])
    voltage, _ = _build(cfg)
    assert voltage["protocol_info"]["params_to_change"]["soma/set_V"][0] == [1.0]


def test_the_unused_command_is_pinned_to_zero(tmp_path):
    """Under current clamp the voltage command must not be left to whatever the
    model's default is."""
    doc, _ = _build(_config(_corpus(tmp_path)))
    assert doc["protocol_info"]["params_to_change"]["soma/V_set"][0] == [0.0, 0.0]


def test_a_modulated_parameter_is_applied_only_during_the_stimulus(tmp_path):
    cfg = _config(_corpus(tmp_path), modulated_parameter="soma/alpha",
                  param_pre_value=1.0, param_stim_value=0.5)
    doc, _ = _build(cfg)
    assert doc["protocol_info"]["params_to_change"]["soma/alpha"][0] == [1.0, 0.5]


def test_auto_means_leave_the_models_own_value_alone(tmp_path):
    cfg = _config(_corpus(tmp_path), modulated_parameter="soma/alpha")
    doc, _ = _build(cfg)
    assert doc["protocol_info"]["params_to_change"]["soma/alpha"][0] == [1.0, 1.0]


def test_params_to_change_is_dense_and_rectangular(tmp_path):
    """CA indexes it positionally; a ragged row is the one silent corruption."""
    doc, _ = _build(_config(_corpus(tmp_path)))
    info = doc["protocol_info"]
    n_exp = len(info["sim_times"])
    for key, rows in info["params_to_change"].items():
        assert len(rows) == n_exp, key
        for exp, row in enumerate(rows):
            assert len(row) == len(info["sim_times"][exp]), f"{key}[{exp}]"


# ---------------------------------------------------------------------------
def test_data_item_names_are_unique(tmp_path):
    doc, _ = _build(_config(_corpus(tmp_path)))
    names = [i["data_item_name"] for i in doc["data_items"]]
    assert len(names) == len(set(names))


def test_two_features_with_the_same_name_are_disambiguated(tmp_path):
    """CA reports a duplicate as a name collision, which names the consequence
    rather than the cause; better to never produce one."""
    feature = {"operation": "max_in_range", "unit": "milliV",
               "unit_confirmed": True, "operation_kwargs": {}, "name_suffix": "same"}
    doc, _ = _build(_config(_corpus(tmp_path), features=[dict(feature), dict(feature)]))
    names = [i["data_item_name"] for i in doc["data_items"]]
    assert len(names) == len(set(names))
    assert any(n.endswith("_2") for n in names)


def test_no_legacy_vocabulary_is_emitted(tmp_path):
    """``variable`` and ``name_for_plotting`` are migrated by CA with a
    deprecation warning, and the migration then trips #466's uniqueness rule --
    reported as a duplicate name, which is nowhere near the cause."""
    doc, _ = _build(_config(_corpus(tmp_path)))
    for item in doc["data_items"]:
        assert "variable" not in item
        assert "name_for_plotting" not in item
        assert item["data_item_name"]
        assert item["trace_name_for_plotting"]


def test_every_item_records_where_it_came_from(tmp_path):
    doc, _ = _build(_config(_corpus(tmp_path)))
    for item in doc["data_items"]:
        assert "sweep" in item["source"]
        assert "Currentsteps" in item["source"]
        assert "csv" in item["source"]


def test_provenance_reaches_the_items(tmp_path):
    cfg = _config(_corpus(tmp_path))
    cfg["provenance"] = {"source_text": "Harvey Davis full dataset",
                         "species": "Rat", "location": "Stellate Ganglion"}
    doc, _ = _build(cfg)
    item = doc["data_items"][0]
    assert item["species"] == "Rat"
    assert item["location"] == "Stellate Ganglion"
    assert item["source"].startswith("Harvey Davis full dataset")


def test_extraction_emits_no_prediction_items(tmp_path):
    """A prediction is an extra entry in obs_data, not something extraction
    invents -- the CLI hardcodes one per experiment."""
    doc, _ = _build(_config(_corpus(tmp_path)))
    assert doc["prediction_items"] == []


def test_the_data_modifier_reaches_the_extracted_value(tmp_path):
    """The step is to -62 mV; with the LJP applied the observable must read
    -78.9, not -62."""
    plain, _ = _build(_config(_corpus(tmp_path, n_sweeps=1)))
    assert plain["data_items"][0]["value"] == pytest.approx(-62.0, abs=0.1)

    cfg = _config(_corpus(tmp_path, n_sweeps=1))
    cfg["data_modifiers"] = [
        {"name": "ljp", "target": "voltage", "modifier": "X - 16.9"}]
    shifted, _ = _build(cfg)
    assert shifted["data_items"][0]["value"] == pytest.approx(-78.9, abs=0.1)


@pytest.mark.parametrize(
    "spec,expected",
    [({"mode": "absolute", "value": 4.0}, 4.0),
     ({"mode": "fraction", "value": 0.1}, 6.2),
     (None, 6.2)],
)
def test_std_modes(tmp_path, spec, expected):
    feature = {"operation": "max_in_range", "unit": "milliV",
               "unit_confirmed": True, "operation_kwargs": {}}
    if spec is not None:
        feature["std"] = spec
    doc, _ = _build(_config(_corpus(tmp_path, n_sweeps=1), features=[feature]))
    assert doc["data_items"][0]["std"] == pytest.approx(expected, abs=0.05)


def test_an_unknown_std_mode_is_refused(tmp_path):
    feature = {"operation": "max_in_range", "unit": "milliV",
               "unit_confirmed": True, "std": {"mode": "vibes"}}
    with pytest.raises(ObsExtractError, match="unknown std mode"):
        _build(_config(_corpus(tmp_path), features=[feature]))


# ---------------------------------------------------------------------------
def test_a_feature_returning_nan_is_skipped_not_fatal(tmp_path):
    """An operation with nothing to return for a silent sweep is ordinary."""
    features = [
        {"operation": "always_nan", "unit": "milliV", "unit_confirmed": True},
        {"operation": "max_in_range", "unit": "milliV", "unit_confirmed": True,
         "name_suffix": "vmax"},
    ]
    doc, outcome = _build(_config(_corpus(tmp_path, n_sweeps=1), features=features))
    assert len(doc["data_items"]) == 1
    assert doc["data_items"][0]["operation"] == "max_in_range"


def test_an_operation_that_raises_is_recorded_and_skipped(tmp_path):
    features = [
        {"operation": "explodes", "unit": "milliV", "unit_confirmed": True},
        {"operation": "max_in_range", "unit": "milliV", "unit_confirmed": True},
    ]
    doc, outcome = _build(_config(_corpus(tmp_path, n_sweeps=1), features=features))
    assert len(doc["data_items"]) == 1
    assert any("boom" in s["reason"] for s in outcome.skipped)


def test_an_unknown_operation_is_recorded_and_skipped(tmp_path):
    features = [
        {"operation": "not_a_real_op", "unit": "milliV", "unit_confirmed": True},
        {"operation": "max_in_range", "unit": "milliV", "unit_confirmed": True},
    ]
    _, outcome = _build(_config(_corpus(tmp_path, n_sweeps=1), features=features))
    assert any("not_a_real_op" in s["reason"] for s in outcome.skipped)


def test_a_group_that_is_not_used_contributes_nothing(tmp_path):
    cfg = _config(_corpus(tmp_path))
    cfg["subprotocols"]["4AP|Currentsteps"]["used"] = False
    with pytest.raises(ObsExtractError, match="no data items"):
        _build(cfg)


def test_no_used_datasets_is_a_clear_error(tmp_path):
    cfg = _config(_corpus(tmp_path))
    for d in cfg["datasets"]:
        d["used"] = False
    with pytest.raises(ObsExtractError, match="nothing to extract"):
        _build(cfg)


def test_an_unreadable_file_is_skipped_with_its_reason(tmp_path):
    root = _corpus(tmp_path)
    (root / "4AP" / "broken.1.Currentsteps.1.csv").write_bytes(b"nope")
    cfg = _config(root)
    doc, outcome = _build(cfg)
    assert outcome.n_experiments == 3, "the good file still contributed"
    assert any("broken" in s["case_name"] for s in outcome.skipped)


def test_cancellation_is_reported_as_cancellation(tmp_path):
    """Not as "nothing was extractable" -- that sends the user looking for a
    fault in a config that was fine."""
    cfg = _config(_corpus(tmp_path))
    with pytest.raises(ObsExtractError, match="cancelled before any data item"):
        _build(cfg, cancelled=lambda: True)


def test_cancellation_part_way_keeps_what_was_already_extracted(tmp_path):
    """A thread cannot be terminated, so cancellation is cooperative: it is
    checked between sweeps and the work already done is not thrown away."""
    cfg = _config(_corpus(tmp_path, n_sweeps=3))
    calls = {"n": 0}

    def after_two_sweeps():
        # Checked once per dataset then once per sweep, so the 4th call is the
        # start of the third sweep.
        calls["n"] += 1
        return calls["n"] > 3

    doc, outcome = _build(cfg, cancelled=after_two_sweeps)
    assert 0 < outcome.n_experiments < 3
    assert doc["data_items"]
    assert any("cancelled part-way" in w for w in outcome.warnings), (
        "a partial result must be labelled as one")


def test_a_missing_binding_for_a_used_kind_is_refused(tmp_path):
    cfg = _config(_corpus(tmp_path))
    cfg["model_binding"]["current_command_param"] = None
    with pytest.raises(ObsExtractError, match="current_command_param"):
        _build(cfg)


def test_the_document_is_validated_by_cuflynx_before_it_is_returned(tmp_path):
    """So a bad extraction fails here, with the config on screen, rather than at
    the editor's Save."""
    import obs_data

    doc, _ = _build(_config(_corpus(tmp_path)))
    parsed = obs_data.parse_obs_data(doc)
    assert parsed.n_experiments == 3
    assert len(parsed.data_items) == 3


def test_an_undetected_stimulus_is_warned_about(tmp_path):
    """Every fractional range then means a fraction of the whole sweep, which is
    a different measurement than the one that was drawn."""
    root = tmp_path
    (root / "4AP").mkdir(exist_ok=True)
    n = 200
    write_csv(root / "4AP" / "flat.1.Currentsteps.1.csv",
              [[ramp(n, -70, -70), np.zeros(n)]], dt=1e-4)
    cfg = _config(root)
    _, outcome = _build(cfg)
    assert any("no stimulus was detected" in w for w in outcome.warnings)


def test_an_unreachable_ca_registry_is_named_as_the_cause(tmp_path):
    """Otherwise every feature is skipped for the same reason and the user is
    sent to inspect a feature list that is fine."""
    with pytest.raises(ObsExtractError, match="operation registry is unavailable"):
        build_obs_data(_config(_corpus(tmp_path)), operation_funcs=None,
                       variables=VARIABLES)
