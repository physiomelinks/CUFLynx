"""Export the current CUFLynx pipeline as a reproducible, standalone bundle.

Produces, into a self-contained folder under the user's outputs dir:
  - ``user_inputs_<yymmdd>.yaml`` — the run config (circulatory_autogen schema +
    CUFLynx ``do_*`` enablement flags), capturing the current settings;
  - ``run_pipeline.py`` — drives circulatory_autogen (CA) from that yaml, running
    simulation / sensitivity / calibration / UQ, each gated by a ``do_*`` flag;
  - ``plot_outputs.py`` — regenerates the equivalent output / progress / analysis
    plots from the saved output data;
  - copies of the model ``.cellml``, ``obs_data.json`` and ``params_for_id.csv``
    (referenced by relative paths), so the bundle reproduces the run on its own.

The scripts are static (everything specific lives in the yaml / bundled files),
which keeps them easy to read and avoids brittle string templating.
"""

from __future__ import annotations

from datetime import date


def dated_suffix() -> str:
    return date.today().strftime("%y%m%d")


class ExportPipelineError(ValueError):
    """Bad export settings supplied by the client (maps to HTTP 422)."""


def _num(value, default, cast, field: str):
    """Coerce a client-supplied setting, treating "not set" as absent.

    The analysis panels populate their fields from CA's *discovered* option
    schema, and CA declares ``default: null`` for some of them —
    ``num_calls_to_function`` (genetic_algorithm) and ``num_walkers`` (mcmc).
    The panel copies that null into its value, and JSON preserves null (unlike
    an undefined, which would be dropped), so the export payload arrives with
    the key **present and null**. A bare ``int(None)`` then raised TypeError and
    the route returned an unhandled 500 with no detail (issue #133). Clearing a
    numeric field in the UI produces the same shape, since PrimeVue's InputNumber
    models an empty field as null.

    A null/blank value therefore means "unset" and falls back to ``default`` —
    the same result as omitting the key. A genuinely malformed value is reported
    as :class:`ExportPipelineError` so the client gets a usable message rather
    than an opaque 500.
    """
    if value is None or (isinstance(value, str) and not value.strip()):
        return cast(default)
    try:
        return cast(value)
    except (TypeError, ValueError) as exc:
        raise ExportPipelineError(
            f"{field}: expected a number, got {value!r}"
        ) from exc


def build_user_inputs(
    *,
    file_prefix: str,
    model_type: str,
    solver: str,
    solver_info: dict,
    dt: float,
    pre_time: float,
    sim_time: float,
    model_file: str,
    obs_file: str | None,
    params_for_id_file: str | None,
    calibration: dict | None,
    sensitivity: dict | None,
    uq: dict | None,
    enabled: dict | None,
) -> dict:
    """Map the current CUFLynx settings to a circulatory_autogen user_inputs dict.

    Resource paths are **relative** (the model/obs/params live alongside the
    script in the export folder under ``resources/``). The ``do_*`` keys are
    CUFLynx-level enablement flags — CA ignores unknown keys, and the exported
    pipeline script reads them to gate each stage.
    """
    calibration = calibration or {}
    sensitivity = sensitivity or {}
    uq = uq or {}
    enabled = enabled or {}

    optimiser_options = {
        "num_calls_to_function": _num(
            calibration.get("num_calls_to_function"), 100, int, "num_calls_to_function"
        ),
        "cost_convergence": _num(
            calibration.get("cost_convergence"), 0.0001, float, "cost_convergence"
        ),
        "max_patience": _num(calibration.get("max_patience"), 10, int, "max_patience"),
    }
    if calibration.get("cost_type"):
        optimiser_options["cost_type"] = calibration["cost_type"]

    ui = {
        # --- general / model ---
        "file_prefix": file_prefix,
        "model_type": model_type,
        "model_file": model_file,  # CUFLynx extra: the (flat) CellML to run/generate from
        "input_param_file": f"{file_prefix}_parameters.csv",
        "resources_dir": "resources",
        # --- solver / sim ---
        "solver": solver,
        "solver_info": {**(solver_info or {}), "solver": solver},
        "dt": dt,
        "pre_time": pre_time,
        "sim_time": sim_time,
        # --- inputs ---
        "params_for_id_file": params_for_id_file,
        "param_id_obs_path": f"resources/{obs_file}" if obs_file else None,
        # --- parameter identification (calibration) ---
        "param_id_method": calibration.get("param_id_method") or "genetic_algorithm",
        "do_ad": str(calibration.get("gradient_method", "FD")).upper() == "AD",
        "optimiser_options": optimiser_options,
        # --- sensitivity ---
        "sa_options": {
            "method": sensitivity.get("method") or "sobol",
            "sample_type": sensitivity.get("sample_type") or "saltelli",
            "num_samples": _num(sensitivity.get("num_samples"), 256, int, "num_samples"),
        },
        # --- UQ / mcmc ---
        "mcmc_options": {
            "num_steps": _num(uq.get("num_steps"), 1000, int, "num_steps"),
            "num_walkers": _num(uq.get("num_walkers"), 64, int, "num_walkers"),
            "cost_type": uq.get("cost_type") or "gaussian_MLE",
        },
        # --- CUFLynx enablement flags (gate the pipeline-script stages) ---
        "do_simulation": bool(enabled.get("do_simulation", True)),
        "do_calibration": bool(enabled.get("do_calibration", False)),
        "do_sensitivity": bool(enabled.get("do_sensitivity", False)),
        "do_mcmc": bool(enabled.get("do_mcmc", False)),
        "do_ia": bool(enabled.get("do_ia", False)),
    }
    return ui


