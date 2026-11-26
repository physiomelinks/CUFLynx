"""Standalone calibration runner — spawned as a subprocess by the API.

Reads a JSON config, drives circulatory_autogen's ``CVS0DParamID`` to calibrate
the model against the uploaded obs_data + params_for_id. The best fit is left in
circulatory_autogen's own files (``best_param_vals.npy`` / ``best_cost.npy`` /
``param_modifiers.json``), which the manager reads -- there is no CUFLynx summary
format any more (#210). Progress (per-generation cost,
etc.) is printed by circulatory_autogen straight to stdout, which the API
captures for the terminal view; run with ``python -u`` so it streams unbuffered.

Usage:  python -u calibration_runner.py <config.json>

config.json:
{
  "model_path": "...cellml", "obs_path": "...json", "params_path": "...csv",
  "output_dir": "...", "file_prefix": "model",
  "settings": { "param_id_method": "genetic_algorithm",
                "num_calls_to_function": 100, "cost_convergence": 0.001,
                "max_patience": 10, "cost_type": "MSE",
                "pre_time": 0.0, "sim_time": 2.0, "dt": 0.01,
                "solver": "CVODE_myokit", "DEBUG": false } }
"""

from __future__ import annotations

import json
import os
import sys
import time
import traceback
from pathlib import Path

import ca_obs
import emulator_config
from ca_imports import ensure_ca_path
from solver_info_defaults import with_declared_step_defaults
from optimiser_budget import default_method

# Force a headless matplotlib backend before circulatory_autogen imports pyplot
# (the post-calibration error plots run server-side with no display).
os.environ.setdefault("MPLBACKEND", "Agg")

#: When this process started: a validation_results.json older than that belongs
#: to an earlier run in the same working dir.
_STARTED = time.time()

# Markers the API watches for in stdout.
DONE_MARKER = "__CALIBRATION_DONE__"
FAIL_MARKER = "__CALIBRATION_FAILED__"

# CUFLynx-level / solver settings that must NOT be forwarded into CA's
# optimiser_options (the rest are the per-method option values the UI collected
# from CA's PARAM_ID_METHODS[method]['options'] schema).
_RESERVED = {
    "param_id_method", "gradient_method", "methods", "num_cores", "DEBUG",
    "sim_time", "pre_time", "dt", "solver", "solver_info", "python_path",
    "config_outputs_dir", "generated_model_format",
}


def _optimiser_options(settings: dict, seed=None) -> dict:
    """Assemble the CA ``optimiser_options`` from the UI settings.

    Forward each method's own option values as-is (each method consumes only its
    own keys) rather than hardcoding a fixed set — so, e.g., multi_start_sp_minimize
    gets num_starts and never a spurious max_patience. When a global random ``seed``
    is set it lands under ``optimiser_options['seed']`` — the key CA's multi-start
    start sampler reads (``PrimitiveParsers.PARAM_ID_METHODS``); ``None`` omits it,
    leaving CA on its own default (non-forced).
    """
    opts = {k: v for k, v in settings.items() if k not in _RESERVED and v is not None}
    if seed is not None:
        opts["seed"] = int(seed)
    return opts


#: The one CA-path rule, from the module that ships into ``runners/`` beside this
#: script. Four runners each carried a copy that inserted only ``src`` -- but CA
#: #431 removed CA's own ``sys.path`` surgery, so a bare-name CA import (
#: ``operation_funcs``, which every AD local-sensitivity run reaches through
#: ``local_sensitivity._check_ad_operations``) no longer resolves from ``src``
#: alone; ``ca_imports.ca_paths()`` adds the three further entries that make it
#: work. The copies also guessed ``<runner>/../../../circulatory_autogen/src``
#: when nothing was configured, which inside the bundle names a directory two
#: levels above it that has never existed.
_ensure_ca_on_path = ensure_ca_path


def _solver_info_from_config(config: dict, settings: dict) -> dict:
    """Solver_info for the chosen backend: the config's solver_info (set in the
    Settings popup) with the solver name + CVODE step defaults filled in."""
    si = dict(config.get("solver_info") or {})
    si.setdefault("solver", config.get("solver") or settings.get("solver", "CVODE_myokit"))
    # The solver's own step defaults first, so CVODE's 1e-4 cap only reaches solvers
    # that list it (see solver_info_defaults and CA's solver_info_fields).
    si = with_declared_step_defaults(si["solver"], si)
    if "MaximumStep" in ca_obs.solver_info_keys(si["solver"]):
        si.setdefault("MaximumStep", settings.get("MaximumStep", 0.0001))
    # No MaximumNumberOfSteps default: CA fills its own for the backends that use it.
    return si


