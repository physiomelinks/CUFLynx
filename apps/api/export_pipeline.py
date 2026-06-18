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
        "num_calls_to_function": int(calibration.get("num_calls_to_function", 100)),
        "cost_convergence": float(calibration.get("cost_convergence", 0.0001)),
        "max_patience": int(calibration.get("max_patience", 10)),
    }
    if calibration.get("cost_type"):
        optimiser_options["cost_type"] = calibration["cost_type"]

    ui = {
        # --- general / model ---
        "file_prefix": file_prefix,
        "model_type": model_type,
        "model_file": model_file,  # CUFLynx extra: the (flat) CellML to run/generate from
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
        "param_id_method": calibration.get("param_id_method", "genetic_algorithm"),
        "do_ad": str(calibration.get("gradient_method", "FD")).upper() == "AD",
        "optimiser_options": optimiser_options,
        # --- sensitivity ---
        "sa_options": {
            "method": sensitivity.get("method", "sobol"),
            "sample_type": sensitivity.get("sample_type", "saltelli"),
            "num_samples": int(sensitivity.get("num_samples", 256)),
        },
        # --- UQ / mcmc ---
        "mcmc_options": {
            "num_steps": int(uq.get("num_steps", 1000)),
            "num_walkers": int(uq.get("num_walkers", 64)),
            "cost_type": uq.get("cost_type", "gaussian_MLE"),
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

Drives circulatory_autogen from the sibling user_inputs_*.yaml, running each
stage that is enabled by a do_* flag in that yaml:
    do_simulation, do_sensitivity, do_calibration, do_mcmc / do_ia (UQ).
Toggle the flags in the yaml (or comment out a block below) to change what runs.

Usage:
    python run_pipeline.py --ca-src /path/to/circulatory_autogen/src
    # or set CIRCULATORY_AUTOGEN_SRC in the environment
"""
import argparse
import glob
import json
import os
import sys

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


def main():
    sys.path.insert(0, resolve_ca_src())
    cfg = load_config()

    resources = os.path.join(HERE, cfg.get("resources_dir", "resources"))
    out_dir = os.path.join(HERE, "output")
    os.makedirs(out_dir, exist_ok=True)

    model_type = cfg.get("model_type", "cellml_only")
    solver = cfg.get("solver")
    solver_info = dict(cfg.get("solver_info", {}))
    dt = float(cfg.get("dt", 0.01))
    sim_time = float(cfg.get("sim_time", 2.0))
    pre_time = float(cfg.get("pre_time", 0.0))
    file_prefix = cfg["file_prefix"]
    model_path = os.path.join(resources, cfg["model_file"])
    obs_path = os.path.join(HERE, cfg["param_id_obs_path"]) if cfg.get("param_id_obs_path") else None
    params_path = (
        os.path.join(resources, cfg["params_for_id_file"]) if cfg.get("params_for_id_file") else None
    )

    # python / casadi_python backends run a generated .py model.
    if model_type in ("python", "casadi_python"):
        from generators.PythonGenerator import PythonGenerator

        model_path = PythonGenerator(
            model_path,
            output_dir=out_dir,
            module_name=f"{file_prefix}_gen",
            casadi_compat=(model_type == "casadi_python"),
        ).generate()

    if cfg.get("do_simulation"):
        print("=== simulation ===", flush=True)
        from solver_wrappers import get_simulation_helper

        helper = get_simulation_helper(
            model_path=model_path, solver=solver, model_type=model_type,
            dt=dt, sim_time=sim_time, pre_time=pre_time, solver_info=solver_info,
        )
        helper.run()
        if hasattr(helper, "get_time"):
            time = [float(t) for t in helper.get_time(include_pre_time=False)]
        else:
            time = [float(t) for t in helper.get_results(["time"], flatten=True)[0]]
        names = helper.get_all_variable_names()
        outputs = {n: [float(v) for v in helper.get_results([n], flatten=True)[0]] for n in names}
        with open(os.path.join(out_dir, "simulation.json"), "w") as fh:
            json.dump({"time": time, "outputs": outputs}, fh)

    if cfg.get("do_sensitivity"):
        print("=== sensitivity analysis ===", flush=True)
        from sensitivity_analysis.sensitivityAnalysis import SensitivityAnalysis

        sa = SensitivityAnalysis(
            model_path=model_path, model_type=model_type, file_name_prefix=file_prefix,
            sa_options={**cfg.get("sa_options", {}), "output_dir": out_dir},
            param_id_output_dir=out_dir, resources_dir=resources, solver_info=solver_info,
            dt=dt, param_id_obs_path=obs_path, params_for_id_path=params_path,
        )
        sa.run_sensitivity_analysis()

    if cfg.get("do_calibration"):
        print("=== calibration ===", flush=True)
        from param_id.paramID import CVS0DParamID

        param_id = CVS0DParamID(
            model_path=model_path, model_type=model_type,
            param_id_method=cfg.get("param_id_method", "genetic_algorithm"),
            file_name_prefix=file_prefix, params_for_id_path=params_path,
            param_id_obs_path=obs_path, sim_time=sim_time, pre_time=pre_time, dt=dt,
            solver_info=solver_info, optimiser_options=cfg.get("optimiser_options", {}),
            do_ad=bool(cfg.get("do_ad", False)), param_id_output_dir=out_dir,
            resources_dir=resources,
        )
        param_id.run()

    if cfg.get("do_mcmc") or cfg.get("do_ia"):
        print("=== uncertainty quantification ===", flush=True)
        # MCMC (do_mcmc) uses CVS0DParamID(mcmc_instead=True); identifiability
        # (do_ia) uses IdentifiabilityAnalysis. Both need a best-fit point — run
        # calibration above (do_calibration) first, or set the best params here.
        # See circulatory_autogen src/scripts/param_id_run_script.py for the full
        # MCMC / Laplace flow to adapt to your study.
        print("  Configure UQ following param_id_run_script.py (needs a best fit).", flush=True)

    print(f"Done. Outputs in {out_dir}", flush=True)


if __name__ == "__main__":
    main()
'''


PLOTTING_SCRIPT = '''#!/usr/bin/env python3
"""Regenerate CUFLynx-equivalent plots from the exported pipeline's output data.

Reproduces:
  - output plots   : simulation traces (output/simulation.json)
  - progress plots : cost vs generation (log y) + parameters vs generation
                     (output/best_cost_history.csv, best_param_vals_history.csv)
  - analysis plots : sensitivity heatmap and/or UQ posteriors (output/results.json)

Writes PNGs next to the data. Usage: python plot_outputs.py
"""
import csv
import glob
import json
import os

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "output")
PALETTE = ["#5b9bd5", "#ed7d31", "#70ad47", "#ffc000", "#a142f4", "#e84a5f"]


def _color(i):
    return PALETTE[i % len(PALETTE)]


def plot_outputs():
    path = os.path.join(OUT, "simulation.json")
    if not os.path.exists(path):
        return
    data = json.load(open(path))
    t = data["time"]
    fig, ax = plt.subplots(figsize=(7, 4))
    for i, (name, series) in enumerate(data["outputs"].items()):
        n = min(len(t), len(series))
        ax.plot(t[:n], series[:n], color=_color(i), lw=1.3, label=name)
    ax.set_xlabel("time")
    ax.set_ylabel("output")
    ax.legend(fontsize=6, ncol=2)
    fig.tight_layout()
    fig.savefig(os.path.join(OUT, "output_plot.png"), dpi=150)
    plt.close(fig)


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
    names = [c.strip() for c in lines[0].split(",")]
    rows = []
    for line in lines[1:]:
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
        ax.set_yscale("log")
        ax.set_xlabel("generation")
        ax.set_ylabel("cost")
        ax.set_title("Cost vs generation")
        fig.tight_layout()
        fig.savefig(os.path.join(OUT, "progress_cost.png"), dpi=150)
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
        fig.savefig(os.path.join(OUT, "progress_params.png"), dpi=150)
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
        fig.savefig(os.path.join(OUT, "analysis_sensitivity.png"), dpi=150)
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
        fig.savefig(os.path.join(OUT, "analysis_uq.png"), dpi=150)
        plt.close(fig)


def main():
    if not os.path.isdir(OUT):
        raise SystemExit(f"No output dir at {OUT} — run run_pipeline.py first.")
    plot_outputs()
    plot_progress()
    plot_analysis()
    print(f"Plots written to {OUT}")


if __name__ == "__main__":
    main()
'''


def render_pipeline_script() -> str:
    """The standalone pipeline driver (reads the sibling dated yaml)."""
    return PIPELINE_SCRIPT


def render_plotting_script() -> str:
    """The standalone plotting script (reads the pipeline's output dir)."""
    return PLOTTING_SCRIPT
