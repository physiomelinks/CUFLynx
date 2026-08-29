"""Calling a CA observable operation, and saying in obs_data what it was called on.

Two things have to agree exactly: how the operation is invoked here on the
recording, and the ``operands`` written into obs_data that tell CA how to invoke
it on the simulation. If they diverge, the extracted value and the simulated
value are computed from different inputs -- and nothing detects it. The residual
is simply wrong, in a way that looks like a bad model rather than a bad pipeline.

The CLI derives them in two independent ``inspect.signature`` walks, several
hundred lines apart (``_extract_feature_with_operation_funcs_user`` decides the
call, ``_make_scalar_item`` decides the operands). They agree today. Here
:func:`plan_call` derives both from one walk and returns them together, so they
cannot stop agreeing.

**Which kwargs to pass** also comes from the signature rather than a hardcoded
list. The CLI passes five names it knows about; anything a user adds to
``operation_funcs_user.py`` tomorrow is silently unreachable. Intersecting the
configured kwargs with the signature means the GUI can offer exactly what CA's
schema says an operation takes, and this passes exactly what it accepts.
"""

from __future__ import annotations

import inspect
from dataclasses import dataclass, field
from typing import Callable

import numpy as np

from .errors import ObsExtractError

#: The operand CA uses for the time vector.
TIME_OPERAND = "time"

#: Kwargs supplied by the pipeline rather than by the user, when an operation
#: declares them. ``series_output`` is forced False because this path wants the
#: reduced scalar; the others are spike-detection settings that belong to the
#: recording, and are overridable per feature.
PIPELINE_KWARGS = {
    "series_output": False,
    "spike_min_thresh": -10.0,
    "dV_dt_thresh": 7.5e3,
}


@dataclass
class CallPlan:
    """How to call one operation, and how to say so in obs_data."""

    operation: str
    operands: list[str]
    kwargs: dict
    #: True when the function's first parameter is the time vector.
    takes_time: bool
    fn: Callable = field(repr=False, default=None)
    #: Kwargs that were configured but the operation does not accept.
    dropped: list[str] = field(default_factory=list)

    def __call__(self, t: np.ndarray, x: np.ndarray):
        args = (t, x) if self.takes_time else (x,)
        return self.fn(*args, **self.kwargs)


def plan_call(
    operation: str,
    fn: Callable,
    measured_variable: str,
    *,
    kwargs: dict | None = None,
    time_operand: str = TIME_OPERAND,
) -> CallPlan:
    """One signature walk; both the call and the operands come out of it.

    An operation whose first parameter is ``t`` (or ``time``) is a function of
    time and the signal, and its operands are ``[time, variable]``. Anything else
    takes the signal alone. That is CA's own convention, read off the function
    rather than guessed from its name.
    """
    if fn is None:
        raise ObsExtractError(
            f"the operation {operation!r} is not available from this "
            f"circulatory_autogen. Check the CA directory in Settings, or pick "
            f"another operation.")
    try:
        params = inspect.signature(fn).parameters
    except (TypeError, ValueError) as exc:  # pragma: no cover - builtins
        raise ObsExtractError(
            f"cannot inspect the operation {operation!r}: {exc}") from exc

    names = list(params)
    takes_time = bool(names) and names[0] in ("t", "time")
    operands = ([time_operand, measured_variable] if takes_time
                else [measured_variable])
    if not measured_variable:
        raise ObsExtractError(
            f"the operation {operation!r} needs a model variable to read, but "
            f"none is bound for this stimulus kind.")

    accepted = set(names)
    call_kwargs: dict = {}
    for key, value in PIPELINE_KWARGS.items():
        if key in accepted:
            call_kwargs[key] = value

    dropped: list[str] = []
    for key, value in (kwargs or {}).items():
        if key in accepted:
            call_kwargs[key] = value
        else:
            dropped.append(key)

    return CallPlan(operation=operation, operands=operands, kwargs=call_kwargs,
                    takes_time=takes_time, fn=fn, dropped=sorted(dropped))


def accepts_range(fn: Callable) -> bool:
    """Whether an operation is measured over a sub-range of the window.

    **Both** ``start_frac`` and ``end_frac``, read off the signature -- not
    ``name.endswith("_in_range")``, which is what the CLI uses and which misses
    ``calc_spike_frequency_windowed``, ``calc_spike_count_windowed`` and the whole
    ``mean_in_range_*`` family. Those all take the fractions and silently receive
    the defaults, so a range the user carefully chose is never applied.
    """
    try:
        params = inspect.signature(fn).parameters
    except (TypeError, ValueError):  # pragma: no cover
        return False
    return "start_frac" in params and "end_frac" in params


def kwarg_defaults(fn: Callable) -> dict:
    """An operation's own keyword defaults.

    Needed because they are not all the same: ``mean_in_range_minus_initial``
    defaults to ``start_frac=0.8``, so a GUI that pre-filled 0.0/1.0 everywhere
    would quietly change what that operation computes. Pre-fill from here.
    """
    try:
        params = inspect.signature(fn).parameters
    except (TypeError, ValueError):  # pragma: no cover
        return {}
    return {name: p.default for name, p in params.items()
            if p.default is not inspect.Parameter.empty}


def evaluate(plan: CallPlan, t: np.ndarray, x: np.ndarray) -> float:
    """Run the operation and insist the answer is one finite number.

    A NaN is not an error here -- an operation asked for a spike time in a sweep
    that never fired has nothing to return, and the caller skips the item with a
    note. An array is an error: it means the operation was a series transform and
    the config asked for it as a scalar.
    """
    value = plan(np.asarray(t, dtype=float), np.asarray(x, dtype=float))
    array = np.asarray(value)
    if array.ndim != 0 and array.size != 1:
        raise ObsExtractError(
            f"the operation {plan.operation!r} returned {array.size} values; a "
            f"scalar observable needs exactly one. If this is a series "
            f"transform, add it as a series feature instead.")
    return float(array.reshape(-1)[0]) if array.size else float("nan")