def _apply_start_point(param_id, values: dict, source_label: str) -> None:
    """Override the gradient-descent start point with a ``{qname: value}`` map.

    CA seeds ``ParamID.param_init`` (the sp_minimize x0) from the model's
    built-in initial values; this replaces it with ``values`` so the descent starts
    from a chosen point instead — the user's current slider values (issue #65) or the
    previous completed calibration's best fit, so a stopped run can be continued
    (issue #83). ``source_label`` names the chosen point for the log line. Parameter
    order follows ``param_id_info``; a param absent from ``values`` keeps its
    model-default init. ``param_init`` is a list with one entry per parameter
    (``[value]`` or a bare value), matching ``get_init_param_vals`` — CA reads
    ``vals[0]`` for the x0.

    Best-effort: a start-point tweak must never abort the run, so any failure is
    logged and the model-default start is kept.
    """
    try:
        pid = param_id.param_id
        names = ca_obs.param_row_keys(pid.param_id_info)
        current = list(pid.param_init) if pid.param_init is not None else [None] * len(names)
        applied = 0
        for i, name in enumerate(names):
            val = values.get(name)
            if val is not None:
                current[i] = [float(val)]
                applied += 1
        pid.param_init = current
        print(
            f"Starting gradient descent from {source_label} "
            f"({applied}/{len(names)} params overridden)",
            flush=True,
        )
    except Exception as exc:  # noqa: BLE001 - never fail the run over a start-point tweak
        print(f"warning: could not apply {source_label} start point: {exc}", flush=True)


def _param_id_class():
    _ensure_ca_on_path()
    from ca_imports import ca_from  # noqa: E402 (shipped into runners/ too)

    return ca_from("param_id.paramID", "CVS0DParamID")


def _build_param_id(config: dict, *, announce: bool = True):
    """CA's ``CVS0DParamID`` for this run's config: the calibration ``run`` makes, and the
    object a stopped run's error vectors are written from (:func:`write_best_fit_errors`),
    which must be the same so its output directory and observables are the run's."""
    from local_sensitivity import resolve_gradient_method  # noqa: E402

    from obs_data import with_ca_obs_path  # noqa: E402 (runners/ too)

    CVS0DParamID = _param_id_class()

    # CA may only be shown the obs_data without its held-out data (obs_data.for_ca)
    config = with_ca_obs_path(config)

    settings = config.get("settings", {})
    output_dir = config["output_dir"]
    os.makedirs(output_dir, exist_ok=True)

    model_type = config.get("model_type", "cellml")
    solver_info = _solver_info_from_config(config, settings)
    # gradient_method drives CA's gradient source for the gradient methods:
    # AD/FSA => automatic (CasADi jacobian or Myokit CVODES forward sensitivity),
    # FD => finite difference. Ignored by the non-gradient methods. Resolved
    # through the shared rule so CA's 'AUTO'/'ANALYTIC'/'' spellings (its schema's
    # default) mean the analytic arm here too -- the raw string put them in the
    # FD bucket, silently downgrading a defaulted gradient calibration.
    do_ad = resolve_gradient_method(settings, model_type) in ("AD", "FSA")

    # Global random seed (Settings popup). When set, seed numpy's legacy global RNG
    # so the GA (which draws from np.random directly) is repeatable, and forward it
    # into optimiser_options for the multi-start start sampler. None => leave every
    # random process non-deterministic (CA's own defaults apply).
    seed = config.get("seed")
    if seed is not None:
        import numpy as np  # noqa: E402

        np.random.seed(int(seed))

    optimiser_options = _optimiser_options(settings, seed)
    method = settings.get("param_id_method") or default_method()

    if announce:
        print(
            f"Starting {method} "
            f"calibration ({optimiser_options.get('num_calls_to_function', '?')} max evals)",
            flush=True,
        )

    emulator_kwargs = emulator_config.engine_kwargs(config)
    note = emulator_config.describe(config)
    if note and announce:
        print(note, flush=True)

    dt = float(settings.get("dt", 0.01))
    # What the solve is configured with, said once: a study's adopted user_inputs
    # (or Settings) decide these, and a run log that cannot show them leaves "was my
    # MaximumStep used?" unanswerable.
    if announce:
        print(f"Solver: {solver_info.get('solver')}, dt = {dt:g}, solver_info = "
              f"{json.dumps(solver_info, sort_keys=True)}", flush=True)

    return CVS0DParamID(
        model_path=config["model_path"],
        model_type=model_type,
        param_id_method=method,
        file_name_prefix=config.get("file_prefix", "model"),
        params_for_id_path=config["params_path"],
        param_id_obs_path=config["obs_path"],
        sim_time=float(settings.get("sim_time", 2.0)),
        pre_time=float(settings.get("pre_time", 0.0)),
        dt=dt,
        solver_info=solver_info,
        optimiser_options=optimiser_options,
        do_ad=do_ad,
        DEBUG=bool(settings.get("DEBUG", False)),
        param_id_output_dir=output_dir,
        resources_dir=os.path.dirname(config["params_path"]),
        # CUFLynx-authored operation/cost funcs loaded from external files (CA #303).
        operation_funcs_external_path=config.get("operation_funcs_external_path"),
        cost_funcs_external_path=config.get("cost_funcs_external_path"),
        **emulator_kwargs,
    )


