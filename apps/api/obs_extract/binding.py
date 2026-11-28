"""Which model variables the recording's signals correspond to.

Extraction has to know five things about the model before it can write a single
obs_data item:

===========================  =====================================================
``clamp_mode_param``         the switch that puts the model in voltage or current
                             clamp, and the value for each
``voltage_command_param``    what the commanded voltage trace is written to
``current_command_param``    what the injected current trace is written to
``measured_voltage_var``     the variable an observable reads under current clamp
``measured_current_var``     the variable an observable reads under voltage clamp
===========================  =====================================================

The CLI hardcodes all five as ``soma_SN/*``. That is why it works for exactly one
model. Here they are configuration, **pre-filled by scoring the loaded model's
own variables**, so the SN names come out by default without being a rule.

The scoring is on the *leaf* of each qualified name, restricted by category: a
command must be a parameter, because a parameter is the only thing CA can change,
and a measured quantity must be a state or an algebraic variable, because
observing a constant is never what was meant. Getting that wrong is not a
cosmetic error -- binding a command to a state produces a run where the stimulus
silently does nothing.

The clamp-mode switch is the one most easily forgotten, and the most damaging to
omit: without it a voltage-clamp protocol runs the model in current clamp, and
every extracted current is measured against a cell that was never clamped.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field

from .errors import ObsExtractError

#: role -> (leaf patterns, best first). Anchored, so ``^v$`` matches a variable
#: actually called ``V`` and not every name containing a v.
_PATTERNS: dict[str, tuple[str, ...]] = {
    "clamp_mode_param": (r"^set_v$", r"^clamp_mode$", r"^vclamp$", r"^v_clamp_on$"),
    "voltage_command_param": (r"^v_set$", r"^v_clamp$", r"^v_cmd$", r"^v_command$",
                              r"^v_hold$"),
    "current_command_param": (r"^i_in$", r"^i_stim$", r"^i_ext$", r"^i_app$",
                              r"^stim_current$", r"^i_inj"),
    "measured_voltage_variable": (r"^v_sensed$", r"^v_m$", r"^vm$", r"^v$",
                                  r"^v_membrane$", r"^membrane_potential$"),
    "measured_current_variable": (r"^i_tot", r"^i_m$", r"^i_ion$", r"^i_total",
                                  r"^i_sensed$"),
}

#: Which category each role must come from. ``params`` for anything the protocol
#: writes to, states/algebraic for anything it reads.
_CATEGORIES: dict[str, tuple[str, ...]] = {
    "clamp_mode_param": ("params",),
    "voltage_command_param": ("params",),
    "current_command_param": ("params",),
    "measured_voltage_variable": ("odes", "algebraic"),
    "measured_current_variable": ("odes", "algebraic"),
}

ROLES = tuple(_PATTERNS)


@dataclass
class ModelBinding:
    """The five roles, plus the numbers that make a clamp mode mean something."""

    clamp_mode_param: str | None = None
    clamp_voltage_value: float = 1.0
    clamp_current_value: float = 0.0
    voltage_command_param: str | None = None
    current_command_param: str | None = None
    measured_voltage_variable: str | None = None
    measured_current_variable: str | None = None
    #: The recorded current is in the instrument's units (pA here) and the model
    #: wants its own (nA), with the sign convention of an injected current. The
    #: CLI writes ``-1e-3 * Im`` as a literal; it is a number because the next
    #: model will not share either the units or the sign.
    current_command_scale: float = -1e-3
    voltage_command_scale: float = 1.0
    #: qname -> unit string, for pre-filling a feature's unit.
    units: dict = field(default_factory=dict)

    def measured_variable(self, stimulus: str) -> str | None:
        """What an observable reads, given what is being commanded.

        Under current clamp you command current and measure voltage; under
        voltage clamp the other way round. One place decides this, because the
        one thing worse than picking the wrong variable is picking a different
        wrong variable in two places.
        """
        if stimulus == "current":
            return self.measured_voltage_variable
        if stimulus == "voltage":
            return self.measured_current_variable
        raise ObsExtractError(
            f"unknown stimulus kind {stimulus!r}; expected 'current' or 'voltage'")

    def command_param(self, stimulus: str) -> str | None:
        return (self.current_command_param if stimulus == "current"
                else self.voltage_command_param)

    def command_scale(self, stimulus: str) -> float:
        return (self.current_command_scale if stimulus == "current"
                else self.voltage_command_scale)

    def clamp_value(self, stimulus: str) -> float:
        return (self.clamp_current_value if stimulus == "current"
                else self.clamp_voltage_value)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict | None) -> ModelBinding:
        data = dict(data or {})
        # The config nests the clamp switch and its two values; the dataclass
        # keeps them flat because every call site wants one of the three.
        mode = data.pop("clamp_mode_param", None)
        if isinstance(mode, dict):
            data["clamp_mode_param"] = mode.get("qname")
            data["clamp_voltage_value"] = float(mode.get("voltage_value", 1.0))
            data["clamp_current_value"] = float(mode.get("current_value", 0.0))
        else:
            data["clamp_mode_param"] = mode
        known = {f for f in cls.__dataclass_fields__}  # noqa: SIM118
        return cls(**{k: v for k, v in data.items() if k in known})


def suggest(variables: dict | None) -> dict:
    """Ranked candidates per role, from the model's own variables.

    ``variables`` is the payload of ``GET /api/models/{id}/variables``:
    ``params``, ``odes``, ``algebraic``, ``all_names``, ``units``.

    Returns ``{role: [{"qname", "score", "category"}, ...]}``, best first, so the
    GUI can preselect the top candidate and still offer the rest in order. A role
    with no candidate gets an empty list rather than a guess -- there is no
    sensible default for "which variable is the membrane potential" in a model
    that does not appear to have one.
    """
    variables = variables or {}
    by_category = {
        "params": list(variables.get("params") or []),
        "odes": list(variables.get("odes") or []),
        "algebraic": list(variables.get("algebraic") or []),
    }
    out: dict[str, list[dict]] = {}
    for role, patterns in _PATTERNS.items():
        scored: list[dict] = []
        for category in _CATEGORIES[role]:
            for qname in by_category.get(category, []):
                score = _score(qname, patterns)
                if score is not None:
                    scored.append({"qname": qname, "score": score,
                                   "category": category})
        # Best pattern first; ties broken by the shorter name, which is almost
        # always the primary variable rather than a derived one.
        scored.sort(key=lambda c: (c["score"], len(c["qname"])))
        out[role] = scored
    return out


def _score(qname: str, patterns: tuple[str, ...]) -> int | None:
    """Index of the first matching pattern, or None. Lower is better."""
    leaf = str(qname).rsplit("/", 1)[-1].strip().lower()
    for i, pattern in enumerate(patterns):
        if re.search(pattern, leaf):
            return i
    return None


def suggested_binding(variables: dict | None) -> ModelBinding:
    """A :class:`ModelBinding` filled with each role's best candidate."""
    ranked = suggest(variables)
    binding = ModelBinding(units=dict((variables or {}).get("units") or {}))
    for role, candidates in ranked.items():
        if candidates:
            setattr(binding, role, candidates[0]["qname"])
    return binding


