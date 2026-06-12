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

# Markers the API watches for in stdout.
DONE_MARKER = "__CALIBRATION_DONE__"
FAIL_MARKER = "__CALIBRATION_FAILED__"


def _ensure_ca_on_path() -> None:
    src = os.environ.get("CIRCULATORY_AUTOGEN_SRC")
    if not src:
        repo_root = Path(__file__).resolve().parents[2]
        src = str(repo_root.parent / "circulatory_autogen" / "src")
    if src not in sys.path:
        sys.path.insert(0, src)


def run(config: dict) -> dict:
    _ensure_ca_on_path()
    from param_id.paramID import CVS0DParamID  # noqa: E402

    settings = config.get("settings", {})
    output_dir = config["output_dir"]
    os.makedirs(output_dir, exist_ok=True)

    solver_info = {
        "solver": settings.get("solver", "CVODE_myokit"),
        "MaximumStep": settings.get("MaximumStep", 0.0001),
        "MaximumNumberOfSteps": settings.get("MaximumNumberOfSteps", 5000),
    }
    optimiser_options = {
        "num_calls_to_function": int(settings.get("num_calls_to_function", 100)),
        "cost_convergence": float(settings.get("cost_convergence", 0.0001)),
        "max_patience": int(settings.get("max_patience", 10)),
    }
    if settings.get("cost_type"):
        optimiser_options["cost_type"] = settings["cost_type"]

    print(
        f"Starting {settings.get('param_id_method', 'genetic_algorithm')} "
        f"calibration ({optimiser_options['num_calls_to_function']} max evals)",
        flush=True,
    )

    param_id = CVS0DParamID(
        model_path=config["model_path"],
        model_type="cellml_only",
        param_id_method=settings.get("param_id_method", "genetic_algorithm"),
        file_name_prefix=config.get("file_prefix", "model"),
        params_for_id_path=config["params_path"],
        param_id_obs_path=config["obs_path"],
        sim_time=float(settings.get("sim_time", 2.0)),
        pre_time=float(settings.get("pre_time", 0.0)),
        dt=float(settings.get("dt", 0.01)),
        solver_info=solver_info,
        optimiser_options=optimiser_options,
        DEBUG=bool(settings.get("DEBUG", False)),
        param_id_output_dir=output_dir,
        resources_dir=os.path.dirname(config["params_path"]),
    )

    param_id.run()

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
        result = {
            "params": params,
            "cost": None if cost is None else float(cost),
            "rank": rank,
        }
        with open(os.path.join(output_dir, "results.json"), "w") as fh:
            json.dump({k: result[k] for k in ("params", "cost")}, fh)
    return result


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
