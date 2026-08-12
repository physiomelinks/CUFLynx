"""FastAPI backend for the CellML slider-visualisation app.

Endpoints
---------
GET  /api/health                       liveness probe
POST /api/models/upload                upload a .cellml file -> metadata
GET  /api/models/{model_id}/variables  classified variable lists
POST /api/simulate                     single run (circulatory_autogen helper)
POST /api/protocol/run                 multi-experiment protocol run
POST /api/obs_data/upload              load obs_data.json (protocol + overlays)
POST /api/params_for_id/upload         load params_for_id.csv -> slider specs

Simulation is delegated to circulatory_autogen via :mod:`engine`; parsing of
CellML metadata, obs_data and params_for_id is dependency-light so these routes
work (and are unit-tested) without Myokit installed.
"""

from __future__ import annotations

import errno
import json
import os
import shutil
import tempfile
import time
import uuid
from pathlib import Path

import yaml

from fastapi import FastAPI, File, HTTPException, Query, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from calibration import (
    calibration,
    list_python_interpreters,
    reset_python_cache,
    resolve_mpiexec,
)
from cellml_flatten import (
    CellMLFlattenError,
    flatten_cellml,
    has_imports,
    pick_main_cellml,
)
from cellml_meta import CellMLModel, CellMLParseError, parse_cellml
import mmt_protocol
import myokit_import
import omex_import
from aadc_check import aadc_status
from version import __version__
from compiler_check import compiler_status
from engine import SimulationError, engine, _circulatory_autogen_src
from examples import EXAMPLE_MODELS, media_type as example_media_type
from local_sensitivity import local_gradient_sources
import export_pipeline
from model_codegen import resolve_model_path, reset_cache as reset_codegen
from obs_data import ObsData, ObsDataError, parse_obs_data
from obs_options import get_obs_data_options, reset_cache as reset_obs_options
import obs_cost
import cost_gradient
import cost_sensitivity
from obs_series import compute_output_series
import params_json
from params_for_id import ParamsForIdError, parse_params_for_id
import saved_runs
from param_io import ParamIOError, load_param_values, save_param_values
from runtime_paths import default_python, frontend_dist, is_frozen, resources_dir
import settings_store
from solver_options import (
    ad_available,
    check_solver_info,
    filter_solver_info,
    get_analysis_options,
    get_param_id_methods,
    get_param_modifier_operations,
    get_param_prior_types,
    gradient_sources,
    get_solver_options,
    reset_cache as reset_solver_options,
)
from user_funcs import (
    UserFuncError,
    delete_user_func,
    external_path as user_func_path,
    external_paths as user_func_paths,
    read_user_funcs,
    save_user_func,
)
from sensitivity import sensitivity
from uq import uq

app = FastAPI(title="CUFLynx API", version=__version__)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

UPLOAD_DIR = Path(tempfile.gettempdir()) / "cuflynx_uploads"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

# Uploads outlive the session that made them on purpose: _get_model() re-derives
# a model from its .cellml after a --reload, and a calib_/sa_/uq_ run dir is read
# back for results. Nothing ever removed them, so a long-lived server accumulated
# every model, obs_data and run directory it had ever been given, in a temp dir
# the OS only clears at boot. Age them out at startup instead.
UPLOAD_TTL_DAYS = float(os.environ.get("CUFLYNX_UPLOAD_TTL_DAYS", "7"))


def prune_upload_dir(directory: Path = UPLOAD_DIR, ttl_days: float = UPLOAD_TTL_DAYS) -> int:
    """Delete uploads and run directories untouched for *ttl_days*.

    Returns the number of entries removed. A ttl of 0 or less disables the
    prune. Never raises: a temp dir we cannot tidy must not stop the server.
    """
    if ttl_days <= 0:
        return 0
    cutoff = time.time() - ttl_days * 86400
    removed = 0
    try:
        entries = list(directory.iterdir())
    except OSError:
        return 0
    for entry in entries:
        try:
            if entry.stat().st_mtime >= cutoff:
                continue
            if entry.is_dir():
                shutil.rmtree(entry, ignore_errors=True)
            else:
                entry.unlink(missing_ok=True)
            removed += 1
        except OSError:
            continue
    return removed


prune_upload_dir()


class _ModelRecord:
    def __init__(self, model_id: str, path: Path, meta: CellMLModel):
        self.model_id = model_id
        self.path = path
        self.meta = meta
        self.obs_data: ObsData | None = None
        # Raw input files persisted on disk for circulatory_autogen calibration.
        self.obs_path: Path | None = None
        self.params_path: Path | None = None


# In-memory registry of uploaded models (process-scoped session store).
_models: dict[str, _ModelRecord] = {}


def _get_model(model_id: str) -> _ModelRecord:
    record = _models.get(model_id)
    if record is None:
        # Recover from the persisted upload if the in-memory registry lost it
        # (e.g. a dev-server --reload wiped it). The CellML file still lives in
        # UPLOAD_DIR, so a parameter change / new plot can re-derive the model
        # and regenerate its python/casadi build instead of failing. obs_data /
        # params_for_id aren't restored (re-upload to run protocols / calibration).
        path = UPLOAD_DIR / f"{model_id}.cellml"
        if path.is_file():
            try:
                meta = parse_cellml(path.read_bytes())
            except CellMLParseError:
                meta = None
            if meta is not None:
                record = _ModelRecord(model_id, path, meta)
                _models[model_id] = record
                return record
        raise HTTPException(status_code=404, detail=f"model {model_id!r} not found")
    return record


def _validate_param_keys(params: dict) -> None:
    bad = [k for k in params if "/" not in k]
    if bad:
        raise HTTPException(
            status_code=422,
            detail=f"param names must be 'component/param' qnames; got {bad}",
        )


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------
class SimulateRequest(BaseModel):
    model_id: str
    params: dict[str, float] = Field(default_factory=dict)
    sim_time: float = 10.0
    pre_time: float = 0.0
    outputs: list[str] | None = None
    # Where the user's custom operation funcs live, so a data_item's operation can
    # be applied to produce its series_output overlay (issue #111). Empty -> only
    # CA's built-in operations are available.
    config_outputs_dir: str = ""

    # "Give me every output you can" rather than a specific list: unresolvable
    # names are skipped instead of failing the run (#150). Used when saving a run
    # so it covers plots that do not exist yet.
    best_effort_outputs: bool = False

class ProtocolRunRequest(BaseModel):
    model_id: str
    protocol_info: dict | None = None
    params: dict[str, float] = Field(default_factory=dict)
    outputs: list[str] | None = None
    config_outputs_dir: str = ""


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
@app.get("/api/health")
def health() -> dict:
    return {"status": "ok"}


# ---------------------------------------------------------------------------
# Runtime config — circulatory_autogen location
# ---------------------------------------------------------------------------
class ConfigRequest(BaseModel):
    # The circulatory_autogen directory (repo root or its `src`).
    #   omitted (None) -> leave unchanged   |   "" -> reset to the default
    # Omission must NOT reset it: the Settings popup saves solver choices with a
    # payload that carries no ca_dir, and treating that as "reset" silently
    # dropped the user's CA directory on every solver change. From source that
    # was invisible (the default is the sibling clone), but the packaged app has
    # no sibling — CA was lost and every non-Myokit backend died with
    # "No module named 'generators'".
    ca_dir: str | None = None
    # Backend solver selection. generated_model_format is CA's `model_type`
    # (cellml_only / python / casadi_python); solver must be compatible with it;
    # solver_info holds the per-solver tuning. Blank/empty => leave unchanged.
    generated_model_format: str = ""
    solver: str = ""
    solver_info: dict = Field(default_factory=dict)
    # Interpreter for calibration / sensitivity / UQ runs.
    #   omitted (None) -> leave unchanged   |   "" -> reset to the default
    # The default is the bundled interpreter (packaged) or the serving one (source),
    # so "" lets the user switch back to "Bundled" after picking an external venv.
    python_path: str | None = None
    # Global random seed for analysis runs (calibration / sensitivity / UQ). When
    # set, it makes CA's random processes (GA, multi-start sampling, Sobol/SALib
    # sampling, MCMC) reproducible. Default is no seed (non-deterministic).
    #   omitted (None) -> leave unchanged   |   "" -> clear (no seed)   |   int -> use it
    seed: int | str | None = None


# Global random seed for analysis runs, or None for non-deterministic (the default).
# Set via POST /api/config, persisted like ca_dir / python_path, and injected into
# every calibration / sensitivity / UQ run config so CA's random processes repeat.
_analysis_seed: int | None = None


def _parse_seed(value) -> int | None:
    """Coerce a seed from the config request. "" (or blank) clears it; an int (or an
    integer-valued string) sets it. Raises ValueError on anything else."""
    s = str(value).strip()
    if s == "":
        return None
    return int(s)


def _ca_src_from_dir(d: str) -> str:
    """Normalize a chosen CA directory to its importable `src` path: accept the
    repo root (append `src`) or a `src` dir directly."""
    p = Path(d).expanduser()
    return str(p / "src") if (p / "src").is_dir() else str(p)


def _set_analysis_python(path: str) -> None:
    """Point every analysis job manager -- and live simulation -- at ``path``.

    All three analysis managers spawn a runner script the same way, so they share
    one interpreter choice; keeping them in lockstep here avoids a per-manager
    setting the UI would have to expose three times.

    Live simulation is set from the same value (#167). It used to be the
    exception -- it ran in-process, in whatever interpreter started the app --
    which made "switch Python" true of analysis and false of the sliders. The
    engine restarts its worker when this changes, so the choice takes effect
    without a restart.

    Both caches are invalidated afterwards, because both answers depend on which
    interpreter this is: the interpreter list now always includes (and probes)
    the configured one, and the model formats include aadc_python only when AADC
    is importable *there*. Leaving either cached meant switching interpreters
    changed nothing visible until a restart.
    """
    calibration.python = path
    sensitivity.python = path
    uq.python = path
    engine.worker_python = path
    reset_python_cache()
    reset_solver_options()


