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

import glob
import json
import os
import re

import ca_run_history
import mcmc_progress

#: What a caller can ask for, and the order the summary lists them in.
SECTIONS = ("calibration", "progress", "sensitivity", "uq", "emulator")

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
            "calibrated model", lambda: _calibrated_model(output_dir, file_prefix), missing),
    }


def _calibrated_model(output_dir, file_prefix):
    """``<prefix>_calibrated.cellml``, found even when the caller could not name the prefix.

    The convention is ``file_prefix``-driven, and the frontend passes the *loaded model's*
    name -- which is empty in exactly the case this feature exists for: opening a directory
    before any model is loaded. So the lookup returned None and the calibrated model went
    unreported for a folder that had one sitting at its top level.

    Falling back to the file itself rather than parsing a prefix out of the run directory's
    name: run directories are ``<method>_<prefix>_<hash>_obs_data`` and a prefix may itself
    contain underscores, so that split has no unambiguous answer. The file does.
    """
    named = ca_run_history.calibrated_model_path(output_dir, file_prefix)
    if named:
        return named
    matches = sorted(glob.glob(
        os.path.join(output_dir, f"*{ca_run_history.CALIBRATED_SUFFIX}")))
    # More than one means several studies shared this directory, and picking one would be
    # picking a model for results it may not belong to. Reported as absent instead.
    return matches[0] if len(matches) == 1 else None


def _newest(paths):
    """The most recently modified of ``paths``, or None."""
    existing = [q for q in paths if os.path.isfile(q)]
    if not existing:
        return None
    return max(existing, key=os.path.getmtime)


_STAMP = re.compile(r"_(\d{6}_\d{6})\.json$")


def _stamped(paths):
    """``{stamp: path}`` for the snapshots whose name carries a run stamp."""
    out = {}
    for q in paths:
        match = _STAMP.search(os.path.basename(q))
        if match:
            out[match.group(1)] = q
    return out


def _snapshot_pair(run_dir):
    """The obs_data and params_for_id the run finished with, as a matched pair.

    Chosen by the stamp in the filename, **not** by mtime. On a real run directory all four
    snapshots carried the same mtime to the microsecond -- CA writes them in one go -- so
    "newest file" was decided by whatever order the glob happened to return, and the obs_data
    of one run was paired with the params_for_id of another. Two files that were never used
    together, presented as the study.

    The stamp is the run's own, so it orders them and it pairs them. A params_for_id with no
    matching obs_data stamp falls back to its own newest rather than to nothing: half a study
    is still worth reporting, and the caller can see the two stamps differ.
    """
    obs_by_stamp = _stamped(glob.glob(os.path.join(run_dir, "*_obs_data_*.json")))
    par_by_stamp = _stamped(glob.glob(os.path.join(run_dir, "*_params_for_id_*.json")))
    if not obs_by_stamp and not par_by_stamp:
        return (_newest(glob.glob(os.path.join(run_dir, "*_obs_data_*.json"))),
                _newest(glob.glob(os.path.join(run_dir, "*_params_for_id_*.json"))))

    stamps = sorted(set(obs_by_stamp) | set(par_by_stamp), reverse=True)
    # Prefer the newest stamp that has both halves; that is the run that completed.
    for stamp in stamps:
        if stamp in obs_by_stamp and stamp in par_by_stamp:
            return obs_by_stamp[stamp], par_by_stamp[stamp]
    newest = stamps[0]
    return obs_by_stamp.get(newest), par_by_stamp.get(newest)


def _study(output_dir, run_dir, file_prefix, missing):
    """The inputs the run was made from, so a loaded directory is a study and not just
    a set of result panels.

    A run directory keeps timestamped snapshots of the obs_data and params_for_id it
    actually used -- ``<hash>_obs_data_<yymmdd_HHMMSS>.json`` -- which is what makes a
    finished run re-openable at all. The newest snapshot in the chosen run is the one that
    run finished with; earlier ones are from re-runs against the same directory.

    The model is resolved by ``file_prefix`` rather than by picking whatever CellML is
    lying about: a directory may hold artefacts from several studies (this is ordinary --
    outputs directories get reused), and attaching one study's model to another's results
    produces numbers that look right.
    """
    prefix = file_prefix or _prefix_from_calibrated_model(output_dir)
    generated = None
    if prefix:
        candidate = os.path.join(output_dir, "generated_models", prefix)
        if os.path.isdir(candidate):
            models = sorted(glob.glob(os.path.join(candidate, "*.cellml")))
            generated = models[0] if len(models) == 1 else None

    # CA's own generation step writes the fitted model *as* the generated model, so a
    # directory produced by cuflynx-param-id has one there and no `*_calibrated.cellml`
    # at all. `generated` above already found it when there is exactly one CellML in the
    # prefix's folder; this names it when the folder holds more than the model.
    model = (generated
             or ca_run_history.ca_calibrated_model(output_dir, prefix)
             or _calibrated_model(output_dir, file_prefix))

    obs = params = None
    if run_dir and os.path.isdir(run_dir):
        obs, params = _snapshot_pair(run_dir)

    # Looked for in the outputs directory, which is where a generated pipeline bundle puts
    # it. Absent is ordinary -- a run started from this app has never written one -- so it
    # is reported rather than treated as a fault.
    user_inputs = _newest(
        glob.glob(os.path.join(output_dir, "user_inputs*.yaml"))
        + glob.glob(os.path.join(output_dir, "user_inputs*.yml"))
    )

    return {
        "model": model,
        "model_is_calibrated": bool(model) and model.endswith(
            ca_run_history.CALIBRATED_SUFFIX),
        "obs_data": obs,
        "params_for_id": params,
        "user_inputs": user_inputs,
        "file_prefix": prefix,
    }


