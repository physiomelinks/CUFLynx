"""The one place that turns "use the emulator" into circulatory_autogen kwargs.

Shared by the calibration, sensitivity and UQ runners, which all build a CA
engine (``CVS0DParamID`` / ``SensitivityAnalysis``) and all have to put it on the
emulator the same way — otherwise a study could be calibrated against a surrogate
and analysed against the solver without anything saying so.

Ships to the runners' directory as data, like the other shared runner modules:
an external interpreter executes those scripts as files, so it must sit beside
them rather than be importable only from the frozen app.
"""

from __future__ import annotations


def engine_kwargs(config: dict) -> dict:
    """CA engine kwargs that put a run on a trained emulator, or ``{}`` if not.

    Empty rather than ``use_emulator=False`` on purpose: a circulatory_autogen
    without emulator support (CA < #333) does not accept the keyword at all, and
    a study that never asked for an emulator should still run against such a CA.
    Asking for one there fails loudly instead, which is correct — there is no
    emulator to be had.
    """
    if not config.get("use_emulator"):
        return {}
    emulator_dir = config.get("emulator_dir")
    if not emulator_dir:
        raise ValueError(
            "use_emulator is set but no emulator directory was given; train an "
            "emulator for this study first"
        )
    return {
        "use_emulator": True,
        "emulator_dir": emulator_dir,
        "emulator_settings": dict(config.get("emulator_settings") or {}),
    }


def describe(config: dict) -> str:
    """One line for the run log saying which forward model is actually being used.

    Worth printing every time: the whole point of the flag is that a run looks
    identical from the outside whether it evaluated the solver or a surrogate of
    it, and the terminal is where a user goes to find out which happened.
    """
    if not config.get("use_emulator"):
        return ""
    return (
        f"Evaluating the trained emulator instead of the solver "
        f"({config.get('emulator_dir')})"
    )