def _restore_persisted_settings() -> None:
    """Re-apply the last-saved ca_dir / solver / interpreter at startup.

    Without this the packaged app forgets where circulatory_autogen and the
    analysis interpreter are every time it's launched — it has no sibling
    checkout to fall back on and no usable default interpreter.

    Best-effort: a stale path (CA moved, venv deleted) must not stop the app from
    starting, so invalid values are dropped and the user re-picks in Settings.
    """
    saved = settings_store.load()

    ca_dir = (saved.get("ca_dir") or "").strip()
    if ca_dir and os.path.isdir(ca_dir):
        os.environ["CIRCULATORY_AUTOGEN_SRC"] = _ca_src_from_dir(ca_dir)

    fmt = (saved.get("generated_model_format") or "").strip()
    if fmt:
        engine.model_type = fmt
    solver = (saved.get("solver") or "").strip()
    if solver:
        engine.solver = solver
    solver_info = saved.get("solver_info")
    if isinstance(solver_info, dict):
        si = dict(solver_info)
        if "dt" in si:
            try:
                engine.dt = float(si.pop("dt"))
            except (TypeError, ValueError):
                si.pop("dt", None)
        # Drop, don't reject: a config saved before a key became unsupported (or
        # before the solver was switched) must not stop the app from starting.
        # Rejection is for a *new* choice, in POST /api/config.
        engine.solver_info = filter_solver_info(engine.solver, si)
    if fmt or solver or solver_info:
        os.environ["CUFLYNX_MODEL_TYPE"] = engine.model_type
        os.environ["CUFLYNX_SOLVER"] = engine.solver
        os.environ["CUFLYNX_SOLVER_INFO"] = json.dumps(engine.solver_info)

    python_path = (saved.get("python_path") or "").strip()
    if python_path and os.path.isfile(python_path) and os.access(python_path, os.X_OK):
        _set_analysis_python(python_path)

    seed = saved.get("seed")
    if isinstance(seed, int) and not isinstance(seed, bool):
        global _analysis_seed
        _analysis_seed = seed


_restore_persisted_settings()


def _config_payload(output_dir: str = "") -> dict:
    """The config the UI reads. ``output_dir`` is the user's outputs directory,
    needed only because a user's own modifier funcs live under it."""
    src = _circulatory_autogen_src()
    p = Path(src)
    ca_dir = str(p.parent) if p.name == "src" else src
    opts = get_solver_options()
    return {
        "ca_dir": ca_dir,
        "ca_src": src,
        # `bool(src)` guard is load-bearing: when frozen and unconfigured, src is ""
        # and Path("").is_dir() is True (empty path -> cwd), which would wrongly
        # report CA as present and skip the first-run "pick a CA dir" prompt.
        "ca_exists": bool(src) and p.is_dir(),
        # Remembered interpreter for analysis runs (blank = none chosen yet).
        "python_path": calibration.python or "",
        # The interpreter "server default" actually resolves to (blank when
        # frozen: analysis then runs in the bundle, there is no external one).
        # Without this the client cannot tell that its "" choice came back as a
        # concrete path, so the picker jumped off "Server default" on reload —
        # and could say nothing about the default's MPI support, though it is as
        # probeable as any other interpreter.
        "python_default": default_python() or "",
        # Global random seed for analysis runs (null = none / non-deterministic).
        "seed": _analysis_seed,
        # Current backend solver selection (engine is the source of truth). dt is
        # carried in solver_info for the UI but stored separately on the engine.
        "generated_model_format": engine.model_type,
        "solver": engine.solver,
        "solver_info": {**engine.solver_info, "dt": engine.dt},
        # Capabilities for the settings UI + AD gating.
        **opts,
        "ad_available": ad_available(engine.model_type, opts),
        # Gradient sources (FD / AD / FSA) available for the current model, for the
        # calibration gradient-source menu — introspected from CA's do_ad/FSA rules.
        # The `requires_all_differentiable` gate (CasADi AD) is a *per-model* property
        # (every op the loaded obs_data uses must be @differentiable), and this route
        # is model-agnostic, so it can't apply that gate here — it passes the sources
        # through with their flags and the client gates them against its in-use
        # differentiability (App.vue `adAvailable`). Passing True keeps those sources
        # in the list rather than dropping them on the coarse whole-registry flag.
        # ...and the per-integrator suitability gate IS applied here (the selected
        # integrator is known): AD/FSA drop out for an unsuitable integrator (#298).
        # Local sensitivity implements its own gradients, and its AD path is
        # CasADi-specific -- so the calibration list above is not the right menu
        # for it. Offering AD for a backend whose AD this path cannot do meant
        # the run started and then refused (#122).
        "local_gradient_sources": local_gradient_sources(
            gradient_sources(
                engine.model_type, engine.solver, True, engine.solver_info.get("method"),
            ),
            engine.model_type,
        ),
        "gradient_sources": gradient_sources(
            engine.model_type, engine.solver, True, engine.solver_info.get("method"),
        ),
        # Myokit JIT-compiles models, so a missing C compiler breaks every
        # simulation. Surfaced here so the UI can warn up front rather than
        # letting the first run fail with an opaque 500 (matters most in the
        # packaged desktop build, which can't ship a compiler).
        "cpp_compiler": compiler_status(),
        # AADC (Matlogica) is optional, proprietary and licensed; the aadc_python
        # format is only offered when it is importable, so the UI needs to be
        # able to say why it is missing and how to get it (#122).
        "aadc": aadc_status(calibration.python),
        "packaged": is_frozen(),
        # Whether a matching MPI launcher is available for the current interpreter,
        # so the UI can warn *before* a num_cores>1 run silently drops to a single
        # core (build_command falls back when no mpiexec is found -- common on
        # Windows without MS-MPI). Tracks the selected interpreter: resolved the
        # same way the run does (see calibration.resolve_mpiexec).
        "mpiexec_available": resolve_mpiexec(calibration.python) is not None,
        # The params_for_id `prior` vocabulary, from CA's schema, so the params
        # editor can offer a picker instead of dropping the column (which
        # silently reverted every non-uniform prior to uniform).
        "param_prior_types": get_param_prior_types(),
        # The modifier `operation` vocabulary (CA's PARAM_MODIFIER_OPERATIONS),
        # so the editor's "create modifier parameter" menu tracks what CA can
        # actually run rather than hardcoding it.
        "param_modifier_operations": get_param_modifier_operations(
            output_dir=_user_func_base_dir(output_dir)
        ),
    }


@app.get("/api/config")
def get_config(output_dir: str = "") -> dict:
    """``output_dir`` is optional and only widens ``param_modifier_operations``
    to include the modifier funcs the user wrote under it."""
    return _config_payload(output_dir)


@app.post("/api/config")
def set_config(req: ConfigRequest) -> dict:
    """Point the backend at a circulatory_autogen directory at runtime.

    Subprocess runs (calibration / sensitivity / UQ) inherit this on their next
    launch. The in-process engine picks it up too, but because Python caches the
    CA modules after the first simulation, switching mid-session fully re-points
    the live-plot engine only after a restart.
    """
    # None => not mentioned in this request, so leave the CA dir alone (see
    # ConfigRequest). Only an explicit "" resets it to the default.
    if req.ca_dir is not None:
        d = req.ca_dir.strip()
        if d:
            if not os.path.isdir(d):
                raise HTTPException(status_code=422, detail=f"not a directory: {d}")
            os.environ["CIRCULATORY_AUTOGEN_SRC"] = _ca_src_from_dir(d)
        else:
            os.environ.pop("CIRCULATORY_AUTOGEN_SRC", None)


    # Interpreter for analysis runs. Shared by all three job managers.
    #   None  -> not in this request, leave unchanged
    #   ""    -> reset to the default (bundled when packaged, serving when source)
    #   path  -> validate + use that external interpreter
    if req.python_path is not None:
        python_path = req.python_path.strip()
        if python_path:
            if not (os.path.isfile(python_path) and os.access(python_path, os.X_OK)):
                raise HTTPException(
                    status_code=422,
                    detail=f"python interpreter not found or not executable: {python_path}",
                )
            _set_analysis_python(python_path)
        else:
            _set_analysis_python(default_python())

    # Solver selection is validated *after* the interpreter, because which model
    # formats exist depends on it: aadc_python is only offered when AADC can be
    # imported. Validating first meant a single request that set both the
    # interpreter and the format was checked against the previous interpreter and
    # rejected the format it had just enabled.
    # Backend solver selection. Validate against CA's schema (re-read against the
    # possibly-new CA dir), then store on the engine (the live-sim source of truth)
    # and export to env so subprocess runs inherit it.
    reset_solver_options()  # capabilities come from the (possibly new) CA
    solvers_by_format = get_solver_options()["solvers_by_format"]

    fmt = (req.generated_model_format or "").strip()
    if fmt:
        if fmt not in solvers_by_format:
            raise HTTPException(status_code=422, detail=f"unknown generated_model_format: {fmt}")
        engine.model_type = fmt
    solver = (req.solver or "").strip()
    if solver:
        valid = solvers_by_format.get(engine.model_type, [])
        if solver not in valid:
            raise HTTPException(
                status_code=422,
                detail=f"solver {solver!r} is not valid for {engine.model_type!r} (choose from {valid})",
            )
        engine.solver = solver
    if req.solver_info:
        si = dict(req.solver_info)
        # dt is edited in the same form but is engine-level (passed separately to
        # the solver), not a solver_info key; pull it out.
        if "dt" in si:
            try:
                engine.dt = float(si.pop("dt"))
            except (TypeError, ValueError):
                si.pop("dt", None)
        # Reject a setting this backend cannot honour rather than storing it to be
        # silently ignored at run time. Checked against the same schema that drives
        # the Settings form, so the form and the validation cannot disagree.
        try:
            check_solver_info(engine.solver, si)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        engine.solver_info = si

    # Global random seed for analysis runs.
    #   None  -> not in this request, leave unchanged
    #   ""    -> clear (no seed, non-deterministic)
    #   int   -> use that seed
    if req.seed is not None:
        try:
            seed = _parse_seed(req.seed)
        except ValueError:
            raise HTTPException(
                status_code=422, detail=f"seed must be an integer, got {req.seed!r}"
            ) from None
        global _analysis_seed
        _analysis_seed = seed

    os.environ["CUFLYNX_MODEL_TYPE"] = engine.model_type
    os.environ["CUFLYNX_SOLVER"] = engine.solver
    os.environ["CUFLYNX_SOLVER_INFO"] = json.dumps(engine.solver_info)

    engine.reset()  # drop cached compiled helpers so the next sim uses the new CA
    reset_codegen()  # regenerate python/casadi models against the new CA / format
    reset_obs_options()  # obs_data operation/cost options come from the new CA too

    payload = _config_payload()
    settings_store.save({k: payload[k] for k in settings_store.PERSISTED_KEYS if k in payload})
    return payload


# ---------------------------------------------------------------------------
# Pipeline export — reproducible script + dated user_inputs.yaml
# ---------------------------------------------------------------------------
class ExportPipelineRequest(BaseModel):
    model_id: str
    # Loaded CellML filename stem (preferred over the internal <model name>).
    file_prefix: str = ""
    sim_time: float = 2.0
    pre_time: float = 0.0
    calibration: dict = Field(default_factory=dict)
    sensitivity: dict = Field(default_factory=dict)
    uq: dict = Field(default_factory=dict)
    enabled: dict = Field(default_factory=dict)
    # Base dir for the export folder; blank => the temp uploads dir.
    config_outputs_dir: str = ""


class ExportPlottingRequest(BaseModel):
    # Where to write plot_outputs.py; blank => the temp uploads dir.
    config_outputs_dir: str = ""
    # The model whose obs_data names the panels. Optional: without it the script
    # still works, discovering its panels from the run directory at draw time --
    # it is just less pleasant to edit, which is the point of naming them.
    model_id: str | None = None


