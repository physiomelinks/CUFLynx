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
import tempfile
import uuid
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from calibration import calibration, list_python_interpreters
from cellml_meta import CellMLModel, CellMLParseError, parse_cellml
from engine import SimulationError, engine, _circulatory_autogen_src
from obs_data import ObsData, ObsDataError, parse_obs_data
from obs_options import get_obs_data_options, reset_cache as reset_obs_options
from params_for_id import ParamsForIdError, parse_params_for_id
from sensitivity import sensitivity
from uq import uq

app = FastAPI(title="CellML Explorer API", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

UPLOAD_DIR = Path(tempfile.gettempdir()) / "cellml_explorer_uploads"
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
    # The circulatory_autogen directory (repo root or its `src`); blank resets to
    # the default (sibling clone / CIRCULATORY_AUTOGEN_SRC).
    ca_dir: str = ""


def _ca_src_from_dir(d: str) -> str:
    """Normalize a chosen CA directory to its importable `src` path: accept the
    repo root (append `src`) or a `src` dir directly."""
    p = Path(d).expanduser()
    return str(p / "src") if (p / "src").is_dir() else str(p)


def _config_payload() -> dict:
    src = _circulatory_autogen_src()
    p = Path(src)
    ca_dir = str(p.parent) if p.name == "src" else src
    return {"ca_dir": ca_dir, "ca_src": src, "ca_exists": p.is_dir()}


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
    d = (req.ca_dir or "").strip()
    if d:
        if not os.path.isdir(d):
            raise HTTPException(status_code=422, detail=f"not a directory: {d}")
        os.environ["CIRCULATORY_AUTOGEN_SRC"] = _ca_src_from_dir(d)
    else:
        os.environ.pop("CIRCULATORY_AUTOGEN_SRC", None)
    engine.reset()  # drop cached compiled helpers so the next sim uses the new CA
    reset_obs_options()  # obs_data operation/cost options come from the new CA too
    return _config_payload()


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


@app.post("/api/models/upload")
async def upload_model(file: UploadFile) -> dict:
    raw = await file.read()
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
        result = engine.simulate(
            model_id=req.model_id,
            model_path=str(record.path),
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
        result = engine.run_protocol(
            model_id=req.model_id,
            model_path=str(record.path),
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
    return CALIBRATION_DEFAULTS


@app.get("/api/calibration/pythons")
def calibration_pythons(refresh: bool = False) -> dict:
    """Discover Python interpreters that can run a calibration."""
    import sys as _sys

    return {
        "default": _sys.executable,
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
        "model_path": str(record.path),
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
    "methods": ["sobol"],
    "sample_type": "saltelli",
    "sample_types": ["saltelli", "sobol"],
    "num_samples": 256,
    "dt": 0.01,
    "solver": "CVODE_myokit",
    "DEBUG": False,
    "num_cores": 1,  # >1 -> mpiexec -n N (parallel sample evaluation)
    # Note: pre_time / sim_time are taken from the obs_data protocol_info (#13).
}


@app.get("/api/sensitivity/defaults")
def sensitivity_defaults() -> dict:
    return SENSITIVITY_DEFAULTS


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
    config = {
        "model_path": str(record.path),
        "obs_path": str(record.obs_path),
        "params_path": str(record.params_path),
        "output_dir": output_dir,
        "file_prefix": record.meta.name or "model",
        "num_cores": int(req.settings.get("num_cores", 1) or 1),
        "python": python_path,
        "settings": req.settings,
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
    return UQ_DEFAULTS


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
        "model_path": str(record.path),
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
_FRONTEND_DIST = Path(__file__).resolve().parents[1] / "web" / "dist"
if _FRONTEND_DIST.is_dir():
    app.mount("/", StaticFiles(directory=str(_FRONTEND_DIST), html=True), name="frontend")
else:

    @app.get("/")
    def _frontend_not_built() -> dict:
        return {
            "detail": "frontend not built — run `yarn build` in apps/web, then reload "
            "http://localhost:8000"
        }
