"""Which parameter is driving the cost, beside the cost itself (issue #188).

The Output plots panel says what the current parameters cost (#159). It does not
say *which* of them the number is about: "the cost is 36.8" leaves the user
dragging sliders one at a time to find out which one moves it. This computes, per
parameter, the derivative of that same cost -- so the panel can say "and it is
`alpha` that is driving it".

**The cost is not recomputed here.** Every evaluation goes through the caller's
``cost_at``, which is the run-and-score path the displayed cost already uses
(``obs_cost.evaluate`` -> CA's ``get_cost_from_operands``). Only the *difference
quotient* lives here. A gradient of a slightly different cost than the one on
screen would be worse than no gradient at all: it would rank parameters against a
number the user cannot see.

**Why finite differences and not CA's own gradient.** ``ParamID`` exposes
``get_gradient`` / ``get_cost_and_gradient``, and they are better -- exact, and
one solve rather than 2M. But they need a *solver-backed* ``ParamID``:
a compiled model, ``do_ad``, and one of casadi_python / aadc_python / (cellml
+ CVODE_myokit + FSA). CUFLynx builds one of those only in the analysis subprocess
tier (calibration / SA / UQ runners), which costs a process launch and a model
compile per call and would put the live drag path back on the wrong side of the
interpreter split #167 removed. The live tier has a cached helper and a scored
run, and differencing it works on every backend the sliders work on. CA's own
``fd_backend`` makes the same trade for observable sensitivities, for the same
reason, and its ``_step`` is what :func:`step_for` mirrors.

**Central, not forward.** Forward differences would reuse the run already on
screen and cost M simulations instead of 2M. They are also the wrong tool near a
minimum, which is exactly where a user who has been dragging sliders ends up: the
truncation error is O(h) and does not vanish with the gradient, so a parameter
that is genuinely flat can come back with a confident sign. Being opt-in buys the
accuracy.
"""

from __future__ import annotations

import math

# ``d ln(J)/d ln(p)`` -- "the cost moves X% per 1% of this parameter", which is
# the question a slider asks. Raw dJ/dp cannot be ranked: across parameters
# measured in mmHg, seconds and litres per second, the largest derivative is
# whichever parameter happens to be smallest in its own units. Imported from
# local SA rather than reimplemented -- two panels reporting "relative
# sensitivity" have to mean the same thing by it, and it already handles the
# parameter sitting at 0 (normalise by its range) and the denominator at 0
# (report nothing, because a perfect fit is not an insensitive parameter).
from local_sensitivity import relative_coeff

# The relative FD step, and CA's own default (``fd_backend.observable_feature_
# sensitivities``' ``h``). Deliberately CA's 1e-3 rather than the 1e-2 CUFLynx's
# local SA defaults to: CA records the two differing by up to 48% on a rough
# functional like `max` of an oscillating trace, and a ranking shown beside the
# cost must not quietly disagree with the analysis tab next to it. (The *cost*
# is a sum over observables and is smoother than any one of them: on the
# Lotka-Volterra fixture h = 1e-4, 1e-3 and 1e-2 agree to under 1%. That is a
# reason to be relaxed about the choice, not a reason to hide it.) Exposed on the
# request, because no single step suits every model.
DEFAULT_REL_STEP = 1e-3


class CostRunError(RuntimeError):
    """A perturbed run that could not be made or could not be scored."""


def step_for(value: float, bounds=None, rel_step: float = DEFAULT_REL_STEP) -> float:
    """The central-difference step for one parameter.

    Relative to the parameter, because the parameters here span orders of
    magnitude and one absolute step cannot suit them all. A parameter sitting at
    exactly zero has no scale of its own, so its slider range supplies one; with
    no usable range, the bare ``rel_step`` -- anything else would be a zero step,
    and a division by zero. Mirrors CA's ``fd_backend._step``.
    """
    if value != 0.0:
        return abs(value) * rel_step
    lo, hi = _range(bounds)
    span = hi - lo
    return rel_step * span if span > 0 else rel_step