def _export_base_dir(configured: str) -> Path:
    configured = (configured or "").strip()
    if configured:
        if not os.path.isabs(configured):
            raise HTTPException(status_code=422, detail="config_outputs_dir must be an absolute path")
        return Path(configured)
    return UPLOAD_DIR


# What each common errno means for someone staring at a failed export. Keyed on
# errno rather than the message text, which is OS- and locale-dependent.
_FS_HINTS = {
    errno.EACCES: "The outputs directory is not writable — pick another, or change its permissions.",
    errno.EPERM: "The outputs directory is not writable — pick another, or change its permissions.",
    errno.EROFS: "That filesystem is read-only — pick an outputs directory you can write to.",
    errno.ENOSPC: "The disk is full.",
    errno.ENAMETOOLONG: (
        "The path is too long — pick a shorter outputs directory (the export folder name "
        "adds a dated suffix)."
    ),
    errno.ENOENT: "A parent directory does not exist.",
    errno.ENOTDIR: "A component of that path is a file, not a directory.",
    errno.EDQUOT: "The disk quota for that location is exhausted.",
}


def _fs_error_detail(exc: OSError, action: str, fallback: Path) -> str:
    """A failed filesystem operation, said in terms the user can act on.

    Names the path that actually failed (``OSError.filename``, which open/mkdir/
    copyfile all set) rather than the path we asked for, since with
    ``parents=True`` they differ — the failure is usually a parent. Issue #135:
    unhandled, these produced a body-less 500 whose only symptom was
    "AxiosError: Request failed with status code 500".
    """
    path = exc.filename or getattr(exc, "filename2", None) or str(fallback)
    reason = exc.strerror or str(exc) or type(exc).__name__
    hint = _FS_HINTS.get(exc.errno)
    detail = f"could not {action} {path}: {reason}"
    return f"{detail}. {hint}" if hint else f"{detail}."


def _fs_error(exc: OSError, action: str, fallback: Path, *, user_dir: bool) -> HTTPException:
    """Map an OSError to an HTTP error carrying the path and the OS message.

    422 when the client chose the location (its own ``config_outputs_dir`` is
    what needs changing), 500 when we picked it — the server's temp dir failing
    is not the client's to fix.
    """
    return HTTPException(
        status_code=422 if user_dir else 500,
        detail=_fs_error_detail(exc, action, fallback),
    )


@app.post("/api/export/pipeline")
def export_pipeline_route(req: ExportPipelineRequest) -> dict:
    """Write a self-contained, reproducible export folder: the dated
    user_inputs yaml + run_pipeline.py + plot_outputs.py + copies of the model /
    obs / params, all referenced by relative paths."""
    record = _get_model(req.model_id)
    suffix = export_pipeline.dated_suffix()
    user_dir = bool((req.config_outputs_dir or "").strip())
    export_dir = _export_base_dir(req.config_outputs_dir) / f"export_{suffix}"
    resources = export_dir / "resources"

    # Use the loaded CellML file's prefix (e.g. "3compartment"), not the internal
    # <model name> (often a generic "cardiovascularSystem"). The client passes it.
    file_prefix = req.file_prefix.strip() or record.meta.name or "model"
    # The model lives where circulatory_autogen resolves model_path:
    # generated_models/<prefix>/<prefix>.cellml. obs/params go in resources/.
    model_file = f"{file_prefix}.cellml"
    model_dir = export_dir / "generated_models" / file_prefix

    # An unwritable outputs dir, a full disk or a too-long path must say which
    # path failed and why, not become a body-less 500 (issue #135).
    obs_file = None
    params_file = None
    # Funcs the user wrote in the GUI, by CA config key -> filename in resources/.
    # An obs_data data_item names its operation and cost_type *by name*, so a
    # study using one of these is not reproducible unless the func travels with
    # it — the exported run would die on an operation CA has never heard of.
    user_func_files: dict[str, str] = {}
    try:
        resources.mkdir(parents=True, exist_ok=True)
        model_dir.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(record.path, model_dir / model_file)
        if record.obs_path is not None:
            obs_file = "obs_data.json"
            shutil.copyfile(record.obs_path, resources / obs_file)
        if record.params_path is not None:
            # Keep the stored suffix: CA branches CSV-vs-JSON on it, so an
            # exported JSON doc renamed to .csv would be misparsed downstream.
            params_file = "params_for_id" + Path(record.params_path).suffix
            shutil.copyfile(record.params_path, resources / params_file)
        for key, src in user_func_paths(req.config_outputs_dir or None).items():
            # Keep CA's own filenames: the export is meant to be readable and
            # handed to circulatory_autogen directly.
            name = Path(src).name
            shutil.copyfile(src, resources / name)
            user_func_files[key] = name
    except OSError as exc:
        raise _fs_error(exc, "write the export to", export_dir, user_dir=user_dir) from exc

    try:
        user_inputs = export_pipeline.build_user_inputs(
            file_prefix=file_prefix,
            model_type=engine.model_type,
            solver=engine.solver,
            solver_info=dict(engine.solver_info),
            dt=engine.dt,
            pre_time=req.pre_time,
            sim_time=req.sim_time,
            model_file=model_file,
            obs_file=obs_file,
            params_for_id_file=params_file,
            user_func_files=user_func_files,
            calibration=req.calibration,
            sensitivity=req.sensitivity,
            uq=req.uq,
            enabled=req.enabled,
        )
    except export_pipeline.ExportPipelineError as exc:
        # A malformed setting is the client's to fix, so report it as such: an
        # unhandled error here surfaces as a bare 500 with no detail, which is
        # all the user saw in issue #133.
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    yaml_name = f"user_inputs_{suffix}.yaml"
    try:
        with open(export_dir / yaml_name, "w") as fh:
            yaml.safe_dump(user_inputs, fh, default_flow_style=False, sort_keys=False)
        (export_dir / "run_pipeline.py").write_text(
            export_pipeline.render_pipeline_script(), encoding="utf-8"
        )
        (export_dir / export_pipeline.PLOT_UTILITIES_NAME).write_text(
            export_pipeline.render_plot_utilities(), encoding="utf-8"
        )
        (export_dir / export_pipeline.PLOTTING_SCRIPT_NAME).write_text(
            export_pipeline.render_plotting_script(
                {"data_items": record.obs_data.data_items} if record.obs_data else None
            ),
            encoding="utf-8",
        )
    except OSError as exc:
        raise _fs_error(exc, "write the export to", export_dir, user_dir=user_dir) from exc

    # Every file actually written, so the UI's "what shipped" list matches the
    # folder. plot_utilities.py used to be written and not listed, which reads
    # as a bundle missing the module its plotting script imports.
    files = [
        yaml_name,
        "run_pipeline.py",
        export_pipeline.PLOTTING_SCRIPT_NAME,
        export_pipeline.PLOT_UTILITIES_NAME,
        f"generated_models/{file_prefix}/{model_file}",
    ]
    if obs_file:
        files.append(f"resources/{obs_file}")
    if params_file:
        files.append(f"resources/{params_file}")
    files.extend(f"resources/{name}" for name in sorted(user_func_files.values()))
    return {"export_dir": str(export_dir), "files": files}


@app.post("/api/export/plotting")
def export_plotting_route(req: ExportPlottingRequest) -> dict:
    """Write just the plotting script (regenerates output/progress/analysis plots
    from a pipeline's output data)."""
    base = _export_base_dir(req.config_outputs_dir)
    path = base / export_pipeline.PLOTTING_SCRIPT_NAME
    utilities = base / export_pipeline.PLOT_UTILITIES_NAME
    obs_doc = None
    record = _models.get(req.model_id) if req.model_id else None
    if record is not None and record.obs_data is not None:
        obs_doc = {"data_items": record.obs_data.data_items}
    try:
        base.mkdir(parents=True, exist_ok=True)
        # Both, always: plot_outputs imports plot_utilities, so one without the
        # other is a script that cannot start.
        utilities.write_text(export_pipeline.render_plot_utilities(), encoding="utf-8")
        path.write_text(export_pipeline.render_plotting_script(obs_doc), encoding="utf-8")
    except OSError as exc:
        raise _fs_error(
            exc, "write the plotting script to", path,
            user_dir=bool((req.config_outputs_dir or "").strip()),
        ) from exc
    return {"path": str(path)}


@app.get("/api/fs/list")
def fs_list(
    path: str | None = Query(default=None), dirs_only: bool = False
) -> dict:
    """List a server-side directory for the in-app file/folder browser.

    This is a localhost tool, so the backend filesystem is the user's own. Used
    to pick an absolute Python interpreter path and the calibration outputs dir.
    Defaults to the user's home directory when no path is given.
    """
    base = Path(path).expanduser() if path else Path.home()
    try:
        base = base.resolve()
    except OSError as exc:
        raise HTTPException(status_code=400, detail=f"invalid path: {path}") from exc
    if not base.is_dir():
        raise HTTPException(status_code=404, detail=f"not a directory: {base}")
    try:
        children = list(base.iterdir())
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc

    entries = []
    for child in children:
        try:
            is_dir = child.is_dir()
        except OSError:
            continue  # broken symlink / unreadable — skip
        if dirs_only and not is_dir:
            continue
        entries.append({"name": child.name, "path": str(child), "is_dir": is_dir})
    entries.sort(key=lambda e: (not e["is_dir"], e["name"].lower()))

    parent = str(base.parent)
    return {
        "path": str(base),
        "parent": None if parent == str(base) else parent,
        "entries": entries,
    }


class MkdirRequest(BaseModel):
    parent: str
    name: str


@app.post("/api/fs/mkdir")
def fs_mkdir(req: MkdirRequest) -> dict:
    """Create a new folder under ``parent`` for the file/folder browser (e.g. to
    make a fresh outputs directory). Localhost tool — see ``fs_list``."""
    name = (req.name or "").strip()
    if not name or name in (".", "..") or "/" in name or "\\" in name:
        raise HTTPException(status_code=422, detail="invalid folder name")
    base = Path(req.parent).expanduser() if req.parent else Path.home()
    try:
        base = base.resolve()
    except OSError as exc:
        raise HTTPException(status_code=400, detail=f"invalid path: {req.parent}") from exc
    if not base.is_dir():
        raise HTTPException(status_code=404, detail=f"not a directory: {base}")
    target = base / name
    try:
        target.mkdir(parents=False, exist_ok=False)
    except FileExistsError as exc:
        raise HTTPException(status_code=409, detail="folder already exists") from exc
    except OSError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"path": str(target)}


