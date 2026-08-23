"""Read everything a finished run left in an outputs directory (#255, #256).

The panels are populated by job polls today, so they only fill for a run started
in this session. A run produced by ``cuflynx-param-id``, by a generated
``run_pipeline.py``, or by the same app yesterday, is invisible: the files are
all there and nothing reads them.

Every artefact already has a directory-keyed reader in :mod:`ca_run_history`.
This is the one place that calls all of them, so a caller asks once and is told
both what was found and what was not.

Deliberately tolerant. A directory may hold a calibration and no UQ, an emulator
and nothing else, or results from a different model entirely -- none of which is
an error, and any of which would make a stricter reader refuse a folder the user
can see perfectly well. Whatever is unreadable is reported by name in ``missing``
rather than raising, so the UI can say what it could not load.
"""
from __future__ import annotations

import json
import os

import ca_run_history

#: What a caller can ask for, and the order the summary lists them in.
SECTIONS = ("calibration", "sensitivity", "uq", "emulator")

COVERAGE_FILE = "posterior_predictive_coverage.json"
PREDICTIVE_FILE = "posterior_predictive.npz"
SERIES_FILE = "posterior_predictive_series.npz"


#: Files that mark a directory as holding a finished run.
_RUN_MARKERS = ("best_param_vals.npy", "mcmc_chain.npy", "best_cost.npy")


def list_run_dirs(output_dir: str) -> list:
    """Every finished run under ``output_dir``, newest first.

    One outputs directory very often holds several: a study fitted to three
    datasets writes three sibling run directories, and ``find_run_dir`` returns
    exactly one of them. Loading that one silently is how a panel ends up
    describing a different dataset than the user thinks they are looking at, so
    the caller is given the whole list and told which was chosen.
    """
    if not output_dir or not os.path.isdir(output_dir):
        return []

    found = []
    candidates = [output_dir]
    try:
        candidates += [entry.path for entry in os.scandir(output_dir) if entry.is_dir()]
    except OSError:
        return []

    for path in candidates:
        newest = None
        for marker in _RUN_MARKERS:
            marker_path = os.path.join(path, marker)
            if os.path.isfile(marker_path):
                stamp = os.path.getmtime(marker_path)
                newest = stamp if newest is None else max(newest, stamp)
        if newest is not None:
            found.append({"path": path, "name": os.path.basename(path) or path,
                          "modified": newest})
    found.sort(key=lambda run: run["modified"], reverse=True)
    return found


def _safely(name, read, missing):
    """Run one reader, recording rather than raising when it cannot.

    A directory that half-loads is far more useful than one that refuses: the
    panels that can fill, fill, and the rest are named.
    """
    try:
        return read()
    except Exception as exc:  # noqa: BLE001 - one bad artefact must not lose the rest
        missing.append("%s (%s)" % (name, exc))
        return None


def _calibration(output_dir, file_prefix, missing):
    best = _safely("best parameter values",
                   lambda: ca_run_history.best_param_values(output_dir), missing)
    return {
        "best": best,
        "modifiers": _safely("modifiers",
                             lambda: ca_run_history.modifiers(output_dir), missing),
        "error_vectors": _safely(
            "error vectors", lambda: ca_run_history.error_vectors(output_dir), missing),
        "calibrated_model": _safely(
            "calibrated model",
            lambda: ca_run_history.calibrated_model_path(output_dir, file_prefix),
            missing),
    }


def _sensitivity(output_dir, missing):
    return {
        "local": _safely("local sensitivity",
                         lambda: ca_run_history.local_sensitivity(output_dir), missing),
        "sobol": _safely("Sobol indices",
                         lambda: ca_run_history.sobol_indices(output_dir), missing),
    }


def _uq(output_dir, run_dir, missing):
    """Posteriors, and how well they reproduce the data.

    The coverage and predictive artefacts live in the run directory beside the
    chain, not in the directory the user chose, which is why both are passed.
    """
    # Read from the run that was chosen, not from output_dir: uq_distributions
    # would resolve the newest run for itself, so picking an earlier one showed
    # its coverage next to a different run's posterior.
    source = run_dir or output_dir
    payload = {
        "params": _safely("UQ posteriors",
                          lambda: ca_run_history.uq_distributions(source), missing),
        "coverage": None,
        "has_posterior_predictive": False,
        "has_sample_traces": False,
    }
    if not run_dir:
        return payload

    coverage_path = os.path.join(run_dir, COVERAGE_FILE)
    if os.path.isfile(coverage_path):
        def read():
            with open(coverage_path) as handle:
                return json.load(handle)
        payload["coverage"] = _safely("posterior predictive coverage", read, missing)

    payload["has_posterior_predictive"] = os.path.isfile(
        os.path.join(run_dir, PREDICTIVE_FILE))
    payload["has_sample_traces"] = os.path.isfile(os.path.join(run_dir, SERIES_FILE))
    return payload


def _emulator(output_dir, file_prefix, obs_path, missing):
    # find_ rather than the plain resolver: this reads a run it did not produce,
    # and emulator_dir is a setting a study can point anywhere -- one trained
    # emulator reused across several obs_data has to.
    emu_dir = _safely(
        "emulator directory",
        lambda: ca_run_history.find_emulator_dir(output_dir, file_prefix, obs_path),
        missing)
    if not emu_dir:
        return {"dir": None, "metadata": None, "error_points": None}
    return {
        "dir": emu_dir,
        "metadata": _safely("emulator metadata",
                            lambda: ca_run_history.emulator_metadata(emu_dir), missing),
        "error_points": _safely(
            "emulator error points",
            lambda: ca_run_history.emulator_error_points(emu_dir), missing),
    }


def load_outputs(output_dir: str, file_prefix: str | None = None,
                 obs_path: str | None = None, run_dir: str | None = None) -> dict:
    """Everything readable in ``output_dir``, plus what was found and what wasn't.

    ``found`` is the point of the return value as much as the data is: "this
    folder has a calibration and an emulator but no UQ" is what the caller needs
    in order to say something true about a directory it did not produce.
    """
    if not output_dir or not os.path.isdir(output_dir):
        return {
            "dir": output_dir, "run_dir": None, "found": [], "missing": [],
            "error": "no such directory: %s" % output_dir,
        }

    missing: list[str] = []
    runs = list_run_dirs(output_dir)
    # An explicit choice wins; otherwise the newest, which is what find_run_dir
    # would have picked anyway -- but now the caller can see the alternatives.
    if run_dir and any(run["path"] == run_dir for run in runs):
        chosen = run_dir
    else:
        chosen = ca_run_history.find_run_dir(output_dir)
    run_dir = chosen

    result = {
        "dir": output_dir,
        "run_dir": run_dir,
        "calibration": _calibration(output_dir, file_prefix, missing),
        "sensitivity": _sensitivity(output_dir, missing),
        "uq": _uq(output_dir, run_dir, missing),
        "emulator": _emulator(output_dir, file_prefix, obs_path, missing),
        "saved_runs_dir": output_dir,
        "run_dirs": runs,
    }

    found = []
    if (result["calibration"] or {}).get("best"):
        found.append("calibration")
    sensitivity = result["sensitivity"] or {}
    if sensitivity.get("local") or sensitivity.get("sobol"):
        found.append("sensitivity")
    if (result["uq"] or {}).get("params"):
        found.append("uq")
    if (result["emulator"] or {}).get("metadata"):
        found.append("emulator")

    result["found"] = found
    result["missing"] = missing
    return result
