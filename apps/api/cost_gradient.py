"""The cost gradient from the forward solve itself, not from differencing it.

Issue #188. Enabling sensitivities makes each solve carry its own derivatives --
Myokit CVODES forward sensitivities (FSA) for ``cellml_only``, CasADi/AADC AD for
the generated formats -- so one run yields the cost *and* ``dJ/dp`` together.
:mod:`cost_sensitivity` remains the fallback for backends that can do neither.

**Why this is the better trade, measured** (Lotka-Volterra, 4 parameters,
cellml_only + CVODE_myokit):

===============================================  ===========
enabling FSA (one-off, a sensitivity recompile)  ~2000 ms
cost + gradient, per solve                       32-56 ms
a plain cost-only solve                          19-63 ms
the same gradient by central differences (2M+1)  ~475 ms
===============================================  ===========

So the derivative is roughly free once enabled, and about ten times cheaper than
differencing. The recompile is paid when the user switches sensitivities on, not
per drag.

**And it is more accurate, which matters more than the speed.** Differencing a
solver-tolerance-limited cost cannot resolve a parameter the cost barely depends
on: across steps 1e-2 .. 1e-6 the central difference for Lotka-Volterra's
``alpha`` gave -2.18, -0.78, -1.68, +0.05, +0.27 -- it changes *sign*, so a flat
parameter comes back with a confident and arbitrary direction. FSA gives -0.559
at every step size, because it never subtracts two nearly-equal numbers.

**``do_ad=True`` is the switch.** ``fsa_gradient_available`` requires it
(``fsa_backend.gradient_available``); without it a perfectly capable cellml_only
+ Myokit run reports no gradient at all.

The gradient comes back in **real parameter units**, not normalised ones --
checked against a converged numerical gradient, which agrees to within 7% on
every parameter whose difference quotient is itself converged.
"""

from __future__ import annotations

import hashlib
import json
import math
import sys
import tempfile

# One built param_id per (model, backend, obs_data, parameters). Building it
# compiles a model -- ~1.5 s with sensitivities -- so it is held for the same
# reason engine.py holds its helper: the compile is the expensive part and the
# parameters change far more often than the model does.
_CACHE: dict = {}
_CACHE_LIMIT = 4


class GradientUnavailable(Exception):
    """No analytic gradient here; the caller should difference instead."""


def _ca_imports():
    from obs_options import _ca_paths  # noqa: PLC0415

    for path in _ca_paths():
        if path not in sys.path:
            sys.path.insert(0, path)
    from param_id.paramID import OpencorParamID  # noqa: PLC0415
    from parsers.PrimitiveParsers import ObsAndParamDataParser  # noqa: PLC0415

    return OpencorParamID, ObsAndParamDataParser


def _bounds_pair(entry):
    """``(min, max)`` from a bounds entry, whichever shape it arrives in.

    The API takes ``[min, max]`` -- which is what the panel sends -- and reading
    it as a mapping raised ``'list' object has no attribute 'get'`` inside the
    build, so every request that carried bounds fell back to differencing while
    reporting, truthfully but uselessly, that no gradient was available. A
    mapping is accepted too rather than tightened away, because that is the
    shape a hand-written request most naturally takes.
    """
    if entry is None:
        return None, None
    if isinstance(entry, dict):
        return entry.get("min"), entry.get("max")
    try:
        low, high = entry
    except (TypeError, ValueError):
        return None, None
    return low, high


def _range_for(low, high, value):
    """A usable ``(min, max)`` for the normalisation object.

    Bounds are only used to build it; the cost and the gradient are evaluated in
    real units. They still must not be degenerate -- a zero span divides by zero
    -- so a parameter with no stated range gets a band around its current value
    rather than a point.
    """
    if low is None or high is None or not (float(high) > float(low)):
        span = abs(value) if value else 1.0
        return value - span, value + span
    return float(low), float(high)


