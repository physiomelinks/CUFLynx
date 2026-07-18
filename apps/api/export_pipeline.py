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
