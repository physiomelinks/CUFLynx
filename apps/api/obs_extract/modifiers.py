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

**An expression may read the recording's other channels**, by the same names a
``target`` accepts: a role (``voltage``, ``current``) or an exact channel name.
That is what a correction which mixes channels needs. The commonest is a series
resistance left uncompensated, where the recorded "voltage" is the command plus
the drop across Rs:

    {"name": "series_resistance", "target": "voltage", "modifier": "X - 0.01112 * current"}

(11.12 MOhm times a current in pA is 0.01112 mV per pA; the coefficient is in
the channels' own recorded units, which nothing here rescales.)

*Ordering.* Modifiers run in list order, and a referenced channel is read **as
it stands when this modifier starts**: after every earlier modifier, before this
one. So an LJP listed before the Rs line has already come off ``voltage`` when
the Rs line reads it, and a gain on ``current`` listed *after* the Rs line has
not yet been applied to the current it subtracts. A modifier that targets
several channels reads all of its references from that same snapshot, so the
order in which it writes them does not matter.

*Which names.* A reference resolves to an exact channel name first, then to the
one channel holding that role. A name that is neither -- a typo, or a channel
this recording does not have -- is refused by name, listing what the recording
does have, and the dataset is skipped rather than extracted uncorrected. A role
held by two channels is refused as ambiguous; name the channel instead. A
channel name that is not a Python identifier (``"Im 2"``) cannot be written in
an expression; reach it through its role.

**Expressions are walked, never evaluated.** ``eval`` on a string a user typed
and forgot -- or that arrived in a config file from somewhere else -- is arbitrary
code execution. :func:`compile_expression` parses with ``ast`` and accepts only
numbers, ``X``, channel names, the four arithmetic operators, ``**`` and unary
minus. A call, an attribute, a comprehension: all refused, naming what was found.
A name is only ever looked up in the recording's channels -- never in Python's
namespace -- so ``__import__`` is just a channel that does not exist. There is no
flag to relax this.
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
    _fn: Callable[..., np.ndarray]
    #: Channel names the expression reads besides ``X``, in first-use order.
    references: tuple[str, ...] = ()

    def apply(self, values: np.ndarray,
              channels: dict[str, np.ndarray] | None = None) -> np.ndarray:
        """``values`` corrected. ``channels`` supplies each name in
        :attr:`references`, already resolved to samples."""
        return self._fn(np.asarray(values, dtype=float), channels or {})

    def describe(self) -> str:
        """One line for the report: what was done to which channel."""
        return f"{self.name}: {self.target} -> {self.expression}"


def compile_expression(
    expression: str, channels=(),
) -> Callable[..., np.ndarray]:
    """A callable for ``expression``, or :class:`ObsExtractError` saying why not.

    The whole grammar is: numbers, ``X``, the names in ``channels``,
    ``+ - * / **``, unary ``+``/``-``, and parentheses. Anything else is refused
    by name, so a user who typed something reasonable-looking but unsupported is
    told what, rather than getting a generic parse failure.

    The callable is ``fn(X)`` or ``fn(X, {name: samples})`` -- the second form
    for an expression that reads other channels.
    """
    text = str(expression or "").strip()
    if not text:
        raise ObsExtractError("a data modifier needs an expression, e.g. 'X - 16.9'")
    try:
        tree = ast.parse(text, mode="eval")
    except SyntaxError as exc:
        raise ObsExtractError(
            f"could not parse the modifier {text!r}: {exc.msg}") from exc

    node = _check(tree.body, text, frozenset(channels or ()))

    def run(values: np.ndarray, refs: dict | None = None) -> np.ndarray:
        return np.asarray(_eval(node, values, refs or {}), dtype=float)

    return run


def referenced_names(expression: str) -> tuple[str, ...]:
    """The names other than ``X`` an expression uses, in first-use order.

    Only parses; :func:`compile_expression` still decides whether the rest of
    the expression is allowed. An unparsable expression has no names here --
    compiling it reports the syntax error.
    """
    try:
        tree = ast.parse(str(expression or "").strip(), mode="eval")
    except SyntaxError:
        return ()
    out: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id != VARIABLE and node.id not in out:
            out.append(node.id)
    return tuple(out)


def _check(node: ast.AST, text: str, channels: frozenset = frozenset()) -> ast.AST:
    """Reject anything outside the grammar, before any data is involved."""
    if isinstance(node, ast.Constant):
        if isinstance(node.value, bool) or not isinstance(node.value, (int, float)):
            raise ObsExtractError(
                f"the modifier {text!r} contains {node.value!r}; only numbers "
                f"and {VARIABLE} are allowed.")
    elif isinstance(node, ast.Name):
        if node.id != VARIABLE and node.id not in channels:
            others = (f", and the channels {', '.join(sorted(channels))}"
                      if channels else "")
            raise ObsExtractError(
                f"the modifier {text!r} uses the name {node.id!r}, which is not "
                f"a channel. Available: {VARIABLE} (the target channel's "
                f"samples){others}.")
    elif isinstance(node, ast.BinOp):
        if type(node.op) not in _BINOPS:
            raise ObsExtractError(
                f"the modifier {text!r} uses {type(node.op).__name__}, which is "
                f"not allowed. Use + - * / or **.")
        _check(node.left, text, channels)
        _check(node.right, text, channels)
    elif isinstance(node, ast.UnaryOp):
        if not isinstance(node.op, (ast.UAdd, ast.USub)):
            raise ObsExtractError(
                f"the modifier {text!r} uses {type(node.op).__name__}, which is "
                f"not allowed.")
        _check(node.operand, text, channels)
    else:
        # Calls, attributes, subscripts, comprehensions, lambdas, walrus...
        raise ObsExtractError(
            f"the modifier {text!r} contains {type(node).__name__}, which is not "
            f"allowed. A modifier is arithmetic on {VARIABLE} and channel names "
            f"only -- no function calls or attributes.")
    return node


def _eval(node: ast.AST, x: np.ndarray, refs: dict):
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.Name):
        if node.id == VARIABLE:
            return x
        if node.id not in refs:
            raise ObsExtractError(
                f"the modifier reads the channel {node.id!r}, which was not supplied")
        return np.asarray(refs[node.id], dtype=float)
    if isinstance(node, ast.BinOp):
        return _BINOPS[type(node.op)](_eval(node.left, x, refs),
                                      _eval(node.right, x, refs))
    if isinstance(node, ast.UnaryOp):
        value = _eval(node.operand, x, refs)
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
        # Which channels exist is a fact about a recording, not the config, so
        # here every other name is accepted as a *candidate* channel and the
        # grammar is checked; apply_modifiers refuses a name the recording
        # does not have.
        references = referenced_names(expression)
        try:
            fn = compile_expression(expression, channels=references)
        except ObsExtractError as exc:
            raise ObsExtractError(f"data modifier {name!r}: {exc}") from exc
        out.append(Modifier(name, target, str(expression).strip(), fn, references))
    return out


