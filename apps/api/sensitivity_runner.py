"""Standalone sensitivity-analysis runner — spawned as a subprocess by the API.

Reads a JSON config and leaves the resulting indices in circulatory_autogen's own
CSV formats, which the manager reads (#210). Two methods are supported:

* ``method: "sobol"`` — circulatory_autogen's ``SensitivityAnalysis`` global
  variance-based (Sobol) engine; also writes CSV/PNG artifacts to ``output_dir``.
* ``method: "local"`` — derivative-based local sensitivity about a nominal point
  (see :mod:`local_sensitivity`). Finite-difference (any backend) or, for
  ``casadi_python`` models, exact CasADi automatic differentiation.

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

import emulator_config

# Markers the API watches for in stdout.
DONE_MARKER = "__SENSITIVITY_DONE__"
FAIL_MARKER = "__SENSITIVITY_FAILED__"
#: Prefix for the one line of run metadata the manager needs and no file holds:
#: which method ran, and (for a local run) the point it was linearised about.
#: The *results* are read from circulatory_autogen's own files (#210).
META_MARKER = "__SENSITIVITY_META__ "

# CUFLynx-level / local-path settings that must NOT be forwarded into CA's
# sa_options (the rest are the CA sensitivity_analysis option values). The UI
# always attaches config_outputs_dir, and folds the calibration panel's GA
# settings (param_id_method / num_calls_to_function / max_patience /
# cost_convergence) into the SA settings when run_calibration_first is set —
# those feed _calibrate_for_nominal, which reads them from settings directly,
# and have no business inside sa_options.
_SA_RESERVED = {
    "method", "gradient_method", "rel_step", "nominal", "run_calibration_first",
    "dt", "DEBUG", "num_cores", "solver", "solver_info", "python_path",
    "sim_time", "pre_time", "cost_type", "generated_model_format",
    "config_outputs_dir", "param_id_method", "num_calls_to_function",
    "max_patience", "cost_convergence",
}


def _ensure_ca_on_path() -> None:
    src = os.environ.get("CIRCULATORY_AUTOGEN_SRC")
    if not src:
        repo_root = Path(__file__).resolve().parents[2]
        src = str(repo_root.parent / "circulatory_autogen" / "src")
    if src not in sys.path:
        sys.path.insert(0, src)


def _sa_options(settings: dict, output_dir: str, seed=None) -> dict:
    """Assemble CA's ``sa_options`` from the UI settings.

    sample_type / num_samples are only used by the Sobol engine, but the
    SensitivityAnalysis constructor needs them present to build its param table;
    harmless placeholders for the local (finite-difference) path. Any additional
    CA sensitivity option the UI collected from CA's ANALYSIS_OPTIONS schema is
    forwarded (forward-compatible: new keys flow through without a runner change);
    CUFLynx-level / local-path keys stay out. A global random ``seed`` is forwarded
    under ``sa_options['seed']``; ``None`` omits it.
    """
    sa_options = {
        "method": settings.get("method", "sobol"),
        "sample_type": settings.get("sample_type", "saltelli"),
        "num_samples": int(settings.get("num_samples") or 256),
        "output_dir": output_dir,
    }
    for k, v in settings.items():
        if k not in _SA_RESERVED and k not in sa_options and v is not None:
            sa_options[k] = v
    if seed is not None:
        sa_options["seed"] = int(seed)
    return sa_options


def _indices_to_dict(sa) -> dict:
    """Read the Sobol indices the engine just wrote back into a JSON-friendly dict.

    ``load_sobol_indices`` returns ``{'S1': {out_name: {param: val}}, 'ST': {...}}``.
    Derive the param/output name lists from it so the frontend heatmap can render a
    stable params x outputs grid.

    CA emits Sobol keys as ``name (ExpX, SubY)`` (plus optional ``[op]``/``#k``).
    Reformat them into the shared ``var^{e,s} [op]`` label so global (Sobol) and
    local runs display identically. Both the ``indices`` dict keys *and* the
    ``output_names`` list are remapped together, keeping each label aligned with
    its indices column (the frontend looks up indices by the output-name string).
    """
    from local_sensitivity import format_sobol_output_name  # noqa: E402

    raw = sa.SA_manager.load_sobol_indices()
    indices: dict = {}
    for kind, by_out in raw.items():
        indices[kind] = {
            format_sobol_output_name(out_name): params
            for out_name, params in (by_out or {}).items()
        }

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


def _solver_info_from_config(config: dict, settings: dict) -> dict:
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


def _build_local_engine(config: dict, settings: dict, solver_info: dict, model_type: str):
    """A ``do_ad`` ``CVS0DParamID`` for the analytic (FSA) local-sensitivity path.

    Not ``run()`` — only its backend-agnostic ``get_observable_sensitivities``
    accessor is used (Myokit CVODES forward sensitivities for cellml +
    CVODE_myokit). Mirrors :func:`_calibrate_for_nominal`'s construction; ``do_ad``
    is forced on so CA takes the analytic-sensitivity path.
    """
    from param_id.paramID import CVS0DParamID  # noqa: E402

    return CVS0DParamID(
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
        do_ad=True,
        DEBUG=bool(settings.get("DEBUG", False)),
        param_id_output_dir=config["output_dir"],
        resources_dir=os.path.dirname(config["params_path"]),
        operation_funcs_external_path=config.get("operation_funcs_external_path"),
        cost_funcs_external_path=config.get("cost_funcs_external_path"),
        # The local arm must sit on the same forward model as the global one, or
        # "Sobol" and "local" in one study would measure different things.
        **emulator_config.engine_kwargs(config),
    )


def _calibrate_for_nominal(config: dict, settings: dict, solver_info: dict, model_type: str):
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
        DEBUG=bool(settings.get("DEBUG", False)),
        param_id_output_dir=config["output_dir"],
        resources_dir=os.path.dirname(config["params_path"]),
        operation_funcs_external_path=config.get("operation_funcs_external_path"),
        cost_funcs_external_path=config.get("cost_funcs_external_path"),
        **emulator_config.engine_kwargs(config),
    )
    param_id.run()
    return np.asarray(param_id.get_best_param_vals(), dtype=float)


def _with_gradient_hint(exc: ValueError) -> ValueError:
    """CA's non-differentiable-operation error, plus what to do about it.

    CA names the offending operation and says it is not ``@differentiable``; it
    cannot say "use FD", because the gradient method is a CUFLynx setting. Any
    other ValueError passes through untouched.
    """
    if "@differentiable" not in str(exc):
        return exc
    return ValueError(
        f"{exc} Switch the gradient method to 'FD' (finite difference), or mark "
        f"the operation @differentiable in circulatory_autogen."
    )


def run(config: dict) -> dict:
    try:
        return _run(config)
    except ValueError as exc:
        raise _with_gradient_hint(exc) from exc


def _run(config: dict) -> dict:
    _ensure_ca_on_path()
    from sensitivity_analysis.sensitivityAnalysis import SensitivityAnalysis  # noqa: E402

    settings = config.get("settings", {})
    method = settings.get("method", "sobol")
    output_dir = config["output_dir"]
    os.makedirs(output_dir, exist_ok=True)

    model_type = config.get("model_type", "cellml")
    solver_info = _solver_info_from_config(config, settings)
    # Global random seed (Settings popup). When set, seed numpy's legacy global RNG
    # so Sobol/SALib sampling is repeatable, and forward it into sa_options. None =>
    # leave the sampling non-deterministic.
    seed = config.get("seed")
    if seed is not None:
        import numpy as np  # noqa: E402

        np.random.seed(int(seed))
    sa_options = _sa_options(settings, output_dir, seed)

    # A ValueError from here naming @differentiable is CA refusing a casadi_python
    # model whose obs operations are not all differentiable -- it builds its
    # casadi-mode operation table during construction, before any gradient method
    # is chosen. `_with_gradient_hint` adds the one thing CA cannot know: that
    # switching to FD is the way out, since the gradient method is a CUFLynx
    # setting. Enriched, never restated -- the registry is CA's.
    emulator_kwargs = emulator_config.engine_kwargs(config)
    note = emulator_config.describe(config)
    if note:
        print(note, flush=True)

    sa = SensitivityAnalysis(
        model_path=config["model_path"],
        model_type=model_type,
        file_name_prefix=config.get("file_prefix", "model"),
        sa_options=sa_options,
        DEBUG=bool(settings.get("DEBUG", False)),
        param_id_output_dir=output_dir,
        resources_dir=os.path.dirname(config["params_path"]),
        solver_info=solver_info,
        dt=float(settings.get("dt", 0.01)),
        param_id_obs_path=config["obs_path"],
        params_for_id_path=config["params_path"],
        operation_funcs_external_path=config.get("operation_funcs_external_path"),
        cost_funcs_external_path=config.get("cost_funcs_external_path"),
        **emulator_kwargs,
    )

    # Local (derivative-based) SA runs single-process; no Sobol sampling / MPI.
    if method == "local":
        import ca_run_history  # noqa: E402 (CA output formats, one place)
        from local_sensitivity import (  # noqa: E402
            compute_local_sensitivity,
            resolve_gradient_method,
        )

        # run_calibration_first: locate a fresh best-fit point here, then take
        # the local sensitivity about it. Otherwise the nominal point comes from
        # the current parameter values / a reused best fit / the bounds.
        best_vals = None
        if bool(settings.get("run_calibration_first", False)):
            best_vals = _calibrate_for_nominal(config, settings, solver_info, model_type)
        # Every gradient source -- FD, AD and FSA alike -- is computed by
        # circulatory_autogen's backend-agnostic accessor, which needs a do_ad
        # param-id engine, so it is always built. FD and AD used to run off the SA
        # manager through CUFLynx's own loop and jacobian; those were
        # reimplementations of CA's fd_backend and casadi_backend, and keeping
        # them in step with CA's flatten/fold contract is what CA #390 broke.
        # The engine costs one model parse; for AD the CasADi graph dominates and
        # was being built anyway, and for FD it is small beside 2M simulations.
        engine = _build_local_engine(config, settings, solver_info, model_type)
        payload = compute_local_sensitivity(
            sa,
            settings,
            best_vals=best_vals,
            best_params=config.get("best_params"),
            model_type=model_type,
            engine=engine,
            current_params=config.get("current_params"),
        )
        # Results in circulatory_autogen's own local-sensitivity CSV format, so
        # the outputs directory holds one format whichever arm produced them and
        # the manager reads CA's file rather than a CUFLynx summary (#210).
        ca_run_history.write_local_sensitivity(
            output_dir, "relative", payload["indices"]["local"], payload["output_names"]
        )
        # The linearisation point and how it was chosen are CUFLynx's own run
        # metadata, not a result: a few hundred bytes over the pipe the manager
        # already reads, rather than another file beside the outputs. Kept small
        # deliberately -- under mpiexec every rank shares this pipe, and a line
        # over PIPE_BUF could interleave.
        print(
            META_MARKER
            + json.dumps({
                "method": "local",
                "gradient_method": payload["gradient_method"],
                "nominal": payload["nominal"],
                "nominal_source": payload["nominal_source"],
            }),
            flush=True,
        )
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
        # Nothing written here: CA already wrote all_outputs_n<N>_Sobol_indices.csv
        # into this directory, and the manager reads that (#210).
        print(META_MARKER + json.dumps({"method": sa_options["method"]}), flush=True)
        _collect_plots(output_dir)
    return result


SA_PLOTS_DIRNAME = "SA_plots"


def _collect_plots(output_dir: str) -> None:
    """Move the figures circulatory_autogen drew into ``SA_plots/``.

    CA writes its Sobol figures straight into the run directory, beside the
    indices, the samples and the npy files -- so a directory of results
    gradually becomes a directory of results and pictures of results, and the
    plots are the part a user goes looking for.

    Moved after the fact rather than by pointing CA at another directory,
    because ``sa_options['output_dir']`` is where CA puts *everything*, plots
    and data alike. Never fatal: the analysis is done and its numbers are
    written, and failing the run over where a PNG sits would be absurd.
    """
    try:
        pngs = [p for p in Path(output_dir).glob("*.png") if p.is_file()]
        if not pngs:
            return
        target = Path(output_dir) / SA_PLOTS_DIRNAME
        target.mkdir(parents=True, exist_ok=True)
        for png in pngs:
            png.replace(target / png.name)
        print(f"Plots moved to {target}", flush=True)
    except OSError as exc:
        print(f"warning: could not collect the SA plots: {exc}", flush=True)


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
