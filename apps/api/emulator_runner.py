"""Standalone emulator-training runner — spawned as a subprocess by the API.

Trains a surrogate of the model's scalar observable features with
circulatory_autogen's ``EmulatorTrainer`` (CA #333), and leaves the bundle in CA's
own format: ``emulator.joblib`` + ``emulator_metadata.json`` + ``training_data.npz``
under ``<output_dir>/emulators/<file_prefix>_<obs_prefix>/``. Nothing CUFLynx-shaped
is written; the manager reads CA's metadata through :mod:`ca_run_history` (#210).

Training simulations are spread across MPI ranks by CA, exactly as the Sobol
sampler's are, so this is launched under ``mpiexec -n N`` for N > 1.

Usage:  python -u emulator_runner.py <config.json>

config.json (same shape as the sensitivity runner):
{
  "model_path": "...cellml", "obs_path": "...json", "params_path": "...csv",
  "output_dir": "...", "file_prefix": "model", "num_cores": 1, "python": null,
  "settings": { "num_train_samples": 128, "sample_type": "sobol",
                "models": "default", "dt": 0.01, "sim_time": 2.0, "pre_time": 0.0,
                "solver": "CVODE_myokit", "DEBUG": false }
}
"""

from __future__ import annotations

import json
import logging
import os
import sys
import traceback
from pathlib import Path

# Markers the API watches for in stdout.
DONE_MARKER = "__EMULATOR_DONE__"
FAIL_MARKER = "__EMULATOR_FAILED__"
#: The one line of run metadata no file holds: where the bundle was written. The
#: scores themselves are read from CA's own emulator_metadata.json (#210).
META_MARKER = "__EMULATOR_META__ "

#: CUFLynx-level / simulation settings that must not be forwarded into CA's
#: ``emulator_settings`` — the rest are CA emulation option values, so a new
#: option added to CA's schema flows through without a runner change.
_EMULATOR_RESERVED = {
    "dt", "DEBUG", "num_cores", "solver", "solver_info", "python_path",
    "sim_time", "pre_time", "cost_type", "generated_model_format",
    "config_outputs_dir", "use_emulator",
}


def _ensure_ca_on_path() -> None:
    src = os.environ.get("CIRCULATORY_AUTOGEN_SRC")
    if not src:
        repo_root = Path(__file__).resolve().parents[2]
        src = str(repo_root.parent / "circulatory_autogen" / "src")
    if src not in sys.path:
        sys.path.insert(0, src)


def _emulator_settings(settings: dict, emu_dir: str, seed=None) -> dict:
    """Assemble CA's ``emulator_settings`` from the UI settings.

    ``emulator_dir`` is set explicitly rather than left to CA's default so the
    bundle lands where :func:`ca_run_history.emulator_dir` will look for it, and
    so a run that trains and a later run that uses it cannot disagree about the
    path. The global analysis seed, when set, drives the design as well — a
    training design is exactly the kind of random process that seed exists for.
    """
    out = {"emulator_dir": emu_dir}
    for key, value in settings.items():
        if key not in _EMULATOR_RESERVED and value is not None:
            out[key] = value
    if seed is not None:
        out["random_seed"] = int(seed)
    return out


def _solver_info_from_config(config: dict, settings: dict) -> dict:
    """Solver_info for the training simulations — the truth solver, not an emulator.

    Same shape as the sensitivity runner's: the Settings popup's solver_info with
    the solver name and the CVODE step default filled in.
    """
    si = dict(config.get("solver_info") or {})
    si.setdefault("solver", config.get("solver") or settings.get("solver", "CVODE_myokit"))
    si.setdefault("MaximumStep", settings.get("MaximumStep", 0.0001))
    return si