def _range(bounds) -> tuple:
    """``(min, max)`` from a client-supplied pair, or ``(0, 0)`` when unusable."""
    try:
        lo, hi = float(bounds[0]), float(bounds[1])
    except (TypeError, ValueError, IndexError, KeyError):
        return 0.0, 0.0
    if not (math.isfinite(lo) and math.isfinite(hi)):
        return 0.0, 0.0
    return lo, hi


def _shape(cost: dict | None) -> tuple:
    """What a cost payload is a mean *over*, so two of them can be differenced.

    The cost is a mean per weighted observable, and an observable that could not
    be scored at one point and could at another changes the function, not the
    argument. Differencing across that reports a derivative of the change in
    bookkeeping -- a large one, and indistinguishable from a real sensitivity.
    """
    items = (cost or {}).get("items") or []
    scored = tuple(i for i, it in enumerate(items) if (it or {}).get("cost") is not None)
    return (cost or {}).get("n_weighted"), scored


def _cost_value(cost: dict | None) -> float | None:
    value = (cost or {}).get("cost")
    if not isinstance(value, (int, float)) or not math.isfinite(value):
        return None
    return float(value)


def evaluate(
    params: dict,
    cost_at,
    *,
    param_names=None,
    bounds=None,
    rel_step: float = DEFAULT_REL_STEP,
    modifiers=None,
) -> dict:
    """``d ln(cost)/d ln(p)`` for each parameter, by central differences.

    ``cost_at(params) -> cost payload`` is injected: it is the caller's run-and-
    score path, so this module needs neither an engine nor CA to be tested, and
    the derivative is of the cost the panel is showing rather than of a second
    one computed here.

    ``modifiers`` rows are differenced **in θ**: each is ``{name, anchor,
    targets, baselines: {qname: baseline}, value: θ, bounds: [θmin, θmax]}``,
    and a perturbed run writes ``(θ±h)·baseline_t`` over every target while
    ``params`` (the physical base point) supplies everything else. The row is
    keyed by the *anchor* so the panel's bars line up with the slider keys.

    Returns the base cost, the step actually used, and one row per parameter with
    ``elasticity`` (the dimensionless figure), ``derivative`` (raw ``dJ/dp`` --
    ``dJ/dθ`` for a modifier) and, when either is missing, a ``reason`` saying
    why. Never a zero standing in for "could not tell": an insensitive parameter
    and a failed solve look identical if both report 0.
    """
    rel_step = float(rel_step)
    if not (math.isfinite(rel_step) and rel_step > 0):
        raise ValueError("rel_step must be a finite positive number")

    # A modifier's targets are in `params` (the physical base point), but they
    # are the modifier's to move -- differencing them separately as free rows
    # would double-count every one of them under the default param_names.
    claimed: set = set()
    for mod in modifiers or []:
        claimed.update(str(t) for t in (mod.get("targets") or []))
    names = [n for n in (param_names or list(params)) if n in params and n not in claimed]
    bounds = bounds or {}

    base = cost_at(dict(params))
    base_cost = _cost_value(base)
    # Modifiers first, matching the analytic arm and the slider column: a new
    # modifier goes to the top of the params editor, and the same quantity
    # listed last here would read as a different parameter.
    rows = []
    setters, row_bounds = {}, {}
    for mod in modifiers or []:
        key = str(mod.get("anchor") or mod.get("name"))
        rows.append(
            {
                "name": key,
                "value": float(mod.get("value", 0.0)),
                "step": None,
                "derivative": None,
                "elasticity": None,
                "reason": None,
            }
        )
        setters[key] = _modifier_setter(mod)
        row_bounds[key] = mod.get("bounds")
    for name in names:
        rows.append(
            {
                "name": name,
                "value": float(params[name]),
                "step": None,
                "derivative": None,
                "elasticity": None,
                "reason": None,
            }
        )
        setters[name] = _free_setter(name)
        row_bounds[name] = bounds.get(name)
    payload = {
        "cost": base_cost,
        "rel_step": rel_step,
        "method": "central finite difference",
        # What the user paid for this, so the toggle's price is not a mystery.
        "n_simulations": 1 + 2 * len(rows),
        "params": rows,
        "unavailable": None,
    }
    if base_cost is None:
        # No number to be sensitive *of*. Reported rather than approximated: a
        # gradient of an unscorable cost would be a gradient of nothing.
        payload["unavailable"] = (
            "the current parameters could not be scored, so there is no cost to "
            "take a gradient of"
        )
        return payload

    base_shape = _shape(base)
    for row in rows:
        name = row["name"]
        value = row["value"]
        step = step_for(value, row_bounds.get(name), rel_step)
        row["step"] = step
        lo, hi = _range(row_bounds.get(name))

        set_point = setters[name]
        try:
            plus = _perturbed(cost_at, params, set_point, name, value + step, base_shape)
            minus = _perturbed(cost_at, params, set_point, name, value - step, base_shape)
        except CostRunError as exc:
            row["reason"] = str(exc)
            continue

        deriv = (plus - minus) / (2.0 * step)
        if not math.isfinite(deriv):
            row["reason"] = "the perturbed costs did not give a finite difference"
            continue
        row["derivative"] = deriv
        row["elasticity"] = relative_coeff(deriv, value, base_cost, hi - lo)
        if row["elasticity"] is None:
            row["reason"] = (
                "the cost is ~0 here, so a relative sensitivity is undefined"
                if abs(base_cost) <= 1e-12
                else "the parameter is 0 and has no range, so there is no scale to "
                     "normalise by"
            )
    return payload