PIPELINE_SCRIPT = '''#!/usr/bin/env python3
"""Reproducible CUFLynx pipeline (exported).

This follows the circulatory_autogen "generation and calibration" tutorial:
build ONE config dict (``inp_data_dict``) from the exported user_inputs_*.yaml,
then drive each stage with the class ``init_from_dict(...)`` constructors. Each
stage runs only if its ``do_*`` flag is set in the yaml — flip them there.

This export folder is self-contained:
    user_inputs_<date>.yaml      the run configuration (edit the do_* flags here)
    generated_models/<prefix>/   the CellML model
    resources/                   obs_data.json + params_for_id.csv
    output/                      results are written here

Usage:
    python run_pipeline.py --ca-src /path/to/circulatory_autogen/src
    # or set CIRCULATORY_AUTOGEN_SRC in the environment
"""
import argparse
import glob
import json
import os
import sys

import numpy as np
import yaml

HERE = os.path.dirname(os.path.abspath(__file__))


def load_config():
    matches = sorted(glob.glob(os.path.join(HERE, "user_inputs_*.yaml")))
    if not matches:
        sys.exit("No user_inputs_*.yaml found next to this script.")
    with open(matches[-1]) as fh:
        return yaml.safe_load(fh)


def resolve_ca_src():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ca-src", default=os.environ.get("CIRCULATORY_AUTOGEN_SRC"))
    args, _ = ap.parse_known_args()
    if not args.ca_src or not os.path.isdir(args.ca_src):
        sys.exit("Pass --ca-src <circulatory_autogen/src> or set CIRCULATORY_AUTOGEN_SRC.")
    return args.ca_src


def build_inp_data_dict(cfg, output_dir):
    """Turn the exported yaml into a circulatory_autogen ``inp_data_dict`` with
    every path resolved to an absolute location inside this export folder. This is
    the dict the ``init_from_dict`` constructors consume (see the CA tutorial)."""
    resources = os.path.join(HERE, cfg.get("resources_dir", "resources"))
    generated_models_dir = os.path.join(HERE, "generated_models")
    solver_info = dict(cfg.get("solver_info", {}))
    solver_info.setdefault("solver", cfg.get("solver"))

    inp = {
        "file_prefix": cfg["file_prefix"],
        "input_param_file": cfg.get("input_param_file", cfg["file_prefix"] + "_parameters.csv"),
        "model_type": cfg.get("model_type", "cellml_only"),
        # The CellML lives at generated_models/<prefix>/<prefix>.cellml — the layout
        # circulatory_autogen resolves model_path to, so every stage agrees.
        "model_path": os.path.join(generated_models_dir, cfg["file_prefix"], cfg["model_file"]),
        "generated_models_dir": generated_models_dir,
        "resources_dir": resources,
        "param_id_output_dir": output_dir,
        "solver_info": solver_info,
        "dt": float(cfg.get("dt", 0.01)),
        "sim_time": float(cfg.get("sim_time", 2.0)),
        "pre_time": float(cfg.get("pre_time", 0.0)),
        "param_id_method": cfg.get("param_id_method", "genetic_algorithm"),
        "do_ad": bool(cfg.get("do_ad", False)),
        "optimiser_options": dict(cfg.get("optimiser_options", {})),
        "mcmc_options": dict(cfg.get("mcmc_options", {})),
        "sa_options": {**cfg.get("sa_options", {}), "output_dir": output_dir},
        "DEBUG": False,
    }
    if cfg.get("param_id_obs_path"):
        inp["param_id_obs_path"] = os.path.join(HERE, cfg["param_id_obs_path"])
        # Run the simulation over the same protocol window as calibration/SA and the
        # live app: when obs_data carries a protocol_info, its pre/sim times take
        # precedence over the yaml. The SA/calibration init_from_dict constructors
        # already do this internally; get_simulation_helper_from_inp_data_dict reads
        # only inp["pre_time"]/["sim_time"], so without this the simulation would run
        # an unwarmed, wrong-length window and its outputs wouldn't match the obs_data.
        try:
            proto = json.loads(open(inp["param_id_obs_path"]).read()).get("protocol_info") or {}
            pre = (proto.get("pre_times") or [None])[0]
            sim = (proto.get("sim_times") or [[None]])[0][0]
            if pre is not None:
                inp["pre_time"] = float(pre)
            if sim is not None:
                inp["sim_time"] = float(sim)
        except (OSError, ValueError, KeyError, IndexError, TypeError):
            pass
    if cfg.get("params_for_id_file"):
        inp["params_for_id_path"] = os.path.join(resources, cfg["params_for_id_file"])
    return inp


def mle_obs_data(obs_path, out_dir, cost_type="gaussian_MLE"):
    """MCMC / Laplace need ln L = -cost, so write a copy of the obs_data with every
    data_item's cost_type set to an MLE cost (mirrors uq_runner._mle_obs_path)."""
    obs = json.loads(open(obs_path).read())
    for item in obs.get("data_items", []):
        item["cost_type"] = cost_type
    out = os.path.join(out_dir, "uq_obs_data.json")
    open(out, "w").write(json.dumps(obs))
    return out


def flat_param_names(param_id):
    return [g[0] if isinstance(g, (list, tuple)) else g for g in param_id.get_param_names()]


def write_uq(out_dir, method, flat, qnames):
    """Per-parameter posterior summary + histogram from samples (N, P)."""
    import numpy as np

    flat = np.asarray(flat)
    params = []
    for i, qname in enumerate(qnames):
        col = np.asarray(flat[:, i], dtype=float)
        col = col[np.isfinite(col)]
        if col.size == 0:
            continue
        counts, edges = np.histogram(col, bins=30)
        q05, q50, q95 = (float(x) for x in np.percentile(col, [5, 50, 95]))
        params.append({
            "qname": qname, "mean": float(np.mean(col)), "std": float(np.std(col)),
            "q05": q05, "q50": q50, "q95": q95,
            "bins": [float(x) for x in edges], "counts": [int(x) for x in counts],
        })
    with open(os.path.join(out_dir, "results.json"), "w") as fh:
        json.dump({"method": method, "params": params}, fh)


def main():
    sys.path.insert(0, resolve_ca_src())
    cfg = load_config()

    output_dir = os.path.join(HERE, "output")
    os.makedirs(output_dir, exist_ok=True)
    inp = build_inp_data_dict(cfg, output_dir)

    # python / casadi_python backends run a generated .py model: build it from the
    # bundled CellML, alongside where circulatory_autogen expects the model.
    if inp["model_type"] in ("python", "casadi_python"):
        from generators.PythonGenerator import PythonGenerator

        cellml_path = os.path.join(HERE, "generated_models", cfg["file_prefix"], cfg["model_file"])
        inp["model_path"] = PythonGenerator(
            cellml_path,
            output_dir=os.path.dirname(cellml_path),
            module_name=cfg["file_prefix"],
            casadi_compat=(inp["model_type"] == "casadi_python"),
        ).generate()

    # ---- 1) Simulation -----------------------------------------------------
    if cfg.get("do_simulation"):
        print("=== simulation ===", flush=True)
        from solver_wrappers import get_simulation_helper_from_inp_data_dict

        sim_helper = get_simulation_helper_from_inp_data_dict(inp)
        sim_helper.run()
        names = sim_helper.get_all_variable_names()
        results = sim_helper.get_results(names, flatten=True)
        # Myokit/OpenCOR/python helpers expose get_time; the CasADi helper doesn't,
        # but resolves the logged sim-time vector as the 'time' variable.
        if hasattr(sim_helper, "get_time"):
            time = [float(t) for t in sim_helper.get_time()]
        else:
            time = [float(t) for t in sim_helper.get_results(["time"], flatten=True)[0]]
        outputs = {name: [float(v) for v in series] for name, series in zip(names, results)}
        with open(os.path.join(output_dir, "simulation.json"), "w") as fh:
            json.dump({"time": time, "outputs": outputs}, fh)

    # ---- 2) Sensitivity analysis ------------------------------------------
    if cfg.get("do_sensitivity"):
        print("=== sensitivity analysis ===", flush=True)
        from sensitivity_analysis.sensitivityAnalysis import SensitivityAnalysis

        sa_agent = SensitivityAnalysis.init_from_dict(inp)
        sa_agent.run_sensitivity_analysis(inp["sa_options"])

    # ---- 3) Calibration ----------------------------------------------------
    best_param_vals = None  # reused by UQ below when available
    if cfg.get("do_calibration"):
        print("=== calibration ===", flush=True)
        from param_id.paramID import CVS0DParamID

        param_id = CVS0DParamID.init_from_dict(inp)
        param_id.run()
        param_id.plot_outputs()
        best_param_vals = param_id.get_best_param_vals()

    # ---- 4) Uncertainty quantification ------------------------------------
    if cfg.get("do_mcmc") or cfg.get("do_ia"):
        method = "mcmc" if cfg.get("do_mcmc") else "laplace"
        print(f"=== uncertainty quantification ({method}) ===", flush=True)
        import param_id.paramID as paramID_module
        from param_id.paramID import CVS0DParamID, ensure_mle_cost_type_for_bayesian_inner

        # MCMC / Laplace need ln L = -cost, so use an MLE obs copy + MLE cost_type.
        cost_type = inp["mcmc_options"].get("cost_type", "gaussian_MLE")
        uq_inp = dict(inp)
        uq_inp["param_id_obs_path"] = mle_obs_data(inp["param_id_obs_path"], output_dir, cost_type)
        uq_inp["optimiser_options"] = {**inp["optimiser_options"], "cost_type": cost_type}
        uq_inp["mcmc_options"] = {**inp["mcmc_options"], "cost_type": cost_type}

        # UQ needs a best fit: reuse the calibration above, else run one now.
        if best_param_vals is None:
            print("  running a calibration first to get the best fit for UQ", flush=True)
            calib = CVS0DParamID.init_from_dict(uq_inp)
            calib.run()
            best_param_vals = calib.get_best_param_vals()
        best_param_vals = np.asarray(best_param_vals, dtype=float)

        if method == "mcmc":
            mcmc = CVS0DParamID.init_from_dict({**uq_inp, "mcmc_instead": True})
            mcmc.set_best_param_vals(best_param_vals)
            ensure_mle_cost_type_for_bayesian_inner(paramID_module.mcmc_object, uq_inp)
            mcmc.run_mcmc()
            if getattr(mcmc, "rank", 0) == 0:
                write_uq(output_dir, method, mcmc.get_mcmc_samples()[0], flat_param_names(mcmc))
        else:
            from identifiabilty_analysis.identifiabilityAnalysis import IdentifiabilityAnalysis

            cvs = CVS0DParamID.init_from_dict(uq_inp)
            ia = IdentifiabilityAnalysis.init_from_dict(uq_inp, cvs.param_id)
            ia.set_best_param_vals(best_param_vals)
            ensure_mle_cost_type_for_bayesian_inner(cvs.param_id, uq_inp)
            ia.run({"method": "Laplace"})
            if getattr(ia, "rank", 0) == 0:
                # CA renamed `mean_Lapalace` -> `mean_Laplace`; prefer the corrected
                # name, fall back to the old spelling for older CA versions.
                laplace_mean = getattr(ia, "mean_Laplace", None)
                if laplace_mean is None:
                    laplace_mean = ia.mean_Lapalace
                samples = np.random.multivariate_normal(
                    laplace_mean, ia.covariance_matrix_Laplace, size=100000
                )
                write_uq(output_dir, method, samples, flat_param_names(cvs))

    print(f"Done. Outputs in {output_dir}", flush=True)


if __name__ == "__main__":
    main()
'''


