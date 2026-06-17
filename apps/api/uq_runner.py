"""Standalone UQ runner — spawned as a subprocess by the API.

Runs uncertainty quantification on a CellML model and writes per-parameter
posterior distributions to ``<output_dir>/results.json``. Two methods:

- ``mcmc``    — emcee sampling (circulatory_autogen ``CVS0DParamID(mcmc_instead=True)``).
- ``laplace`` — Gaussian approx around the best fit (``IdentifiabilityAnalysis``).

Both need a best-fit point. With ``run_calibration_first`` the runner does its own
GA calibration; otherwise the API passes the reused best fit as ``config["best_params"]``
(qname -> value) from the latest completed calibration.

Usage:  python -u uq_runner.py <config.json>
"""

from __future__ import annotations

import json
import os
import sys
import traceback
from pathlib import Path

# Headless matplotlib for any plots circulatory_autogen produces server-side.
os.environ.setdefault("MPLBACKEND", "Agg")

# Markers the API watches for in stdout.
DONE_MARKER = "__UQ_DONE__"
FAIL_MARKER = "__UQ_FAILED__"

NUM_BINS = 30
LAPLACE_SAMPLES = 100000


def _ensure_ca_on_path() -> None:
    src = os.environ.get("CIRCULATORY_AUTOGEN_SRC")
    if not src:
        repo_root = Path(__file__).resolve().parents[2]
        src = str(repo_root.parent / "circulatory_autogen" / "src")
    if src not in sys.path:
        sys.path.insert(0, src)


def _solver_info(config: dict, settings: dict) -> dict:
    """Solver_info for the chosen backend: the config's solver_info (set in the
    Settings popup) with the solver name + CVODE step defaults filled in."""
    si = dict(config.get("solver_info") or {})
    si.setdefault("solver", config.get("solver") or settings.get("solver", "CVODE_myokit"))
    si.setdefault("MaximumStep", settings.get("MaximumStep", 0.0001))
    si.setdefault("MaximumNumberOfSteps", settings.get("MaximumNumberOfSteps", 5000))
    return si


def _mle_obs_path(config, cost_type: str) -> str:
    """Write a copy of the obs_data with every data_item's ``cost_type`` set to an
    MLE cost. MCMC / Laplace require ``ln L = -cost``, and the per-observable cost
    is read from the data_items at construction (defaults to MSE otherwise)."""
    obs = json.loads(Path(config["obs_path"]).read_text())
    for item in obs.get("data_items", []):
        item["cost_type"] = cost_type
    out = os.path.join(config["output_dir"], "uq_obs_data.json")
    Path(out).write_text(json.dumps(obs))
    return out


def _make_param_id(config, settings, obs_path, *, mcmc, options_key, options):
    """Construct a CVS0DParamID (no run). ``options_key`` is 'optimiser_options' or
    'mcmc_options'; ``mcmc`` toggles mcmc_instead."""
    from param_id.paramID import CVS0DParamID  # noqa: E402

    kwargs = dict(
        model_path=config["model_path"],
        model_type=config.get("model_type", "cellml_only"),
        param_id_method=settings.get("param_id_method", "genetic_algorithm"),
        mcmc_instead=mcmc,
        file_name_prefix=config.get("file_prefix", "model"),
        params_for_id_path=config["params_path"],
        param_id_obs_path=obs_path,
        sim_time=float(settings.get("sim_time", 2.0)),
        pre_time=float(settings.get("pre_time", 0.0)),
        dt=float(settings.get("dt", 0.01)),
        solver_info=_solver_info(config, settings),
        DEBUG=bool(settings.get("DEBUG", False)),
        param_id_output_dir=config["output_dir"],
        resources_dir=os.path.dirname(config["params_path"]),
    )
    kwargs[options_key] = options
    return CVS0DParamID(**kwargs)


def _flat_param_names(param_id):
    """Representative qname per parameter group (first of each list), matching the
    column order of best-fit vectors / samples."""
    return [grp[0] if isinstance(grp, (list, tuple)) else grp for grp in param_id.get_param_names()]


def _best_from_reuse(param_id, best_params: dict):
    import numpy as np

    return np.array(
        [float(best_params[name]) for name in _flat_param_names(param_id)], dtype=float
    )


def _distributions(flat, qnames):
    """Per-parameter posterior summary + histogram from samples (N, P)."""
    import numpy as np

    out = []
    for i, qname in enumerate(qnames):
        col = np.asarray(flat[:, i], dtype=float)
        col = col[np.isfinite(col)]
        if col.size == 0:
            continue
        counts, edges = np.histogram(col, bins=NUM_BINS)
        q05, q50, q95 = (float(x) for x in np.percentile(col, [5, 50, 95]))
        out.append(
            {
                "qname": qname,
                "mean": float(np.mean(col)),
                "std": float(np.std(col)),
                "q05": q05,
                "q50": q50,
                "q95": q95,
                "bins": [float(x) for x in edges],
                "counts": [int(x) for x in counts],
            }
        )
    return out