@app.get("/api/examples/{name}")
def get_example_model(name: str) -> FileResponse:
    """Serve a bundled example study by its logical name.

    The Start dialog loads the returned archive through the normal .omex upload
    flow, so no separate ingest path is needed -- and the example arrives with
    its obs_data and params_for_id, which a loose CellML could not carry.
    """
    filename = EXAMPLE_MODELS.get(name)
    if filename is None:
        raise HTTPException(status_code=404, detail=f"unknown example model: {name}")
    path = resources_dir() / filename
    if not path.is_file():
        raise HTTPException(status_code=404, detail=f"example model file missing: {filename}")
    return FileResponse(path, media_type=example_media_type(filename), filename=filename)


def _obs_data_document(record, protocol_info=None) -> dict | None:
    """The loaded obs_data as CA's parser wants it: one dict with protocol_info.

    obs_cost hands this to CA so the cost is computed by the same code the
    calibration runs, rather than reproduced from the data_items here.
    """
    obs = getattr(record, "obs_data", None)
    if obs is None:
        return None
    proto = protocol_info if protocol_info is not None else obs.protocol_info
    if proto is None:
        return None
    return {
        "protocol_info": proto,
        "data_items": obs.data_items,
        "prediction_items": obs.prediction_items,
    }


def _with_obs_operands(outputs: list[str], record) -> list[str]:
    """``outputs`` plus every operand the loaded obs_data scores on."""
    obs = getattr(record, "obs_data", None)
    if obs is None:
        return outputs
    wanted = list(outputs)
    seen = set(wanted)
    for item in obs.data_items:
        if not isinstance(item, dict):
            continue
        for operand in item.get("operands") or []:
            if operand not in seen:
                seen.add(operand)
                wanted.append(operand)
    return wanted


def _protocol_from_mmt(data: bytes, filename: str, out_dir: str) -> dict:
    """The .mmt's ``[[protocol]]`` as an obs_data document, ready to adopt.

    The model import takes the ``[[model]]`` section alone, on purpose: the
    protocol belongs in obs_data, and baking Myokit's stimulus into the exported
    CellML would give the model two sources of pacing that disagree. That left
    the user retyping a protocol they had already written, so it is converted
    here and offered to the client.

    Offered, not applied. A model is often dropped alongside an obs_data the user
    wrote themselves, and silently replacing that would be worse than not
    converting at all -- so the decision is the client's, and this only reports
    what is available. A protocol that cannot be converted returns its reason
    rather than nothing: "no protocol appeared" is a question, and the answer is
    usually a one-line fact about the file.
    """
    stem = Path(filename).stem or "model"
    obs_name = f"{stem}_obs_data.json"
    try:
        info, notes = mmt_protocol.protocol_info_from_mmt(data, filename=filename)
    except mmt_protocol.MmtProtocolError as exc:
        return {"filename": obs_name, "obs_data": None, "notes": [], "reason": str(exc)}

    # data_items are the user's to write -- what the model should be measured
    # against is not in the .mmt. An empty list is a valid obs_data document, and
    # is enough to run the protocol.
    obs_data = {"protocol_info": info, "data_items": []}

    # Keep a file beside the converted CellML, for the same reason: a conversion
    # that existed only in memory would be invisible and unreproducible. Never
    # overwrite -- an obs_data already on disk may hold hand-written data_items
    # that nothing here could reconstruct.
    saved = None
    if out_dir:
        try:
            target = Path(out_dir) / obs_name
            if target.exists():
                notes = [*notes, f"{target} already exists and was left alone."]
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(json.dumps(obs_data, indent=4) + "\n", encoding="utf-8")
                saved = str(target)
        except OSError:
            # Keeping a copy is a convenience; failing to is no reason to
            # withhold a protocol that converted.
            saved = None

    return {"filename": obs_name, "obs_data": obs_data, "notes": notes, "path": saved, "reason": None}


@app.post("/api/models/upload")
async def upload_model(
    file: UploadFile | None = None,
    files: list[UploadFile] = File(default_factory=list),
    output_dir: str | None = Query(default=None),
) -> dict:
    """Upload a CellML model. Accepts either a single self-contained ``.cellml``
    (``file``, back-compatible) or a bundle of files (``files``): a non-flattened
    main model plus the sister files it imports. A non-flattened / CellML 1.1
    bundle is resolved and flattened to one self-contained CellML 2.0 document
    before it is saved, so the rest of the pipeline sees a flat model as usual.
    """
    uploads = list(files) + ([file] if file is not None else [])
    if not uploads:
        raise HTTPException(status_code=422, detail="no file uploaded")

    raw_by_name: dict[str, bytes] = {}
    for up in uploads:
        raw_by_name[up.filename or f"model_{len(raw_by_name)}.cellml"] = await up.read()

    single = len(raw_by_name) == 1
    only_name, only_bytes = next(iter(raw_by_name.items()))

    # A Myokit model is converted to CellML on the way in (#27), so everything
    # downstream -- the metadata parser, params_for_id naming, the exported
    # pipeline, CA itself -- keeps seeing the CellML it already expects.
    converted_from = None
    converted_path = None
    protocol: dict | None = None
    if single and (
        myokit_import.is_myokit_filename(only_name) or myokit_import.looks_like_myokit(only_bytes)
    ):
        out_base = _user_func_base_dir(output_dir or "")
        mmt_bytes = only_bytes
        try:
            only_bytes, converted_path = myokit_import.cellml_from_myokit(
                only_bytes,
                filename=only_name,
                out_dir=out_base,
            )
        except myokit_import.MyokitImportError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        converted_from = only_name
        raw_by_name = {Path(only_name).stem + ".cellml": only_bytes}
        # The [[protocol]] section the model import deliberately leaves behind.
        # Offered rather than applied: the client decides, because a model
        # dropped alongside an obs_data the user wrote must not be overridden.
        protocol = _protocol_from_mmt(mmt_bytes, only_name, out_base)

    if single and not has_imports(only_bytes):
        # Self-contained single file: save as-is (unchanged behaviour).
        raw = only_bytes
    else:
        # A main model + sisters (or a single file that itself imports): resolve
        # imports and flatten to one CellML 2.0 document. Write the bundle to a
        # temp dir so libCellML resolves the sisters by their relative hrefs.
        try:
            main_name = pick_main_cellml(raw_by_name)
            with tempfile.TemporaryDirectory() as td:
                for name, data in raw_by_name.items():
                    (Path(td) / os.path.basename(name)).write_bytes(data)
                flat = flatten_cellml(str(Path(td) / os.path.basename(main_name)), td)
            raw = flat.encode("utf-8")
        except CellMLFlattenError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    try:
        meta = parse_cellml(raw)
    except CellMLParseError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    model_id = uuid.uuid4().hex
    path = UPLOAD_DIR / f"{model_id}.cellml"
    path.write_bytes(raw)
    _models[model_id] = _ModelRecord(model_id, path, meta)

    return {
        "model_id": model_id,
        "name": meta.name,
        "variable_count": meta.variable_count,
        "params": meta.params,
        "odes": meta.odes,
        # Set when a Myokit model was converted on the way in (#27), so the UI can
        # say the model it is showing is not the file that was dropped.
        "converted_from": converted_from,
        "converted_cellml_path": converted_path,
        # The .mmt's [[protocol]] section as obs_data, for the client to adopt if
        # it has no obs_data of its own. None for CellML, and for a .mmt whose
        # protocol cannot be converted -- in which case `reason` says why.
        "protocol_obs_data": protocol,
    }


@app.post("/api/omex/upload")
async def upload_omex(
    file: UploadFile = File(...),
    output_dir: str | None = Query(default=None),
) -> dict:
    """Load a whole COMBINE archive: model + obs_data + params_for_id (#149).

    Dropped on any of the import boxes, because an archive is not "an obs_data
    file" or "a params file" -- it is the study, and making the user unzip it and
    drop three files in the right order is the thing this removes.

    Each part is loaded through the same code path a dropped file would take, so
    an archive cannot behave differently from its own contents.
    """
    data = await file.read()
    try:
        parts = omex_import.unpack(data)
    except omex_import.OmexImportError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    out_dir = _user_func_base_dir(output_dir or "")

    # The model first: obs_data and params attach to it.
    raw_by_name = parts["cellml"]

    # A Myokit model in the archive goes through the same conversion a dropped
    # .mmt does (#27), including the offer of its [[protocol]] as an obs_data --
    # so an archive built around a .mmt behaves like the file it contains rather
    # than like a lesser kind of study.
    converted_from = None
    protocol = None
    if len(raw_by_name) == 1 and myokit_import.is_myokit_filename(parts["master"] or ""):
        only_name = parts["master"]
        mmt_bytes = raw_by_name[only_name]
        try:
            cellml_bytes, _saved = myokit_import.cellml_from_myokit(
                mmt_bytes, filename=only_name, out_dir=out_dir
            )
        except myokit_import.MyokitImportError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        converted_from = only_name
        protocol = _protocol_from_mmt(mmt_bytes, only_name, out_dir)
        raw_by_name = {Path(only_name).stem + ".cellml": cellml_bytes}
        parts = {**parts, "master": Path(only_name).stem + ".cellml"}

    if len(raw_by_name) == 1 and not has_imports(next(iter(raw_by_name.values()))):
        raw = next(iter(raw_by_name.values()))
    else:
        try:
            main_name = parts["master"] or pick_main_cellml(raw_by_name)
            with tempfile.TemporaryDirectory() as td:
                for name, blob in raw_by_name.items():
                    (Path(td) / os.path.basename(name)).write_bytes(blob)
                raw = flatten_cellml(
                    str(Path(td) / os.path.basename(main_name)), td
                ).encode("utf-8")
        except CellMLFlattenError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    try:
        meta = parse_cellml(raw)
    except CellMLParseError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    model_id = uuid.uuid4().hex
    path = UPLOAD_DIR / f"{model_id}.cellml"
    path.write_bytes(raw)
    _models[model_id] = _ModelRecord(model_id, path, meta)

    result = {
        "model_id": model_id,
        "name": meta.name,
        "variable_count": meta.variable_count,
        "params": meta.params,
        "odes": meta.odes,
        "model_filename": parts["master"],
        "obs_data": None,
        "params_for_id": None,
        # Where PhLynx's editor state was kept, so the archive round-trips (#149).
        "module_config_path": None,
        # Set when the archive's model was a .mmt (#27), same fields the
        # single-file upload returns so the UI needs no second code path.
        "converted_from": converted_from,
        "protocol_obs_data": protocol,
    }

    # obs_data and params_for_id are optional: an archive with only a model is a
    # perfectly good archive, and refusing it would be worse than loading what is
    # there. A part that fails to parse is reported without losing the rest.
    if parts["obs"]:
        name, blob = parts["obs"]
        try:
            parsed = parse_obs_data(json.loads(blob))
            _models[model_id].obs_data = parsed
            obs_path = UPLOAD_DIR / f"{model_id}_obs_data.json"
            obs_path.write_bytes(blob)
            _models[model_id].obs_path = obs_path
            result["obs_data"] = {
                "filename": name,
                **parsed.summary(),
                "data_items": parsed.data_items,
                "prediction_items": parsed.prediction_items,
                "protocol_info": parsed.protocol_info,
            }
        except (ValueError, ObsDataError) as exc:
            result["obs_data"] = {"filename": name, "error": str(exc)}

    if parts["params"]:
        name, blob = parts["params"]
        try:
            entries = parse_params_for_id(blob, meta.initial_values)
            _models[model_id].params_path = _save_params_file(model_id, blob)
            result["params_for_id"] = {
                "filename": name,
                "params": [e.as_dict() for e in entries],
            }
        except ParamsForIdError as exc:
            result["params_for_id"] = {"filename": name, "error": str(exc)}

    if parts["module_config"]:
        _name, blob = parts["module_config"]
        # Beside the model in `generated_models/<prefix>/`, not among the run
        # outputs: this is PhLynx's editor state for that model, not a result of
        # anything. Same layout the export bundle uses, so the archive round-trips
        # into a folder CA already understands.
        result["module_config_path"] = omex_import.save_module_config(
            blob, _model_dir(out_dir, Path(parts["master"] or "").stem or meta.name)
        )

    # An obs_data in the archive is the author's own and always wins; only when
    # there is none does the .mmt's protocol become the study's protocol.
    if result["obs_data"] is None and protocol and protocol.get("obs_data"):
        try:
            parsed = parse_obs_data(protocol["obs_data"])
        except (ValueError, ObsDataError):
            parsed = None
        if parsed is not None:
            _models[model_id].obs_data = parsed
            obs_path = UPLOAD_DIR / f"{model_id}_obs_data.json"
            obs_path.write_text(json.dumps(protocol["obs_data"], indent=4), encoding="utf-8")
            _models[model_id].obs_path = obs_path
            result["obs_data"] = {
                "filename": protocol["filename"],
                **parsed.summary(),
                "data_items": parsed.data_items,
                "prediction_items": parsed.prediction_items,
                "protocol_info": parsed.protocol_info,
                "derived_from_mmt": True,
            }

    return result