def _param_id_info(
    names: list[str], values: dict, bounds: dict | None, modifiers=None
) -> dict:
    """CA's ``param_id_info`` for the parameters on the sliders.

    A modifier occupies **one** entry naming all of its targets, exactly as a
    params_for_id modifier row does, and its slot carries theta. The
    ``modifiers`` block is what makes CA treat it as one: ``fsa_backend`` reads
    ``modifier_weights_by_index`` off it and combines the per-member CVODES
    columns into ``dJ/dtheta = sum_i w_i * dJ/dp_i`` -- so the chain rule is
    CA's, not a second implementation here.

    ``baselines`` is left ``None`` deliberately: ``OpencorParamID.__init__``
    calls ``resolve_modifier_baselines`` against its freshly-built sim helper,
    before any parameter has been written. Filling them from the request would
    trust numbers the *client* resolved and could compound across drags.
    """
    mins, maxs = [], []
    param_names: list[list[str]] = []

    # Modifiers first, so the panel lists them where the slider column does --
    # a new modifier goes to the top of the params editor, and a bar chart that
    # put the same quantity last would read as a different parameter.
    blocks = []
    for mod in modifiers or []:
        targets = [str(t) for t in (mod.get("targets") or [])]
        if not targets:
            continue
        theta = float(mod.get("value", 0.0) or 0.0)
        low, high = _range_for(*_bounds_pair(mod.get("bounds")), theta)
        blocks.append({
            "index": len(param_names),
            "name": mod.get("name") or targets[0],
            "operation": mod.get("operation") or "scale",
            "targets": targets,
            "baselines": None,  # CA resolves these; see the docstring
        })
        param_names.append(targets)
        mins.append(low)
        maxs.append(high)

    for name in names:
        low, high = _range_for(*_bounds_pair((bounds or {}).get(name)),
                               float(values.get(name, 0.0) or 0.0))
        mins.append(low)
        maxs.append(high)
        # One entry per parameter, as a list, which is the grouped shape CA reads
        # a params_for_id row into (issue #193).
        param_names.append([name])

    info = {
        "param_names": param_names,
        "param_mins": mins,
        "param_maxs": maxs,
    }
    if blocks:
        info["modifiers"] = blocks
    return info


def _check_baselines(pid, modifiers) -> None:
    """Refuse when CA's baselines are not the ones the request was built on.

    The two arms resolve baselines from different places -- CA from its own sim
    helper's pristine defaults, the differencing arm from the ``baselines`` the
    client sends -- and theta means *different physical values* if they differ.
    They normally agree (the client's came from the same model defaults), but
    "normally" is not good enough for a number read off a bar chart: a silent
    divergence would flip the panel's answer whenever the analytic arm became
    available. Falling back names the parameter instead.
    """
    resolved = {
        block.get("name"): block
        for block in (getattr(pid, "param_id_info", None) or {}).get("modifiers") or []
    }
    for mod in modifiers:
        block = resolved.get(mod.get("name"))
        theirs = block.get("baselines") if block else None
        if theirs is None:
            raise GradientUnavailable(
                f"modifier {mod.get('name')!r} has no resolved baselines")
        ours = mod.get("baselines") or {}
        for target, value in zip(block.get("targets") or [], theirs):
            mine = ours.get(target)
            if mine is None:
                continue
            if not math.isclose(float(mine), float(value), rel_tol=1e-6, abs_tol=0.0):
                raise GradientUnavailable(
                    f"modifier {mod.get('name')!r} baseline for {target} is "
                    f"{float(value):g} in the model but {float(mine):g} in the "
                    f"request, so theta would not mean the same thing in both"
                )


