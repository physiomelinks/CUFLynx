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
    user_func_files: dict | None = None,
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
        # --- user-authored operation / cost funcs (CA #303) ---
        # An obs_data data_item names its operation and cost_type by name, so a
        # study using a func the user wrote in the GUI is not reproducible unless
        # the func travels with it: CA would fail on an operation it has never
        # heard of. Relative, like every other resource here, and resolved
        # against the export folder by build_inp_data_dict.
        **{key: f"resources/{name}" for key, name in (user_func_files or {}).items()},
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
    # User-authored operation/cost funcs travel with the export, so point CA at
    # the copies in this folder rather than wherever they lived on the machine
    # that produced it (CA #303). Without these the run dies on the first
    # data_item naming a func the user wrote.
    for key in ("operation_funcs_external_path", "cost_funcs_external_path"):
        if cfg.get(key):
            inp[key] = os.path.join(HERE, cfg[key])
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


PLOT_UTILITIES_SCRIPT = '''#!/usr/bin/env python3
"""Finding and loading a CUFLynx run's data. Machinery for plot_outputs.py.

Nothing here decides how anything *looks*. It locates the run directory, reads
the files a run leaves behind, and lays panels out on a grid; every colour,
label, axis and limit lives in plot_outputs.py, which is the file to edit.

Kept separate so that editing a plot never means reading past code that has
nothing to do with plots. You should not need to open this file.
"""

import csv
import glob
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))

# Filled in by load_plotting_libs(), so importing this module stays cheap and a
# missing matplotlib is reported by plot_outputs rather than at import time.
plt = None
np = None

PLOTS_DIRNAME = "pyscript_plots"


def _default_out():
    """Where to look for run data, when nobody says.

    Two layouts reach this script. run_pipeline.py writes into `output/` beside
    it, so that is preferred. But CUFLynx also drops these scripts straight into
    the outputs directory the user chose, where the run data is in
    circulatory_autogen's own `<method>_<model>_<hash>_obs_data/` folders and
    there is no `output/` at all.
    """
    from_env = os.environ.get("CUFLYNX_OUTPUT_DIR")
    if from_env:
        return from_env
    beside = os.path.join(HERE, "output")
    return beside if os.path.isdir(beside) else HERE


OUT = _default_out()
PLOTS = os.path.join(OUT, PLOTS_DIRNAME)


def set_output_dir(path):
    """Point everything at another run directory."""
    global OUT, PLOTS
    OUT = os.path.abspath(path)
    PLOTS = os.path.join(OUT, PLOTS_DIRNAME)
    return OUT


def output_dir_from_argv(argv):
    """The directory named on the command line, or None."""
    args = [a for a in argv if not a.startswith("-")]
    flagged = [a.split("=", 1)[1] for a in argv if a.startswith("--output-dir=")]
    if "--output-dir" in argv:
        idx = argv.index("--output-dir")
        flagged += argv[idx + 1 : idx + 2]
    chosen = (flagged or args or [None])[0]
    return chosen


def load_plotting_libs():
    """Import matplotlib and numpy, or explain what to install."""
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
    return plt, np


# ---------------------------------------------------------------------------
# Finding things
# ---------------------------------------------------------------------------
def find(name):
    """The first file called `name` anywhere under the run directory."""
    matches = glob.glob(os.path.join(OUT, "**", name), recursive=True)
    return matches[0] if matches else None


def resolve_name(names, wanted):
    """Find `wanted` among a run's variable names, whichever way it is spelled.

    Three differences, all cosmetic and all fatal if ignored: obs_data writes
    ``aortic_root/v`` with a slash, the saved npz writes a dot, and
    circulatory_autogen's flat CellML calls the component ``aortic_root_module``.
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


def pick(series, name):
    """The array for `name`, or None if this run did not record it."""
    key = resolve_name(series, name)
    return series[key] if key else None


def is_time(operand):
    """Whether an operand names the time axis rather than a fitted series."""
    tail = str(operand).replace("/", ".").split(".")[-1].strip().lower()
    return tail in ("time", "t")


def tex(label, operation=""):
    """A panel or bar label, with the operation kept out of maths mode's way."""
    if not operation:
        return f"${label}$"
    return f"${label}$ ({operation.replace('_', ' ')})"


# ---------------------------------------------------------------------------
# Reading what a run leaves behind
# ---------------------------------------------------------------------------
def simulation():
    """``(time, {variable: series})`` from an exported pipeline run, or None."""
    path = os.path.join(OUT, "simulation.json")
    if not os.path.isfile(path):
        return None
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    if isinstance(data.get("experiments"), list):
        return [(e.get("time", []), e.get("outputs", {})) for e in data["experiments"]]
    return [(data.get("time", []), data.get("outputs", {}))]


def cost_history():
    """Rows of the cost history, newest column first, or []."""
    path = find("best_cost_history.csv")
    if not path:
        return []
    rows = []
    with open(path, newline="", encoding="utf-8") as fh:
        for row in csv.reader(fh):
            values = []
            for cell in row:
                try:
                    values.append(float(cell))
                except ValueError:
                    values = []
                    break
            if values:
                rows.append(values)
    return rows


def param_history():
    """``(generations, [(name, values), ...])`` for the fitted parameters."""
    path = find("best_param_vals_history.csv")
    if not path:
        return [], []
    rows = []
    header = []
    with open(path, newline="", encoding="utf-8") as fh:
        for i, row in enumerate(csv.reader(fh)):
            try:
                rows.append([float(c) for c in row])
            except ValueError:
                if i == 0:
                    header = row
    if not rows:
        return [], []
    columns = list(zip(*rows))
    names = header if len(header) == len(columns) else [f"p{i}" for i in range(len(columns))]
    return list(range(len(rows))), list(zip(names, columns))


def results():
    """The analysis payload (sensitivity indices / UQ samples), or None."""
    path = find("results.json")
    if not path:
        return None
    # Deliberately not swallowing a parse error: a corrupt results.json is worth
    # a warning, and run_sections turns the exception into one without costing
    # the figures that rendered perfectly well.
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def latest_obs_data():
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
        if isinstance(doc, dict) and doc.get("data_items"):
            return doc
    return None


def observed(doc=None):
    """One entry per fitted data_item: variable, label, operation, value, series.

    ``series`` is the operands with any time operand removed, so two data_items
    that reduce to the same trace are two targets on one curve rather than two
    curves.
    """
    doc = doc if doc is not None else latest_obs_data()
    out = []
    for item in (doc or {}).get("data_items", []):
        operands = list(item.get("operands") or [])
        series = tuple(o for o in operands if not is_time(o))
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


def best_fit_runs():
    """``[(experiment, time, {variable: series}), ...]`` from the saved npz files."""
    files = sorted(
        glob.glob(
            os.path.join(OUT, "**", "all_outputs_with_best_param_vals_exp_*.npz"), recursive=True
        )
    )
    files = [f for f in files if not f.endswith("_plot.npz")] or files
    runs = []
    for path in files:
        stem = os.path.basename(path)
        exp = "".join(c for c in stem.split("exp_")[-1] if c.isdigit()) or "0"
        data = np.load(path, allow_pickle=True)
        names = list(data.keys())
        time_key = next((n for n in names if n.endswith("time")), None)
        time = data[time_key] if time_key else np.arange(len(data[names[0]]))
        runs.append((exp, time, {n: data[n] for n in names if n != time_key}))
    return runs


def error_vector(name):
    """A saved error vector as a flat array, or None."""
    path = find(name)
    if not path:
        return None
    values = np.asarray(np.load(path, allow_pickle=True), dtype=float).ravel()
    return values if len(values) else None


# ---------------------------------------------------------------------------
# Layout
# ---------------------------------------------------------------------------
def save(fig, filename, dpi=150):
    """Write a figure into the plots directory and close it."""
    os.makedirs(PLOTS, exist_ok=True)
    path = os.path.join(PLOTS, filename)
    fig.savefig(path, dpi=dpi)
    plt.close(fig)
    return path


def grid(n, cols=3, fig_w=5.0, fig_h=3.4):
    """A figure with `n` axes laid out in a grid; unused axes are hidden."""
    cols = max(1, min(cols, n))
    rows = int(np.ceil(n / cols))
    fig, axes = plt.subplots(rows, cols, figsize=(fig_w * cols, fig_h * rows), squeeze=False)
    for ax in axes.flat[n:]:
        ax.axis("off")
    return fig, list(axes.flat[:n])


def paginate(items, per_page):
    """`items` split into pages, so a 456-variable model is readable."""
    return [items[i : i + per_page] for i in range(0, len(items), per_page)]


# What this script can draw, and therefore what it looks for before claiming
# there is nothing to do.
INPUTS = (
    "simulation.json",
    "best_cost_history.csv",
    "best_param_vals_history.csv",
    "results.json",
    "percent_error_vec.npy",
)


def nothing_to_plot():
    """The inputs, if none of them are anywhere under OUT; else an empty list."""
    if any(find(name) for name in INPUTS):
        return []
    if glob.glob(
        os.path.join(OUT, "**", "all_outputs_with_best_param_vals_exp_*.npz"), recursive=True
    ):
        return []
    return list(INPUTS)


def run_sections(sections):
    """Draw each section, reporting failures without losing the others.

    A malformed results.json should not cost you the simulation plots that
    rendered perfectly well.
    """
    failures = []
    for section in sections:
        try:
            section()
        except Exception as exc:  # noqa: BLE001 - report and carry on
            failures.append(f"{getattr(section, '__name__', section)}: {exc}")
    return failures
'''


