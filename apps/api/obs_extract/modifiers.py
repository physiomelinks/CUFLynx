"""Corrections applied to a recorded channel before anything reads it.

A recorded signal is rarely the quantity you want. The commonest case is the
liquid junction potential -- a few millivolts of offset between the pipette and
bath solutions that has to come off every voltage trace -- but it is one of a
family: an amplifier gain that was set wrong, a unit that was recorded in A and
is wanted in pA, a known baseline drift.

The CLI this replaces has a single ``--ljp`` float. That works for exactly one
correction on exactly one channel. Here a modifier is instead a named expression
against a target:

    {"name": "liquid_junction_potential", "target": "voltage", "modifier": "X - 16.9"}
    {"name": "amplifier_gain",            "target": "current", "modifier": "X * 1.02"}

``X`` is the channel's samples. Modifiers apply **in order**, to recorded
channels, before stimulus-window detection, before feature extraction and before
the clamp command trace is built -- so every downstream number sees one corrected
signal rather than each stage correcting for itself. Each is named so the report
can list exactly what was applied.

**Expressions are walked, never evaluated.** ``eval`` on a string a user typed
and forgot -- or that arrived in a config file from somewhere else -- is arbitrary
code execution. :func:`compile_expression` parses with ``ast`` and accepts only
numbers, ``X``, the four arithmetic operators, ``**`` and unary minus. A call, an
attribute, a name that is not ``X``, a comprehension: all refused, naming what was
found. There is no flag to relax this.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from typing import Callable

import numpy as np

from .errors import ObsExtractError

#: The free variable an expression may use: the channel's samples.
VARIABLE = "X"

#: Binary operators with an obvious numeric meaning and no surprises. Notably
#: absent: ``%``, ``//``, ``&``, ``|``, ``^``, ``<<``, ``>>`` -- none of them mean
#: anything sensible applied elementwise to a voltage trace, so allowing them
#: would only widen what a typo can do.
_BINOPS: dict[type, Callable] = {
    ast.Add: np.add,
    ast.Sub: np.subtract,
    ast.Mult: np.multiply,
    ast.Div: np.divide,
    ast.Pow: np.power,
}


@dataclass(frozen=True)
class Modifier:
    """One named correction, ready to apply."""

    name: str
    target: str
    expression: str
    _fn: Callable[[np.ndarray], np.ndarray]

    def apply(self, values: np.ndarray) -> np.ndarray:
        return self._fn(np.asarray(values, dtype=float))

    def describe(self) -> str:
        """One line for the report: what was done to which channel."""
        return f"{self.name}: {self.target} -> {self.expression}"


def compile_expression(expression: str) -> Callable[[np.ndarray], np.ndarray]:
    """A callable for ``expression``, or :class:`ObsExtractError` saying why not.

    The whole grammar is: numbers, ``X``, ``+ - * / **``, unary ``+``/``-``, and
    parentheses. Anything else is refused by name, so a user who typed something
    reasonable-looking but unsupported is told what, rather than getting a
    generic parse failure.
    """
    text = str(expression or "").strip()
    if not text:
        raise ObsExtractError("a data modifier needs an expression, e.g. 'X - 16.9'")
    try:
        tree = ast.parse(text, mode="eval")
    except SyntaxError as exc:
        raise ObsExtractError(
            f"could not parse the modifier {text!r}: {exc.msg}") from exc

    node = _check(tree.body, text)

    def run(values: np.ndarray) -> np.ndarray:
        return np.asarray(_eval(node, values), dtype=float)

    return run


def _check(node: ast.AST, text: str) -> ast.AST:
    """Reject anything outside the grammar, before any data is involved."""
    if isinstance(node, ast.Constant):
        if isinstance(node.value, bool) or not isinstance(node.value, (int, float)):
            raise ObsExtractError(
                f"the modifier {text!r} contains {node.value!r}; only numbers "
                f"and {VARIABLE} are allowed.")
    elif isinstance(node, ast.Name):
        if node.id != VARIABLE:
            raise ObsExtractError(
                f"the modifier {text!r} uses the name {node.id!r}. The only "
                f"variable available is {VARIABLE}, the channel's samples.")
    elif isinstance(node, ast.BinOp):
        if type(node.op) not in _BINOPS:
            raise ObsExtractError(
                f"the modifier {text!r} uses {type(node.op).__name__}, which is "
                f"not allowed. Use + - * / or **.")
        _check(node.left, text)
        _check(node.right, text)
    elif isinstance(node, ast.UnaryOp):
        if not isinstance(node.op, (ast.UAdd, ast.USub)):
            raise ObsExtractError(
                f"the modifier {text!r} uses {type(node.op).__name__}, which is "
                f"not allowed.")
        _check(node.operand, text)
    else:
        # Calls, attributes, subscripts, comprehensions, lambdas, walrus...
        raise ObsExtractError(
            f"the modifier {text!r} contains {type(node).__name__}, which is not "
            f"allowed. A modifier is arithmetic on {VARIABLE} only -- no function "
            f"calls, attributes or names.")
    return node


def _eval(node: ast.AST, x: np.ndarray):
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.Name):
        return x
    if isinstance(node, ast.BinOp):
        return _BINOPS[type(node.op)](_eval(node.left, x), _eval(node.right, x))
    if isinstance(node, ast.UnaryOp):
        value = _eval(node.operand, x)
        return value if isinstance(node.op, ast.UAdd) else np.negative(value)
    raise ObsExtractError(  # pragma: no cover - _check ran first
        f"unexpected node {type(node).__name__}")


def load_modifiers(entries) -> list[Modifier]:
    """Compile a config's ``data_modifiers`` list.

    Every expression is compiled here, up front, so a typo is reported when the
    config is validated rather than part-way through an extraction that has
    already written half its output.
    """
    out: list[Modifier] = []
    for i, raw in enumerate(entries or []):
        if not isinstance(raw, dict):
            raise ObsExtractError(f"data_modifiers[{i}] is not an object")
        name = str(raw.get("name") or f"modifier {i}")
        target = str(raw.get("target") or "").strip()
        if not target:
            raise ObsExtractError(
                f"data modifier {name!r} has no target. Give it a channel role "
                f"('voltage' or 'current') or a channel name.")
        expression = raw.get("modifier")
        try:
            fn = compile_expression(expression)
        except ObsExtractError as exc:
            raise ObsExtractError(f"data modifier {name!r}: {exc}") from exc
        out.append(Modifier(name, target, str(expression).strip(), fn))
    return out


def apply_modifiers(
    signals: dict[str, np.ndarray],
    roles: dict[str, str | None],
    modifiers: list[Modifier],
) -> tuple[dict[str, np.ndarray], list[str]]:
    """Apply every modifier to the channels it targets.

    ``target`` matches a **role** (``voltage``/``current``) or an exact channel
    name, so a config written against one instrument's channel names still works
    on another's as long as the roles resolve.

    A modifier that matches nothing is reported rather than ignored: it almost
    always means the target was misspelled or the recording does not have that
    channel, and silently skipping it would extract uncorrected data that looks
    correct.
    """
    out = {k: np.asarray(v, dtype=float) for k, v in signals.items()}
    notes: list[str] = []
    for mod in modifiers:
        targets = [name for name, role in roles.items()
                   if role == mod.target or name == mod.target]
        if not targets:
            notes.append(
                f"data modifier {mod.name!r} targets {mod.target!r}, which this "
                f"recording has no channel for; not applied.")
            continue
        for name in targets:
            out[name] = mod.apply(out[name])
        notes.append(f"applied {mod.describe()} to {', '.join(targets)}")
    return out, notes
