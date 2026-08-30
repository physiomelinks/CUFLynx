"""Binding the recording's signals to a model's variables.

The CLI hardcodes five ``soma_SN/*`` names, which is why it works for exactly one
model. These tests check that the SN names still come out by default -- and that
they come out because generic patterns match them, not because they are written
down anywhere.
"""

from __future__ import annotations

import pytest

from obs_extract import ModelBinding, ObsExtractError, suggest, suggested_binding
from obs_extract.binding import ROLES, validate

pytestmark = pytest.mark.unit

#: The SN model's shape, as GET /api/models/{id}/variables reports it.
SN_VARIABLES = {
    "params": ["soma_SN/set_V", "soma_SN/V_set", "soma_SN/I_in", "soma_SN/g_Na",
               "soma_SN/alpha_4AP", "soma_SN/C_sensed"],
    "odes": ["soma_SN/V", "soma_SN/Cai", "soma_SN/m"],
    "algebraic": ["soma_SN/V_sensed", "soma_SN/I_tot_pA", "soma_SN/i_Na"],
    "all_names": ["soma_SN/set_V", "soma_SN/V_set", "soma_SN/I_in", "soma_SN/g_Na",
                  "soma_SN/alpha_4AP", "soma_SN/C_sensed", "soma_SN/V",
                  "soma_SN/Cai", "soma_SN/m", "soma_SN/V_sensed",
                  "soma_SN/I_tot_pA", "soma_SN/i_Na"],
    "units": {"soma_SN/V_sensed": "millivolt", "soma_SN/I_tot_pA": "picoampere"},
}

#: A model with nothing resembling a patch-clamp rig.
LOTKA_VARIABLES = {
    "params": ["main/alpha", "main/beta"],
    "odes": ["main/x", "main/y"],
    "algebraic": [],
    "all_names": ["main/alpha", "main/beta", "main/x", "main/y"],
    "units": {},
}


def test_the_sn_names_come_out_by_default():
    binding = suggested_binding(SN_VARIABLES)
    assert binding.clamp_mode_param == "soma_SN/set_V"
    assert binding.voltage_command_param == "soma_SN/V_set"
    assert binding.current_command_param == "soma_SN/I_in"
    assert binding.measured_voltage_variable == "soma_SN/V_sensed"
    assert binding.measured_current_variable == "soma_SN/I_tot_pA"


def test_they_come_out_of_generic_patterns_not_a_hardcoded_list():
    """The same patterns bind a differently-named model, so nothing is special
    about ``soma_SN``."""
    other = {
        "params": ["cell/vclamp", "cell/v_cmd", "cell/i_stim"],
        "odes": ["cell/vm"],
        "algebraic": ["cell/i_ion"],
        "all_names": ["cell/vclamp", "cell/v_cmd", "cell/i_stim", "cell/vm",
                      "cell/i_ion"],
    }
    binding = suggested_binding(other)
    assert binding.clamp_mode_param == "cell/vclamp"
    assert binding.voltage_command_param == "cell/v_cmd"
    assert binding.current_command_param == "cell/i_stim"
    assert binding.measured_voltage_variable == "cell/vm"
    assert binding.measured_current_variable == "cell/i_ion"


def test_a_model_with_no_clamp_gets_no_guess():
    """An empty list, not a wrong answer. There is no sensible default for
    'which variable is the membrane potential' in a model that has none."""
    binding = suggested_binding(LOTKA_VARIABLES)
    for role in ROLES:
        assert getattr(binding, role) is None
    assert all(not candidates for candidates in suggest(LOTKA_VARIABLES).values())


def test_candidates_are_ranked_so_the_gui_can_offer_alternatives():
    ranked = suggest(SN_VARIABLES)
    voltages = [c["qname"] for c in ranked["measured_voltage_variable"]]
    assert voltages[0] == "soma_SN/V_sensed"
    assert "soma_SN/V" in voltages, "the runner-up is still offered"