def write_best_fit_errors(config: dict) -> dict:
    """The error vectors of a calibration that was stopped before it wrote them: CA's
    ``save_best_fit_errors`` on an object rebuilt from the run's config, at the run's
    ``best_param_vals.npy`` (its best so far). A libcuflynx without it writes nothing."""
    if not hasattr(_param_id_class(), "save_best_fit_errors"):
        print("This libcuflynx does not write a stopped calibration's error vectors.",
              flush=True)
        return {"rank": 0, "errors": None}
    print("Writing the best-so-far fit's error per observable", flush=True)
    param_id = _build_param_id(config, announce=False)
    paths = param_id.save_best_fit_errors()
    if paths is None and getattr(param_id, "rank", 0) == 0:
        print("The stopped calibration saved no best fit, so it has no errors.", flush=True)
    return {"rank": getattr(param_id, "rank", 0), "errors": paths}


def run(config: dict) -> dict:
    if config.get("best_fit_errors_only"):
        return write_best_fit_errors(config)
    from obs_data import prediction_items_of  # noqa: E402 (runners/ too)

    settings = config.get("settings", {})
    output_dir = config["output_dir"]
    # The prediction_items as the user wrote them, held-out data included: the CA
    # object is only shown the document without it (obs_data.for_ca).
    prediction_items = prediction_items_of(_read_json(config["obs_path"]))
    emulated = bool(emulator_config.engine_kwargs(config))
    param_id = _build_param_id(config)

    # Gradient descent (sp_minimize) starts from param_init, which CA seeds from the
    # model's built-in initial values. The user can instead start from a chosen point
    # (issues #65 / #83) via the ``start_from`` selector: ``current`` = the UI slider
    # values, ``best_fit`` = the previous completed calibration's best fit (so a
    # stopped run can be continued). ``model`` (default) keeps CA's model-default x0.
    # The legacy ``start_from_current`` boolean maps to ``current``.
    start_from = settings.get("start_from")
    if not start_from:
        start_from = "current" if settings.get("start_from_current") else "model"
    if start_from == "current" and config.get("current_params"):
        _apply_start_point(param_id, config["current_params"], "current parameter values")
    elif start_from == "best_fit" and config.get("best_fit_params"):
        _apply_start_point(param_id, config["best_fit_params"], "previous best fit")

    param_id.run()

    # Post-calibration fit-error vectors (percent + std error per observable),
    # which drive the Analysis-tab bar charts. Best-effort; never fails the run.
    errors = _generate_error_vectors(param_id, output_dir)

    # The best fit against the obs_data's held-out data, if it carries any.
    # Rank 0 only, like everything CA writes after a run.
    if getattr(param_id, "rank", 0) == 0:
        _validate_held_out(param_id, prediction_items, emulated)

    # Under mpiexec every rank runs this script; only rank 0 holds the
    # authoritative best fit and writes the results (mirrors param_id_run_script).
    rank = getattr(param_id, "rank", 0)
    best_vals = param_id.get_best_param_vals()
    param_names = param_id.get_param_names()  # list of lists of qnames

    result = {"params": {}, "cost": None, "rank": rank}
    if rank == 0:
        # Raw slot value per member qname. For a modifier this is theta at every
        # member (anchor included) -- deliberately, because best-fit reuse
        # (start-from-best-fit, SA nominal) matches by anchor and needs theta
        # there, not a physical value. The physical expansion is separate below.
        params: dict[str, float] = {}
        for i, name_list in enumerate(param_names):
            for qname in name_list:
                params[qname] = float(best_vals[i])
        cost = getattr(getattr(param_id, "param_id", None), "best_cost", None)
        info = getattr(param_id, "param_id_info", None) or {}
        modifiers = _result_modifiers(info, best_vals)
        # The calibrated model (the best fit written into the model, #114) is CA's to write
        # (CVS0DParamID.write_calibrated_model, CA #650), with modifiers expanded as the
        # calibration ran them; it records the file in the study manifest too.
        calibrated_path = _ca_write_calibrated_model(param_id, config, output_dir)
        # No results.json. Everything below is already on disk, written by CA
        # itself, and the manager reads it from there (#210) -- serialising a
        # second copy put a file in the user's working dir that is no part
        # of the study and made CUFLynx the author of a format duplicating CA's.
        # The return value still carries it, because that is this function's
        # contract with its caller inside this process.
        result = {
            "params": params,
            "modifiers": modifiers,
            "cost": None if cost is None else float(cost),
            "calibrated_model_path": calibrated_path,
            **errors,
            "rank": rank,
        }
    return result