def _prefix_from_calibrated_model(output_dir):
    """The study's prefix, taken from the one artefact that carries it unambiguously.

    Run directories are ``<method>_<prefix>_<hash>_obs_data`` and a prefix may itself
    contain underscores, so splitting those has no single answer. The calibrated model's
    name does -- and the parsing lives with the naming, in ``ca_run_history``.
    """
    matches = sorted(glob.glob(
        os.path.join(output_dir, f"*{ca_run_history.CALIBRATED_SUFFIX}")))
    if len(matches) != 1:
        return None
    return ca_run_history.prefix_from_calibrated_model(matches[0])


def _progress(run_dir, output_dir, missing):
    """The per-generation history the Progress tab draws.

    The result files say where a calibration *ended*; this is how it got there,
    and it is written by every run into the same directory. Left out, a loaded
    calibration showed its best fit and an empty Progress tab -- which reads as
    "this run recorded nothing", not as "nobody asked for it".

    Read from the chosen run directory, so the history belongs to the run whose
    best fit is being shown beside it rather than to whichever run
    ``read_run_history`` would have resolved for itself.
    """
    return _safely(
        "progress history",
        lambda: ca_run_history.progress_history(run_dir or output_dir),
        missing,
    )


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

    # Where the posterior was actually read from, so the panel can say so when it is not the
    # run being shown elsewhere.
    uq_source = {"path": source}

    def posteriors():
        # The chosen run is the newest of *any* kind, and `uq_distributions` resolves the
        # newest run for itself -- so in a directory whose newest run is a calibration, both
        # land on a run holding no chain and the answer was "no UQ" for a folder with a
        # finished UQ in it. Observed on a real outputs directory.
        found = ca_run_history.uq_distributions(source)
        if found:
            return found
        # Newest first, so "the UQ" means the most recent one, consistent with how the run
        # itself is chosen. An explicit run_dir that *has* a chain never reaches here, so a
        # deliberate choice is still honoured.
        for entry in list_run_dirs(output_dir):
            candidate = entry["path"]
            if candidate == source:
                continue
            found = ca_run_history.uq_distributions(candidate)
            if found:
                uq_source["path"] = candidate
                return found
        return found

    payload = {
        "params": _safely("UQ posteriors", posteriors, missing),
        "coverage": None,
        "progress": None,
        "has_posterior_predictive": False,
        "has_sample_traces": False,
    }
    # After the read, because that is what decides which run answered.
    payload["run_dir"] = uq_source["path"]
    # Follow the run that actually supplied the posterior, not the one chosen for the
    # directory as a whole. This module's own docstring says the coverage and the posterior
    # have to describe the same run -- and once the posterior can come from a different run
    # than the newest, reading the artefacts from the newest breaks exactly that promise:
    # a calibration run holds no coverage, so the numbers vanished beside a posterior that
    # had loaded fine.
    answered = uq_source["path"]
    if not answered or not os.path.isdir(answered):
        return payload

    coverage_path = os.path.join(answered, COVERAGE_FILE)
    if os.path.isfile(coverage_path):
        def read():
            with open(coverage_path) as handle:
                return json.load(handle)
        payload["coverage"] = _safely("posterior predictive coverage", read, missing)

    payload["has_posterior_predictive"] = os.path.isfile(
        os.path.join(answered, PREDICTIVE_FILE))
    payload["has_sample_traces"] = os.path.isfile(os.path.join(answered, SERIES_FILE))

    # The chain itself, as the three views the UQ Progress tab draws (#244). The posterior
    # says where the sampler ended up; this is the run that got there, and it is the same
    # file -- so a reopened UQ that showed its distributions beside three empty trace plots
    # was only ever missing this call.
    #
    # Read from the run that supplied the posterior, for the reason above it: traces and
    # distributions describing different runs is worse than no traces.
    #
    # `burn_in` is the sampler's default rather than the setting the run used: a finished
    # directory does not record it, and the alternative -- no cumulative-mean view at all --
    # loses more than a default that matches what most runs asked for.
    def chain_progress():
        labels = [row[0] for row in ca_run_history.param_names(answered) or []]
        return mcmc_progress.progress(answered, labels)

    progress = _safely("MCMC progress", chain_progress, missing)
    payload["progress"] = progress if (progress or {}).get("steps") else None
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
        "progress": _progress(run_dir, output_dir, missing),
        "sensitivity": _sensitivity(output_dir, missing),
        "uq": _uq(output_dir, run_dir, missing),
        "emulator": _emulator(output_dir, file_prefix, obs_path, missing),
        "study": _safely("study inputs",
                         lambda: _study(output_dir, run_dir, file_prefix, missing), missing),
        "saved_runs_dir": output_dir,
        "run_dirs": runs,
    }

    found = []
    if (result["calibration"] or {}).get("best"):
        found.append("calibration")
    if (result["progress"] or {}).get("cost_history"):
        found.append("progress")
    sensitivity = result["sensitivity"] or {}
    if sensitivity.get("local") or sensitivity.get("sobol"):
        found.append("sensitivity")
    if (result["uq"] or {}).get("params"):
        found.append("uq")
    # Reported separately from the result panels: a directory can hold the inputs a run was
    # made from without holding the run's results, and vice versa, and "what can be reopened"
    # is a different question from "what can be shown".
    study = result["study"] or {}
    if any(study.get(k) for k in ("model", "obs_data", "params_for_id", "user_inputs")):
        found.append("study inputs")
    if (result["emulator"] or {}).get("metadata"):
        found.append("emulator")

    result["found"] = found
    result["missing"] = missing
    return result