def _free_setter(name: str):
    """Write a free parameter's perturbed value at its own key."""
    def set_point(perturbed: dict, value: float) -> None:
        perturbed[name] = value

    return set_point


def _modifier_setter(mod: dict):
    """Write a modifier's perturbed θ as ``θ·baseline_t`` over every target.

    θ itself never enters the params dict -- the live routes take physical
    values only, and the whole point of differencing in θ is that one step
    moves every target in proportion.
    """
    targets = list(mod.get("targets") or [])
    baselines = dict(mod.get("baselines") or {})

    def set_point(perturbed: dict, theta: float) -> None:
        for target in targets:
            baseline = baselines.get(target)
            if baseline is not None:
                perturbed[target] = theta * float(baseline)

    return set_point


def _perturbed(cost_at, params: dict, set_point, name: str, value: float, base_shape) -> float:
    """The cost at one perturbed point, or a ``CostRunError`` saying what went wrong.

    The parameter is deliberately *not* clamped to its params_for_id range: those
    are search bounds, not physical limits, and clamping the step at a bound
    would silently halve it -- turning the central difference into a one-sided
    one while still dividing by ``2h``, i.e. reporting half the gradient.
    """
    perturbed = dict(params)
    set_point(perturbed, value)
    try:
        cost = cost_at(perturbed)
    except Exception as exc:  # noqa: BLE001 - a solve that failed is a reason, not a crash
        raise CostRunError(_short(f"{name} = {value:g} did not run: {exc}")) from exc
    got = _cost_value(cost)
    if got is None:
        raise CostRunError(f"the run at {name} = {value:g} could not be scored")
    if _shape(cost) != base_shape:
        raise CostRunError(
            f"the run at {name} = {value:g} scored a different set of observables, "
            "so the difference is not a derivative of one cost"
        )
    return got


def _short(text: str, limit: int = 200) -> str:
    """A solver's message trimmed to a row of a table; the log keeps the rest."""
    text = " ".join(str(text).split())
    return text if len(text) <= limit else text[: limit - 1] + "…"
