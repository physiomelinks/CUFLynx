"""Opening a study solves it the way the study was solved.

The app keeps ``DEFAULT_DT`` until something changes it, and opening a finished run from its
output directory used to adopt nothing at all. Two things went wrong with that, and these
tests cover both routes out.

**The trace.** ``dt`` is the *output* interval. A study run at 1e-4 reopened at 0.01 samples
straight over every 1-4 ms action potential, so the trace shows only the voltage between
spikes and appears to sit near -20 mV. Nothing is wrong with the resting potential or the
parameters, and nothing on screen says so. ``_adopt_study_solver_info`` applies what the
manifest now records -- dt, solver, and options such as ``MaximumStep``.

**The cost.** A study whose series are sampled finer than the app's dt cannot be scored at
all: CA will not compare a solver output against data it cannot be resampled onto.
``_finest_scored_obs_dt`` recovers that requirement from the obs_data, for the manifests
written before solver settings were recorded in them.

The subtlety the second half exists for is *scored*. SN_full ships eight placeholder series
-- zero weight, no samples, ``obs_dt`` 1e-4 -- and honouring those would drop the app's
timestep a hundredfold to satisfy items nothing ever reads.
"""
import json

import pytest

from main import _finest_scored_obs_dt


def write(tmp_path, items):
    path = tmp_path / "obs_data.json"
    path.write_text(json.dumps({"data_items": items}))
    return str(path)


def series(obs_dt=1e-4, weight=1.0, value=(0.0, 1.0), **extra):
    row = {"data_type": "series", "operands": ["soma/V"], "operation": "series",
           "value": list(value), "std": 1.0, "weight": weight, "obs_dt": obs_dt}
    row.update(extra)
    return row


def test_a_scored_series_sets_the_requirement(tmp_path):
    assert _finest_scored_obs_dt(write(tmp_path, [series(obs_dt=1e-4)])) == pytest.approx(1e-4)


def test_the_finest_of_several_wins(tmp_path):
    path = write(tmp_path, [series(obs_dt=0.01), series(obs_dt=1e-4), series(obs_dt=0.5)])
    assert _finest_scored_obs_dt(path) == pytest.approx(1e-4)


def test_zero_weighted_series_are_ignored(tmp_path):
    """A zero weight drops an item from the cost, so it cannot require anything of dt."""
    assert _finest_scored_obs_dt(write(tmp_path, [series(weight=0.0)])) is None


def test_empty_series_are_ignored(tmp_path):
    """Nothing to compare against, whatever the solver produces."""
    assert _finest_scored_obs_dt(write(tmp_path, [series(value=[])])) is None


def test_the_sn_full_placeholders_impose_nothing(tmp_path):
    """The case that started this: eight empty, zero-weighted series at 1e-4 beside real
    constants. The app must stay at its own dt rather than run a hundred times slower."""
    items = [series(obs_dt=1e-4, weight=0.0, value=[]) for _ in range(8)]
    items.append({"data_type": "constant", "operands": ["soma/V"], "operation": "max",
                  "value": 1.0, "std": 5.0, "weight": 1.0})
    assert _finest_scored_obs_dt(write(tmp_path, items)) is None


def test_a_real_series_among_placeholders_still_counts(tmp_path):
    items = [series(obs_dt=1e-4, weight=0.0, value=[]) for _ in range(8)]
    items.append(series(obs_dt=2e-4, weight=1.0, value=[0.0, 1.0]))
    assert _finest_scored_obs_dt(write(tmp_path, items)) == pytest.approx(2e-4)


def test_constants_alone_impose_nothing(tmp_path):
    assert _finest_scored_obs_dt(write(tmp_path, [
        {"data_type": "constant", "operands": ["soma/V"], "operation": "max",
         "value": 1.0, "std": 1.0, "weight": 1.0}])) is None


@pytest.mark.parametrize("bad", [None, "", "/nonexistent/obs_data.json"])
def test_a_missing_path_is_none_not_an_error(bad):
    """Opening a study whose obs_data has moved should still show the results that are
    there, so this reports 'no requirement' rather than raising."""
    assert _finest_scored_obs_dt(bad) is None


