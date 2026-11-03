"""Data modifiers: the general form of the CLI's single ``--ljp`` float.

The security half of this file matters as much as the arithmetic half. A
modifier expression arrives from a config file that may have been written
anywhere, and is applied to a file the user browsed to. ``eval`` on it would be
arbitrary code execution, so the grammar is walked and everything outside it is
refused -- and there is no flag to relax that.
"""

from __future__ import annotations

import numpy as np
import pytest

from obs_extract import (
    ObsExtractError,
    apply_modifiers,
    check_references,
    compile_expression,
    load_modifiers,
)

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    "expression,x,expected",
    [
        ("X - 16.9", [0.0, -70.0], [-16.9, -86.9]),   # the liquid junction potential
        ("X * 1.02", [100.0], [102.0]),                # an amplifier gain
        ("X", [1.0, 2.0], [1.0, 2.0]),                 # identity
        ("-X", [1.0], [-1.0]),
        ("(X - 10) * 2", [15.0], [10.0]),
        ("X / 1000", [1000.0], [1.0]),                 # a unit conversion
        ("X ** 2", [3.0], [9.0]),
        ("2 * X + 1", [3.0], [7.0]),
        ("X - -5", [0.0], [5.0]),
    ],
)
def test_arithmetic(expression, x, expected):
    fn = compile_expression(expression)
    assert np.allclose(fn(np.array(x, dtype=float)), expected)


@pytest.mark.parametrize(
    "expression,fragment",
    [
        ("__import__('os').system('ls')", "Call"),
        ("X.mean()", "Call"),  # a Call wrapping an Attribute; the outer node is reported
        ("open('/etc/passwd').read()", "Call"),
        ("np.abs(X)", "Call"),
        ("Y - 1", "'Y'"),
        ("X.real", "Attribute"),  # an attribute with no call is still refused
        ("[i for i in X]", "ListComp"),
        ("lambda: 1", "Lambda"),
        ("X if X else 0", "IfExp"),
        ("X % 3", "Mod"),
        ("X & 1", "BitAnd"),
        ("'abc'", "'abc'"),
    ],
)
def test_everything_outside_the_grammar_is_refused(expression, fragment):
    """And the message names what was found, so a mistake is fixable."""
    with pytest.raises(ObsExtractError) as exc:
        compile_expression(expression)
    assert fragment in str(exc.value)


def test_a_call_never_runs_even_partially(tmp_path):
    """The walk happens before any evaluation, so nothing executes on refusal."""
    marker = tmp_path / "written"
    expr = f"__import__('pathlib').Path({str(marker)!r}).write_text('x')"
    with pytest.raises(ObsExtractError):
        compile_expression(expr)
    assert not marker.exists()


def test_an_empty_expression_is_refused():
    with pytest.raises(ObsExtractError, match="needs an expression"):
        compile_expression("")


def test_a_syntax_error_is_reported_as_the_users_error():
    with pytest.raises(ObsExtractError, match="could not parse"):
        compile_expression("X - ")


# ---------------------------------------------------------------------------
def test_load_modifiers_compiles_up_front():
    """A typo must surface when the config is validated, not half way through
    an extraction that has already written output."""
    with pytest.raises(ObsExtractError, match="liquid_junction_potential"):
        load_modifiers([{"name": "liquid_junction_potential", "target": "voltage",
                         "modifier": "X - "}])


def test_a_modifier_needs_a_target():
    with pytest.raises(ObsExtractError, match="no target"):
        load_modifiers([{"name": "ljp", "modifier": "X - 16.9"}])