PLOTTING_SCRIPT = '''#!/usr/bin/env python3
"""Plots from a CUFLynx run — yours to edit.

    python plot_outputs.py                       # find the run data automatically
    python plot_outputs.py --output-dir <dir>    # a specific run directory
    CUFLYNX_OUTPUT_DIR=<dir> python plot_outputs.py

Every figure is drawn by a function in this file, and each one is ordinary
matplotlib. Finding and loading the data is `plot_utilities.py`, which you
should not need to open.

WHAT TO EDIT
    STYLE                one place for colours, sizes and dpi
    panel_*              one function per fitted observable, named after it
    PANELS               which of those panels appear, and in what order
    plot_*               one function per figure -- best fit, progress,
                         error bars, analysis, simulation traces
    FIGURES              which figures get drawn at all

To change one plot, edit its function. To drop it, remove it from FIGURES.
To add one, write a function and add it.
"""

import os
import sys

import plot_utilities as util

# ---------------------------------------------------------------------------
# STYLE — shared by every figure below
# ---------------------------------------------------------------------------
STYLE = {
    "palette": ["#5b9bd5", "#ed7d31", "#70ad47", "#ffc000", "#a142f4", "#e84a5f"],
    "target_colour": "#333333",
    # Dash patterns for observed-value lines, so several on one axes stay
    # tellable apart in grey scale as well as in colour.
    "target_dashes": [(4, 2), (1, 1.6), (6, 2, 1, 2), (3, 1, 1, 1), (8, 3)],
    "panel_cols": 3,
    "panel_size": (5.0, 3.4),   # inches, per panel
    "dpi": 150,
    # The pipeline logs every model variable, so one figure of 456 panels is
    # unusable. Traces are paginated at this many per page.
    "panels_per_page": 12,
}

PALETTE = STYLE["palette"]
TARGET_COLOUR = STYLE["target_colour"]
TARGET_DASHES = STYLE["target_dashes"]

# Bound to matplotlib/numpy in main(), so this file reads like a normal script.
plt = None
np = None


def colour(i):
    return PALETTE[i % len(PALETTE)]


def pick(series, name):
    """The array recorded for `name`, or None. Spelling differences handled."""
    return util.pick(series, name)


# <<PANELS>>


# ---------------------------------------------------------------------------
# BEST FIT — the calibrated traces, with what they were fitted to
# ---------------------------------------------------------------------------
def plot_best_fit():
    """One figure per experiment, one panel per entry in PANELS."""
    for exp, t, series in util.best_fit_runs():
        panels = PANELS or _discovered_panels(exp, series)
        if not panels:
            _plot_all_traces(t, series, f"best_fit_exp{exp}")
            continue
        fig, axes = util.grid(
            len(panels), STYLE["panel_cols"], *STYLE["panel_size"]
        )
        for ax, panel in zip(axes, panels):
            panel(ax, t, series)
        fig.tight_layout()
        util.save(fig, f"best_fit_exp{exp}.png", STYLE["dpi"])


# ---------------------------------------------------------------------------
# ERROR BARS — how far each observable ended up from its target
# ---------------------------------------------------------------------------
def plot_error_bars():
    """Sorted and signed: "which is worst, and in which direction"."""
    labels = [util.tex(o["label"], o["operation"]) for o in util.observed()]
    for name, title, unit, filename in (
        ("percent_error_vec.npy", "Best fit: error per observable", "error (%)",
         "calibration_percent_error.png"),
        ("std_error_vec.npy", "Best fit: error in standard deviations", "error (std)",
         "calibration_std_error.png"),
    ):
        values = util.error_vector(name)
        if values is None:
            continue
        names = labels if len(labels) == len(values) else [str(i) for i in range(len(values))]
        order = np.argsort(values)
        fig, ax = plt.subplots(figsize=(max(6, 0.5 * len(values) + 3), 4))
        ax.barh(
            range(len(values)),
            values[order],
            color=["#c0504d" if v < 0 else PALETTE[0] for v in values[order]],
        )
        ax.set_yticks(range(len(values)))
        ax.set_yticklabels([names[i] for i in order], fontsize=9)
        ax.axvline(0, color="#333", lw=1)
        ax.set_xlabel(unit)
        ax.set_title(title)
        ax.grid(axis="x", alpha=0.25)
        fig.tight_layout()
        util.save(fig, filename, STYLE["dpi"])


# ---------------------------------------------------------------------------
# PROGRESS — how the calibration got there
# ---------------------------------------------------------------------------
def plot_progress():
    costs = util.cost_history()
    if costs:
        best = [row[0] for row in costs]
        fig, ax = plt.subplots(figsize=(6, 4))
        ax.plot(range(len(best)), best, color=PALETTE[0], lw=1.6)
        ax.set_yscale("log")
        ax.set_xlabel("generation")
        ax.set_ylabel("best cost")
        ax.set_title("Cost")
        ax.grid(alpha=0.25)
        fig.tight_layout()
        util.save(fig, "progress_cost.png", STYLE["dpi"])

    generations, params = util.param_history()
    if params:
        fig, axes = util.grid(len(params), STYLE["panel_cols"], 4.5, 3.0)
        for i, (ax, (name, values)) in enumerate(zip(axes, params)):
            ax.plot(generations, values, color=colour(i), lw=1.4)
            ax.set_title(name, fontsize=8)
            ax.set_xlabel("generation")
            ax.grid(alpha=0.25)
        fig.tight_layout()
        util.save(fig, "progress_params.png", STYLE["dpi"])


# ---------------------------------------------------------------------------
# ANALYSIS — sensitivity indices and UQ posteriors
# ---------------------------------------------------------------------------
def plot_analysis():
    """Sensitivity heatmap and UQ posteriors, from the analysis results.json."""
    res = util.results()
    if not res:
        return

    # Sensitivity: indices are {kind: {output: {param: value}}}.
    indices = res.get("indices")
    if indices:
        kind = "local" if "local" in indices else ("ST" if "ST" in indices else next(iter(indices)))
        by_out = indices[kind]
        outs = res.get("output_names") or list(by_out.keys())
        params = res.get("param_names") or sorted({p for o in by_out.values() for p in o})
        mat = np.array(
            [[by_out.get(o, {}).get(p, np.nan) for o in outs] for p in params], dtype=float
        )
        # A local index is signed -- which way a parameter pushes an output is
        # half the answer -- so it gets a diverging map centred on zero.
        signed = kind == "local"
        vmax = np.nanmax(np.abs(mat)) or 1.0
        fig, ax = plt.subplots(figsize=(1.2 + 0.5 * len(outs), 1 + 0.4 * len(params)))
        im = ax.imshow(
            mat, aspect="auto", cmap="coolwarm" if signed else "viridis",
            vmin=-vmax if signed else 0, vmax=vmax,
        )
        ax.set_xticks(range(len(outs)))
        ax.set_xticklabels(outs, rotation=90, fontsize=6)
        ax.set_yticks(range(len(params)))
        ax.set_yticklabels(params, fontsize=6)
        ax.set_title(f"Sensitivity ({kind})")
        fig.colorbar(im, ax=ax)
        fig.tight_layout()
        util.save(fig, "analysis_sensitivity.png", STYLE["dpi"])

    # UQ posteriors: [{qname, mean, std, q05, q95, bins, counts}].
    uq_params = res.get("params") if isinstance(res.get("params"), list) else None
    if uq_params and all("counts" in p for p in uq_params):
        n = len(uq_params)
        fig, axes = plt.subplots(n, 1, figsize=(5, 2 * n), squeeze=False)
        for i, param in enumerate(uq_params):
            ax = axes[i][0]
            edges = np.array(param["bins"])
            counts = np.array(param["counts"])
            centres = (
                0.5 * (edges[:-1] + edges[1:])
                if len(edges) == len(counts) + 1
                else np.arange(len(counts))
            )
            width = (centres[1] - centres[0]) if len(centres) > 1 else 1
            ax.bar(centres, counts, width=width, color=PALETTE[0], alpha=0.6)
            ax.axvline(param["mean"], color=PALETTE[5])
            ax.set_title(param.get("qname", f"param {i}"), fontsize=7)
        fig.tight_layout()
        util.save(fig, "analysis_uq.png", STYLE["dpi"])


# ---------------------------------------------------------------------------
# SIMULATION TRACES — every logged variable, from an exported pipeline run
# ---------------------------------------------------------------------------
def plot_simulation_outputs():
    runs = util.simulation()
    if not runs:
        return
    for e, (t, outputs) in enumerate(runs):
        suffix = f"_exp{e}" if len(runs) > 1 else ""
        _plot_all_traces(t, outputs, f"output_plot{suffix}")


def _plot_all_traces(t, outputs, stem):
    """A panel per variable, paginated.

    A panel each, not one shared axes: model variables span wildly different
    scales -- pressures ~1e4, flows ~1e-4, valve states 0/1 -- so on a common
    linear axis all but the largest collapse onto zero.
    """
    names = list(outputs)
    if not names:
        return
    pages = util.paginate(names, STYLE["panels_per_page"])
    for page_no, page in enumerate(pages, start=1):
        fig, axes = util.grid(len(page), STYLE["panel_cols"], 4.5, 2.6)
        for i, (ax, name) in enumerate(zip(axes, page)):
            values = outputs[name]
            ax.plot(t[: len(values)], values[: len(t)], color=colour(i), lw=1.1)
            ax.set_title(name, fontsize=7)
            ax.tick_params(labelsize=6)
            ax.grid(alpha=0.25)
        fig.tight_layout()
        page_suffix = f"_p{page_no}" if len(pages) > 1 else ""
        util.save(fig, f"{stem}{page_suffix}.png", STYLE["dpi"])


def _discovered_panels(exp, series):
    """Panels built at run time, when this script was written without obs_data.

    Grouped by series, so a trace fitted on its mean and its max is one panel
    with two targets rather than two panels of the same curve.
    """
    wanted = [o for o in util.observed() if o["experiment"] == int(exp)] or util.observed()
    order, by_series = [], {}
    for item in wanted:
        key = util.resolve_name(series, item["variable"])
        if key is None:
            continue
        if key not in by_series:
            by_series[key] = {"label": item["label"], "targets": []}
            order.append(key)
        by_series[key]["targets"].append(item)

    def make(key, i):
        def panel(ax, t, series_):
            group = by_series[key]
            values = series_[key]
            ax.plot(t[: len(values)], values[: len(t)], color=colour(i), lw=1.4, label="best fit")
            for j, target in enumerate(group["targets"]):
                value = target["value"]
                if isinstance(value, (int, float)):
                    ax.axhline(
                        value, color=TARGET_COLOUR, lw=1.1,
                        dashes=TARGET_DASHES[j % len(TARGET_DASHES)],
                        label=f"{target['operation'] or 'observed'} = {value:.4g}",
                    )
            ax.set_title(util.tex(group["label"]), fontsize=10)
            ax.set_xlabel("time")
            ax.grid(alpha=0.25)
            ax.legend(fontsize=7, loc="best")

        return panel

    return [make(key, i) for i, key in enumerate(order)]


# ---------------------------------------------------------------------------
# The figures, in order. Comment one out to stop drawing it.
# ---------------------------------------------------------------------------
FIGURES = [
    plot_simulation_outputs,
    plot_best_fit,
    plot_progress,
    plot_error_bars,
    plot_analysis,
]


def main():
    global plt, np

    chosen = util.output_dir_from_argv(sys.argv[1:])
    if chosen:
        util.set_output_dir(chosen)

    if not os.path.isdir(util.OUT):
        raise SystemExit(f"No such directory: {util.OUT}")
    missing = util.nothing_to_plot()
    if missing:
        raise SystemExit(
            f"Nothing to plot in {util.OUT} — found none of {', '.join(missing)}. "
            f"Run run_pipeline.py first, or point this at a run directory: "
            f"python plot_outputs.py --output-dir <dir>"
        )

    plt, np = util.load_plotting_libs()
    os.makedirs(util.PLOTS, exist_ok=True)

    failures = util.run_sections(FIGURES)
    print(f"Plots written to {util.PLOTS}")
    for failure in failures:
        print(f"WARNING: {failure}")


if __name__ == "__main__":
    main()
'''


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
            "# no generated panels here. plot_best_fit() falls back to discovering\n"
            "# them from the obs_data.json in the run directory. Re-export with a\n"
            "# model loaded to get one named function per observable instead.\n"
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


def render_pipeline_script() -> str:
    """The standalone pipeline driver (reads the sibling dated yaml)."""
    return PIPELINE_SCRIPT


PLOT_UTILITIES_NAME = "plot_utilities.py"
PLOTTING_SCRIPT_NAME = "plot_outputs.py"


def render_plot_utilities() -> str:
    """The machinery half: finding the run, reading its files, laying out axes.

    Split from the script the user edits so that changing a plot never means
    reading past code that has nothing to do with plots.
    """
    return PLOT_UTILITIES_SCRIPT


def render_plotting_script(obs_data: dict | None = None) -> str:
    """The half the user edits, and the one they run.

    With an ``obs_data`` document, the best-fit panels are generated as named
    functions with the variables written in, so the script is something a user
    edits rather than something they read around. Without one it still works,
    discovering the panels from the run directory at draw time.
    """
    return PLOTTING_SCRIPT.replace("# <<PANELS>>", _panel_functions(obs_data))