def test_unreadable_json_is_none_not_an_error(tmp_path):
    path = tmp_path / "obs_data.json"
    path.write_text("{not json")
    assert _finest_scored_obs_dt(str(path)) is None


def test_a_series_without_obs_dt_is_skipped(tmp_path):
    row = series()
    del row["obs_dt"]
    assert _finest_scored_obs_dt(write(tmp_path, [row])) is None


def test_a_nonpositive_obs_dt_is_skipped(tmp_path):
    """Zero would set a timestep no solver can take."""
    assert _finest_scored_obs_dt(write(tmp_path, [series(obs_dt=0.0)])) is None


def test_a_bare_list_document_is_accepted(tmp_path):
    """Some obs_data files are a bare list rather than {'data_items': [...]}."""
    path = tmp_path / "obs_data.json"
    path.write_text(json.dumps([series(obs_dt=1e-4)]))
    assert _finest_scored_obs_dt(str(path)) == pytest.approx(1e-4)


# ------------------------------------------------- solver settings recorded by the manifest

from engine import engine  # noqa: E402
from main import _adopt_study_solver_info  # noqa: E402


@pytest.fixture
def restore_engine():
    """The engine is a module-level singleton, so a test that changes it must put it back."""
    before = (engine.dt, engine.solver, dict(getattr(engine, "solver_info", {}) or {}))
    yield
    engine.dt, engine.solver = before[0], before[1]
    engine.solver_info = before[2]


def test_a_recorded_dt_is_adopted_and_reported(restore_engine):
    """The -20 mV case. dt is the *output* interval, so a 10 ms grid samples over every
    1-4 ms action potential and the trace appears to sit between spikes."""
    engine.dt = 0.01
    notes = _adopt_study_solver_info({"solver": "CVODE_myokit", "dt": 1e-4})
    assert engine.dt == pytest.approx(1e-4)
    assert any("0.0001" in n and "0.01" in n for n in notes), \
        'the change must be reported, not applied silently -- it makes runs 100x slower'


def test_solver_options_are_carried_onto_the_engine(restore_engine):
    engine.solver_info = {"rtol": 1e-6}
    _adopt_study_solver_info({"dt": 1e-4, "MaximumStep": 1e-4})
    assert engine.solver_info["MaximumStep"] == pytest.approx(1e-4)
    assert engine.solver_info["rtol"] == pytest.approx(1e-6), 'existing options survive'


def test_the_solver_is_adopted(restore_engine):
    engine.solver = "something_else"
    notes = _adopt_study_solver_info({"solver": "CVODE_myokit"})
    assert engine.solver == "CVODE_myokit"
    assert any("CVODE_myokit" in n for n in notes)


def test_an_unchanged_dt_is_not_reported(restore_engine):
    """Opening a study already solved the way the app is set up should say nothing."""
    engine.dt = 1e-4
    assert _adopt_study_solver_info({"dt": 1e-4}) == []


def test_a_manifest_without_solver_settings_changes_nothing(restore_engine):
    """Every manifest written before this existed. They must still open."""
    before = engine.dt
    assert _adopt_study_solver_info(None) == []
    assert _adopt_study_solver_info({}) == []
    assert engine.dt == before


def test_a_nonsense_dt_is_ignored(restore_engine):
    before = engine.dt
    for bad in ({"dt": "fast"}, {"dt": None}, {"dt": 0}, {"dt": -1}):
        _adopt_study_solver_info(bad)
        assert engine.dt == before


def test_cas_method_key_is_not_mistaken_for_a_solver_option(restore_engine):
    """CA writes `method: CVODE` beside `solver: CVODE_myokit`; only the latter is ours."""
    engine.solver_info = {}
    _adopt_study_solver_info({"solver": "CVODE_myokit", "method": "CVODE", "dt": 1e-4})
    assert "method" not in engine.solver_info
