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
import traceback
from pathlib import Path

import emulator_config

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
    # No MaximumNumberOfSteps default: myokit_helper never reads it (myokit's
    # Simulation has no max-step-count knob), CA's migrate_legacy_solver_info_keys
    # pops it for solve_ivp/casadi, and CA fills its own default for the backends
    # that do use it. Injecting it here only seeded an inert setting.
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
    from local_sensitivity import resolve_gradient_method  # noqa: E402

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

    print(
        f"Starting {settings.get('param_id_method', 'genetic_algorithm')} "
        f"calibration ({optimiser_options.get('num_calls_to_function', '?')} max evals)",
        flush=True,
    )

    emulator_kwargs = emulator_config.engine_kwargs(config)
    note = emulator_config.describe(config)
    if note:
        print(note, flush=True)

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
        # CUFLynx-authored operation/cost funcs loaded from external files (CA #303).
        operation_funcs_external_path=config.get("operation_funcs_external_path"),
        cost_funcs_external_path=config.get("cost_funcs_external_path"),
        **emulator_kwargs,
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
        # Save the calibrated CellML (best-fit values baked into the flat model)
        # so it can be reloaded and reproduce the calibrated simulation (#114).
        # The write gets *physical* values: a modifier slot's theta expands to
        # theta*baseline_i via CA's own expansion. If that expansion fails with
        # modifiers present, skip the write rather than bake theta in as if it
        # were a volume or a compliance.
        try:
            expanded = _expanded_best_fit(info, param_names, best_vals)
            calibrated_path = _write_calibrated_cellml(config, expanded, output_dir)
        except Exception as exc:  # noqa: BLE001 - best-effort, never fails the run
            print(
                f"warning: could not expand the modifier best fit for the "
                f"calibrated CellML: {exc}",
                flush=True,
            )
            calibrated_path = None
        # No results.json. Everything below is already on disk, written by CA
        # itself, and the manager reads it from there (#210) -- serialising a
        # second copy put a file in the user's outputs directory that is no part
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


def _expanded_best_fit(param_id_info: dict, param_names, best_vals) -> dict:
    """Best-fit values as *physical* model values, one per member qname.

    A modifier's slot (theta) expands to ``theta * baseline_i`` through CA's
    ``expand_modifier_param_vals`` -- the same arithmetic the calibration ran
    with, not a reimplementation. On a CA predating modifiers the vector passes
    through raw, which is exactly today's behavior (no modifiers can exist
    there). Unresolved baselines raise: writing theta into a CellML as if it
    were a physical value is the failure this function exists to prevent.
    """
    vals = [float(v) for v in best_vals]
    try:
        from parsers.PrimitiveParsers import expand_modifier_param_vals  # noqa: PLC0415
    except ImportError:
        expanded = vals
    else:
        expanded = expand_modifier_param_vals(param_id_info or {}, vals)
    out: dict[str, float] = {}
    for i, name_list in enumerate(param_names):
        slot = expanded[i]
        if isinstance(slot, (list, tuple)):
            for qname, val in zip(name_list, slot):
                out[qname] = float(val)
        else:
            for qname in name_list:
                out[qname] = float(slot)
    return out


def _write_calibrated_cellml(config: dict, params: dict, output_dir: str) -> str | None:
    """Bake the best-fit values into the uploaded flat CellML and save it beside
    the results (issue #114). Best-effort: never fails the calibration.

    Returns the written path, or None if there's no CellML to update or nothing
    resolved. The source is ``config["cellml_path"]`` (the original uploaded model,
    regardless of the generated_model_format used for the run)."""
    cellml_path = config.get("cellml_path")
    if not cellml_path or not str(cellml_path).endswith(".cellml") or not params:
        return None
    try:
        from calibrated_model import calibrated_cellml  # local import: pure-XML

        text = Path(cellml_path).read_text(encoding="utf-8")
        new_text, report = calibrated_cellml(text, params)
        if not report["updated"]:
            return None
        prefix = config.get("file_prefix") or Path(cellml_path).stem
        out_path = os.path.join(output_dir, f"{prefix}_calibrated.cellml")
        Path(out_path).write_text(new_text, encoding="utf-8")
        if report["unresolved"]:
            print(
                f"calibrated model: {len(report['updated'])} params written, "
                f"{len(report['unresolved'])} unresolved: {report['unresolved']}",
                flush=True,
            )
        else:
            print(f"Saved calibrated model: {out_path}", flush=True)
        return out_path
    except Exception as exc:  # noqa: BLE001 - saving the model must not fail the run
        print(f"WARNING: could not save calibrated model: {exc}", flush=True)
        return None


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
