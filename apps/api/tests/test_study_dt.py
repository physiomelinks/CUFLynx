"""Opening a study adopts the timestep its own data requires.

The app keeps ``DEFAULT_DT`` (0.01 s) until something changes it, and opening a finished run
from its output directory never did. A study whose series are sampled finer than that cannot
be scored at all -- CA refuses to compare a solver output against data it cannot be resampled
onto -- so ``POST /api/protocol/run`` failed on an SN_full study, and because the CA guard of
the day called ``exit()`` rather than raising, it failed as a bare 500.

``_finest_scored_obs_dt`` reads that requirement off the obs_data itself. Not off the study
manifest, which records no timestep; not off a ``user_inputs.yaml``, which a finished run
directory need not contain.

The subtlety these tests exist for is *scored*. SN_full ships eight placeholder series --
zero weight, no samples, ``obs_dt`` 1e-4 -- and honouring those would drop the app's timestep
a hundredfold to satisfy items nothing ever reads.
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