def validate(binding: ModelBinding, variables: dict | None, *, stimulus_kinds=()) -> list[str]:
    """Check a binding against the model. Returns warnings; raises on errors.

    The distinction: a **warning** is a role left unset that this extraction does
    not need, or a variable that exists but in an unexpected category. An
    **error** is a qname the model does not have, or a role that *is* needed for
    one of the stimulus kinds actually in use and is missing -- because that
    produces a document CA will either reject or, worse, run with the stimulus
    quietly doing nothing.
    """
    variables = variables or {}
    all_names = set(variables.get("all_names") or [])
    params = set(variables.get("params") or [])
    states = set(variables.get("odes") or []) | set(variables.get("algebraic") or [])
    warnings: list[str] = []

    for role in ROLES:
        qname = getattr(binding, role, None)
        if not qname:
            continue
        if all_names and qname not in all_names:
            raise ObsExtractError(
                f"{role} is bound to {qname!r}, which this model does not have.")
        wants_param = "params" in _CATEGORIES[role]
        if wants_param and params and qname not in params:
            raise ObsExtractError(
                f"{role} is bound to {qname!r}, which is not a parameter of this "
                f"model. A protocol can only change parameters, so a command "
                f"bound to a state or an algebraic variable would silently do "
                f"nothing.")
        if not wants_param and states and qname not in states:
            warnings.append(
                f"{role} is bound to {qname!r}, which is a parameter rather than "
                f"a computed variable; observing it will give a constant.")

    kinds = set(stimulus_kinds or ())
    for kind in sorted(kinds):
        if not binding.command_param(kind):
            raise ObsExtractError(
                f"a {kind}-clamp group is included, but no "
                f"{kind}_command_param is bound -- there is nothing to write the "
                f"stimulus to.")
        if not binding.measured_variable(kind):
            raise ObsExtractError(
                f"a {kind}-clamp group is included, but the variable it would "
                f"measure is not bound.")
    if kinds and not binding.clamp_mode_param:
        warnings.append(
            "no clamp_mode_param is bound. If this model has a switch between "
            "voltage and current clamp, leaving it unset runs every experiment "
            "in whichever mode the model defaults to.")
    return warnings