@app.get("/api/models/{model_id}/variables")
def get_variables(model_id: str) -> dict:
    record = _get_model(model_id)
    m = record.meta
    return {
        "params": m.params,
        "odes": m.odes,
        "algebraic": m.algebraic,
        "all_names": m.all_names,
        "initial_values": m.initial_values,
        # qname -> CellML units identifier, used to label plot axes (#125).
        "units": m.units,
    }


def _single_run_cost(record, result: dict, output_dir) -> dict | None:
    """What a single run's parameters cost against the loaded obs_data (#159).

    Its own function because the cost-sensitivity path differences exactly this
    (#188): a gradient of a cost computed even slightly differently from the one
    on screen would rank parameters against a number the user cannot see.
    """
    return obs_cost.evaluate(
        record.obs_data.data_items,
        {0: {**result.get("outputs", {}), "time": result.get("time", [])}},
        output_dir,
        obs_data=_obs_data_document(record),
        dt=engine.dt,
    )


def _protocol_run_cost(record, result: dict, protocol_info, output_dir) -> dict | None:
    """The same, for a protocol run's per-experiment segments.

    `time` is an operand of any windowed or peak-timing operation, and it is
    returned beside the outputs rather than in them -- so it is folded in here,
    or every such observable goes unscored. Keyed by (experiment, subexperiment)
    where the run kept its segments, which is what a data_item names and what CA
    scores against (#181); the per-experiment traces stay as a fallback for a
    payload without them.
    """
    scored_by = {
        e: {**exp.get("outputs", {}), "time": exp.get("time", [])}
        for e, exp in enumerate(result.get("experiments", []))
    }
    for sub in result.get("subexperiments") or []:
        scored_by[(sub["experiment_idx"], sub["subexperiment_idx"])] = sub.get(
            "outputs", {}
        )
    return obs_cost.evaluate(
        record.obs_data.data_items, scored_by, output_dir,
        obs_data=_obs_data_document(record, protocol_info),
        dt=engine.dt,
    )


@app.post("/api/simulate")
def simulate(req: SimulateRequest) -> dict:
    record = _get_model(req.model_id)
    _validate_param_keys(req.params)
    outputs = req.outputs or record.meta.odes
    try:
        # Resolve the path for the backend the *live* run will actually use: the
        # engine falls back when the configured format cannot run in-process
        # (#122), and a model generated for one backend is not readable by
        # another -- a generated .py handed to Myokit fails as invalid XML.
        live_type, live_solver, _fell_back = engine.live_backend()
        model_path = resolve_model_path(str(record.path), live_type, model_id=req.model_id)
        result = engine.simulate(
            model_id=req.model_id,
            model_path=model_path,
            params=req.params,
            sim_time=req.sim_time,
            pre_time=req.pre_time,
            outputs=outputs,
            best_effort_outputs=req.best_effort_outputs,
        )
    except SimulationError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 - anything else still owes a reason
        raise HTTPException(
            status_code=500, detail=engine.describe_exception(exc)
        ) from exc

    # Per data_item, the operation's series_output (transformed) series so the
    # Output plots overlay matches CA's saved figures (issue #111).
    if record.obs_data is not None:
        output_dir = _user_func_base_dir(req.config_outputs_dir)
        result["output_series"] = compute_output_series(
            record.obs_data.data_items, result.get("outputs", {}), output_dir
        )
        result["cost"] = _single_run_cost(record, result, output_dir)
    return result


@app.post("/api/protocol/run")
def protocol_run(req: ProtocolRunRequest) -> dict:
    record = _get_model(req.model_id)
    _validate_param_keys(req.params)

    protocol_info = req.protocol_info
    if protocol_info is None and record.obs_data is not None:
        protocol_info = record.obs_data.protocol_info
    if protocol_info is None:
        raise HTTPException(
            status_code=422,
            detail="no protocol_info supplied and no obs_data loaded for this model",
        )

    outputs = req.outputs or (record.meta.odes + record.meta.algebraic)
    # Every operand an obs_data item scores on, so the cost is over all of them
    # and not only the ones that happen to be plotted (#159). Asking for a few
    # extra series is cheap; a cost that silently omits half the observables
    # looks like a better fit than it is.
    outputs = _with_obs_operands(outputs, record)
    try:
        live_type, live_solver, _fell_back = engine.live_backend()
        model_path = resolve_model_path(str(record.path), live_type, model_id=req.model_id)
        result = engine.run_protocol(
            model_id=req.model_id,
            model_path=model_path,
            protocol_info=protocol_info,
            params=req.params,
            outputs=outputs,
        )
    except SimulationError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 - anything else still owes a reason
        # Model compilation, unit conversion and CA-internal failures all land
        # here. Left uncaught they became a bodyless 500, which is how issue
        # #138's "Request failed with status code 500" reached the browser.
        raise HTTPException(
            status_code=500, detail=engine.describe_exception(exc)
        ) from exc

    # Per experiment, the series_output (transformed) series for each data_item
    # scoped to that experiment, keyed by its global data_item index so the
    # frontend can attach it to the matching overlay (issue #111).
    if record.obs_data is not None:
        output_dir = _user_func_base_dir(req.config_outputs_dir)
        items = record.obs_data.data_items
        for e, exp in enumerate(result.get("experiments", [])):
            scoped = [
                (idx, it)
                for idx, it in enumerate(items)
                if isinstance(it, dict) and it.get("experiment_idx", 0) == e
            ]
            local = compute_output_series(
                [it for _, it in scoped], exp.get("outputs", {}), output_dir
            )
            exp["output_series"] = {scoped[k][0]: v for k, v in local.items()}
        # What these parameters cost against the loaded data (#159). From this
        # run, not another: scoring a slider move must not double the work it
        # already took to draw it.
        result["cost"] = _protocol_run_cost(record, result, protocol_info, output_dir)
    return result


class CostSensitivityRequest(BaseModel):
    model_id: str
    params: dict[str, float] = Field(default_factory=dict)
    # Which parameters to differentiate, in the order the rows should come back.
    # Defaults to every parameter supplied; the client narrows it so a model with
    # thirty sliders does not pay for sixty simulations it did not ask for.
    param_names: list[str] | None = None
    # The slider's [min, max] per parameter. Used only where the parameter sits
    # at exactly 0, which has no scale of its own to step or normalise by.
    bounds: dict[str, list[float]] | None = None
    rel_step: float = cost_sensitivity.DEFAULT_REL_STEP
    # The same run description the displayed run used, so the base cost here is
    # the number the panel is showing rather than one from a different run.
    sim_time: float = 10.0
    pre_time: float = 0.0
    outputs: list[str] | None = None
    protocol_info: dict | None = None
    config_outputs_dir: str = ""
    # Modifier sliders, differenced in θ: [{name, anchor, targets, operation,
    # baselines: {qname: baseline}, value: θ, bounds: [θmin, θmax]}]. Their
    # targets are excluded from param_names by the client (and re-excluded by
    # cost_sensitivity.evaluate) -- they are the modifier's to move.
    modifiers: list[dict] | None = None


