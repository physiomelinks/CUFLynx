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

from obs_extract import ObsExtractError, apply_modifiers, compile_expression, load_modifiers

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
