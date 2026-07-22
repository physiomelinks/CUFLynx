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

import json
import os
import shutil
import tempfile
import uuid
from pathlib import Path

import yaml

from fastapi import FastAPI, File, HTTPException, Query, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from calibration import calibration, list_python_interpreters, resolve_mpiexec
from cellml_flatten import (
    CellMLFlattenError,
    flatten_cellml,
    has_imports,
    pick_main_cellml,
)
from cellml_meta import CellMLModel, CellMLParseError, parse_cellml
from compiler_check import compiler_status
from engine import SimulationError, engine, _circulatory_autogen_src
import export_pipeline
from model_codegen import resolve_model_path, reset_cache as reset_codegen
from obs_data import ObsData, ObsDataError, parse_obs_data
from obs_options import get_obs_data_options, reset_cache as reset_obs_options
from params_for_id import ParamsForIdError, parse_params_for_id
from runtime_paths import default_python, frontend_dist, is_frozen
import settings_store
from solver_options import (
    ad_available,
    get_analysis_options,
    get_param_id_methods,
    gradient_sources,
    get_solver_options,
    reset_cache as reset_solver_options,
)
from sensitivity import sensitivity
from uq import uq

app = FastAPI(title="CUFLynx API", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

UPLOAD_DIR = Path(tempfile.gettempdir()) / "cuflynx_uploads"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)


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


class ProtocolRunRequest(BaseModel):
    model_id: str
    protocol_info: dict | None = None
    params: dict[str, float] = Field(default_factory=dict)
    outputs: list[str] | None = None


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


def _ca_src_from_dir(d: str) -> str:
    """Normalize a chosen CA directory to its importable `src` path: accept the
    repo root (append `src`) or a `src` dir directly."""
    p = Path(d).expanduser()
    return str(p / "src") if (p / "src").is_dir() else str(p)


def _set_analysis_python(path: str) -> None:
    """Point every analysis job manager at ``path``.

    All three spawn a runner script the same way, so they share one interpreter
    choice; keeping them in lockstep here avoids a per-manager setting the UI
    would have to expose three times.
    """
    calibration.python = path
    sensitivity.python = path
    uq.python = path


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
        engine.solver_info = si
    if fmt or solver or solver_info:
        os.environ["CUFLYNX_MODEL_TYPE"] = engine.model_type
        os.environ["CUFLYNX_SOLVER"] = engine.solver
        os.environ["CUFLYNX_SOLVER_INFO"] = json.dumps(engine.solver_info)

    python_path = (saved.get("python_path") or "").strip()
    if python_path and os.path.isfile(python_path) and os.access(python_path, os.X_OK):
        _set_analysis_python(python_path)


_restore_persisted_settings()


def _config_payload() -> dict:
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
        "gradient_sources": gradient_sources(engine.model_type, engine.solver, True),
        # Myokit JIT-compiles models, so a missing C compiler breaks every
        # simulation. Surfaced here so the UI can warn up front rather than
        # letting the first run fail with an opaque 500 (matters most in the
        # packaged desktop build, which can't ship a compiler).
        "cpp_compiler": compiler_status(),
        "packaged": is_frozen(),
        # Whether a matching MPI launcher is available for the current interpreter,
        # so the UI can warn *before* a num_cores>1 run silently drops to a single
        # core (build_command falls back when no mpiexec is found -- common on
        # Windows without MS-MPI). Tracks the selected interpreter: resolved the
        # same way the run does (see calibration.resolve_mpiexec).
        "mpiexec_available": resolve_mpiexec(calibration.python) is not None,
    }


@app.get("/api/config")
def get_config() -> dict:
    return _config_payload()


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
        engine.solver_info = si
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


def _export_base_dir(configured: str) -> Path:
    configured = (configured or "").strip()
    if configured:
        if not os.path.isabs(configured):
            raise HTTPException(status_code=422, detail="config_outputs_dir must be an absolute path")
        return Path(configured)
    return UPLOAD_DIR