@app.post("/api/cost_sensitivity")
def cost_sensitivity_route(req: CostSensitivityRequest) -> dict:
    """``d ln(cost)/d ln(p)`` per parameter, about the current slider values (#188).

    Opt-in, and priced accordingly: 2M+1 simulations for M parameters. The runs
    go through the *live* engine and are scored by the same two helpers the
    displayed cost uses, so the gradient is of the cost on screen -- see
    :mod:`cost_sensitivity` for why this differences rather than calling CA's
    analytic ``get_gradient``.
    """
    record = _get_model(req.model_id)
    _validate_param_keys(req.params)
    if record.obs_data is None:
        raise HTTPException(
            status_code=422,
            detail="no obs_data is loaded, so there is no cost to be sensitive to",
        )

    output_dir = _user_func_base_dir(req.config_outputs_dir)
    protocol_info = req.protocol_info
    if protocol_info is None:
        protocol_info = record.obs_data.protocol_info

    try:
        live_type, live_solver, _fell_back = engine.live_backend()
        model_path = resolve_model_path(str(record.path), live_type, model_id=req.model_id)
    except Exception as exc:  # noqa: BLE001 - a model that cannot be resolved owes a reason
        raise HTTPException(
            status_code=500, detail=engine.describe_exception(exc)
        ) from exc

    # Deliberately the *same* run each endpoint above makes, down to which
    # outputs are requested: a superset would score observables the panel's cost
    # does not, and the gradient would then belong to a different number.
    def cost_at(params: dict) -> dict | None:
        if protocol_info is not None:
            outputs = req.outputs or (record.meta.odes + record.meta.algebraic)
            result = engine.run_protocol(
                model_id=req.model_id,
                model_path=model_path,
                protocol_info=protocol_info,
                params=params,
                outputs=_with_obs_operands(outputs, record),
            )
            return _protocol_run_cost(record, result, protocol_info, output_dir)
        result = engine.simulate(
            model_id=req.model_id,
            model_path=model_path,
            params=params,
            sim_time=req.sim_time,
            pre_time=req.pre_time,
            outputs=req.outputs or record.meta.odes,
        )
        return _single_run_cost(record, result, output_dir)

    # The sensitivity solve first: enabling FSA/AD makes the forward solve carry
    # its own derivatives, so one run gives the cost and dJ/dp together -- about
    # ten times cheaper than 2M+1 differenced solves, and resolvable where
    # differencing is not (a parameter the cost barely depends on comes back with
    # an arbitrary sign from a difference quotient; see cost_gradient).
    try:
        return cost_gradient.evaluate(
            req.params,
            model_path=model_path,
            model_type=live_type,
            # The chosen solver must ride along: solver_info alone has no
            # `solver` key, and CA then falls through to its OpenCOR helper --
            # which is None here, and which this project must never use.
            solver_info={**(engine.solver_info or {}), "solver": live_solver},
            dt=engine.dt,
            obs_data=_obs_data_document(record, protocol_info),
            sim_time=req.sim_time,
            pre_time=req.pre_time,
            param_names=req.param_names,
            bounds=req.bounds,
            output_dir=output_dir,
            modifiers=req.modifiers,
        )
    except cost_gradient.GradientUnavailable as exc:
        # Not an error: differencing works on every backend the sliders work on.
        # The reason travels with the result so the panel can say which it used,
        # and is logged because a fallback that is really *our* bug reads exactly
        # like a backend that cannot do it -- which is how a list/dict mix-up in
        # the bounds silently differenced every request that carried them.
        fallback_reason = str(exc)
        print(f"[cost_sensitivity] no analytic gradient, differencing instead: "
              f"{fallback_reason}", flush=True)

    try:
        result = cost_sensitivity.evaluate(
            req.params,
            cost_at,
            param_names=req.param_names,
            bounds=req.bounds,
            rel_step=req.rel_step,
            modifiers=req.modifiers,
        )
        result["analytic"] = False
        result["fallback_reason"] = fallback_reason
        return result
    except SimulationError as exc:
        # Only the *base* run reaches here: a perturbed one that fails is that
        # parameter's reason, not the whole request's failure.
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 - anything else still owes a reason
        raise HTTPException(
            status_code=500, detail=engine.describe_exception(exc)
        ) from exc


@app.post("/api/obs_data/upload")
async def upload_obs_data(
    request: Request,
    model_id: str | None = Query(default=None),
    # Set by the editor's Save (#215): the dated copy is written where the study
    # lives instead of being handed to the browser as a download. Absent for a
    # plain file upload, which has a file on disk already.
    output_dir: str | None = Query(default=None),
    filename: str | None = Query(default=None),
) -> dict:
    ctype = request.headers.get("content-type", "")
    if ctype.startswith("multipart/form-data"):
        form = await request.form()
        upload = form.get("file")
        model_id = form.get("model_id", model_id)
        if upload is None:
            raise HTTPException(status_code=422, detail="no file provided")
        raw = await upload.read()
        try:
            obj = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise HTTPException(status_code=422, detail=f"invalid JSON: {exc}") from exc
    else:
        try:
            obj = await request.json()
        except json.JSONDecodeError as exc:
            raise HTTPException(status_code=422, detail=f"invalid JSON: {exc}") from exc
        if isinstance(obj, dict) and "obs_data" in obj and "protocol_info" not in obj:
            model_id = obj.get("model_id", model_id)
            obj = obj["obs_data"]

    try:
        parsed = parse_obs_data(obj)
    except ObsDataError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    if model_id and model_id in _models:
        _models[model_id].obs_data = parsed
        obs_path = UPLOAD_DIR / f"{model_id}_obs_data.json"
        obs_path.write_text(json.dumps(obj), encoding="utf-8")
        _models[model_id].obs_path = obs_path

    saved_path = _save_edited_copy(
        output_dir, filename, json.dumps(obj, indent=1).encode("utf-8")
    )

    return {
        "model_id": model_id,
        # Where Save put the dated copy, so the panel can say it (#215).
        "saved_path": saved_path,
        **parsed.summary(),
        "data_items": parsed.data_items,
        "prediction_items": parsed.prediction_items,
        # protocol_info lets the frontend plot the controlled (params_to_change)
        # inputs per experiment; null for data-only obs_data.
        "protocol_info": parsed.protocol_info,
    }


@app.get("/api/obs_data/options")
def obs_data_options(refresh: bool = False, output_dir: str = "") -> dict:
    """Operation (obs_funcs) and cost_type (cost_func) names from circulatory_autogen.

    Drives the obs_data editor's dropdowns; degrades to a small built-in set when
    CA can't be introspected. ``output_dir`` locates the user's custom funcs so
    they appear in the lists.
    """
    return get_obs_data_options(refresh=refresh, output_dir=_user_func_base_dir(output_dir))


class UserFuncRequest(BaseModel):
    # The func being edited, or "" for a new one. NOT the name it is saved under:
    # that comes from the `def` in `source`, so the name is entered in one place.
    # Sent so that renaming the `def` renames the func instead of leaving the old
    # one behind as a stale copy.
    name: str = ""
    source: str
    # The user's output directory (config_outputs_dir); funcs are stored there so
    # they travel with the run outputs. Empty falls back to the config dir.
    output_dir: str = ""


def _user_func_base_dir(output_dir: str | None) -> str | None:
    """Normalise a client-supplied output dir for user-func storage: '' -> None
    (config-dir fallback), and require an absolute path when given."""
    d = (output_dir or "").strip()
    if d and not os.path.isabs(d):
        raise HTTPException(status_code=422, detail="output_dir must be an absolute path")
    return d or None


def _model_dir(base_dir: str | None, file_prefix: str | None) -> str | None:
    """``<base_dir>/generated_models/<prefix>/`` — where a model's own files live.

    The layout circulatory_autogen resolves ``model_path`` against and the one
    the export bundle writes, so anything belonging to *the model* (rather than
    to a run) has one place to go in both. ``None`` when there is no outputs
    directory to put it under.
    """
    if not base_dir:
        return None
    return str(Path(base_dir) / "generated_models" / (file_prefix or "model"))


def _save_edited_copy(output_dir: str | None, filename: str | None, data: bytes) -> str | None:
    """Write the dated copy of a config file the user just saved in an editor.

    The obs_data and params_for_id editors used to hand this file to the browser
    as a download; issue #215 makes Save write it where the study lives instead.
    Into the configured outputs directory, or the app's own config dir when
    there is none -- Save must never silently lose an edit because no directory
    was chosen, so there is a fallback rather than a refusal.

    Only the *basename* is taken from the client: the editors name the file
    (``<stem>_<yymmdd>.json``) but they do not choose where it goes, and a
    filename carrying ``..`` must not be able to walk out of the directory.
    """
    if not filename:
        return None
    base = _user_func_base_dir(output_dir) or str(settings_store.config_dir())
    target = Path(base) / Path(filename).name
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    except OSError as exc:
        raise _fs_error(
            exc, "save the edited file to", target.parent, user_dir=bool(output_dir)
        ) from exc
    return str(target)


# One trio of routes per user-func kind: list, save, delete. CUFLynx saves the
# func to an external file under the user's output directory and points CA at it
# via that kind's config key (issue #104 rework; CA #303, CA #383), instead of
# writing into CA's tracked tree.
#
# Registered from `user_funcs.KINDS` rather than written out three times. The
# bodies differed only by a kind string, so the third copy (modifiers) was the
# point at which "one more near-identical block" stopped being cheaper than the
# loop. The paths are still literal, so the URLs are exactly what they were --
# a `{kind}` path parameter would have changed them.
def _register_user_func_routes(kind: str, label: str) -> None:
    def list_funcs(output_dir: str = "") -> dict:
        return read_user_funcs(kind, _user_func_base_dir(output_dir))

    def save_func(req: UserFuncRequest) -> dict:
        try:
            return save_user_func(
                kind, req.name, req.source, _user_func_base_dir(req.output_dir)
            )
        except UserFuncError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    def delete_func(name: str, output_dir: str = "") -> dict:
        try:
            return delete_user_func(kind, name, _user_func_base_dir(output_dir))
        except UserFuncError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    # Named and documented per kind, so /docs reads as it did when these were
    # three hand-written trios.
    list_funcs.__name__ = f"list_{kind}_funcs"
    list_funcs.__doc__ = f"User-authored {label} + the editor templates."
    save_func.__name__ = f"save_{kind}_func"
    save_func.__doc__ = (
        f"Create or update a user {kind} func; then it appears in the options "
        f"list. Named by the ``def`` in ``source``; ``req.name`` is only the "
        f"entry being edited."
    )
    delete_func.__name__ = f"delete_{kind}_func"
    delete_func.__doc__ = f"Remove a user {kind} func."

    app.get(f"/api/{kind}_funcs")(list_funcs)
    app.post(f"/api/{kind}_funcs")(save_func)
    app.delete(f"/api/{kind}_funcs/{{name}}")(delete_func)


for _kind, _label in (
    ("operation", "observable operations (issue #58)"),
    ("cost", "cost functions (issue #104)"),
    # The third kind, and the one a params_for_id refers to rather than an
    # obs_data: a modifier maps the calibrated theta to each parameter it
    # governs (CA #383).
    ("modifier", "parameter modifiers (CA #383)"),
):
    _register_user_func_routes(_kind, _label)


class SaveParamsRequest(BaseModel):
    values: dict[str, float]  # {qname: value} — the current slider values
    order: list[str] = Field(default_factory=list)  # qname order for the npy array
    filename: str = "manual_params.npy"
    output_dir: str = ""  # where to save; empty -> the uploads dir
    # The traces those values produced, saved under the same prefix so the run
    # can be overlaid later without re-running it (#126). Omitted -> params only.
    result: dict | None = None


class LoadParamsRequest(BaseModel):
    path: str
    order: list[str] = Field(default_factory=list)  # current qnames, to name npy values


@app.post("/api/params/save")
def save_params(req: SaveParamsRequest) -> dict:
    """Save the current slider values to a named file (issue #106). Format follows
    the extension: ``.csv`` -> CSV, else numpy ``.npy`` (default). Saved under the
    user's output directory, falling back to the uploads dir."""
    out_dir = _user_func_base_dir(req.output_dir) or str(UPLOAD_DIR)
    try:
        path = save_param_values(req.values, req.order, out_dir, req.filename)
    except ParamIOError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"could not write file: {exc}") from exc

    # The traces alongside the values, under the same prefix (#126). Saving the
    # parameters is the user's action; the outputs riding along must not be able
    # to fail it, so a write error here is reported without losing the params.
    outputs_path = None
    outputs_error = None
    if req.result:
        try:
            outputs_path = saved_runs.save_run(path, req.values, req.result)
        except OSError as exc:
            outputs_error = _fs_error_detail(exc, "write the saved outputs to", Path(path))
    return {"path": path, "outputs_path": outputs_path, "outputs_error": outputs_error}