PLOTTING_SCRIPT = '''#!/usr/bin/env python3
"""Regenerate CUFLynx-equivalent plots from the exported pipeline's output data.

Reproduces:
  - output plots   : simulation traces (output/simulation.json)
  - best-fit plots : calibrated traces with the observations drawn on them
                     (all_outputs_with_best_param_vals_exp_*.npz + obs_data.json)
  - error bars     : how far each observable ended up from its target
                     (percent_error_vec.npy, std_error_vec.npy)
  - progress plots : cost vs generation (log y) + parameters vs generation
                     (output/best_cost_history.csv, best_param_vals_history.csv)
  - analysis plots : sensitivity heatmap and/or UQ posteriors (output/results.json)

Writes PNGs into a `pyscript_plots/` folder beside the data.

Usage:
    python plot_outputs.py                        # output/ beside this script,
                                                  # or this script's own folder
    python plot_outputs.py --output-dir <dir>     # a specific run directory
    CUFLYNX_OUTPUT_DIR=<dir> python plot_outputs.py
"""
import csv
import glob
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def _default_out():
    """Where to look for run data, when nobody says.

    Two layouts reach this script. run_pipeline.py writes into `output/` beside
    it, so that is preferred. But CUFLynx also drops this script straight into
    the outputs directory the user chose in the app, where the run data is in
    circulatory_autogen's own `<method>_<model>_<hash>_obs_data/` folders and
    there is no `output/` at all -- which used to fail with "run run_pipeline.py
    first" after a perfectly good calibration.
    """
    from_env = os.environ.get("CUFLYNX_OUTPUT_DIR")
    if from_env:
        return from_env
    beside = os.path.join(HERE, "output")
    return beside if os.path.isdir(beside) else HERE


OUT = _default_out()
# Plots are written here rather than among the data, so a directory holding one
# run's results does not gradually become a directory holding results and
# pictures of results.
PLOTS_DIRNAME = "pyscript_plots"
PLOTS = os.path.join(OUT, PLOTS_DIRNAME)

# --- Style, in one place ---------------------------------------------------
FIG_W, FIG_H = 5.0, 3.4   # inches per panel
DPI = 150
TARGET_COLOUR = "#333333"
# Dash patterns for the observed-value lines, one per target on a panel.
TARGET_DASHES = [(4, 2), (1, 1.6), (6, 2, 1, 2), (3, 1, 1, 1), (8, 3)]

# matplotlib and numpy are imported in main(), after the checks there -- not at
# module level. Running this script before run_pipeline.py, or from the wrong
# directory, is a far more common mistake than a missing matplotlib, and an
# import error at line 17 buries the message that would have said so.
plt = None
np = None


def _load_plotting_libs():
    global plt, np
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as _plt
        import numpy as _np
    except ImportError as exc:
        raise SystemExit(
            "This script needs matplotlib and numpy, and could not import them "
            f"({exc}). Install them into the Python you are running this with: "
            "python -m pip install matplotlib numpy"
        )
    plt, np = _plt, _np
PALETTE = ["#5b9bd5", "#ed7d31", "#70ad47", "#ffc000", "#a142f4", "#e84a5f"]


def _color(i):
    return PALETTE[i % len(PALETTE)]


# One variable per panel, and at most this many panels per PNG. The pipeline
# logs *every* model variable, so a single axes would be unreadable and a single
# figure of 456 panels unusable.
PANELS_PER_PAGE = 12
PANEL_COLS = 3


def _plot_panels(t, outputs, stem, name_suffix=""):
    """Draw `outputs` as a grid of one-variable panels, paginated.

    A panel each, not one shared axes: model variables span wildly different
    scales -- pressures ~1e4, flows ~1e-4, valve states 0/1 -- so on a common
    linear axis all but the largest collapse onto zero. This mirrors how CUFLynx
    plots them (one cell per variable), which is the point of the export.
    """
    names = list(outputs)
    if not names:
        return []
    written = []
    pages = (len(names) + PANELS_PER_PAGE - 1) // PANELS_PER_PAGE
    for page in range(pages):
        chunk = names[page * PANELS_PER_PAGE : (page + 1) * PANELS_PER_PAGE]
        rows = (len(chunk) + PANEL_COLS - 1) // PANEL_COLS
        fig, axes = plt.subplots(
            rows, PANEL_COLS, figsize=(4.2 * PANEL_COLS, 2.4 * rows), squeeze=False
        )
        for i, name in enumerate(chunk):
            ax = axes[i // PANEL_COLS][i % PANEL_COLS]
            series = outputs[name]
            n = min(len(t), len(series))
            ax.plot(t[:n], series[:n], color=_color(page * PANELS_PER_PAGE + i), lw=1.2)
            # The variable names the panel; no legend to cover the trace.
            ax.set_title(name, fontsize=7)
            ax.set_xlabel("time", fontsize=7)
            ax.tick_params(labelsize=6)
        # Blank any unused cells so a part-full page has no empty axes frames.
        for j in range(len(chunk), rows * PANEL_COLS):
            axes[j // PANEL_COLS][j % PANEL_COLS].axis("off")
        suffix = f"{name_suffix}" + (f"_p{page + 1}" if pages > 1 else "")
        out_path = os.path.join(PLOTS, f"{stem}{suffix}.png")
        fig.tight_layout()
        fig.savefig(out_path, dpi=150)
        plt.close(fig)
        written.append(out_path)
    return written


def plot_outputs():
    path = os.path.join(OUT, "simulation.json")
    if not os.path.exists(path):
        return
    data = json.load(open(path))
    # A protocol run records one entry per experiment rather than a single
    # time/outputs pair; plotting them together would put experiment 1's trace on
    # experiment 0's axes.
    experiments = data.get("experiments")
    if isinstance(experiments, list) and experiments:
        for e, exp in enumerate(experiments):
            _plot_panels(exp.get("time", []), exp.get("outputs", {}), "output_plot", f"_exp{e}")
        return
    _plot_panels(data.get("time", []), data.get("outputs", {}), "output_plot")


def _read_cost_history():
    path = _find("best_cost_history.csv")
    if not path:
        return None
    rows = []
    for line in open(path):
        line = line.strip()
        if not line:
            continue
        try:
            rows.append([float(x) for x in line.split(",")])
        except ValueError:
            continue
    return rows


def _read_param_history():
    path = _find("best_param_vals_history.csv")
    if not path:
        return None, []
    lines = [ln.strip() for ln in open(path) if ln.strip()]
    if not lines:
        return None, []
    # A header is optional: CA writes one, but a bare numeric file must not have
    # its first row of data eaten as column names.
    first = [c.strip() for c in lines[0].split(",")]
    try:
        [float(c) for c in first]
        has_header = False
    except ValueError:
        has_header = True
    names = first if has_header else [f"p{i}" for i in range(len(first))]
    rows = []
    for line in lines[0 if not has_header else 1 :]:
        try:
            row = [float(x) for x in line.split(",")]
        except ValueError:
            continue
        if len(row) == len(names):
            rows.append(row)
    return rows, names


def _find(name):
    matches = glob.glob(os.path.join(OUT, "**", name), recursive=True)
    return matches[0] if matches else None


def plot_progress():
    costs = _read_cost_history()
    if costs:
        best = [row[0] for row in costs]
        fig, ax = plt.subplots(figsize=(6, 4))
        ax.plot(range(len(best)), best, color=PALETTE[0], marker="o", ms=3)
        # Log only when it is defined: a zero or negative cost (a perfect fit, or
        # a cost function that can go negative) would silently drop points.
        if all(v > 0 for v in best):
            ax.set_yscale("log")
        ax.set_xlabel("generation")
        ax.set_ylabel("cost")
        ax.set_title("Cost vs generation")
        fig.tight_layout()
        fig.savefig(os.path.join(PLOTS, "progress_cost.png"), dpi=150)
        plt.close(fig)

    params, names = _read_param_history()
    if params:
        arr = np.array(params)
        fig, ax = plt.subplots(figsize=(6, 4))
        for j, name in enumerate(names):
            ax.plot(range(arr.shape[0]), arr[:, j], color=_color(j), label=name)
        ax.set_xlabel("generation")
        ax.set_ylabel("normalised value")
        ax.set_title("Parameters vs generation")
        ax.legend(fontsize=6)
        fig.tight_layout()
        fig.savefig(os.path.join(PLOTS, "progress_params.png"), dpi=150)
        plt.close(fig)


def plot_analysis():
    path = _find("results.json")
    if not path:
        return
    res = json.load(open(path))
    # Sensitivity heatmap (indices: {kind: {output: {param: value}}}).
    indices = res.get("indices")
    if indices:
        kind = "local" if "local" in indices else ("ST" if "ST" in indices else next(iter(indices)))
        by_out = indices[kind]
        outs = res.get("output_names") or list(by_out.keys())
        params = res.get("param_names") or sorted({p for o in by_out.values() for p in o})
        mat = np.array([[by_out.get(o, {}).get(p, np.nan) for o in outs] for p in params], dtype=float)
        signed = kind == "local"
        vmax = np.nanmax(np.abs(mat)) or 1.0
        fig, ax = plt.subplots(figsize=(1.2 + 0.5 * len(outs), 1 + 0.4 * len(params)))
        im = ax.imshow(mat, aspect="auto", cmap="coolwarm" if signed else "viridis",
                       vmin=-vmax if signed else 0, vmax=vmax)
        ax.set_xticks(range(len(outs))); ax.set_xticklabels(outs, rotation=90, fontsize=6)
        ax.set_yticks(range(len(params))); ax.set_yticklabels(params, fontsize=6)
        ax.set_title(f"Sensitivity ({kind})")
        fig.colorbar(im, ax=ax)
        fig.tight_layout()
        fig.savefig(os.path.join(PLOTS, "analysis_sensitivity.png"), dpi=150)
        plt.close(fig)
    # UQ posteriors (params: [{qname, mean, std, q05, q95, bins, counts}]).
    uq_params = res.get("params") if isinstance(res.get("params"), list) else None
    if uq_params and all("counts" in p for p in uq_params):
        n = len(uq_params)
        fig, axes = plt.subplots(n, 1, figsize=(5, 2 * n), squeeze=False)
        for i, p in enumerate(uq_params):
            ax = axes[i][0]
            edges = np.array(p["bins"]); counts = np.array(p["counts"])
            centers = 0.5 * (edges[:-1] + edges[1:]) if len(edges) == len(counts) + 1 else np.arange(len(counts))
            ax.bar(centers, counts, width=(centers[1] - centers[0]) if len(centers) > 1 else 1,
                   color=PALETTE[0], alpha=0.6)
            ax.axvline(p["mean"], color=PALETTE[5])
            ax.set_title(p.get("qname", f"param {i}"), fontsize=7)
        fig.tight_layout()
        fig.savefig(os.path.join(PLOTS, "analysis_uq.png"), dpi=150)
        plt.close(fig)


# What this script can draw, and therefore what it looks for before claiming
# there is nothing to do.
INPUTS = (
    "simulation.json",
    "best_cost_history.csv",
    "best_param_vals_history.csv",
    "results.json",
    "percent_error_vec.npy",
)


def _nothing_to_plot():
    """The inputs, if none of them are anywhere under OUT; else an empty list."""
    if any(_find(name) for name in INPUTS):
        return []
    if glob.glob(os.path.join(OUT, "**", "all_outputs_with_best_param_vals_exp_*.npz"),
                 recursive=True):
        return []
    return list(INPUTS)


# ---------------------------------------------------------------------------
# Best-fit plots, from what a calibration leaves on disk
#
# circulatory_autogen draws these itself, but only from a live paramID object --
# it re-runs the model to get the traces. Everything those figures show is
# already saved, so this reads the artefacts instead: the npz holds every
# variable's best-fit series, obs_data.json says which of them were fitted and
# to what, and the error vectors are the bars. No model, no solver, no CA.
# ---------------------------------------------------------------------------
def _latest_obs_data():
    """The obs_data.json belonging to this run, or None.

    A run directory keeps a dated copy per attempt; the newest is the one the
    saved vectors came from.
    """
    matches = sorted(
        glob.glob(os.path.join(OUT, "**", "*obs_data*.json"), recursive=True),
        key=os.path.getmtime,
    )
    for path in reversed(matches):
        try:
            with open(path, encoding="utf-8-sig") as fh:
                doc = json.load(fh)
        except (OSError, ValueError):
            continue
        items = doc.get("data_items") if isinstance(doc, dict) else None
        if items:
            return doc
    return None


def _resolve_name(names, wanted):
    """Find `wanted` among npz keys across the naming conventions in play.

    Three differences, all cosmetic and all fatal if ignored: obs_data writes
    ``aortic_root/v`` with a slash, the npz writes a dot, and circulatory_autogen's
    flat CellML calls the component ``aortic_root_module``. The app resolves
    exactly this in engine._resolve_output_key; kept in step with it.
    """
    if wanted in names:
        return wanted
    text = str(wanted)
    for sep in ("/", "."):
        if sep not in text:
            continue
        comp, var = text.split(sep, 1)
        bare = comp[:-7] if comp.endswith("_module") else comp
        for candidate_comp in (comp, bare, bare + "_module"):
            for out_sep in (".", "/"):
                candidate = f"{candidate_comp}{out_sep}{var}"
                if candidate in names:
                    return candidate
        if var in names:
            return var
    return None


def _is_time(operand):
    """Whether an operand names the time axis rather than a fitted series."""
    tail = str(operand).replace("/", ".").split(".")[-1].strip().lower()
    return tail in ("time", "t")


def _observed(doc):
    """One entry per fitted data_item, with what to draw and where it belongs.

    ``series`` is the operands with any time operand removed: two data_items that
    reduce to the same series are two targets on one trace, not two traces. A
    pressure's mean, max and min are the usual case, and `time_at_max`-style
    operations add a time operand to the same series -- so time is dropped rather
    than treated as part of the identity.
    """
    out = []
    for item in doc.get("data_items", []):
        operands = [o for o in (item.get("operands") or [])]
        series = tuple(o for o in operands if not _is_time(o))
        variable = series[0] if series else (operands[0] if operands else item.get("variable"))
        out.append(
            {
                "variable": variable,
                "series": series or (variable,),
                "label": item.get("name_for_plotting") or item.get("variable") or variable,
                "operation": item.get("operation") or "",
                "value": item.get("value"),
                "experiment": int(item.get("experiment_idx", 0) or 0),
            }
        )
    return out


def _tex(label, operation=""):
    """A panel/bar label, with the operation kept out of maths mode's way."""
    if not operation:
        return f"${label}$"
    return f"${label}$ ({operation.replace('_', ' ')})"


def _npz_series(path):
    """{name: array} from a saved outputs npz, plus its time vector."""
    data = np.load(path, allow_pickle=True)
    names = list(data.keys())
    time_key = next((n for n in names if n.endswith("time")), None)
    time = data[time_key] if time_key else np.arange(len(data[names[0]]))
    return time, {n: data[n] for n in names if n != time_key}


# ===========================================================================
# THE BEST-FIT PANELS — this is the part to edit
#
# One function per panel, generated from your obs_data.json with the variable
# names filled in. Each is independent and ordinary matplotlib:
#
#   * change a colour, a line style, an axis label   -> edit that function
#   * drop a panel                                   -> remove it from PANELS
#   * reorder the figure                             -> reorder PANELS
#   * add your own                                   -> write a function taking
#                                                       (ax, t, series) and add it
#
# `series` is {variable_name: array} straight out of the run's npz, and `pick`
# finds a variable whichever way it is spelled (obs_data says "aortic_root/v",
# the npz says "aortic_root_module.v").
# ===========================================================================
def pick(series, name):
    """The array for `name`, or None if this run did not record it."""
    key = _resolve_name(series, name)
    return series[key] if key else None


# <<PANELS>>


def plot_best_fit():
    """One panel per fitted observable, with the observation drawn on it.

    The npz names variables with a dot (``aortic_root.v``) and obs_data with a
    slash (``aortic_root/v``); same variable, two spellings, so both are tried.
    """
    files = sorted(glob.glob(os.path.join(OUT, "**", "all_outputs_with_best_param_vals_exp_*.npz"),
                             recursive=True))
    files = [f for f in files if not f.endswith("_plot.npz")] or files
    if not files:
        return
    doc = _latest_obs_data()
    observed = _observed(doc) if doc else []

    for path in files:
        stem = os.path.basename(path)
        exp = "".join(c for c in stem.split("exp_")[-1] if c.isdigit()) or "0"
        time, series = _npz_series(path)

        if PANELS:
            _draw_panel_grid(PANELS, time, series, f"best_fit_exp{exp}")
            continue

        # No generated panels (exported without an obs_data.json): fall back to
        # discovering them from whatever obs_data is in the run directory.
        wanted = [o for o in observed if o["experiment"] == int(exp)] or observed

        # One panel per distinct series, not per data_item: fitting a trace's
        # mean and its max is two targets on one curve, and drawing the curve
        # twice says there are two of them.
        panels = []
        by_series = {}
        for item in wanted:
            key = _resolve_name(series, item["variable"])
            if key is None:
                continue
            if key not in by_series:
                by_series[key] = {"label": item["label"], "targets": []}
                panels.append(key)
            by_series[key]["targets"].append(item)

        if not panels:
            # Nothing was fitted, or nothing matched: the traces are still worth
            # having, so fall back to the full set the pagination already handles.
            _plot_panels(time, series, f"best_fit_exp{exp}")
            continue

        cols = min(PANEL_COLS, len(panels))
        rows = int(np.ceil(len(panels) / cols))
        fig, axes = plt.subplots(rows, cols, figsize=(5 * cols, 3.4 * rows), squeeze=False)
        for ax in axes.flat[len(panels):]:
            ax.axis("off")

        # Each target gets its own dash pattern so several on one axes stay
        # tellable apart in grey scale as well as in colour.
        dashes = [(4, 2), (1, 1.6), (6, 2, 1, 2), (3, 1, 1, 1), (8, 3)]
        for i, key in enumerate(panels):
            ax = axes.flat[i]
            values = series[key]
            ax.plot(time[: len(values)], values[: len(time)], color=_color(i), lw=1.4,
                    label="best fit")
            for j, target in enumerate(by_series[key]["targets"]):
                value = target["value"]
                if not isinstance(value, (int, float)):
                    continue
                ax.axhline(value, color="#333", lw=1.1, dashes=dashes[j % len(dashes)],
                           label=f"{target['operation'] or 'observed'} = {value:.4g}")
            ax.set_title(_tex(by_series[key]["label"]), fontsize=10)
            ax.set_xlabel("time")
            ax.grid(alpha=0.25)
            ax.legend(fontsize=7, loc="best")
        fig.tight_layout()
        fig.savefig(os.path.join(PLOTS, f"best_fit_exp{exp}.png"), dpi=150)
        plt.close(fig)


def _draw_panel_grid(panels, t, series, stem):
    """Lay the panel functions out in a grid and save the figure.

    Deliberately dumb: it arranges axes and calls each function. Everything that
    decides how a panel *looks* lives in the panel functions above, where it can
    be edited without reading this.
    """
    drawn = list(panels)
    cols = min(PANEL_COLS, len(drawn))
    rows = int(np.ceil(len(drawn) / cols))
    fig, axes = plt.subplots(rows, cols, figsize=(FIG_W * cols, FIG_H * rows), squeeze=False)
    for ax in axes.flat[len(drawn):]:
        ax.axis("off")
    for ax, panel in zip(axes.flat, drawn):
        panel(ax, t, series)
    fig.tight_layout()
    out_path = os.path.join(PLOTS, f"{stem}.png")
    fig.savefig(out_path, dpi=DPI)
    plt.close(fig)
    return out_path


def _error_bar_figure(values, labels, title, filename, unit):
    order = np.argsort(values)
    fig, ax = plt.subplots(figsize=(max(6, 0.5 * len(values) + 3), 4))
    colors = ["#c0504d" if v < 0 else "#5b9bd5" for v in np.asarray(values)[order]]
    ax.barh(range(len(values)), np.asarray(values)[order], color=colors)
    ax.set_yticks(range(len(values)))
    # Already TeX-wrapped by _tex; wrapping again gives "$$x$ (max)$".
    ax.set_yticklabels([labels[i] for i in order], fontsize=9)
    ax.axvline(0, color="#333", lw=1)
    ax.set_xlabel(unit)
    ax.set_title(title)
    ax.grid(axis="x", alpha=0.25)
    fig.tight_layout()
    fig.savefig(os.path.join(PLOTS, filename), dpi=150)
    plt.close(fig)


def plot_error_bars():
    """How far each fitted observable ended up from its target.

    Sorted, and signed, because "which of these is worst and in which direction"
    is the question these answer.
    """
    doc = _latest_obs_data()
    # One bar per data_item, unlike the panels: each has its own error, so the
    # operation is what distinguishes them and belongs in the label.
    labels = [_tex(o["label"], o["operation"]) for o in _observed(doc)] if doc else []
    for name, title, unit, filename in (
        ("percent_error_vec.npy", "Best fit: error per observable", "error (%)",
         "calibration_percent_error.png"),
        ("std_error_vec.npy", "Best fit: error in standard deviations", "error (std)",
         "calibration_std_error.png"),
    ):
        path = _find(name)
        if not path:
            continue
        values = np.load(path, allow_pickle=True)
        values = np.asarray(values, dtype=float).ravel()
        if not len(values):
            continue
        names = labels if len(labels) == len(values) else [str(i) for i in range(len(values))]
        _error_bar_figure(values, names, title, filename, unit)


def main():
    global OUT, PLOTS
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    flagged = [a.split("=", 1)[1] for a in sys.argv[1:] if a.startswith("--output-dir=")]
    if "--output-dir" in sys.argv[1:]:
        idx = sys.argv[1:].index("--output-dir")
        flagged += sys.argv[1 + idx + 1 : 1 + idx + 2]
    chosen = (flagged or args or [None])[0]
    if chosen:
        OUT = os.path.abspath(chosen)
        PLOTS = os.path.join(OUT, PLOTS_DIRNAME)

    if not os.path.isdir(OUT):
        raise SystemExit(f"No such directory: {OUT}")
    # "Does a directory called output/ exist" used to stand in for "is there
    # anything to plot". It cannot any more -- the default now falls back to this
    # script's own folder, which always exists -- and it was the wrong question
    # anyway: a directory can be there and hold nothing this script can draw.
    missing = _nothing_to_plot()
    if missing:
        raise SystemExit(
            f"Nothing to plot in {OUT} — found none of {', '.join(missing)}. "
            f"Run run_pipeline.py first, or point this at a run directory: "
            f"python plot_outputs.py --output-dir <dir>"
        )
    _load_plotting_libs()
    os.makedirs(PLOTS, exist_ok=True)
    # Each section is independent: a malformed results.json should not cost you
    # the simulation plots that rendered perfectly well.
    failures = []
    for step in (plot_outputs, plot_best_fit, plot_progress, plot_error_bars, plot_analysis):
        try:
            step()
        except Exception as exc:  # noqa: BLE001 - report and carry on
            failures.append(f"{step.__name__}: {exc}")
    print(f"Plots written to {PLOTS}")
    for failure in failures:
        print(f"WARNING: {failure}")


if __name__ == "__main__":
    main()
'''


