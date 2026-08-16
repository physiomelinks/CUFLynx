"""Standalone UQ runner — spawned as a subprocess by the API.

Runs uncertainty quantification on a CellML model and persists the posterior
*samples* it settled on (``uq_posterior_samples.npy``); the manager bins them into
the per-parameter distributions the UQ panel plots (#210). Two methods:

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

import emulator_config
from obs_data import data_items_of

# Headless matplotlib for any plots circulatory_autogen produces server-side.
os.environ.setdefault("MPLBACKEND", "Agg")

# Markers the API watches for in stdout.
DONE_MARKER = "__UQ_DONE__"
FAIL_MARKER = "__UQ_FAILED__"
#: Prefix for the one line of run metadata no file holds: which method ran. The
#: posterior itself is read from the samples on disk (#210).
META_MARKER = "__UQ_META__ "

# CUFLynx-level / calibration settings that must NOT be forwarded into CA's
# UQ_options (the rest are the CA UQ option values the UI collected).
# config_outputs_dir is attached to every run by the UI (App.vue) alongside
# python_path; cost_convergence is NOT reserved here because for MCMC it is a
# genuine UQ_options value (_uq_options sets it explicitly).
_UQ_RESERVED = {
    "method", "run_calibration_first", "num_cores", "dt", "DEBUG", "solver",
    "solver_info", "python_path", "sim_time", "pre_time", "generated_model_format",
    "param_id_method", "num_calls_to_function", "max_patience", "gradient_method",
    "config_outputs_dir",
}

#: Histogram resolution. Imported lazily from ca_run_history where the manager
#: re-bins the same samples, so the two cannot drift apart; the literal is the
#: fallback for a runner executed without the app modules importable.
try:
    from ca_run_history import NUM_BINS
except ImportError:  # pragma: no cover - the app module is normally importable
    NUM_BINS = 40
LAPLACE_SAMPLES = 100000


def _optimiser_options(settings: dict, seed=None) -> dict:
    """optimiser_options for the ``run_calibration_first`` best-fit search. A global
    random ``seed`` lands under ``optimiser_options['seed']`` (the multi-start start
    sampler key); ``None`` omits it."""
    opts = {
        "num_calls_to_function": int(settings.get("num_calls_to_function", 100)),
        "cost_convergence": float(settings.get("cost_convergence", 0.001)),
        "max_patience": int(settings.get("max_patience", 10)),
        "cost_type": settings.get("cost_type", "gaussian_MLE"),
    }
    if seed is not None:
        opts["seed"] = int(seed)
    return opts


def _uq_options(settings: dict, seed=None) -> dict:
    """CA ``UQ_options`` from the UI settings. Any additional CA UQ option the UI
    collected from CA's ANALYSIS_OPTIONS schema is forwarded (forward-compatible).
    A global random ``seed`` is forwarded under ``UQ_options['seed']``; ``None``
    omits it."""
    opts = {
        "num_steps": int(settings.get("num_steps") or 1000),
        "num_walkers": int(settings.get("num_walkers") or 64),
        "cost_convergence": float(settings.get("cost_convergence", 0.001)),
        "cost_type": settings.get("cost_type", "gaussian_MLE"),
    }
    for k, v in settings.items():
        if k not in _UQ_RESERVED and k not in opts and v is not None:
            opts[k] = v
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


def _solver_info(config: dict, settings: dict) -> dict:
    """Solver_info for the chosen backend: the config's solver_info (set in the
    Settings popup) with the solver name + CVODE step defaults filled in."""
    si = dict(config.get("solver_info") or {})
    si.setdefault("solver", config.get("solver") or settings.get("solver", "CVODE_myokit"))
    si.setdefault("MaximumStep", settings.get("MaximumStep", 0.0001))
    # No MaximumNumberOfSteps default: myokit_helper never reads it (myokit's
    # Simulation has no max-step-count knob), CA's migrate_legacy_solver_info_keys
    # pops it for solve_ivp/casadi, and CA fills its own default for the backends
    # that do use it. Injecting it here only seeded an inert setting.
    return si


def _mle_obs_path(config, cost_type: str) -> str:
    """Write a copy of the obs_data with every data_item's ``cost_type`` set to an
    MLE cost. MCMC / Laplace require ``ln L = -cost``, and the per-observable cost
    is read from the data_items at construction (defaults to MSE otherwise).

    The items are reached through :func:`obs_data.data_items_of` because an
    obs_data may be an object *or* a bare array of data_items, and only the
    object form was handled here: UQ on a data-only file (3compartment,
    heat_fenics) died with ``'list' object has no attribute 'get'``. The items
    are edited in place and the document written back in the shape it arrived
    in, so CA reads the same shape the user supplied.
    """
    obs = json.loads(Path(config["obs_path"]).read_text())
    for item in data_items_of(obs):
        if isinstance(item, dict):
            item["cost_type"] = cost_type
    out = os.path.join(config["output_dir"], "uq_obs_data.json")
    Path(out).write_text(json.dumps(obs), encoding="utf-8")
    return out


def _make_param_id(config, settings, obs_path, *, mcmc, options_key, options):
    """Construct a CVS0DParamID (no run). ``options_key`` is 'optimiser_options' or
    the UQ options kwarg this CA takes (see uq_options_kwarg); ``mcmc`` toggles
    mcmc_instead."""
    from ca_imports import ca_from  # noqa: E402 (shipped into runners/ too)

    CVS0DParamID = ca_from("param_id.paramID", "CVS0DParamID")

    kwargs = dict(
        model_path=config["model_path"],
        model_type=config.get("model_type", "cellml"),
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
        # CUFLynx-authored operation/cost funcs loaded from external files (CA #303).
        operation_funcs_external_path=config.get("operation_funcs_external_path"),
        cost_funcs_external_path=config.get("cost_funcs_external_path"),
        # On the trained emulator when the user asked for it, so a posterior is
        # sampled from the same forward model the calibration used (CA #333).
        **emulator_config.engine_kwargs(config),
    )
    kwargs[options_key] = options
    return CVS0DParamID(**kwargs)


def _has_run_uq(param_id) -> bool:
    """Whether this CA can run UQ on an already-built engine (CA #392).

    Older CA only offers ``run_mcmc()`` on an object constructed with
    ``mcmc_instead=True``, so the caller must keep building a second one.
    """
    return callable(getattr(param_id, "run_UQ", None))


def uq_options_kwarg() -> str:
    """Whether this CA takes ``UQ_options=`` or the older ``mcmc_options=``.

    CA renamed the argument once MCMC became one method of uncertainty
    quantification rather than the whole of it. It still accepts the old name as a
    deprecated alias that warns on every construction, so detecting the supported
    spelling keeps a current CA quiet without breaking an older one.
    """
    try:
        import inspect

        from ca_imports import ca_from

        CVS0DParamID = ca_from("param_id.paramID", "CVS0DParamID")

        if "UQ_options" in inspect.signature(CVS0DParamID.__init__).parameters:
            return "UQ_options"
    except Exception:
        pass
    return "mcmc_options"


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

    from ca_imports import ca_from, ca_import  # noqa: E402 (shipped into runners/ too)

    pid = ca_import("param_id.paramID")
    ensure_mle_cost_type_for_bayesian_inner = ca_from(
        "param_id.paramID", "ensure_mle_cost_type_for_bayesian_inner")
    IdentifiabilityAnalysis = ca_from(
        "identifiabilty_analysis.identifiabilityAnalysis", "IdentifiabilityAnalysis")

    settings = config.get("settings", {})
    method = settings.get("method", "mcmc")
    output_dir = config["output_dir"]
    os.makedirs(output_dir, exist_ok=True)

    # Global random seed (Settings popup). When set, seed numpy's legacy global RNG
    # so the MCMC walker initialisation / Laplace sampling (which draw from np.random
    # directly) and any emcee sampling are repeatable, and forward it into CA's
    # option dicts. None => leave every random process non-deterministic.
    seed = config.get("seed")
    if seed is not None:
        np.random.seed(int(seed))
    optimiser_options = _optimiser_options(settings, seed)
    uq_options = _uq_options(settings, seed)
    uq_key = uq_options_kwarg()
    # Minimal inp_data_dict so ensure_mle_cost_type_for_bayesian_inner can pick the
    # MLE cost from our option dicts (required for ln L = -cost in MCMC / Laplace).
    # Keyed by the spelling this CA reads, so the cost_type is found either way.
    inp = {
        "DEBUG": bool(settings.get("DEBUG", False)),
        "optimiser_options": optimiser_options,
        uq_key: uq_options,
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
        # Reuse the calibration engine when one was just built: CA's run_UQ
        # promotes it in place (OpencorMCMC.from_param_id), so the model
        # compiled for the GA is the one UQ samples with. Before CA #392 the
        # only way in was mcmc_instead=True at construction, which forced a
        # second CVS0DParamID and a second compile (CUFLynx #216/#217).
        mcmc = ga if (run_calib and _has_run_uq(ga)) else _make_param_id(
            config, settings, obs_path, mcmc=True, options_key=uq_key,
            options=uq_options,
        )
        best = best if run_calib else _best_from_reuse(mcmc, reuse_best)
        mcmc.set_best_param_vals(best)
        ensure_mle_cost_type_for_bayesian_inner(pid.mcmc_object, inp)
        if _has_run_uq(mcmc):
            mcmc.run_UQ(uq_options)
        else:
            mcmc.run_mcmc()  # a CA predating run_UQ
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
            config["model_path"], config.get("model_type", "cellml"), config.get("file_prefix", "model"),
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
        # CA renamed the misspelt `mean_Lapalace` -> `mean_Laplace`; read the
        # corrected name but fall back to the old one so we work against any CA.
        laplace_mean = getattr(ia, "mean_Laplace", None)
        if laplace_mean is None:
            laplace_mean = ia.mean_Lapalace
        flat = np.random.multivariate_normal(
            laplace_mean, ia.covariance_matrix_Laplace, size=LAPLACE_SAMPLES
        )
        qnames = _flat_param_names(cvs)
    else:
        raise RuntimeError(f"unknown UQ method: {method!r}")

    # The samples *are* the result, so they are what is persisted -- numeric, in
    # CA's own .npy idiom, rather than a CUFLynx-shaped results.json (#210). The
    # manager summarises them into histograms from there, with the same bin count.
    import ca_run_history  # noqa: E402 (CA output formats live in one place)

    ca_run_history.write_uq_samples(output_dir, np.asarray(flat), qnames)
    print(META_MARKER + json.dumps({"method": method}), flush=True)
    return {"rank": 0, "method": method, "params": _distributions(np.asarray(flat), qnames)}


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