@app.get("/api/runs")
def list_saved_runs(dir: str | None = Query(default=None)) -> dict:
    """Saved runs in an output directory, for the "show saved" list (#126).

    Metadata only — the traces are fetched per run when one is shown.
    """
    base = _user_func_base_dir(dir or "") or str(UPLOAD_DIR)
    return {"dir": base, "runs": saved_runs.list_runs(base)}


@app.get("/api/runs/load")
def load_saved_run(path: str = Query(...)) -> dict:
    """One saved run, with its series, to overlay on the plots (#126)."""
    try:
        return saved_runs.load_run(path)
    except saved_runs.SavedRunError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/api/params/load")
def load_params(req: LoadParamsRequest) -> dict:
    """Load slider values from a ``.npy`` or ``.csv`` file (issue #106). For npy,
    ``order`` (the current qnames) names the bare array; csv carries its own names."""
    try:
        values = load_param_values(req.path, req.order)
    except ParamIOError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"values": values}


def _save_params_file(model_id: str, data: bytes | str) -> Path:
    """Persist an uploaded params_for_id, canonicalised to JSON.

    **A CSV is converted on the way in** (by CA, which owns the conversion), so
    the stored study, the export bundle and every runner see the one canonical
    form. JSON is the only form that can express a modifier, its inputs or a
    prior's parameters, so keeping a CSV as the stored form means those are
    unrepresentable in whatever the user's outputs directory ends up holding.

    The conversion needs CA. When it is unavailable the CSV is stored as-is
    rather than refusing the upload: a study must not become unloadable because
    the CA directory has not been set yet (the packaged app starts in exactly
    that state), and CA's own CSV path still reads it.

    CA branches CSV-vs-JSON on the filename suffix (``get_param_id_info``), so
    the suffix must follow the content. The stale other-suffix twin is removed
    so a format switch cannot leave two files disagreeing about which is current.
    """
    raw = data if isinstance(data, bytes) else data.encode()
    if not params_json.looks_like_json(raw):
        try:
            doc = params_json.csv_to_json(raw)
        except params_json.ParamsJsonError:
            pass  # no CA: keep the CSV, which CA's own CSV path still reads
        else:
            raw = json.dumps(doc, indent=2).encode()
    suffix = ".json" if params_json.looks_like_json(raw) else ".csv"
    path = UPLOAD_DIR / f"{model_id}_params_for_id{suffix}"
    path.write_bytes(raw)
    other_suffix = ".csv" if suffix == ".json" else ".json"
    (UPLOAD_DIR / f"{model_id}_params_for_id{other_suffix}").unlink(missing_ok=True)
    return path


@app.post("/api/params_for_id/upload")
async def upload_params_for_id(
    request: Request,
    model_id: str | None = Query(default=None),
    # Set by the editor's Save (#215); see upload_obs_data.
    output_dir: str | None = Query(default=None),
    filename: str | None = Query(default=None),
) -> dict:
    ctype = request.headers.get("content-type", "")
    if ctype.startswith("multipart/form-data"):
        form = await request.form()
        upload = form.get("file")
        model_id = form.get("model_id", model_id)
        if upload is None:
            raise HTTPException(status_code=422, detail="no file provided")
        data = await upload.read()
    else:
        data = await request.body()

    initial_values: dict[str, float] = {}
    if model_id and model_id in _models:
        initial_values = _models[model_id].meta.initial_values

    try:
        entries = parse_params_for_id(data, initial_values)
    except ParamsForIdError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    if model_id and model_id in _models:
        _models[model_id].params_path = _save_params_file(model_id, data)

    raw = data if isinstance(data, bytes) else data.encode()
    saved_path = _save_edited_copy(output_dir, filename, raw)

    return {"params": [e.as_dict() for e in entries], "saved_path": saved_path}


# ---------------------------------------------------------------------------
# Calibration (circulatory_autogen parameter identification)
# ---------------------------------------------------------------------------
class CalibrationRequest(BaseModel):
    model_id: str
    settings: dict = Field(default_factory=dict)
    # Current parameter values from the UI sliders ({qname: value}); used as the
    # gradient-descent start point when settings['start_from'] == 'current' (#65).
    current_params: dict | None = None


CALIBRATION_DEFAULTS = {
    "param_id_method": "genetic_algorithm",
    # NB: `methods` is replaced by the CA-introspected list in calibration_defaults();
    # this literal is only a shape placeholder.
    "methods": ["genetic_algorithm", "CMA-ES"],
    "num_calls_to_function": 100,
    "cost_convergence": 0.001,
    "max_patience": 10,
    "cost_type": "",
    "dt": 0.01,
    "solver": "CVODE_myokit",
    "DEBUG": False,
    "num_cores": 1,  # >1 -> mpiexec -n N (parallel GA population evaluation)
    # Note: pre_time / sim_time are taken from the obs_data protocol_info (#13).
}


@app.get("/api/calibration/defaults")
def calibration_defaults() -> dict:
    # `methods` is introspected from CA's PARAM_ID_METHODS schema (never hardcoded);
    # falls back to the built-in list on an older CA without that schema.
    return {**CALIBRATION_DEFAULTS, "methods": get_param_id_methods()}


@app.get("/api/calibration/pythons")
def calibration_pythons(refresh: bool = False) -> dict:
    """Discover Python interpreters that can run a calibration.

    ``default`` is null in the packaged desktop build: there, the app's own
    "interpreter" is the frozen bundle, which cannot run a runner script. The
    client then requires an explicit pick from ``pythons``.
    """
    return {
        "default": default_python(),
        "pythons": list_python_interpreters(refresh=refresh),
    }


@app.post("/api/calibration/run")
def calibration_run(req: CalibrationRequest) -> dict:
    record = _get_model(req.model_id)
    if record.obs_path is None or record.params_path is None:
        raise HTTPException(
            status_code=422,
            detail="calibration requires both an obs_data.json and a "
            "params_for_id.csv to be uploaded for this model",
        )
    python_path = req.settings.get("python_path") or None
    if python_path and not (
        os.path.isfile(python_path) and os.access(python_path, os.X_OK)
    ):
        raise HTTPException(
            status_code=422,
            detail=f"python interpreter not found or not executable: {python_path}",
        )

    # start_from == 'best_fit' continues from the previous calibration's best fit
    # (#83) — including one you stopped early (its best-so-far is recovered). The
    # backend supplies those values so the UI needn't carry them.
    best_fit_params = None
    if req.settings.get("start_from") == "best_fit":
        best_fit_params = calibration.last_completed_best_params(req.model_id)
        if not best_fit_params:
            raise HTTPException(
                status_code=422,
                detail="cannot start from the previous best fit: no finished "
                "calibration (completed or stopped) is available for this model yet",
            )

    configured = (req.settings.get("config_outputs_dir") or "").strip()
    if configured:
        if not os.path.isabs(configured):
            raise HTTPException(
                status_code=422,
                detail="config_outputs_dir must be an absolute path",
            )
        output_dir = configured
    else:
        output_dir = str(UPLOAD_DIR / f"calib_{req.model_id}_{uuid.uuid4().hex[:8]}")
    config = {
        "model_id": req.model_id,
        "model_path": resolve_model_path(str(record.path), engine.model_type, model_id=req.model_id),
        "model_type": engine.model_type,
        "solver": engine.solver,
        "solver_info": dict(engine.solver_info),
        "obs_path": str(record.obs_path),
        "params_path": str(record.params_path),
        # CUFLynx-authored operation/cost funcs, saved under the output dir; CA
        # loads them from these paths (CA #303).
        "operation_funcs_external_path": user_func_path("operation", configured or None),
        "cost_funcs_external_path": user_func_path("cost", configured or None),
        "output_dir": output_dir,
        "file_prefix": record.meta.name or "model",
        "num_cores": int(req.settings.get("num_cores", 1) or 1),
        "python": python_path,
        "settings": req.settings,
        # The original uploaded CellML, so the runner can save a calibrated copy
        # with best-fit values baked in (issue #114), independent of model_type.
        "cellml_path": str(record.path),
        "current_params": req.current_params,
        "best_fit_params": best_fit_params,
        # Global random seed (Settings popup); None => non-deterministic run.
        "seed": _analysis_seed,
    }
    try:
        job_id = calibration.start(config)
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"job_id": job_id}


@app.get("/api/calibration/{job_id}/status")
def calibration_status(job_id: str, offset: int = 0) -> dict:
    status = calibration.status(job_id, offset)
    if status is None:
        raise HTTPException(status_code=404, detail="calibration job not found")
    return status


@app.get("/api/calibration/{job_id}/calibrated_model")
def download_calibrated_model(job_id: str) -> FileResponse:
    """Download the calibrated CellML saved when the run finished (issue #114) —
    the best-fit values baked into the uploaded flat model, for reloading."""
    status = calibration.status(job_id)
    if status is None:
        raise HTTPException(status_code=404, detail="calibration job not found")
    path = status.get("calibrated_model_path")
    if not path or not os.path.isfile(path):
        raise HTTPException(status_code=404, detail="no calibrated model for this job")
    return FileResponse(path, media_type="application/xml", filename=os.path.basename(path))


@app.get("/api/calibration/{job_id}/progress")
def calibration_progress(job_id: str) -> dict:
    prog = calibration.progress(job_id)
    if prog is None:
        raise HTTPException(status_code=404, detail="calibration job not found")
    return prog


@app.post("/api/calibration/{job_id}/cancel")
def calibration_cancel(job_id: str) -> dict:
    if not calibration.cancel(job_id):
        raise HTTPException(status_code=404, detail="calibration job not found")
    return {"cancelled": True}


# ---------------------------------------------------------------------------
# Sensitivity analysis (circulatory_autogen Sobol indices)
# ---------------------------------------------------------------------------
class SensitivityRequest(BaseModel):
    model_id: str
    settings: dict = Field(default_factory=dict)
    # Current parameter values from the UI sliders ({qname: value}); used as the
    # nominal (linearisation) point for local SA when nominal=="current" (#65).
    current_params: dict | None = None


SENSITIVITY_DEFAULTS = {
    "method": "sobol",
    "methods": ["sobol", "local"],
    "sample_type": "saltelli",
    "sample_types": ["saltelli", "sobol"],
    "num_samples": 256,
    # Local (derivative-based) sensitivity gradient source. The available list is
    # NOT hardcoded here: sensitivity_defaults() sources it from CA's gradient_sources
    # accessor for the current model (FD always; AD for casadi_python; FSA for
    # cellml_only + CVODE_myokit), exactly like the calibration menu.
    "gradient_method": "FD",
    "rel_step": 0.01,  # relative central-difference step about the nominal point
    # Where the nominal (linearisation) point comes from. "current" (default)
    # uses the model's current parameter values; "best_fit" reuses a completed
    # calibration; "midpoint"/"geometric" derive it from the params_for_id bounds.
    "nominal": "current",
    "nominals": ["current", "best_fit", "midpoint", "geometric"],
    # Local SA convenience flag: when True, run a fresh calibration first and take
    # the local sensitivity about that best fit. Default False — the user can run
    # a calibration separately and then reuse it via nominal="best_fit". The GA
    # settings come from the Calibration panel (folded in by the frontend), so
    # they are not duplicated here.
    "run_calibration_first": False,
    "dt": 0.01,
    "solver": "CVODE_myokit",
    "DEBUG": False,
    "num_cores": 1,  # >1 -> mpiexec -n N (parallel sample evaluation; Sobol only)
    # Note: pre_time / sim_time are taken from the obs_data protocol_info (#13).
}