def run(config: dict) -> dict:
    _ensure_ca_on_path()
    import numpy as np
    import param_id.paramID as pid
    from param_id.paramID import ensure_mle_cost_type_for_bayesian_inner
    from identifiabilty_analysis.identifiabilityAnalysis import IdentifiabilityAnalysis

    settings = config.get("settings", {})
    method = settings.get("method", "mcmc")
    output_dir = config["output_dir"]
    os.makedirs(output_dir, exist_ok=True)

    optimiser_options = {
        "num_calls_to_function": int(settings.get("num_calls_to_function", 100)),
        "cost_convergence": float(settings.get("cost_convergence", 0.001)),
        "max_patience": int(settings.get("max_patience", 10)),
        "cost_type": settings.get("cost_type", "gaussian_MLE"),
    }
    mcmc_options = {
        "num_steps": int(settings.get("num_steps", 1000)),
        "num_walkers": int(settings.get("num_walkers", 64)),
        "cost_convergence": float(settings.get("cost_convergence", 0.001)),
        "cost_type": settings.get("cost_type", "gaussian_MLE"),
    }
    # Minimal inp_data_dict so ensure_mle_cost_type_for_bayesian_inner can pick the
    # MLE cost from our option dicts (required for ln L = -cost in MCMC / Laplace).
    inp = {
        "DEBUG": bool(settings.get("DEBUG", False)),
        "optimiser_options": optimiser_options,
        "mcmc_options": mcmc_options,
    }

    run_calib = bool(settings.get("run_calibration_first", False))
    reuse_best = config.get("best_params")
    obs_path = _mle_obs_path(config, settings.get("cost_type", "gaussian_MLE"))

    print(
        f"Starting {method} UQ "
        f"({'fresh calibration' if run_calib else 'reusing calibration best fit'})",
        flush=True,
    )

    # ---- best-fit point ----------------------------------------------------
    ga = None
    if run_calib:
        ga = _make_param_id(
            config, settings, obs_path, mcmc=False, options_key="optimiser_options",
            options=optimiser_options,
        )
        ga.run()
        best = np.asarray(ga.get_best_param_vals(), dtype=float)
    elif not reuse_best:
        raise RuntimeError("no best_params supplied and run_calibration_first is false")

    # ---- run the chosen method --------------------------------------------
    if method == "mcmc":
        mcmc = _make_param_id(
            config, settings, obs_path, mcmc=True, options_key="mcmc_options",
            options=mcmc_options,
        )
        best = best if run_calib else _best_from_reuse(mcmc, reuse_best)
        mcmc.set_best_param_vals(best)
        ensure_mle_cost_type_for_bayesian_inner(pid.mcmc_object, inp)
        mcmc.run_mcmc()
        rank = getattr(mcmc, "rank", 0)
        if rank != 0:
            return {"rank": rank}
        flat = mcmc.get_mcmc_samples()[0]
        qnames = _flat_param_names(mcmc)
    elif method == "laplace":
        cvs = ga or _make_param_id(
            config, settings, obs_path, mcmc=False, options_key="optimiser_options",
            options=optimiser_options,
        )
        best = best if run_calib else _best_from_reuse(cvs, reuse_best)
        ia = IdentifiabilityAnalysis(
            config["model_path"], config.get("model_type", "cellml_only"), config.get("file_prefix", "model"),
            param_id_output_dir=output_dir,
            resources_dir=os.path.dirname(config["params_path"]),
            param_id=cvs.param_id,
        )
        ia.set_best_param_vals(best)
        ensure_mle_cost_type_for_bayesian_inner(cvs.param_id, inp)
        ia.run({"method": "Laplace"})
        rank = getattr(ia, "rank", 0)
        if rank != 0:
            return {"rank": rank}
        flat = np.random.multivariate_normal(
            ia.mean_Lapalace, ia.covariance_matrix_Laplace, size=LAPLACE_SAMPLES
        )
        qnames = _flat_param_names(cvs)
    else:
        raise RuntimeError(f"unknown UQ method: {method!r}")

    payload = {"method": method, "params": _distributions(np.asarray(flat), qnames)}
    with open(os.path.join(output_dir, "results.json"), "w") as fh:
        json.dump(payload, fh)
    return {"rank": 0, **payload}


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(f"{FAIL_MARKER} usage: uq_runner.py <config.json>", flush=True)
        return 2
    config = json.loads(Path(argv[1]).read_text())
    try:
        result = run(config)
    except Exception as exc:  # surface to the captured stdout for the UI
        print(f"{FAIL_MARKER} {exc}", flush=True)
        traceback.print_exc()
        _abort_mpi()
        return 1
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