def test_a_command_is_only_ever_looked_for_among_parameters():
    """CA can only change parameters. A command bound to a state would run and
    silently do nothing, which is the worst possible outcome."""
    ranked = suggest(SN_VARIABLES)
    for role in ("clamp_mode_param", "voltage_command_param", "current_command_param"):
        assert all(c["category"] == "params" for c in ranked[role])
    for role in ("measured_voltage_variable", "measured_current_variable"):
        assert all(c["category"] in ("odes", "algebraic") for c in ranked[role])


def test_suggest_carries_the_units_for_prefilling_a_features_unit():
    binding = suggested_binding(SN_VARIABLES)
    assert binding.units["soma_SN/V_sensed"] == "millivolt"


# ---------------------------------------------------------------------------
def test_measured_variable_follows_the_clamp_direction():
    """Command current, measure voltage; command voltage, measure current."""
    binding = suggested_binding(SN_VARIABLES)
    assert binding.measured_variable("current") == "soma_SN/V_sensed"
    assert binding.measured_variable("voltage") == "soma_SN/I_tot_pA"
    with pytest.raises(ObsExtractError, match="unknown stimulus kind"):
        binding.measured_variable("pressure")


def test_clamp_value_differs_by_direction():
    binding = ModelBinding(clamp_mode_param="soma_SN/set_V",
                           clamp_voltage_value=1.0, clamp_current_value=0.0)
    assert binding.clamp_value("voltage") == 1.0
    assert binding.clamp_value("current") == 0.0


def test_from_dict_unpacks_the_nested_clamp_switch():
    binding = ModelBinding.from_dict({
        "clamp_mode_param": {"qname": "a/s", "voltage_value": 2.0, "current_value": -1.0},
        "current_command_param": "a/i",
    })
    assert binding.clamp_mode_param == "a/s"
    assert binding.clamp_voltage_value == 2.0
    assert binding.clamp_current_value == -1.0
    assert binding.current_command_param == "a/i"


def test_from_dict_ignores_keys_it_does_not_know():
    """A config from a newer CUFLynx must not crash an older one here."""
    binding = ModelBinding.from_dict({"current_command_param": "a/i", "future": 1})
    assert binding.current_command_param == "a/i"


# ---------------------------------------------------------------------------
def test_validate_rejects_a_variable_the_model_does_not_have():
    binding = ModelBinding(current_command_param="soma_SN/nope")
    with pytest.raises(ObsExtractError, match="does not have"):
        validate(binding, SN_VARIABLES)


def test_validate_rejects_a_command_bound_to_a_state():
    binding = ModelBinding(current_command_param="soma_SN/V")
    with pytest.raises(ObsExtractError, match="not a parameter"):
        validate(binding, SN_VARIABLES)


def test_validate_warns_when_an_observable_is_a_parameter():
    binding = ModelBinding(measured_voltage_variable="soma_SN/g_Na")
    warnings = validate(binding, SN_VARIABLES)
    assert any("constant" in w for w in warnings)


def test_validate_demands_a_command_for_a_stimulus_kind_in_use():
    binding = ModelBinding(measured_voltage_variable="soma_SN/V_sensed")
    with pytest.raises(ObsExtractError, match="current_command_param"):
        validate(binding, SN_VARIABLES, stimulus_kinds={"current"})


def test_validate_demands_the_variable_that_would_be_measured():
    binding = ModelBinding(current_command_param="soma_SN/I_in")
    with pytest.raises(ObsExtractError, match="measure"):
        validate(binding, SN_VARIABLES, stimulus_kinds={"current"})


def test_a_missing_clamp_switch_is_a_warning_not_an_error():
    """Not every model has one -- but leaving it unset on a model that does runs
    every experiment in whichever mode the model defaults to, so it is said."""
    binding = ModelBinding(current_command_param="soma_SN/I_in",
                           measured_voltage_variable="soma_SN/V_sensed")
    warnings = validate(binding, SN_VARIABLES, stimulus_kinds={"current"})
    assert any("clamp_mode_param" in w for w in warnings)


def test_validate_passes_a_complete_binding_silently():
    binding = suggested_binding(SN_VARIABLES)
    assert validate(binding, SN_VARIABLES, stimulus_kinds={"current", "voltage"}) == []