@app.get("/api/sensitivity/defaults")
def sensitivity_defaults() -> dict:
    # `options` are CA's sensitivity_analysis descriptors (introspected from
    # ANALYSIS_OPTIONS, never hardcoded) so the Sobol settings form tracks CA.
    sa = get_analysis_options().get("sensitivity_analysis", {})
    # Local-SA gradient sources for the current model, from CA's gradient_sources
    # accessor (FD / AD / FSA) — same source of truth as the calibration menu, so
    # FSA surfaces for cellml_only + CVODE_myokit and AD for casadi_python. The
    # requires_all_differentiable (CasADi AD) gate is applied client-side against
    # the in-use differentiability (SensitivityPanel adAvailable), so pass True here.
    grad = gradient_sources(
        engine.model_type, engine.solver, True, engine.solver_info.get("method"),
    )
    gradient_methods = [
        {"value": g["value"], "label": g["label"],
         "requires_all_differentiable": bool(g.get("requires_all_differentiable"))}
        for g in grad
    ]
    return {
        **SENSITIVITY_DEFAULTS,
        "gradient_methods": gradient_methods,
        "options": sa.get("options", []),
    }


@app.post("/api/sensitivity/run")
def sensitivity_run(req: SensitivityRequest) -> dict:
    record = _get_model(req.model_id)
    if record.obs_path is None or record.params_path is None:
        raise HTTPException(
            status_code=422,
            detail="sensitivity analysis requires both an obs_data.json and a "
            "params_for_id.csv to be uploaded for this model",
        )
    python_path = req.settings.get("python_path") or None
    if python_path and not (
        os.path.isfile(python_path) and os.access(python_path, os.X_OK)
    ):
        raise HTTPException(
            status_code=422,
            detail=f"python interpreter not found or not executable: {python_path}",
        )

    # Local SA with nominal="best_fit" reuses a completed calibration's best fit
    # (mirrors UQ's reuse mode). run_calibration_first runs a fresh one in the
    # runner instead, so no reuse is needed here.
    best_params = None
    if (
        req.settings.get("method") == "local"
        and not req.settings.get("run_calibration_first", False)
        and req.settings.get("nominal") == "best_fit"
    ):
        if calibration.busy:
            raise HTTPException(
                status_code=409,
                detail="a calibration is still running; wait for it to finish "
                "before running local sensitivity about its best fit",
            )
        best_params = calibration.last_completed_best_params(req.model_id)
        if not best_params:
            raise HTTPException(
                status_code=422,
                detail="no completed calibration to reuse — run a calibration to "
                "completion first, enable 'run a fresh calibration first', or pick "
                "a different nominal point",
            )

    configured = (req.settings.get("config_outputs_dir") or "").strip()
    if configured:
        if not os.path.isabs(configured):
            raise HTTPException(
                status_code=422,
                detail="config_outputs_dir must be an absolute path",
            )
        output_dir = configured
    else:
        output_dir = str(UPLOAD_DIR / f"sa_{req.model_id}_{uuid.uuid4().hex[:8]}")
    # Local (finite-difference) SA is single-process; only Sobol parallelises
    # sample evaluation across MPI ranks.
    num_cores = int(req.settings.get("num_cores", 1) or 1)
    if req.settings.get("method") == "local":
        num_cores = 1
    config = {
        "model_path": resolve_model_path(str(record.path), engine.model_type, model_id=req.model_id),
        "model_type": engine.model_type,
        "solver": engine.solver,
        "solver_info": dict(engine.solver_info),
        "obs_path": str(record.obs_path),
        "params_path": str(record.params_path),
        # CUFLynx-authored operation/cost funcs, saved under the output dir; CA
        # loads them from these paths (CA #303).
        "operation_funcs_external_path": user_func_path("operation", configured or None),
        "cost_funcs_external_path": user_func_path("cost", configured or None),
        "output_dir": output_dir,
        "file_prefix": record.meta.name or "model",
        "num_cores": num_cores,
        "python": python_path,
        "settings": req.settings,
        "best_params": best_params,
        "current_params": req.current_params,
        # Global random seed (Settings popup); None => non-deterministic run.
        "seed": _analysis_seed,
    }
    try:
        job_id = sensitivity.start(config)
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"job_id": job_id}


@app.get("/api/sensitivity/{job_id}/status")
def sensitivity_status(job_id: str, offset: int = 0) -> dict:
    status = sensitivity.status(job_id, offset)
    if status is None:
        raise HTTPException(status_code=404, detail="sensitivity job not found")
    return status


@app.post("/api/sensitivity/{job_id}/cancel")
def sensitivity_cancel(job_id: str) -> dict:
    if not sensitivity.cancel(job_id):
        raise HTTPException(status_code=404, detail="sensitivity job not found")
    return {"cancelled": True}


# ---------------------------------------------------------------------------
# UQ — uncertainty quantification (MCMC / Laplace posterior on parameters)
# ---------------------------------------------------------------------------
class UQRequest(BaseModel):
    model_id: str
    settings: dict = Field(default_factory=dict)


UQ_DEFAULTS = {
    "method": "mcmc",
    "methods": ["mcmc", "laplace"],
    "num_steps": 1000,
    "num_walkers": 64,
    "cost_type": "gaussian_MLE",
    "cost_convergence": 0.001,
    "dt": 0.01,
    "solver": "CVODE_myokit",
    "DEBUG": False,
    "num_cores": 1,  # >1 -> mpiexec -n N
    # False (default) reuses the latest completed calibration's best fit;
    # True runs a fresh GA calibration first (self-contained).
    "run_calibration_first": False,
    "param_id_method": "genetic_algorithm",
    "num_calls_to_function": 100,
    "max_patience": 10,
}


@app.get("/api/uq/defaults")
def uq_defaults() -> dict:
    # `uq_options` / `ia_options` are CA's descriptors (introspected from
    # ANALYSIS_OPTIONS, never hardcoded) so the UQ settings forms track CA.
    # get_analysis_options normalises CA's pre-rename 'mcmc' mode key to 'uq'.
    ao = get_analysis_options()
    uq_options = ao.get("uq", {}).get("options", [])
    return {
        **UQ_DEFAULTS,
        "uq_options": uq_options,
        # The pre-rename field name, still emitted so a browser holding a cached
        # older bundle keeps rendering its settings form.
        "mcmc_options": uq_options,
        "ia_options": ao.get("identifiability_analysis", {}).get("options", []),
    }


@app.post("/api/uq/run")
def uq_run(req: UQRequest) -> dict:
    record = _get_model(req.model_id)
    if record.obs_path is None or record.params_path is None:
        raise HTTPException(
            status_code=422,
            detail="UQ requires both an obs_data.json and a params_for_id.csv to be "
            "uploaded for this model",
        )
    python_path = req.settings.get("python_path") or None
    if python_path and not (
        os.path.isfile(python_path) and os.access(python_path, os.X_OK)
    ):
        raise HTTPException(
            status_code=422,
            detail=f"python interpreter not found or not executable: {python_path}",
        )

    # Reuse mode (default): need a completed calibration's best fit to start from.
    best_params = None
    if not req.settings.get("run_calibration_first", False):
        if calibration.busy:
            raise HTTPException(
                status_code=409,
                detail="a calibration is still running; wait for it to finish before "
                "running UQ (or enable 'run a fresh calibration first')",
            )
        best_params = calibration.last_completed_best_params(req.model_id)
        if not best_params:
            raise HTTPException(
                status_code=422,
                detail="no completed calibration to reuse — run a calibration to "
                "completion first, or enable 'run a fresh calibration first'",
            )

    configured = (req.settings.get("config_outputs_dir") or "").strip()
    if configured:
        if not os.path.isabs(configured):
            raise HTTPException(
                status_code=422,
                detail="config_outputs_dir must be an absolute path",
            )
        output_dir = configured
    else:
        output_dir = str(UPLOAD_DIR / f"uq_{req.model_id}_{uuid.uuid4().hex[:8]}")
    config = {
        "model_id": req.model_id,
        "model_path": resolve_model_path(str(record.path), engine.model_type, model_id=req.model_id),
        "model_type": engine.model_type,
        "solver": engine.solver,
        "solver_info": dict(engine.solver_info),
        "obs_path": str(record.obs_path),
        "params_path": str(record.params_path),
        # CUFLynx-authored operation/cost funcs, saved under the output dir; CA
        # loads them from these paths (CA #303).
        "operation_funcs_external_path": user_func_path("operation", configured or None),
        "cost_funcs_external_path": user_func_path("cost", configured or None),
        "output_dir": output_dir,
        "file_prefix": record.meta.name or "model",
        "num_cores": int(req.settings.get("num_cores", 1) or 1),
        "python": python_path,
        "settings": req.settings,
        "best_params": best_params,
        # Global random seed (Settings popup); None => non-deterministic run.
        "seed": _analysis_seed,
    }
    try:
        job_id = uq.start(config)
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"job_id": job_id}


@app.get("/api/uq/{job_id}/status")
def uq_status(job_id: str, offset: int = 0) -> dict:
    status = uq.status(job_id, offset)
    if status is None:
        raise HTTPException(status_code=404, detail="UQ job not found")
    return status


@app.post("/api/uq/{job_id}/cancel")
def uq_cancel(job_id: str) -> dict:
    if not uq.cancel(job_id):
        raise HTTPException(status_code=404, detail="UQ job not found")
    return {"cancelled": True}


# ---------------------------------------------------------------------------
# Static frontend — single-server deployment
# ---------------------------------------------------------------------------
# Serve the built Vue app (apps/web/dist) from the same server as the API so the
# whole thing runs as one process on one port. Mounted LAST so the /api/* routes
# above take precedence; the SPA is served for everything else. The app uses no
# client-side routing, so html=True (index.html for "/") is sufficient.
_FRONTEND_DIST = frontend_dist()
if _FRONTEND_DIST.is_dir():
    app.mount("/", StaticFiles(directory=str(_FRONTEND_DIST), html=True), name="frontend")
else:

    @app.get("/")
    def _frontend_not_built() -> dict:
        return {
            "detail": "frontend not built — run `yarn build` in apps/web, then reload "
            "http://localhost:8000"
        }