def _result_modifiers(param_id_info: dict, best_vals) -> list:
    """The modifier metadata a run carries back to the app.

    ``{name, anchor, targets, operation, baselines, theta}`` per modifier --
    the anchor is ``targets[0]`` (the same name every anchor-keyed consumer
    collapses to), theta the best-fit slot value, baselines as CA resolved them
    before the run. The frontend applies theta to the modifier slider and skips
    its targets when applying the per-member params map.
    """
    out = []
    for mod in (param_id_info or {}).get("modifiers") or []:
        idx = mod.get("index")
        theta = None
        if idx is not None and 0 <= int(idx) < len(best_vals):
            theta = float(best_vals[int(idx)])
        targets = list(mod.get("targets") or [])
        baselines = mod.get("baselines")
        out.append({
            "name": mod.get("name"),
            "anchor": targets[0] if targets else None,
            "targets": targets,
            "operation": mod.get("operation"),
            "baselines": None if baselines is None else [float(b) for b in baselines],
            "theta": theta,
        })
    return out


#: what a libcuflynx without CVS0DParamID.write_calibrated_model is told
CALIBRATED_MODEL_NEEDS = ("this circulatory_autogen has no write_calibrated_model, which writes "
                          "the calibrated model: upgrade to libcuflynx 0.8.0 or later "
                          "(circulatory_autogen #650). The calibration result is kept; "
                          "only the calibrated CellML is not written.")


def _ca_write_calibrated_model(param_id, config: dict, output_dir: str) -> str | None:
    """CA writes ``<output_dir>/<prefix>_calibrated.cellml`` and records it in the study
    manifest (``CVS0DParamID.write_calibrated_model``, CA #650); returns its path.

    None for a model that is not CellML (nothing to write), and when CA has no such writer
    or the write fails: both are said in the run log, and neither fails the calibration,
    whose result is already on disk and can be written into a model again from there."""
    cellml_path = config.get("cellml_path")
    if not cellml_path or not str(cellml_path).endswith(".cellml"):
        return None
    write = getattr(param_id, "write_calibrated_model", None)
    if write is None:
        print(f"ERROR: {CALIBRATED_MODEL_NEEDS}", flush=True)
        return None
    try:
        path = write(cellml_path=cellml_path, output_dir=output_dir)
    except Exception as exc:  # noqa: BLE001 - the calibration result stands without it
        print(f"WARNING: circulatory_autogen could not write the calibrated model: {exc}",
              flush=True)
        return None
    return path if path and os.path.isfile(path) else None