def test_apply_by_role_and_by_channel_name():
    signals = {"Vm0": np.array([0.0, -70.0]), "Im0": np.array([10.0, 20.0])}
    roles = {"Vm0": "voltage", "Im0": "current"}

    by_role, notes = apply_modifiers(signals, roles, load_modifiers(
        [{"name": "ljp", "target": "voltage", "modifier": "X - 16.9"}]))
    assert np.allclose(by_role["Vm0"], [-16.9, -86.9])
    assert np.allclose(by_role["Im0"], [10.0, 20.0]), "the other channel is untouched"
    assert any("applied ljp" in n for n in notes)

    by_name, _ = apply_modifiers(signals, roles, load_modifiers(
        [{"name": "gain", "target": "Im0", "modifier": "X * 2"}]))
    assert np.allclose(by_name["Im0"], [20.0, 40.0])


def test_modifiers_apply_in_order():
    """Order is part of the meaning: subtract-then-scale is not scale-then-subtract."""
    signals = {"Vm": np.array([100.0])}
    roles = {"Vm": "voltage"}
    mods = load_modifiers([
        {"name": "offset", "target": "voltage", "modifier": "X - 20"},
        {"name": "gain", "target": "voltage", "modifier": "X * 2"},
    ])
    got, _ = apply_modifiers(signals, roles, mods)
    assert got["Vm"][0] == pytest.approx(160.0)

    reversed_mods = load_modifiers([
        {"name": "gain", "target": "voltage", "modifier": "X * 2"},
        {"name": "offset", "target": "voltage", "modifier": "X - 20"},
    ])
    got2, _ = apply_modifiers(signals, roles, reversed_mods)
    assert got2["Vm"][0] == pytest.approx(180.0)


def test_a_modifier_that_matches_nothing_is_reported():
    """Silently skipping it would extract uncorrected data that looks correct."""
    signals = {"Vm": np.array([1.0])}
    roles = {"Vm": "voltage"}
    _, notes = apply_modifiers(signals, roles, load_modifiers(
        [{"name": "ljp", "target": "curent", "modifier": "X - 16.9"}]))  # typo
    assert any("not applied" in n and "curent" in n for n in notes)


def test_the_input_is_not_mutated():
    original = np.array([1.0, 2.0])
    signals = {"Vm": original}
    got, _ = apply_modifiers(signals, {"Vm": "voltage"}, load_modifiers(
        [{"name": "m", "target": "voltage", "modifier": "X * 10"}]))
    assert np.allclose(original, [1.0, 2.0])
    assert np.allclose(got["Vm"], [10.0, 20.0])


def test_describe_reads_as_a_report_line():
    mod = load_modifiers(
        [{"name": "liquid_junction_potential", "target": "voltage",
          "modifier": "X - 16.9"}])[0]
    assert mod.describe() == "liquid_junction_potential: voltage -> X - 16.9"


# ---------------------------------------------------------------------------
# Channel references: an expression may read the recording's other channels.
#
# The case that needs it: on the Wistar .wcp recordings the "Vm" channel is the
# command plus I*Rs (36 Kv-90 plateaus fit Vm = cmd + 11.12 MOhm * Im with a
# 0.03 mV residual), so the command is Vm - Rs*Im -- which X alone cannot say.

RS_MV_PER_PA = 0.01112  # 11.12 MOhm * 1 pA = 0.01112 mV


def _rs_recording():
    command = np.array([-89.69, -84.61, -79.53])
    im = np.array([0.0, 2000.0, 9000.0])  # pA
    return {"Vm0": command + RS_MV_PER_PA * im, "Im0": im}, \
        {"Vm0": "voltage", "Im0": "current"}, command


def test_a_series_resistance_correction_reads_the_current_channel():
    signals, roles, command = _rs_recording()
    got, notes = apply_modifiers(signals, roles, load_modifiers([
        {"name": "series_resistance", "target": "voltage",
         "modifier": f"X - {RS_MV_PER_PA} * current"}]))
    assert np.allclose(got["Vm0"], command)
    assert np.allclose(got["Im0"], signals["Im0"]), "the referenced channel is read, not written"
    assert any("applied series_resistance" in n for n in notes)


def test_a_reference_may_use_the_exact_channel_name():
    signals, roles, command = _rs_recording()
    got, _ = apply_modifiers(signals, roles, load_modifiers([
        {"name": "rs", "target": "Vm0", "modifier": f"X - {RS_MV_PER_PA} * Im0"}]))
    assert np.allclose(got["Vm0"], command)