def render_pipeline_script() -> str:
    """The standalone pipeline driver (reads the sibling dated yaml)."""
    return PIPELINE_SCRIPT


def _identifier(text: str) -> str:
    """A readable Python identifier from a label like ``v_{AR}``."""
    cleaned = []
    for ch in str(text):
        cleaned.append(ch if (ch.isalnum() or ch == "_") else "_")
    name = "".join(cleaned).strip("_")
    while "__" in name:
        name = name.replace("__", "_")
    if not name or name[0].isdigit():
        name = f"panel_{name}" if name else "panel"
    return name


def _panel_functions(obs_data: dict | None) -> str:
    """Generate one named panel function per fitted series.

    The alternative -- a loop over whatever obs_data happens to be next to the
    data -- produces a script that works and cannot be edited: to change one
    panel you have to understand the loop that draws all of them. Here each
    panel is a few lines of ordinary matplotlib with the variable names already
    written in, so changing one is changing one.
    """
    items = (obs_data or {}).get("data_items") or []
    if not items:
        return (
            "# No obs_data was available when this script was written, so there are\n"
            "# no generated panels. The best-fit section falls back to discovering\n"
            "# them from the obs_data.json in the run directory.\n"
            "PANELS = []"
        )

    # Group exactly as the drawing code does: one panel per series, with a time
    # operand ignored, so several operations on one trace share an axes.
    groups: list[dict] = []
    index: dict[tuple, dict] = {}
    for item in items:
        operands = list(item.get("operands") or [])
        series = tuple(
            o for o in operands
            if str(o).replace("/", ".").split(".")[-1].strip().lower() not in ("time", "t")
        )
        variable = series[0] if series else (operands[0] if operands else item.get("variable"))
        if not variable:
            continue
        key = series or (variable,)
        group = index.get(key)
        if group is None:
            group = {
                "variable": variable,
                "label": item.get("name_for_plotting") or item.get("variable") or variable,
                "described": item.get("variable") or "",
                "targets": [],
            }
            index[key] = group
            groups.append(group)
        group["targets"].append(item)

    used: set[str] = set()
    blocks: list[str] = []
    names: list[str] = []
    for panel_idx, group in enumerate(groups):
        name = f"panel_{_identifier(group['label'])}"
        suffix = 2
        while name in used:
            name = f"panel_{_identifier(group['label'])}_{suffix}"
            suffix += 1
        used.add(name)
        names.append(name)

        described = group["described"]
        title = f"${group['label']}$"
        lines = [
            f"def {name}(ax, t, series):",
            f'    """{described or group["label"]} — from {group["variable"]}."""',
            f'    y = pick(series, {group["variable"]!r})',
            "    if y is None:",
            f'        ax.set_title({title!r} + " (not recorded)")',
            "        return",
            f"    ax.plot(t[: len(y)], y[: len(t)], color=PALETTE[{panel_idx % 6}], "
            f'lw=1.4, label="best fit")',
        ]
        for i, target in enumerate(group["targets"]):
            value = target.get("value")
            operation = (target.get("operation") or "observed").replace("_", " ")
            if isinstance(value, (int, float)):
                lines.append(
                    f"    ax.axhline({value!r}, color=TARGET_COLOUR, lw=1.1, "
                    f"dashes=TARGET_DASHES[{i % 5}], "
                    f'label="{operation} = {value:.4g}")'
                )
        lines += [
            f"    ax.set_title({title!r}, fontsize=10)",
            '    ax.set_xlabel("time")',
            "    ax.grid(alpha=0.25)",
            '    ax.legend(fontsize=7, loc="best")',
        ]
        blocks.append("\n".join(lines))

    listing = "\n".join(f"    {n}," for n in names)
    blocks.append(
        "# The figure, in order. Comment a line out to drop that panel.\n"
        f"PANELS = [\n{listing}\n]"
    )
    return "\n\n\n".join(blocks)


def render_plotting_script(obs_data: dict | None = None) -> str:
    """The standalone plotting script.

    With an ``obs_data`` document, the best-fit panels are generated as named
    functions with the variables written in, so the script is something a user
    edits rather than something they read around. Without one it still works,
    discovering the panels from the run directory at draw time.
    """
    return PLOTTING_SCRIPT.replace("# <<PANELS>>", _panel_functions(obs_data))