def _generate_error_vectors(param_id, output_dir: str) -> dict:
    """Load the best fit's per-observable percent/std error vectors.

    A libcuflynx with ``save_best_fit_errors`` writes them itself when ``run`` ends
    (``percent_error_vec.npy``, ``std_error_vec.npy``, ``error_vec_names.npy``), from one
    simulation at the best fit and without figures. An older one writes them only from
    its plotting, so for it this runs plot_param_id_script's steps
    (simulate_with_best_param_vals -> plot_outputs) first.
    Returns ``{"percent_error", "std_error", "error_labels"}`` on rank 0 (values
    may be None when they could not be made); ``{}`` on other ranks. Best-effort:
    a failure here must not fail the calibration.
    """
    import numpy as np

    if not hasattr(param_id, "save_best_fit_errors"):
        try:
            param_id.simulate_with_best_param_vals()
            param_id.plot_outputs()
        except Exception as exc:  # noqa: BLE001 - plotting is best-effort
            print(f"warning: post-calibration error plots skipped: {exc}", flush=True)

    if getattr(param_id, "rank", 0) != 0:
        return {}

    out = {"percent_error": None, "std_error": None, "error_labels": None}
    try:
        pe = _find_output_file(param_id, output_dir, "percent_error_vec.npy")
        se = _find_output_file(param_id, output_dir, "std_error_vec.npy")
        if pe and se:
            out["percent_error"] = [float(x) for x in np.load(pe)]
            out["std_error"] = [float(x) for x in np.load(se)]
            obs_info = getattr(param_id, "obs_info", {}) or {}
            out["error_labels"] = [str(n) for n in ca_obs.item_labels(obs_info)]
    except Exception as exc:  # noqa: BLE001
        print(f"warning: could not load error vectors: {exc}", flush=True)
    return out


def _read_json(path: str):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 - CA reports an unreadable obs_data itself
        return None


def _validate_held_out(param_id, prediction_items: list, emulated: bool) -> str | None:
    """Have libcuflynx validate the best fit against the held-out data (CA #535).

    libcuflynx does all of it: ``save_prediction_data`` simulates every experiment
    at the best fit, scores the prediction items that carry data
    (``param_id.validation``) and writes ``validation_results.json`` into its run
    directory, which is what the manager reads. CUFLynx computes nothing; a
    libcuflynx without ``param_id.validation`` gives no validation, and the run log
    says why. Returns the file's path, or None when nothing was validated.

    Best-effort: a validation that cannot be made must never fail the calibration.
    """
    import ca_run_history  # noqa: PLC0415 (CA output formats, one place)
    from obs_data import ca_accepts_held_out  # noqa: PLC0415 (runners/ too)

    if not any(isinstance(it, dict) and it.get("value") is not None
               for it in prediction_items):
        return None
    if emulated:
        # An emulator predicts the scalar data_item features only, never traces.
        print("held-out data not validated: the calibration ran on the emulator, "
              "which predicts no traces", flush=True)
        return None
    if not ca_accepts_held_out():
        print("held-out data not validated: that needs a newer libcuflynx "
              "(one with param_id.validation)", flush=True)
        return None
    run_dir = getattr(param_id, "output_dir", None)
    if not run_dir:
        return None
    try:
        param_id.save_prediction_data()
    except Exception as exc:  # noqa: BLE001 - never fail the run over its validation
        print(f"warning: held-out data not validated: {exc}", flush=True)
        return None
    written = os.path.join(run_dir, ca_run_history.VALIDATION_RESULTS_FILE)
    if os.path.isfile(written) and os.path.getmtime(written) >= _STARTED - 1.0:
        return written
    return None


def _find_output_file(param_id, output_dir: str, name: str) -> str | None:
    """Locate an output file, tolerating the ``<case_type>`` subdir
    circulatory_autogen writes into (e.g. ``genetic_algorithm_<prefix>_…``).

    param_id.output_dir is that nested dir; fall back to a recursive glob under
    the top-level output_dir (mirrors calibration._find_history_file).
    """
    import glob

    nested = getattr(param_id, "output_dir", None)
    for base in (nested, output_dir):
        if base:
            direct = os.path.join(base, name)
            if os.path.exists(direct):
                return direct
    matches = glob.glob(os.path.join(output_dir, "**", name), recursive=True)
    return matches[0] if matches else None


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(f"{FAIL_MARKER} usage: calibration_runner.py <config.json>", flush=True)
        return 2
    config = json.loads(Path(argv[1]).read_text())
    try:
        result = run(config)
    except Exception as exc:  # surface to the captured stdout for the UI
        print(f"{FAIL_MARKER} {exc}", flush=True)
        traceback.print_exc()
        _abort_mpi()
        return 1
    # Only rank 0 reports completion (avoids duplicate markers under mpiexec).
    if result.get("rank", 0) == 0:
        if "cost" in result:
            print(f"best cost: {result['cost']}", flush=True)
        print(DONE_MARKER, flush=True)
    return 0


def _abort_mpi() -> None:
    """Abort all MPI ranks so a failure on one rank doesn't hang the others."""
    try:
        from mpi4py import MPI

        if MPI.COMM_WORLD.Get_size() > 1:
            MPI.COMM_WORLD.Abort(1)
    except Exception:
        pass


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
