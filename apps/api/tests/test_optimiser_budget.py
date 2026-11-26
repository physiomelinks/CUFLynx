"""The unset calibration budget: one GA population, from CA's option schema.

CA's genetic algorithm exits when ``num_calls_to_function`` is below one
generation's population, so a fixed fallback of 100 killed every calibrate-first
sensitivity run and every exported calibration the user had not given a budget.
"""
import types

import pytest

import export_pipeline
import optimiser_budget
import sensitivity_runner
import uq_runner
from conftest import set_ca_module

# A GA schema with numbers unlike CA's real ones, so a test cannot pass by
# recomputing CA's defaults: population 5 + 5*3 + 7 = 27, under DEBUG 2 + 2*1 + 4 = 8.
GA_OPTIONS = [
    {"name": "num_calls_to_function", "default": None},
    {"name": "num_elite", "default": 9, "debug_default": 1},
    {"name": "num_survivors", "default": 5, "debug_default": 2},
    {"name": "num_mutations_per_survivor", "default": 3, "debug_default": 1},
    {"name": "num_cross_breed", "default": 7, "debug_default": 4},
]
METHOD_OPTIONS = {
    "genetic_algorithm": GA_OPTIONS,
    "CMA-ES": [{"name": "num_calls_to_function", "default": 5000}],
    "sp_minimize": [{"name": "max_iter", "default": 10}],
}


@pytest.fixture
def fake_schema(monkeypatch):
    set_ca_module(monkeypatch, "parsers.PrimitiveParsers", types.SimpleNamespace(
        param_id_method_options=lambda m: METHOD_OPTIONS.get(m, [])))


def test_population_from_schema_defaults():
    assert optimiser_budget.ga_population(GA_OPTIONS) == 27


def test_population_under_debug_uses_debug_defaults():
    assert optimiser_budget.ga_population(GA_OPTIONS, debug=True) == 8


def test_population_takes_user_set_sizes_over_the_schema():
    assert optimiser_budget.ga_population(GA_OPTIONS, {"num_survivors": 10}) == 10 + 30 + 7


def test_default_budget_per_method(fake_schema):
    assert optimiser_budget.default_num_calls("genetic_algorithm") == 27
    assert optimiser_budget.default_num_calls("genetic_algorithm", debug=True) == 8
    assert optimiser_budget.default_num_calls(None) == 27
    # CA's own default for a method that declares one; the old 100 only without.
    assert optimiser_budget.default_num_calls("CMA-ES") == 5000
    assert optimiser_budget.default_num_calls("sp_minimize") == optimiser_budget.LEGACY_FALLBACK


def test_default_budget_without_a_schema_keeps_the_old_fallback(monkeypatch):
    set_ca_module(monkeypatch, "parsers.PrimitiveParsers", types.SimpleNamespace())
    assert optimiser_budget.default_num_calls("genetic_algorithm") == 100


def _export_calibration(**calibration):
    return export_pipeline.build_user_inputs(
        file_prefix="m", model_type="cellml_only", solver="CVODE_myokit", solver_info={},
        dt=0.01, pre_time=0.0, sim_time=1.0, model_file="m.cellml", obs_file=None,
        params_for_id_file=None, calibration=calibration, sensitivity=None, uq=None,
        enabled=None,
    )["optimiser_options"]["num_calls_to_function"]


def test_export_falls_back_to_one_population(fake_schema):
    assert _export_calibration(param_id_method="genetic_algorithm") == 27
    assert _export_calibration(param_id_method="genetic_algorithm",
                               num_calls_to_function=None) == 27
    # The exported yaml carries no DEBUG, so CA runs it with DEBUG off.
    assert _export_calibration(param_id_method="genetic_algorithm", DEBUG=True) == 27
    assert _export_calibration(param_id_method="genetic_algorithm",
                               num_calls_to_function=400) == 400


class _StopAfterInit(Exception):
    pass


def _calibrate_first_budget(monkeypatch, settings):
    seen = {}

    class FakeParamID:
        def __init__(self, **kwargs):
            seen.update(kwargs)
            raise _StopAfterInit

    set_ca_module(monkeypatch, "param_id.paramID",
                  types.SimpleNamespace(CVS0DParamID=FakeParamID))
    config = {"model_path": "m.cellml", "params_path": "p.json", "obs_path": "o.json",
              "output_dir": "out"}
    with pytest.raises(_StopAfterInit):
        sensitivity_runner._calibrate_for_nominal(config, settings, {}, "cellml_only")
    return seen["optimiser_options"]["num_calls_to_function"], seen["DEBUG"]


def test_calibrate_first_falls_back_to_one_population(monkeypatch, fake_schema):
    assert _calibrate_first_budget(monkeypatch, {"param_id_method": "genetic_algorithm"}) == (27, False)
    assert _calibrate_first_budget(
        monkeypatch, {"param_id_method": "genetic_algorithm", "DEBUG": True}) == (8, True)
    assert _calibrate_first_budget(
        monkeypatch, {"param_id_method": "genetic_algorithm", "num_calls_to_function": None}) == (27, False)
    assert _calibrate_first_budget(
        monkeypatch, {"param_id_method": "genetic_algorithm", "num_calls_to_function": 300}) == (300, False)


def test_uq_calibrate_first_falls_back_to_one_population(fake_schema):
    """The UQ runner's calibrate-first search (and the profile likelihood's
    re-calibrations, which take its optimiser_options) gets the same fallback."""
    def budget(**settings):
        return uq_runner._optimiser_options(settings)["num_calls_to_function"]

    assert budget() == 27
    assert budget(num_calls_to_function=None, max_patience=None, cost_convergence=None) == 27
    assert budget(DEBUG=True) == 8
    assert budget(num_calls_to_function=300) == 300
