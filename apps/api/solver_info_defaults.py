"""The step settings a solver declares for itself, filled in before CVODE's defaults can be.

Three places fill an absent step setting with a CVODE value: CA's ``ProtocolRunner``
(``MaximumStep`` 0.001, ``MaximumNumberOfSteps`` 5000), CA's user_inputs parser
(``MaximumNumberOfSteps`` 5000) and the analysis runners here (``MaximumStep`` 1e-4).
For CVODE those mean a cap on each step and a step budget per output interval.
``BE_batched_c`` reads the same keys differently: no cap unless one is given, and a
budget per block for the *whole run* (10 000 000 by default). A CVODE default
therefore either slows it by orders of magnitude or stops it a few seconds into a run
with "too many steps (MaximumNumberOfSteps)".

So each of the two keys a solver declares in CA's ``SOLVER_INFO_FIELDS`` is set to the
solver's own declared default before anything else fills it: ``MaximumNumberOfSteps``
whenever it declares one, ``MaximumStep`` only where the declared default is None (no
cap). A solver
that declares a cap (CVODE_myokit's 0.001) keeps the defaults it runs with today. A
value the user set always wins.

A leaf (stdlib and ``ca_imports`` only), shipped into ``runners/`` beside the
analysis runners that use it (``packaging/cuflynx.spec``).
"""

from __future__ import annotations

from ca_imports import ca_from

#: The keys CA's ProtocolRunner and parser, and the runners here, default with CVODE values.
STEP_KEYS = ("MaximumStep", "MaximumNumberOfSteps")


def declared_step_defaults(solver: str) -> dict:
    """``{key: default}`` for the step keys ``solver`` declares; {} when CA cannot say."""
    try:
        fields = ca_from("parsers.PrimitiveParsers", "SOLVER_INFO_FIELDS").get(solver) or []
    except Exception:  # noqa: BLE001 - no CA / an older one: leave the settings as they are
        return {}
    out = {}
    for field in fields:
        name, default = field.get("name"), field.get("default")
        if (name == "MaximumNumberOfSteps" and default is not None) or (
            name == "MaximumStep" and default is None
        ):
            out[name] = default
    return out


def with_declared_step_defaults(solver: str, solver_info: dict | None) -> dict:
    """``solver_info`` with the solver's declared step defaults under it."""
    return {**declared_step_defaults(solver), **(solver_info or {})}