def resolve_channel(name: str, roles: dict[str, str | None]) -> str:
    """The recorded channel ``name`` refers to: an exact channel name, else the
    one channel holding that role. Raises, naming what is available."""
    if name in roles:
        return name
    holders = [ch for ch, role in roles.items() if role == name]
    if len(holders) == 1:
        return holders[0]
    if len(holders) > 1:
        raise ObsExtractError(
            f"{name!r} is the role of {len(holders)} channels "
            f"({', '.join(holders)}); name the channel instead.")
    available = sorted(set(roles) | {r for r in roles.values() if r})
    raise ObsExtractError(
        f"{name!r} is not a channel of this recording. It has: "
        f"{', '.join(available) or 'no channels'}.")


def check_references(modifiers: list[Modifier], roles: dict[str, str | None]) -> None:
    """Refuse a modifier that reads a channel this recording does not have.

    Called once per recording, before any sweep is read, so a typo costs one
    skipped dataset with a message rather than a half-corrected extraction.
    """
    for mod in modifiers:
        for ref in mod.references:
            try:
                resolve_channel(ref, roles)
            except ObsExtractError as exc:
                raise ObsExtractError(f"data modifier {mod.name!r}: {exc}") from exc


def apply_modifiers(
    signals: dict[str, np.ndarray],
    roles: dict[str, str | None],
    modifiers: list[Modifier],
) -> tuple[dict[str, np.ndarray], list[str]]:
    """Apply every modifier to the channels it targets.

    ``target`` matches a **role** (``voltage``/``current``) or an exact channel
    name, so a config written against one instrument's channel names still works
    on another's as long as the roles resolve.

    A channel the expression reads is taken as it stands before this modifier
    and after the earlier ones (see the module docstring). A reference the
    recording cannot satisfy raises :class:`ObsExtractError`.

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
        refs = {}
        for ref in mod.references:
            try:
                refs[ref] = out[resolve_channel(ref, roles)]
            except ObsExtractError as exc:
                raise ObsExtractError(f"data modifier {mod.name!r}: {exc}") from exc
        # ``out`` is rebound per channel, never written in place, so ``refs``
        # stays the pre-modifier snapshot whatever order the targets go in.
        for name in targets:
            out[name] = mod.apply(out[name], refs)
        notes.append(f"applied {mod.describe()} to {', '.join(targets)}")
    return out, notes
