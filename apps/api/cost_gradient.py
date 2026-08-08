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


def _param_id_info(names: list[str], values: dict, bounds: dict | None) -> dict:
    """CA's ``param_id_info`` for the parameters on the sliders.

    Bounds are only used to build the normalisation object; the cost and the
    gradient are evaluated in real units. They still must not be degenerate --
    a zero span divides by zero -- so a parameter with no stated range gets a
    band around its current value rather than a point.
    """
    mins, maxs = [], []
    for name in names:
        low, high = _bounds_pair((bounds or {}).get(name))
        value = float(values.get(name, 0.0) or 0.0)
        if low is None or high is None or not (float(high) > float(low)):
            span = abs(value) if value else 1.0
            low, high = value - span, value + span
        mins.append(float(low))
        maxs.append(float(high))
    return {
        # One entry per parameter, as a list, which is the grouped shape CA reads
        # a params_for_id row into (issue #193).
        "param_names": [[n] for n in names],
        "param_mins": mins,
        "param_maxs": maxs,
    }


def _build(key, *, model_path, model_type, solver_info, dt, obs_data, sim_time,
           pre_time, names, values, bounds, output_dir):
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
        param_id_info=_param_id_info(names, values, bounds),
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
) -> dict:
    """``d ln(cost)/d ln(p)`` for each parameter, from one sensitivity solve.

    Raises :class:`GradientUnavailable` when this backend cannot, so the caller
    falls back to differencing rather than losing the panel.
    """
    names = [n for n in (param_names or list(params)) if n in params]
    if not names:
        raise GradientUnavailable("no parameters to measure")

    # Keyed by the obs_data's *content*: the caller rebuilds the document per
    # request, so an identity key would never hit and every drag would recompile.
    key = (
        str(model_path), model_type, repr(sorted((solver_info or {}).items())),
        float(dt), float(sim_time), float(pre_time), tuple(names),
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
                values=params, bounds=bounds, output_dir=output_dir)
        values = [float(params[n]) for n in names]
        cost, gradient = pid.get_cost_and_gradient(values)
    except GradientUnavailable:
        raise
    except Exception as exc:  # noqa: BLE001 - any CA/solver problem falls back
        _CACHE.pop(key, None)
        raise GradientUnavailable(str(exc)) from exc

    cost = float(cost)
    flat = [float(g) for g in _flatten(gradient)]
    if len(flat) != len(names):
        raise GradientUnavailable(
            f"the gradient has {len(flat)} entries for {len(names)} parameters")

    rows = []
    for name, value, derivative in zip(names, values, flat):
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
