"""Export the current CUFLynx study as a reproducible, standalone pipeline bundle.

CUFLynx's part is turning the GUI's settings into a circulatory_autogen
user_inputs dict (:func:`build_user_inputs`). circulatory_autogen writes the
bundle from it (:func:`write_bundle`, CA's ``write_pipeline_bundle``): the dated
``user_inputs_<yymmdd>.yaml``, ``run_pipeline.py``, ``plot_outputs.py`` +
``plot_utilities.py``, the model under ``generated_models/<prefix>/``, the
obs_data, params_for_id and user funcs under ``resources/``, and a trained
emulator the study uses under ``emulator/``. The bundle is a working dir of its
own: ``run_pipeline.py`` writes its results, study manifest and ``inputs/`` into
the bundle folder.
"""

from __future__ import annotations

from datetime import date

from ca_imports import ca_from, ensure_ca_path
from obs_data import INCLUDE_PREDICTION_ITEMS
from optimiser_budget import default_method, default_num_calls


def dated_suffix() -> str:
    return date.today().strftime("%y%m%d")


class ExportPipelineError(ValueError):
    """Bad export settings supplied by the client (maps to HTTP 422)."""


def _num(value, default, cast, field: str, unset=None):
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
        return cast(unset() if unset is not None else default)
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
    model_path: str,
    obs_path: str | None,
    params_for_id_path: str | None,
    user_func_paths: dict | None = None,
    calibration: dict | None,
    sensitivity: dict | None,
    uq: dict | None,
    enabled: dict | None,
    use_emulator: bool = False,
    emulator_dir: str | None = None,
    emulator_settings: dict | None = None,
    prediction_features: bool = False,
) -> dict:
    """Map the current CUFLynx settings to the user_inputs dict
    :func:`write_bundle` writes a bundle from.

    Paths are the study's own files, absolute: CA copies each into the bundle
    and rewrites the yaml to the bundled copy. ``user_func_paths`` is
    ``{config key: path}`` of the user funcs and an external_python model. The
    ``do_*`` keys gate the stages of ``run_pipeline.py``.

    The emulator: ``use_emulator`` makes the analyses evaluate one.
    ``emulator_dir`` is the trained bundle the study uses, which CA copies into
    the bundle; with ``use_emulator`` and none trained, the pipeline trains one
    first (``do_emulation``) into the bundle's own ``emulators/``.
    ``emulator_settings`` are the Emulator panel's training settings, written
    either way so turning on ``do_emulation`` trains with them.

    ``prediction_features`` is whether the libcuflynx in use supports
    ``sa_options.include_prediction_items``; the key is written only then, and
    only when the sensitivity settings ask for it.
    """
    calibration = calibration or {}
    sensitivity = sensitivity or {}
    uq = uq or {}
    enabled = enabled or {}

    optimiser_options = {
        # Unset: CA's own default for the method -- one population for the GA, the
        # smallest budget it runs. The yaml carries no DEBUG, so CA runs it off.
        "num_calls_to_function": _num(
            calibration.get("num_calls_to_function"),
            None, int, "num_calls_to_function",
            unset=lambda: default_num_calls(calibration.get("param_id_method")),
        ),
        "cost_convergence": _num(
            calibration.get("cost_convergence"), 0.0001, float, "cost_convergence"
        ),
        "max_patience": _num(calibration.get("max_patience"), 10, int, "max_patience"),
    }
    if calibration.get("cost_type"):
        optimiser_options["cost_type"] = calibration["cost_type"]

    settings = {k: v for k, v in (emulator_settings or {}).items() if k != "emulator_dir"}
    if use_emulator and emulator_dir:
        settings["emulator_dir"] = emulator_dir

    ui = {
        # --- general / model ---
        "file_prefix": file_prefix,
        "model_type": model_type,
        "model_path": model_path,
        # --- solver / sim ---
        "solver": solver,
        "solver_info": {**(solver_info or {}), "solver": solver},
        "dt": dt,
        "pre_time": pre_time,
        "sim_time": sim_time,
        # --- inputs ---
        **({"param_id_obs_path": obs_path} if obs_path else {}),
        **({"params_for_id_path": params_for_id_path} if params_for_id_path else {}),
        # User-authored funcs (CA #303, #383): a data_item or params_for_id entry
        # names its func by name, so the study does not run without the file.
        **dict(user_func_paths or {}),
        # --- parameter identification (calibration) ---
        "param_id_method": calibration.get("param_id_method") or default_method(),
        "do_ad": str(calibration.get("gradient_method", "FD")).upper() == "AD",
        "optimiser_options": optimiser_options,
        # --- sensitivity ---
        "sa_options": {
            "method": sensitivity.get("method") or "sobol",
            "sample_type": sensitivity.get("sample_type") or "saltelli",
            "num_samples": _num(sensitivity.get("num_samples"), 256, int, "num_samples"),
        },
        # --- UQ ---
        "UQ_options": {
            "num_steps": _num(uq.get("num_steps"), 1000, int, "num_steps"),
            "num_walkers": _num(uq.get("num_walkers"), 64, int, "num_walkers"),
            "cost_type": uq.get("cost_type") or "gaussian_MLE",
        },
        # --- emulator ---
        "do_emulation": bool(use_emulator and not emulator_dir),
        "use_emulator": bool(use_emulator),
        "emulator_settings": settings,
        # --- stages run_pipeline.py runs ---
        "do_simulation": bool(enabled.get("do_simulation", True)),
        "do_calibration": bool(enabled.get("do_calibration", False)),
        "do_sensitivity": bool(enabled.get("do_sensitivity", False)),
        "do_uq": bool(enabled.get("do_uq", False)),
        "do_ia": bool(enabled.get("do_ia", False)),
    }
    if prediction_features and sensitivity.get(INCLUDE_PREDICTION_ITEMS):
        ui["sa_options"][INCLUDE_PREDICTION_ITEMS] = True
    return ui


_GENERATOR = "scripts.generate_pipeline_script"


def _generator(*names):
    """Names from circulatory_autogen's bundle writer (``generate_pipeline_script``).

    Raises :class:`ca_imports.CaImportError` when the engine cannot be imported:
    the bundle's scripts are the engine's, and there is no CUFLynx copy to fall
    back to.
    """
    ensure_ca_path()  # the configured CA dir first, as every other CA call here does
    return ca_from(_GENERATOR, *names)


def render_pipeline_script() -> str:
    """The standalone pipeline driver, ``run_pipeline.py``: the engine's own."""
    return _generator("render_pipeline_script")()


PLOT_UTILITIES_NAME = "plot_utilities.py"
PLOTTING_SCRIPT_NAME = "plot_outputs.py"


def render_plot_utilities() -> str:
    """``plot_utilities.py``, the machinery the plotting script imports: the engine's."""
    return _generator("render_plot_utilities")()


def render_plotting_script(obs_data: dict | list | None = None) -> str:
    """``plot_outputs.py``, the script the user edits and runs: the engine's, with the
    best-fit panels named after ``obs_data``'s data_items when one is given."""
    return _generator("render_plotting_script")(obs_data)


def write_bundle(inp_data_dict: dict, export_dir: str) -> list[str]:
    """Write the bundle with circulatory_autogen's ``write_pipeline_bundle``.

    Returns the paths written, relative to ``export_dir``. Raises
    :class:`ExportPipelineError` when CA refuses the configuration.
    """
    write, bundle_error = _generator("write_pipeline_bundle", "PipelineBundleError")
    try:
        return list(write(inp_data_dict, export_dir))
    except bundle_error as exc:
        raise ExportPipelineError(str(exc)) from exc