def _build(key, *, model_path, model_type, solver_info, dt, obs_data, sim_time,
           pre_time, names, values, bounds, output_dir, modifiers=None):
    OpencorParamID, ObsAndParamDataParser = _ca_imports()

    parser = ObsAndParamDataParser()
    parsed = parser.parse_obs_data_json(
        obs_data_dict=obs_data, pre_time=float(pre_time), sim_time=float(sim_time))
    # process_obs_info writes .npy copies of the ground truth unconditionally; a
    # scratch dir keeps a slider drag from depositing files in the user's outputs.
    with tempfile.TemporaryDirectory() as scratch:
        obs_info = parser.process_obs_info(
            gt_df=parsed["gt_df"], output_dir=scratch, dt=float(dt))
    protocol_info = parser.process_protocol_and_weights(
        gt_df=parsed["gt_df"], protocol_info=parsed["protocol_info"], dt=float(dt))

    from user_funcs import external_path  # noqa: PLC0415

    pid = OpencorParamID(
        model_path=str(model_path),
        param_id_method="genetic_algorithm",  # unused: nothing here optimises
        obs_info=obs_info,
        param_id_info=_param_id_info(names, values, bounds, modifiers),
        protocol_info=protocol_info,
        prediction_info=None,
        solver_info=dict(solver_info or {}),
        dt=float(dt),
        # The switch. Without it fsa_gradient_available() is False even on a
        # cellml_only + Myokit run that could produce sensitivities perfectly well.
        do_ad=True,
        model_type=model_type,
        operation_funcs_external_path=external_path("operation", output_dir or None),
        cost_funcs_external_path=external_path("cost", output_dir or None),
    )

    if model_type == "cellml_only" and not pid.fsa_gradient_available():
        raise GradientUnavailable(
            "this model and solver cannot produce CVODES forward sensitivities")

    _CACHE[key] = pid
    while len(_CACHE) > _CACHE_LIMIT:
        _CACHE.pop(next(iter(_CACHE)))
    return pid


# Above this, the sensitivity solve stops being worth trusting. Measured on
# Lotka-Volterra against a 1e-12 reference: the gradient's largest relative error
# is ~7e-6 at 1e-6, 1e-8 and 1e-10 alike -- flat -- and only then degrades, to
# 6e-5 at 1e-5 and 1e-4 at 1e-4. So 1e-6 is as good as 1e-8 here and warning at
# anything tighter would cry wolf; 1e-5 is where the accuracy actually goes.
FSA_TOLERANCE_LIMIT = 1e-6


def tolerance_warning(solver_info: dict | None) -> str | None:
    """A caution when the solver is too loose for the sensitivities to be trusted.

    Only a caution: a loose gradient still ranks parameters correctly long after
    its digits stop being right, and the user chose this tolerance deliberately.
    CA substitutes 1e-8/1e-8 of its own when neither is set and FSA is on, so an
    unset pair needs no warning.
    """
    info = solver_info or {}
    loose = {
        key: float(info[key])
        for key in ("rtol", "atol")
        if info.get(key) is not None and float(info[key]) > FSA_TOLERANCE_LIMIT
    }
    if not loose:
        return None
    shown = ", ".join(f"{k}={v:g}" for k, v in sorted(loose.items()))
    return (
        f"the solver tolerance ({shown}) is looser than {FSA_TOLERANCE_LIMIT:g}, "
        f"so these sensitivities carry solver noise: measured error grows about "
        f"tenfold per decade beyond it. The ranking is usually still right; the "
        f"digits are not."
    )


def method_for(model_type: str) -> str:
    """What the gradient would be called for this backend, for the UI to report."""
    if model_type == "casadi_python":
        return "CasADi AD"
    if model_type == "aadc_python":
        return "AADC AD"
    return "Myokit CVODES FSA"