def run(config: dict) -> dict:
    _ensure_ca_on_path()
    from emulators.emulator_trainer import EmulatorTrainer  # noqa: E402
    import ca_run_history  # noqa: E402  (CA output formats, one place)

    settings = config.get("settings", {})
    output_dir = config["output_dir"]
    os.makedirs(output_dir, exist_ok=True)
    file_prefix = config.get("file_prefix", "model")
    emu_dir = ca_run_history.emulator_dir(output_dir, file_prefix, config.get("obs_path"))

    inp = {
        "model_path": config["model_path"],
        "model_type": config.get("model_type", "cellml_only"),
        "file_prefix": file_prefix,
        "param_id_method": settings.get("param_id_method", "genetic_algorithm"),
        "params_for_id_path": config["params_path"],
        "param_id_obs_path": config["obs_path"],
        "param_id_output_dir": output_dir,
        "resources_dir": os.path.dirname(config["params_path"]),
        "sim_time": float(settings.get("sim_time", 2.0)),
        "pre_time": float(settings.get("pre_time", 0.0)),
        "dt": float(settings.get("dt", 0.01)),
        "solver_info": _solver_info_from_config(config, settings),
        "DEBUG": bool(settings.get("DEBUG", False)),
        "operation_funcs_external_path": config.get("operation_funcs_external_path"),
        "cost_funcs_external_path": config.get("cost_funcs_external_path"),
        # Training always runs the real solver; the trainer forces this off too,
        # but saying it here keeps the config honest about what is being run.
        "use_emulator": False,
        "do_emulation": True,
        "emulator_settings": _emulator_settings(settings, emu_dir, config.get("seed")),
    }

    samples = inp["emulator_settings"].get("num_train_samples", 128)
    print(
        f"Training an emulator from {samples} simulations "
        f"across {config.get('num_cores', 1)} rank(s)",
        flush=True,
    )

    trainer = EmulatorTrainer.init_from_dict(inp)
    bundle = trainer.train()

    # Only rank 0 fits and writes (CA returns None elsewhere), mirroring the Sobol
    # runner's rank-0-reports rule.
    rank = getattr(trainer, "rank", 0)
    if rank != 0 or bundle is None:
        return {"rank": rank}

    for label, score in zip(bundle.feature_labels, bundle.meta.get("feature_r2", [])):
        print(f"    held-out R2 {float(score):8.4f}   {label}", flush=True)
    print(META_MARKER + json.dumps({"emulator_dir": emu_dir}), flush=True)
    print(f"Emulator saved to {emu_dir}", flush=True)
    return {"rank": 0, "emulator_dir": emu_dir}


#: Rank variables the common launchers set. Read from the environment rather than by importing
#: mpi4py: this runs before CA is on the path, and CA's own rule is never to open MPI at module
#: scope (CA #396).
_RANK_VARS = ("OMPI_COMM_WORLD_RANK", "PMI_RANK", "PMIX_RANK", "SLURM_PROCID")


def mpi_rank() -> int:
    """This process's MPI rank, or 0 when it was not launched by a launcher we recognise."""
    for var in _RANK_VARS:
        value = os.environ.get(var)
        if value is not None:
            try:
                return int(value)
            except ValueError:
                continue
    return 0


def configure_logging(rank: int) -> None:
    """Give the root logger a handler, so Python never falls back to logging.lastResort.

    autoemulate logs through the library-safe pattern -- its own logger carries only a
    NullHandler -- so its records propagate to the root, where nothing was configured. Python
    then uses ``logging.lastResort``, which writes to ``sys.stderr``. Under ``mpiexec`` the
    non-zero ranks have theirs torn down, so every INFO line autoemulate emitted became a
    "--- Logging error --- ValueError: I/O operation on closed file" traceback in the run log:
    alarming, and about a message nobody needed.

    Rank 0 keeps its output, on this process's own stdout -- the stream the API already reads
    for the run log. Every other rank gets a NullHandler, which is the rank-0-reports rule the
    rest of this runner follows.

    Leaves an already-configured root alone: the caller's choice wins over this default.
    """
    root = logging.getLogger()
    if root.handlers:
        return
    if rank == 0:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))
        root.addHandler(handler)
        root.setLevel(logging.INFO)
    else:
        root.addHandler(logging.NullHandler())


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(f"{FAIL_MARKER} usage: emulator_runner.py <config.json>", flush=True)
        return 2
    config = json.loads(Path(argv[1]).read_text())
    configure_logging(mpi_rank())
    try:
        result = run(config)
    except Exception as exc:  # surface to the captured stdout for the UI
        print(f"{FAIL_MARKER} {_with_install_hint(exc)}", flush=True)
        traceback.print_exc()
        _abort_mpi()
        return 1
    if result.get("rank", 0) == 0:
        print(DONE_MARKER, flush=True)
    return 0


def _with_install_hint(exc: Exception) -> str:
    """CA's "autoemulate is not installed" message, plus which interpreter it means.

    CA names the package and the pip command; it cannot know that CUFLynx runs
    this in an interpreter the user picked in Settings, which is the part that
    makes the message actionable here.
    """
    text = str(exc)
    if "autoemulate" not in text:
        return text
    return (
        f"{text} Note that CUFLynx runs training in the Python chosen in Settings "
        f"({sys.executable}), so install it there."
    )


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
