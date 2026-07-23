"""Standalone calibration runner — spawned as a subprocess by the API.

Reads a JSON config, drives circulatory_autogen's ``CVS0DParamID`` to calibrate
the model against the uploaded obs_data + params_for_id, and writes the best-fit
parameters to ``<output_dir>/results.json``. Progress (per-generation cost,
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
import traceback
from pathlib import Path

# Force a headless matplotlib backend before circulatory_autogen imports pyplot
# (the post-calibration error plots run server-side with no display).
os.environ.setdefault("MPLBACKEND", "Agg")

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


def _ensure_ca_on_path() -> None:
    src = os.environ.get("CIRCULATORY_AUTOGEN_SRC")
    if not src:
        repo_root = Path(__file__).resolve().parents[2]
        src = str(repo_root.parent / "circulatory_autogen" / "src")
    if src not in sys.path:
        sys.path.insert(0, src)


def _solver_info_from_config(config: dict, settings: dict) -> dict:
    """Solver_info for the chosen backend: the config's solver_info (set in the
    Settings popup) with the solver name + CVODE step defaults filled in."""
    si = dict(config.get("solver_info") or {})
    si.setdefault("solver", config.get("solver") or settings.get("solver", "CVODE_myokit"))
    si.setdefault("MaximumStep", settings.get("MaximumStep", 0.0001))
    si.setdefault("MaximumNumberOfSteps", settings.get("MaximumNumberOfSteps", 5000))
    return si


def _apply_start_point(param_id, values: dict, source_label: str) -> None:
    """Override the gradient-descent start point with a ``{qname: value}`` map.

    CA seeds ``OpencorParamID.param_init`` (the sp_minimize x0) from the model's
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
        names = [
            n[0] if isinstance(n, (list, tuple)) else n
            for n in pid.param_id_info["param_names"]
        ]
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


def run(config: dict) -> dict:
    _ensure_ca_on_path()
    from param_id.paramID import CVS0DParamID  # noqa: E402

    settings = config.get("settings", {})
    output_dir = config["output_dir"]
    os.makedirs(output_dir, exist_ok=True)

    model_type = config.get("model_type", "cellml_only")
    solver_info = _solver_info_from_config(config, settings)
    # gradient_method drives CA's gradient source for the gradient methods:
    # AD/FSA => automatic (CasADi jacobian or Myokit CVODES forward sensitivity),
    # FD => finite difference. Ignored by the non-gradient methods.
    do_ad = str(settings.get("gradient_method", "FD")).upper() in ("AD", "FSA")

    # Global random seed (Settings popup). When set, seed numpy's legacy global RNG
    # so the GA (which draws from np.random directly) is repeatable, and forward it
    # into optimiser_options for the multi-start start sampler. None => leave every
    # random process non-deterministic (CA's own defaults apply).
    seed = config.get("seed")
    if seed is not None:
        import numpy as np  # noqa: E402

        np.random.seed(int(seed))

    optimiser_options = _optimiser_options(settings, seed)

    print(
        f"Starting {settings.get('param_id_method', 'genetic_algorithm')} "
        f"calibration ({optimiser_options.get('num_calls_to_function', '?')} max evals)",
        flush=True,
    )

    param_id = CVS0DParamID(
        model_path=config["model_path"],
        model_type=model_type,
        param_id_method=settings.get("param_id_method", "genetic_algorithm"),
        file_name_prefix=config.get("file_prefix", "model"),
        params_for_id_path=config["params_path"],
        param_id_obs_path=config["obs_path"],
        sim_time=float(settings.get("sim_time", 2.0)),
        pre_time=float(settings.get("pre_time", 0.0)),
        dt=float(settings.get("dt", 0.01)),
        solver_info=solver_info,
        optimiser_options=optimiser_options,
        do_ad=do_ad,
        DEBUG=bool(settings.get("DEBUG", False)),
        param_id_output_dir=output_dir,
        resources_dir=os.path.dirname(config["params_path"]),
    )

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

    # Under mpiexec every rank runs this script; only rank 0 holds the
    # authoritative best fit and writes the results (mirrors param_id_run_script).
    rank = getattr(param_id, "rank", 0)
    best_vals = param_id.get_best_param_vals()
    param_names = param_id.get_param_names()  # list of lists of qnames

    result = {"params": {}, "cost": None, "rank": rank}
    if rank == 0:
        params: dict[str, float] = {}
        for i, name_list in enumerate(param_names):
            for qname in name_list:
                params[qname] = float(best_vals[i])
        cost = getattr(getattr(param_id, "param_id", None), "best_cost", None)
        payload = {
            "params": params,
            "cost": None if cost is None else float(cost),
            **errors,
        }
        result = {**payload, "rank": rank}
        with open(os.path.join(output_dir, "results.json"), "w") as fh:
            json.dump(payload, fh)
    return result


def _generate_error_vectors(param_id, output_dir: str) -> dict:
    """Run circulatory_autogen's post-calibration plotting to produce the
    per-observable percent/std error vectors, then load them.

    Mirrors plot_param_id_script (simulate_with_best_param_vals -> plot_outputs),
    which writes ``percent_error_vec.npy`` / ``std_error_vec.npy`` to output_dir.
    Returns ``{"percent_error", "std_error", "error_labels"}`` on rank 0 (values
    may be None when plotting is unavailable); ``{}`` on other ranks. Best-effort:
    a plotting failure must not fail the calibration.
    """
    import numpy as np

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
            names = obs_info.get("names_for_plotting", [])
            out["error_labels"] = [str(n) for n in names]
    except Exception as exc:  # noqa: BLE001
        print(f"warning: could not load error vectors: {exc}", flush=True)
    return out


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