@app.post("/api/export/pipeline")
def export_pipeline_route(req: ExportPipelineRequest) -> dict:
    """Write a self-contained, reproducible export folder: the dated
    user_inputs yaml + run_pipeline.py + plot_outputs.py + copies of the model /
    obs / params, all referenced by relative paths."""
    record = _get_model(req.model_id)
    suffix = export_pipeline.dated_suffix()
    export_dir = _export_base_dir(req.config_outputs_dir) / f"export_{suffix}"
    resources = export_dir / "resources"
    resources.mkdir(parents=True, exist_ok=True)

    # Use the loaded CellML file's prefix (e.g. "3compartment"), not the internal
    # <model name> (often a generic "cardiovascularSystem"). The client passes it.
    file_prefix = req.file_prefix.strip() or record.meta.name or "model"
    # The model lives where circulatory_autogen resolves model_path:
    # generated_models/<prefix>/<prefix>.cellml. obs/params go in resources/.
    model_file = f"{file_prefix}.cellml"
    model_dir = export_dir / "generated_models" / file_prefix
    model_dir.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(record.path, model_dir / model_file)
    obs_file = None
    if record.obs_path is not None:
        obs_file = "obs_data.json"
        shutil.copyfile(record.obs_path, resources / obs_file)
    params_file = None
    if record.params_path is not None:
        params_file = "params_for_id.csv"
        shutil.copyfile(record.params_path, resources / params_file)

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
        calibration=req.calibration,
        sensitivity=req.sensitivity,
        uq=req.uq,
        enabled=req.enabled,
    )
    yaml_name = f"user_inputs_{suffix}.yaml"
    with open(export_dir / yaml_name, "w") as fh:
        yaml.safe_dump(user_inputs, fh, default_flow_style=False, sort_keys=False)
    (export_dir / "run_pipeline.py").write_text(export_pipeline.render_pipeline_script())
    (export_dir / "plot_outputs.py").write_text(export_pipeline.render_plotting_script())

    files = [
        yaml_name, "run_pipeline.py", "plot_outputs.py",
        f"generated_models/{file_prefix}/{model_file}",
    ]
    if obs_file:
        files.append(f"resources/{obs_file}")
    if params_file:
        files.append(f"resources/{params_file}")
    return {"export_dir": str(export_dir), "files": files}


@app.post("/api/export/plotting")
def export_plotting_route(req: ExportPlottingRequest) -> dict:
    """Write just the plotting script (regenerates output/progress/analysis plots
    from a pipeline's output data)."""
    base = _export_base_dir(req.config_outputs_dir)
    base.mkdir(parents=True, exist_ok=True)
    path = base / "plot_outputs.py"
    path.write_text(export_pipeline.render_plotting_script())
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


@app.post("/api/models/upload")
async def upload_model(
    file: UploadFile | None = None,
    files: list[UploadFile] = File(default_factory=list),
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
    }


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
    }


@app.post("/api/simulate")
def simulate(req: SimulateRequest) -> dict:
    record = _get_model(req.model_id)
    _validate_param_keys(req.params)
    outputs = req.outputs or record.meta.odes
    try:
        model_path = resolve_model_path(str(record.path), engine.model_type, model_id=req.model_id)
        result = engine.simulate(
            model_id=req.model_id,
            model_path=model_path,
            params=req.params,
            sim_time=req.sim_time,
            pre_time=req.pre_time,
            outputs=outputs,
        )
    except SimulationError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
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
    try:
        model_path = resolve_model_path(str(record.path), engine.model_type, model_id=req.model_id)
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
    return result


@app.post("/api/obs_data/upload")
async def upload_obs_data(
    request: Request, model_id: str | None = Query(default=None)
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
        obs_path.write_text(json.dumps(obj))
        _models[model_id].obs_path = obs_path

    return {
        "model_id": model_id,
        **parsed.summary(),
        "data_items": parsed.data_items,
        "prediction_items": parsed.prediction_items,
        # protocol_info lets the frontend plot the controlled (params_to_change)
        # inputs per experiment; null for data-only obs_data.
        "protocol_info": parsed.protocol_info,
    }


@app.get("/api/obs_data/options")
def obs_data_options(refresh: bool = False) -> dict:
    """Operation (obs_funcs) and cost_type (cost_func) names from circulatory_autogen.

    Drives the obs_data editor's dropdowns; degrades to a small built-in set when
    CA can't be introspected.
    """
    return get_obs_data_options(refresh=refresh)


@app.post("/api/params_for_id/upload")
async def upload_params_for_id(
    request: Request, model_id: str | None = Query(default=None)
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
        params_path = UPLOAD_DIR / f"{model_id}_params_for_id.csv"
        params_path.write_bytes(data if isinstance(data, bytes) else data.encode())
        _models[model_id].params_path = params_path

    return {"params": [e.as_dict() for e in entries]}


# ---------------------------------------------------------------------------
# Calibration (circulatory_autogen parameter identification)
# ---------------------------------------------------------------------------
class CalibrationRequest(BaseModel):
    model_id: str
    settings: dict = Field(default_factory=dict)


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
        "output_dir": output_dir,
        "file_prefix": record.meta.name or "model",
        "num_cores": int(req.settings.get("num_cores", 1) or 1),
        "python": python_path,
        "settings": req.settings,
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
    grad = gradient_sources(engine.model_type, engine.solver, True)
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
        "output_dir": output_dir,
        "file_prefix": record.meta.name or "model",
        "num_cores": num_cores,
        "python": python_path,
        "settings": req.settings,
        "best_params": best_params,
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
    # `mcmc_options` / `ia_options` are CA's descriptors (introspected from
    # ANALYSIS_OPTIONS, never hardcoded) so the UQ settings forms track CA.
    ao = get_analysis_options()
    return {
        **UQ_DEFAULTS,
        "mcmc_options": ao.get("mcmc", {}).get("options", []),
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
        "output_dir": output_dir,
        "file_prefix": record.meta.name or "model",
        "num_cores": int(req.settings.get("num_cores", 1) or 1),
        "python": python_path,
        "settings": req.settings,
        "best_params": best_params,
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