def evaluate(
    params: dict,
    *,
    model_path,
    model_type: str,
    solver_info: dict,
    dt: float,
    obs_data: dict,
    sim_time: float,
    pre_time: float,
    param_names=None,
    bounds=None,
    output_dir=None,
    modifiers=None,
) -> dict:
    """``d ln(cost)/d ln(p)`` for each parameter, from one sensitivity solve.

    ``modifiers`` rows are measured in **theta**: each becomes one CA entry
    naming all of its targets, and CA's own chain rule
    ``dJ/dtheta = sum_i w_i * dJ/dp_i`` (``fsa_backend`` via
    ``modifier_weights_by_index``, w_i = baseline_i for scale) combines the
    per-member sensitivity columns. Rows come back keyed by the modifier's
    anchor, the same key the differencing arm uses.

    Raises :class:`GradientUnavailable` when this backend cannot, so the caller
    falls back to differencing rather than losing the panel -- which is also
    what happens on a CA too old for an operation's weights, since
    ``modifier_weights_by_index`` raises there rather than guessing one.
    """
    modifiers = [m for m in (modifiers or []) if m.get("targets")]
    # A modifier's targets are the modifier's to move: differencing them as free
    # parameters too would report a second, contradictory answer for the same
    # quantity (cost_sensitivity.evaluate excludes them for the same reason).
    claimed = {str(t) for m in modifiers for t in (m.get("targets") or [])}
    names = [n for n in (param_names or list(params)) if n in params and n not in claimed]
    if not names and not modifiers:
        raise GradientUnavailable("no parameters to measure")

    # Keyed by the obs_data's *content*: the caller rebuilds the document per
    # request, so an identity key would never hit and every drag would recompile.
    # The modifier *structure* is part of the key (theta's value is not): the
    # entry layout it produces is baked into the built param_id, but a drag must
    # still reuse the compile.
    mod_key = tuple(
        (m.get("name"), m.get("operation"), tuple(m.get("targets") or []))
        for m in modifiers
    )
    key = (
        str(model_path), model_type, repr(sorted((solver_info or {}).items())),
        float(dt), float(sim_time), float(pre_time), tuple(names), mod_key,
        hashlib.sha1(
            json.dumps(obs_data, sort_keys=True, default=str).encode()
        ).hexdigest(),
    )
    pid = _CACHE.get(key)
    try:
        if pid is None:
            pid = _build(
                key, model_path=model_path, model_type=model_type,
                solver_info=solver_info, dt=dt, obs_data=obs_data,
                sim_time=sim_time, pre_time=pre_time, names=names,
                values=params, bounds=bounds, output_dir=output_dir,
                modifiers=modifiers)
        if modifiers:
            _check_baselines(pid, modifiers)
        # One value per *entry*, in the order _param_id_info laid them out:
        # theta per modifier, then the free parameters. CA expands theta into
        # its targets itself, so a physical value here would be read as theta.
        values = [float(m.get("value", 0.0) or 0.0) for m in modifiers]
        values += [float(params[n]) for n in names]
        cost, gradient = pid.get_cost_and_gradient(values)
    except GradientUnavailable:
        raise
    except Exception as exc:  # noqa: BLE001 - any CA/solver problem falls back
        _CACHE.pop(key, None)
        raise GradientUnavailable(str(exc)) from exc

    cost = float(cost)
    flat = [float(g) for g in _flatten(gradient)]
    if len(flat) != len(values):
        raise GradientUnavailable(
            f"the gradient has {len(flat)} entries for {len(values)} parameters")

    # A modifier's row is keyed by its *anchor* (targets[0]), the key its slider
    # carries, so the panel's bars line up with the sliders whichever arm
    # computed them -- the differencing arm keys and orders them the same way.
    row_names = [(m.get("targets") or [None])[0] for m in modifiers] + names
    rows = []
    for name, value, derivative in zip(row_names, values, flat):
        rows.append({
            "name": name,
            "value": value,
            "derivative": derivative,
            # Relative, so parameters measured in mmHg, seconds and litres per
            # second can be ranked against each other at all.
            "elasticity": _elasticity(value, cost, derivative),
            "reason": None if _elasticity(value, cost, derivative) is not None
            else "the cost or the parameter is zero, so a relative sensitivity has no meaning",
        })

    return {
        "cost": cost,
        "params": rows,
        # One solve, not 2M+1 -- the number the panel reports.
        "n_simulations": 1,
        "rel_step": None,
        "method": method_for(model_type),
        "analytic": True,
        "tolerance_warning": tolerance_warning(solver_info),
    }


def _flatten(gradient):
    try:
        return list(gradient.ravel())  # numpy
    except AttributeError:
        pass
    out = []
    for item in gradient:
        try:
            out.extend(_flatten(item))
        except TypeError:
            out.append(item)
    return out


def _elasticity(value: float, cost: float, derivative: float):
    """``(p/J) dJ/dp``. None when it cannot be formed, rather than 0 or inf --
    "no meaning here" and "this parameter does nothing" are different answers."""
    if not math.isfinite(derivative) or cost == 0 or value == 0:
        return None
    result = (value / cost) * derivative
    return result if math.isfinite(result) else None