def test_load_modifiers_accepts_a_reference_and_records_it():
    mod = load_modifiers([{"name": "rs", "target": "voltage",
                           "modifier": "X - 0.01112 * current + 0 * current"}])[0]
    assert mod.references == ("current",)


def test_a_reference_to_a_channel_the_recording_lacks_is_refused_by_name():
    signals, roles, _ = _rs_recording()
    mods = load_modifiers([{"name": "rs", "target": "voltage",
                            "modifier": "X - 0.01112 * Iref"}])
    with pytest.raises(ObsExtractError) as exc:
        apply_modifiers(signals, roles, mods)
    message = str(exc.value)
    assert "'Iref'" in message and "rs" in message
    assert "Im0" in message and "current" in message, "says what the recording has"
    with pytest.raises(ObsExtractError, match="'Iref'"):
        check_references(mods, roles)


def test_a_role_held_by_two_channels_is_ambiguous():
    signals = {"Vm": np.zeros(2), "I1": np.ones(2), "I2": np.ones(2)}
    roles = {"Vm": "voltage", "I1": "current", "I2": "current"}
    with pytest.raises(ObsExtractError, match="I1, I2"):
        apply_modifiers(signals, roles, load_modifiers(
            [{"name": "rs", "target": "voltage", "modifier": "X - current"}]))


def test_a_name_never_reaches_python():
    """A reference is looked up in the channels only, so a builtin is just a
    channel that does not exist -- and a call is still refused at compile."""
    signals, roles, _ = _rs_recording()
    with pytest.raises(ObsExtractError, match="'__import__' is not a channel"):
        apply_modifiers(signals, roles, load_modifiers(
            [{"name": "m", "target": "voltage", "modifier": "X + __import__"}]))
    with pytest.raises(ObsExtractError, match="Call"):
        load_modifiers([{"name": "m", "target": "voltage",
                         "modifier": "X - current.sum()"}])


def test_compile_expression_only_knows_the_channels_it_is_given():
    fn = compile_expression("X - 2 * current", channels=["current"])
    assert np.allclose(fn(np.array([10.0]), {"current": np.array([3.0])}), [4.0])
    with pytest.raises(ObsExtractError, match="'current'"):
        compile_expression("X - 2 * current")


def test_a_reference_sees_earlier_modifiers_but_not_later_ones():
    """Order is part of the meaning here too: a gain on the current listed
    *before* the Rs line is in the current it subtracts; listed after, it is not."""
    signals = {"Vm": np.array([100.0]), "Im": np.array([10.0])}
    roles = {"Vm": "voltage", "Im": "current"}
    gain = {"name": "gain", "target": "current", "modifier": "X * 2"}
    rs = {"name": "rs", "target": "voltage", "modifier": "X - current"}

    gain_first, _ = apply_modifiers(signals, roles, load_modifiers([gain, rs]))
    assert gain_first["Vm"][0] == pytest.approx(80.0)
    assert gain_first["Im"][0] == pytest.approx(20.0)

    rs_first, _ = apply_modifiers(signals, roles, load_modifiers([rs, gain]))
    assert rs_first["Vm"][0] == pytest.approx(90.0)
    assert rs_first["Im"][0] == pytest.approx(20.0)


def test_a_modifier_reads_its_references_from_before_itself():
    """A modifier targeting both channels reads its references from one
    snapshot, so which target it writes first changes nothing."""
    signals = {"A": np.array([1.0]), "B": np.array([10.0])}
    roles = {"A": "x", "B": "x"}  # one role, two channels: the target matches both
    got, _ = apply_modifiers(signals, roles, load_modifiers(
        [{"name": "swap", "target": "x", "modifier": "A + B - X"}]))
    assert got["A"][0] == pytest.approx(10.0)
    assert got["B"][0] == pytest.approx(1.0)
