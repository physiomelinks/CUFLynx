"""Standalone sensitivity-analysis runner — spawned as a subprocess by the API.

Reads a JSON config and writes the resulting indices to
``<output_dir>/results.json``. Two methods are supported:

* ``method: "sobol"`` — circulatory_autogen's ``SensitivityAnalysis`` global
  variance-based (Sobol) engine; also writes CSV/PNG artifacts to ``output_dir``.
* ``method: "local"`` — derivative-based local sensitivity about a nominal point
  (see :mod:`local_sensitivity`). Only the finite-difference gradient source is
  wired up today.

Progress is printed straight to stdout, which the API captures for the terminal
view; run with ``python -u`` so it streams unbuffered.

Usage:  python -u sensitivity_runner.py <config.json>

config.json (same shape as the calibration runner):
{
  "model_path": "...cellml", "obs_path": "...json", "params_path": "...csv",
  "output_dir": "...", "file_prefix": "model", "num_cores": 1, "python": null,
  "settings": { "method": "sobol", "sample_type": "saltelli",
                "num_samples": 256, "dt": 0.01, "solver": "CVODE_myokit",
                "DEBUG": false }
  // or, for local: "settings": { "method": "local", "gradient_method": "FD",
  //                              "rel_step": 0.01, "nominal": "midpoint" }
}
"""

from __future__ import annotations

import json
import os
import sys
import traceback
from pathlib import Path

# Markers the API watches for in stdout.
DONE_MARKER = "__SENSITIVITY_DONE__"
FAIL_MARKER = "__SENSITIVITY_FAILED__"


def _ensure_ca_on_path() -> None:
    src = os.environ.get("CIRCULATORY_AUTOGEN_SRC")
    if not src:
        repo_root = Path(__file__).resolve().parents[2]
        src = str(repo_root.parent / "circulatory_autogen" / "src")
    if src not in sys.path:
        sys.path.insert(0, src)


def _indices_to_dict(sa) -> dict:
    """Read the Sobol indices the engine just wrote back into a JSON-friendly dict.

    ``load_sobol_indices`` returns ``{'S1': {out_name: {param: val}}, 'ST': {...}}``.
    Derive the param/output name lists from it so the frontend heatmap can render a
    stable params x outputs grid.
    """
    indices = sa.SA_manager.load_sobol_indices()
    param_names: list[str] = []
    output_names: list[str] = []
    for kind in ("S1", "ST"):
        for out_name, params in (indices.get(kind) or {}).items():
            if out_name not in output_names:
                output_names.append(out_name)
            for p in params:
                if p not in param_names:
                    param_names.append(p)
    return {
        "indices": indices,
        "param_names": param_names,
        "output_names": output_names,
    }


def _calibrate_for_nominal(config: dict, settings: dict, solver_info: dict):
    """Run a GA calibration in-process and return its best-fit parameter vector.

    Used by the local-SA ``run_calibration_first`` path to find the point to
    linearise about. Mirrors :mod:`calibration_runner`'s ``CVS0DParamID`` setup;
    the returned vector is ordered to match ``get_param_names()`` (and therefore
    the SA param order, both derived from params_for_id).
    """
    import numpy as np  # noqa: E402
    from param_id.paramID import CVS0DParamID  # noqa: E402

    optimiser_options = {
        "num_calls_to_function": int(settings.get("num_calls_to_function", 100)),
        "cost_convergence": float(settings.get("cost_convergence", 0.0001)),
        "max_patience": int(settings.get("max_patience", 10)),
    }
    if settings.get("cost_type"):
        optimiser_options["cost_type"] = settings["cost_type"]

    print(
        "Running a calibration first to locate the best-fit nominal point "
        f"({settings.get('param_id_method', 'genetic_algorithm')}, "
        f"{optimiser_options['num_calls_to_function']} max evals)",
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
        param_id_output_dir=config["output_dir"],
        resources_dir=os.path.dirname(config["params_path"]),
    )
    param_id.run()
    return np.asarray(param_id.get_best_param_vals(), dtype=float)


def run(config: dict) -> dict:
    _ensure_ca_on_path()
    from sensitivity_analysis.sensitivityAnalysis import SensitivityAnalysis  # noqa: E402

    settings = config.get("settings", {})
    method = settings.get("method", "sobol")
    output_dir = config["output_dir"]
    os.makedirs(output_dir, exist_ok=True)

    solver_info = {
        "solver": settings.get("solver", "CVODE_myokit"),
        "MaximumStep": settings.get("MaximumStep", 0.0001),
        "MaximumNumberOfSteps": settings.get("MaximumNumberOfSteps", 5000),
    }
    # sample_type / num_samples are only used by the Sobol engine, but the
    # SensitivityAnalysis constructor needs them present to build its param
    # table; harmless placeholders for the local (finite-difference) path.
    sa_options = {
        "method": method,
        "sample_type": settings.get("sample_type", "saltelli"),
        "num_samples": int(settings.get("num_samples", 256)),
        "output_dir": output_dir,
    }

    sa = SensitivityAnalysis(
        model_path=config["model_path"],
        model_type="cellml_only",
        file_name_prefix=config.get("file_prefix", "model"),
        sa_options=sa_options,
        DEBUG=bool(settings.get("DEBUG", False)),
        param_id_output_dir=output_dir,
        resources_dir=os.path.dirname(config["params_path"]),
        solver_info=solver_info,
        dt=float(settings.get("dt", 0.01)),
        param_id_obs_path=config["obs_path"],
        params_for_id_path=config["params_path"],
    )

    # Local (derivative-based) SA runs single-process; no Sobol sampling / MPI.
    if method == "local":
        from local_sensitivity import compute_local_sensitivity  # noqa: E402

        # run_calibration_first: locate a fresh best-fit point here, then take
        # the local sensitivity about it. Otherwise the nominal point comes from
        # the current parameter values / a reused best fit / the bounds.
        best_vals = None
        if bool(settings.get("run_calibration_first", False)):
            best_vals = _calibrate_for_nominal(config, settings, solver_info)
        payload = compute_local_sensitivity(
            sa, settings, best_vals=best_vals, best_params=config.get("best_params")
        )
        with open(os.path.join(output_dir, "results.json"), "w") as fh:
            json.dump(payload, fh)
        print(f"Local sensitivity analysis completed; results in {output_dir}", flush=True)
        return {"rank": 0, **payload}

    print(
        f"Starting {sa_options['method']} sensitivity analysis "
        f"({sa_options['num_samples']} samples, {sa_options['sample_type']} sampling)",
        flush=True,
    )

    sa.run_sensitivity_analysis()

    # Under mpiexec every rank runs this script; only rank 0 holds the gathered
    # outputs and writes the indices (mirrors sensitivity_analysis_run_script).
    rank = getattr(getattr(sa, "SA_manager", None), "rank", 0)
    result = {"rank": rank}
    if rank == 0:
        payload = _indices_to_dict(sa)
        result.update(payload)
        with open(os.path.join(output_dir, "results.json"), "w") as fh:
            json.dump(payload, fh)
    return result


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(f"{FAIL_MARKER} usage: sensitivity_runner.py <config.json>", flush=True)
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
