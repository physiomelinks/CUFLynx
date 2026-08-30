"""FastAPI backend for the CellML slider-visualisation app.

Endpoints
---------
GET  /api/health                       liveness probe
POST /api/models/upload                upload a .cellml file -> metadata
GET  /api/models/{model_id}/variables  classified variable lists
GET  /api/models/{model_id}/source     the .py / .mmt the user actually wrote
POST /api/models/{model_id}/edit       open that source in the user's own editor
POST /api/simulate                     single run (circulatory_autogen helper)
POST /api/protocol/run                 multi-experiment protocol run
POST /api/obs_data/upload              load obs_data.json (protocol + overlays)
POST /api/params_for_id/upload         load params_for_id.csv -> slider specs

Simulation is delegated to circulatory_autogen via :mod:`engine`; parsing of
CellML metadata, obs_data and params_for_id is dependency-light so these routes
work (and are unit-tested) without Myokit installed.
"""

from __future__ import annotations

import base64
import contextlib
import errno
import io
import json
import os
import re
import shutil
import tempfile
import time
import uuid
import zipfile
from pathlib import Path

import yaml

from fastapi import FastAPI, File, HTTPException, Query, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response
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
from py_model_meta import PyModelParseError, looks_like_py_filename, parse_py_model
import solver_plots
import mmt_protocol
import myokit_import
import easyml_import
import omex_import
import omex_export
from inbox import APP_NAME, RECEIVE_PORTS, inbox  # noqa: F401 - RECEIVE_PORTS is the contract
from aadc_check import aadc_status
import editor_launch
from version import __version__
from compiler_check import compiler_status
from engine import SimulationError, engine, _circulatory_autogen_src
from examples import EXAMPLE_MODELS, media_type as example_media_type
from local_sensitivity import local_gradient_sources
import ca_imports
import export_pipeline
from model_codegen import resolve_model_path, reset_cache as reset_codegen
from obs_data import ObsData, ObsDataError, parse_obs_data
import obs_extract
from obs_extract.job import obs_extract_jobs
from obs_options import get_obs_data_options, get_operation_funcs, reset_cache as reset_obs_options
import obs_cost
import cost_gradient
import cost_sensitivity
from obs_series import compute_output_series
import params_json
import ca_run_history
import load_outputs
from params_for_id import ParamsForIdError, parse_params_for_id
import saved_runs
from param_io import ParamIOError, load_param_values, save_param_values
from runtime_paths import default_python, frontend_dist, is_frozen, resources_dir
import settings_store
from solver_options import (
    ad_available,
    ca_model_type,
    canonical_model_type,
    check_solver_info,
    filter_solver_info,
    get_analysis_options,
    emulator_availability,
    get_param_id_methods,
    get_param_modifier_operations,
    get_param_prior_types,
    gradient_sources,
    get_solver_options,
    reset_cache as reset_solver_options,
)
from user_funcs import (
    FUNC_KINDS,
    UserFuncError,
    delete_user_func,
    external_path as user_func_path,
    external_paths as user_func_paths,
    model_source_path as study_model_source_path,
    read_user_funcs,
    save_model_module,
    save_user_func,
)
from sensitivity import sensitivity
from emulator import emulator
from uq import uq

app = FastAPI(title="CUFLynx API", version=__version__)

#: Origins allowed to reach the API from a *page* rather than from the app's own
#: frontend. The first two are the Vite dev server; the rest are PhLynx, which
#: hands a study to a running CUFLynx by posting it to the inbox (#287).
#:
#: An explicit list, never ``*``: this is the only door into an API that is
#: otherwise trusted because nothing off-machine can reach it, and widening it is
#: meant to be one reviewable line.
ALLOWED_ORIGINS = [
    "http://localhost:5173",
    "http://127.0.0.1:5173",
    "https://www.phlynx.com",
    "https://phlynx.com",
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def _allow_private_network(request: Request, call_next):
    """Answer Chrome's Private Network Access preflight.

    A page on the public internet reaching 127.0.0.1 is a private-network request:
    Chrome sends ``Access-Control-Request-Private-Network: true`` on the preflight
    and refuses the real request unless the response grants it. ``CORSMiddleware``
    knows nothing about this header, so the grant has to be added here.

    Only ever added for an origin already in ``ALLOWED_ORIGINS`` -- the CORS layer
    decides *who* may talk to us, and this only says "yes, I know I am local".

    This corner of the platform is moving: Chrome's successor proposal (Local
    Network Access) intends to put a browser permission prompt in front of these
    requests. That would add a prompt rather than break this, but the flow's UX is
    not entirely ours.
    """
    response = await call_next(request)
    if (
        request.method == "OPTIONS"
        and request.headers.get("access-control-request-private-network") == "true"
        and request.headers.get("origin") in ALLOWED_ORIGINS
    ):
        response.headers["Access-Control-Allow-Private-Network"] = "true"
    return response

UPLOAD_DIR = Path(tempfile.gettempdir()) / "cuflynx_uploads"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

# The figures an external_python model draws for itself live under the uploads
# dir, so the TTL prune below ages them out with everything else. Told to the
# store rather than duplicated in it: one definition of where uploads go.
solver_plots.set_root(UPLOAD_DIR / "solver_plots")

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
    def __init__(self, model_id: str, path: Path, meta: CellMLModel,
                 file_prefix: str | None = None):
        self.model_id = model_id
        self.path = path
        self.meta = meta
        #: The study's ``file_prefix``: the stem of the file the user loaded, and
        #: the name circulatory_autogen builds everything else out of -- the run
        #: directory ``<method>_<file_prefix>_<obs_prefix>``, the generated model
        #: under ``generated_models/<file_prefix>/``, ``<file_prefix>_calibrated``,
        #: ``emulators/<file_prefix>_<obs_prefix>``.
        #:
        #: **Not the CellML ``<model name>``.** These used to be the model name,
        #: so a study loaded from ``3compartment_flat.cellml`` ran as
        #: ``CardiovascularSystem`` -- the name inside the file -- and its results
        #: landed under a prefix nothing else in the app used. CA keys on
        #: file_prefix, and one study cannot have two names.
        self.file_prefix = file_prefix or None
        self.obs_data: ObsData | None = None
        # Raw input files persisted on disk for circulatory_autogen calibration.
        self.obs_path: Path | None = None
        self.params_path: Path | None = None
        # The .omex this study was loaded from, kept whole so it can be sent back
        # with every member CUFLynx does not understand intact (#287/#290).
        self.archive_path: Path | None = None


# In-memory registry of uploaded models (process-scoped session store).
_models: dict[str, _ModelRecord] = {}


def _get_model(model_id: str) -> _ModelRecord:
    record = _models.get(model_id)
    if record is None:
        # Recover from the persisted upload if the in-memory registry lost it
        # (e.g. a dev-server --reload wiped it). The model file still lives in
        # UPLOAD_DIR, so a parameter change / new plot can re-derive the model
        # and regenerate its python/casadi build instead of failing. obs_data /
        # params_for_id aren't restored (re-upload to run protocols / calibration).
        # Both upload formats are recoverable, and by the same rule: re-derive
        # the metadata from the persisted file with the parser that made it.
        # An external_python model is re-read by AST here exactly as it was at
        # upload -- recovery must not become the one path that imports it.
        for suffix, parse, failure in (
            (".cellml", parse_cellml, CellMLParseError),
            (".py", parse_py_model, PyModelParseError),
        ):
            path = UPLOAD_DIR / f"{model_id}{suffix}"
            if not path.is_file():
                continue
            try:
                meta = parse(path.read_bytes())
            except failure:
                continue
            record = _ModelRecord(model_id, path, meta, _recover_prefix(model_id))
            record.archive_path = _model_archive_path(model_id)
            _models[model_id] = record
            return record
        raise HTTPException(status_code=404, detail=f"model {model_id!r} not found")
    return record


#: A model id is generated here (``uuid4().hex``), but on the way back in it is a
#: client string, and the one rule for those is that they are never joined onto a
#: path unchecked -- the same discipline ``solver_plots`` applies to its segments.
_SAFE_MODEL_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")

#: The suffixes of a model *source*: the file the user wrote, as opposed to the
#: model CUFLynx runs. Ordered by how directly they are the model -- an
#: external_python model **is** its ``.py``, while a ``.mmt`` or an EasyML
#: ``.model`` sits beside the CellML it was converted into at import (#27).
#: A plain CellML model has no
#: entry here on purpose: it is edited in PhLynx, and the flattened document
#: CUFLynx generated is not a file the user has ever seen.
MODEL_SOURCE_SUFFIXES = (".py", ".mmt", ".model")


def _save_model_source(model_id: str, suffix: str, data: bytes) -> None:
    """Keep the dropped file beside the model derived from it.

    Only for a model whose source is *not* what gets simulated: a ``.mmt`` is
    converted to CellML at the door, so without this the file the user wrote
    exists nowhere on the server and "show me my model" has nothing to show.
    The converted CellML is still written exactly as before -- it is what every
    simulation path uses, and this copy is only ever read back to look at.
    """
    with contextlib.suppress(OSError):
        (UPLOAD_DIR / f"{model_id}{suffix}").write_bytes(data)


def _model_source_path(model_id: str) -> Path | None:
    """The stored source for *model_id*, or None when it has none."""
    if not _SAFE_MODEL_ID.match(str(model_id or "")):
        return None
    for suffix in MODEL_SOURCE_SUFFIXES:
        path = UPLOAD_DIR / f"{model_id}{suffix}"
        if path.is_file():
            return path
    return None


#: The archive a study was loaded from, kept whole beside the model derived from
#: it. Deliberately **not** in ``MODEL_SOURCE_SUFFIXES``: that tuple drives the
#: "Edit source" button, and an ``.omex`` there would try to open a zip in the
#: user's text editor. Its only reader is the PhLynx send, which has to return
#: every member CUFLynx does not understand byte-for-byte (#287/#290).
MODEL_ARCHIVE_SUFFIX = ".omex"


def _save_model_archive(model_id: str, data: bytes) -> Path | None:
    """Keep the uploaded archive; never fatal, the study still loaded without it."""
    path = UPLOAD_DIR / f"{model_id}{MODEL_ARCHIVE_SUFFIX}"
    with contextlib.suppress(OSError):
        path.write_bytes(data)
        return path
    return None


def _model_archive_path(model_id: str) -> Path | None:
    """The stored source archive for *model_id*, or None when it has none."""
    if not _SAFE_MODEL_ID.match(str(model_id or "")):
        return None
    path = UPLOAD_DIR / f"{model_id}{MODEL_ARCHIVE_SUFFIX}"
    return path if path.is_file() else None


#: Where a model's ``file_prefix`` is kept so it survives the in-memory registry.
#: ``_get_model`` re-derives a record from the uploaded file after a dev-server
#: reload, and the uploaded file is named by model_id -- so without this the study
#: would quietly change prefix mid-session and its next run would land in a second
#: directory beside the first.
PREFIX_SUFFIX = ".prefix"


def _remember_prefix(model_id: str, filename: str | None) -> str:
    """The study's prefix from the file it was loaded as, kept beside the upload."""
    prefix = _display_stem(Path(str(filename or "")).stem, "model")
    with contextlib.suppress(OSError):
        (UPLOAD_DIR / f"{model_id}{PREFIX_SUFFIX}").write_text(prefix, encoding="utf-8")
    return prefix


def _recover_prefix(model_id: str) -> str | None:
    try:
        return (UPLOAD_DIR / f"{model_id}{PREFIX_SUFFIX}").read_text(encoding="utf-8").strip()
    except OSError:
        return None


def _study_dir(model_id: str) -> Path:
    """This model's own corner of the upload directory.

    The study's obs_data and params_for_id are named after the study rather than
    after the session -- see :func:`_study_file` -- so they need somewhere the
    name cannot collide with another model loaded in the same session. The
    directory carries the model_id; the files carry the study's name.
    """
    directory = UPLOAD_DIR / model_id
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def _study_file(record: "_ModelRecord", suffix: str) -> Path:
    """Where a study's obs_data / params_for_id is kept for CA to read.

    **The name matters to circulatory_autogen.** CA takes its
    ``param_id_obs_file_prefix`` from the obs_data's *filename*, and builds the
    run directory (``<method>_<file_prefix>_<obs_prefix>``) and the emulator
    directory out of it. These files used to be named ``<model_id>_obs_data.json``
    -- a session uuid -- so a real run landed in
    ``genetic_algorithm_Study_2e40cca71775406d85df803806997208_obs_data``, and
    re-uploading the same obs_data produced a *different* uuid and therefore a
    different run directory: results for one study scattered across as many
    directories as the session had upload events, none of them reopening as the
    same study.
    """
    return _study_dir(record.model_id) / f"{_record_prefix(record)}{suffix}"


def _record_prefix(record: "_ModelRecord", fallback: str = "model") -> str:
    """The study's ``file_prefix`` -- what CA names its outputs after.

    Every analysis run, the export bundle and the model's own directory ask here,
    so a study has exactly one name on disk. Falls back rather than raising: a
    record recovered after a dev-server reload may predate its sidecar, and a run
    under "model" is better than a 500.
    """
    return _display_stem(getattr(record, "file_prefix", None) or "", fallback)


def _display_stem(name: str, fallback: str) -> str:
    """A filename stem safe to put in a Content-Disposition header."""
    stem = re.sub(r"[^A-Za-z0-9._-]+", "_", str(name or "")).strip("._-")
    return stem or fallback


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
    """Liveness, and *which* app is alive.

    ``app`` and ``version`` exist for PhLynx: it finds a running CUFLynx by
    probing a small range of ports (:data:`RECEIVE_PORTS`), and without a marker
    it could not tell us from anything else that answers ``/api/health`` on 8787 --
    and would post a study at it.
    """
    return {"status": "ok", "app": APP_NAME, "version": __version__}


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
    # (cellml / python / casadi_python); solver must be compatible with it;
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
    emulator.python = path
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

    # Canonicalised on the way in: settings persist across upgrades, so a config
    # written before CA renamed the format still names the old one. Left alone it
    # would fail the /api/config validation below and the user would have to
    # re-pick a setting they never changed.
    fmt = canonical_model_type((saved.get("generated_model_format") or "").strip())
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
        #
        # The packaged app bundles libcuflynx (#18), so an unset directory no longer
        # means CA is absent: an importable package is CA, and prompting for a
        # directory the user does not need is the wrong first run. A configured
        # directory still wins -- that is how a developer points the app at a
        # checkout -- and this only decides whether CA is *present*, never which
        # one is used; ca_imports owns that.
        "ca_exists": (bool(src) and p.is_dir()) or ca_imports.installed_package_available(),
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
        # The new directory may use the other module layout (flat vs libcuflynx.,
        # CA #437), and reset_solver_options() below re-introspects CA before
        # engine.reset() gets to it.
        ca_imports.reset_cache()


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

    # An .omex or a saved study can carry the retired spelling too; accept it and
    # store the current one, so it is normalised once here rather than everywhere
    # engine.model_type is read.
    fmt = canonical_model_type((req.generated_model_format or "").strip())
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
    file_prefix = req.file_prefix.strip() or _record_prefix(record)
    # The model lives where circulatory_autogen resolves model_path:
    # generated_models/<prefix>/<prefix>.cellml. obs/params go in resources/.
    # The suffix is the uploaded file's own: an external_python model is a .py,
    # and copying it out as ".cellml" would hand CA a file whose name says it is
    # something it is not.
    model_file = file_prefix + (Path(record.path).suffix or ".cellml")
    external_model = Path(record.path).suffix.lower() == ".py"
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
        # include_modules only for an external_python study: the stored
        # user_model.py is whatever .py was uploaded last under this output dir,
        # and a CellML study's bundle must not carry an external_model_path.
        for key, src in user_func_paths(
            req.config_outputs_dir or None, include_modules=external_model
        ).items():
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
            # The exported bundle is a user_inputs.yaml for the CA the user has,
            # so it is written in that CA's spelling for the same reason a run
            # config is (see ca_model_type). A current CA accepts either.
            model_type=ca_model_type(engine.model_type),
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
    return FileResponse(
        path,
        media_type=example_media_type(filename),
        filename=filename,
        # An example is a file that ships with the app, so it changes when the app
        # does -- and the browser is the one that fetches it and posts the bytes
        # back to the upload route. Without this the response carried only an
        # etag, which lets a browser reuse a cached copy without asking: an
        # updated example then imports as the old one, from a server that is
        # serving the new one. `no-cache` still allows a 304 on the etag.
        headers={"Cache-Control": "no-cache"},
    )


def _obs_data_document(record, protocol_info=None) -> dict | None:
    """The loaded obs_data as CA's parser wants it: one dict with protocol_info.

    obs_cost hands this to CA so the cost is computed by the same code the
    calibration runs, rather than reproduced from the data_items here.
    """
    obs = getattr(record, "obs_data", None)
    if obs is None:
        return None
    proto = protocol_info if protocol_info is not None else obs.protocol_info
    document = {
        "data_items": obs.data_items,
        "prediction_items": obs.prediction_items,
    }
    # An obs_data with no protocol_info is valid and common -- it says what to measure
    # without saying how to drive the model, and CA builds the timeline from
    # sim_time/pre_time instead. Returning None for it meant a protocol-less study was
    # reported as having no obs_data at all, and the emulator's cost (which has no
    # fallback, by design) simply never appeared. The key is omitted rather than set to
    # None because CA's parser refuses an explicit None where it accepts an absence.
    if proto is not None:
        document["protocol_info"] = proto
    return document


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
    try:
        info, notes = mmt_protocol.protocol_info_from_mmt(data, filename=filename)
    except mmt_protocol.MmtProtocolError as exc:
        return _offer_protocol(None, [], filename, out_dir, reason=str(exc))
    return _offer_protocol(info, notes, filename, out_dir)


def _protocol_from_easyml(read: dict, filename: str, out_dir: str) -> dict:
    """The stimulus synthesised for an EasyML model, as an obs_data document.

    An EasyML file carries no stimulus at all -- openCARP's own driver supplies
    one from the command line -- so unlike a ``.mmt`` there is nothing here to
    convert, only a default to propose. That is the more important reason to
    offer it rather than apply it, and the reason the notes say what was chosen.
    """
    return _offer_protocol(
        read.get("protocol_info"),
        list(read.get("protocol_notes") or []),
        filename,
        out_dir,
        reason=read.get("protocol_reason"),
    )


def _offer_protocol(info, notes, filename: str, out_dir: str, reason: str | None = None) -> dict:
    """One protocol offer, however it was arrived at.

    Shared because the two callers differ only in where the schedule came from,
    and the part after that -- what the document looks like, where the copy goes,
    and the refusal to overwrite -- is the part worth having in one place.
    """
    stem = Path(filename).stem or "model"
    obs_name = f"{stem}_obs_data.json"
    if info is None:
        return {"filename": obs_name, "obs_data": None, "notes": [], "reason": reason}

    # data_items are the user's to write -- what the model should be measured
    # against is not in the model file. An empty list is a valid obs_data
    # document, and is enough to run the protocol.
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

    # An external_python model: the user's own solver class, run by CA's
    # ExternalSimulationHelper. Decided by extension and only for a single file,
    # because a .py has no sniffable signature and a bundle means "a model and
    # the sisters it imports", which is a CellML idea.
    #
    # First, before the Myokit sniff: those heuristics read *content*, and a
    # Python module that mentions a Myokit-ish word is still a Python module.
    # Parsed by AST, never imported -- see py_model_meta.
    if single and looks_like_py_filename(only_name):
        try:
            meta = parse_py_model(only_bytes)
        except PyModelParseError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        model_id = uuid.uuid4().hex
        path = UPLOAD_DIR / f"{model_id}.py"
        path.write_bytes(only_bytes)
        _models[model_id] = _ModelRecord(
            model_id, path, meta, _remember_prefix(model_id, only_name))
        # The study's own copy, beside its user funcs (CA's external_model_path).
        # It travels with the export the way an operation func does -- and it is
        # what runs: resolve_model_path prefers it over the uploaded temp file,
        # so "Edit source" opens the model rather than a doomed sibling of it.
        # Best-effort: an unwritable output dir must not fail the upload, which
        # has already succeeded everywhere that matters.
        with contextlib.suppress(OSError):
            save_model_module(only_bytes, _user_func_base_dir(output_dir or ""))
        return {
            "model_id": model_id,
            "name": meta.name,
            "variable_count": meta.variable_count,
            "params": meta.params,
            "odes": meta.odes,
            # The one field a CellML/mmt upload does not carry. Its presence is
            # what tells the client this model is run by the user's own code --
            # different solver menu, possible extra plots, no generated model.
            "model_format": "external_python",
            "converted_from": None,
            "converted_cellml_path": None,
            "protocol_obs_data": None,
        }

    # A Myokit model is converted to CellML on the way in (#27), so everything
    # downstream -- the metadata parser, params_for_id naming, the exported
    # pipeline, CA itself -- keeps seeing the CellML it already expects.
    converted_from = None
    converted_path = None
    protocol: dict | None = None
    # Anything the reader had to decide for itself. Empty for CellML; for an
    # EasyML model it is never empty, because that format leaves the membrane
    # equation out and something had to be put in its place.
    import_warnings: list[str] = []
    # The bytes the user dropped, kept once the model_id exists: the conversion
    # below replaces `only_bytes` with CellML, and the original would otherwise
    # be gone the moment this request returns.
    source_suffix: str | None = None
    source_bytes: bytes | None = None
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
        source_suffix, source_bytes = ".mmt", mmt_bytes
        raw_by_name = {Path(only_name).stem + ".cellml": only_bytes}
        # The [[protocol]] section the model import deliberately leaves behind.
        # Offered rather than applied: the client decides, because a model
        # dropped alongside an obs_data the user wrote must not be overridden.
        protocol = _protocol_from_mmt(mmt_bytes, only_name, out_base)
    elif single and easyml_import.wants_easyml(only_name, only_bytes):
        # openCARP's EasyML rides the same rail, for the same reason: by the time
        # the model reaches the engine it is CellML, so no solver, model type or
        # packaging anywhere downstream has to know this format exists.
        out_base = _user_func_base_dir(output_dir or "")
        easyml_bytes = only_bytes
        try:
            read = easyml_import.import_easyml(
                only_bytes,
                filename=only_name,
                out_dir=out_base,
            )
        except easyml_import.EasyMLImportError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        only_bytes = read["cellml"]
        converted_path = read["cellml_path"]
        converted_from = only_name
        source_suffix, source_bytes = ".model", easyml_bytes
        raw_by_name = {Path(only_name).stem + ".cellml": only_bytes}
        # These are not diagnostics. A synthesised membrane equation and a gate
        # started at its steady state are places where the imported model differs
        # from the file, and the user is the only one who can judge them.
        import_warnings = list(read["warnings"])
        protocol = _protocol_from_easyml(read, only_name, out_base)

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
    if source_bytes is not None and source_suffix is not None:
        _save_model_source(model_id, source_suffix, source_bytes)
    # The file the user dropped names the study, whatever the CellML calls itself
    # inside. A .mmt converted at the door keeps its own stem for the same reason:
    # the study is the thing the user has a name for.
    source_name = converted_from or (main_name if not single else only_name)
    _models[model_id] = _ModelRecord(
        model_id, path, meta, _remember_prefix(model_id, source_name))

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
        # The .mmt's [[protocol]] section -- or an EasyML model's synthesised
        # stimulus -- as obs_data, for the client to adopt if it has no obs_data
        # of its own. None for CellML, and when the protocol cannot be produced
        # -- in which case `reason` says why.
        "protocol_obs_data": protocol,
        # What the import had to decide. Shown, not logged: see the comment
        # where import_warnings is set.
        "warnings": import_warnings,
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
    return import_omex_bytes(await file.read(), output_dir)


def _no_obs_data_warning(parts: dict, source: str = "archive") -> list[str]:
    """Why an archive's observations tab is empty, in one sentence.

    An archive with no obs_data is perfectly valid and must still load (#149), so
    this is a warning and not an error. But "the study had no observations" and
    "the observations were there and CUFLynx passed over them" produced the same
    thing on screen -- nothing -- and the second is a bug in the file that the
    user could fix in a minute if anyone told them about it.
    """
    # Only the members CUFLynx could not read. One it *could* read and ruled out
    # is not observations, and neither is an archive that simply carries none --
    # a PhLynx project export has a model, an editor state and a simulation
    # settings file and was never going to have observations. Saying so every
    # time is how a banner becomes something people click past, which costs the
    # one case below that is worth reading.
    unreadable = [s for s in (parts.get("obs_skipped") or []) if not s.get("identified")]
    if unreadable:
        looked_at = "; ".join(f"{s['name']} ({s['reason']})" for s in unreadable)
        return [
            f"No obs_data was loaded. Could not read: {looked_at}. A member with 'obs' in "
            "its name is always taken as the obs_data."
        ]
    if source == "archive":
        # Neither an obs_data nor a params_for_id is required to drop a study in,
        # and most archives that carry a model carry neither -- a PhLynx project
        # export never does. The empty tabs already say so, and a banner on the
        # ordinary case is how people learn to click past the one below.
        return []
    # Reopening a run directory is a different claim: this is a study that was
    # *run*, so observations are what it was scored against, and their absence is
    # worth the one sentence.
    missing = "obs_data" if parts.get("params") else "obs_data or params_for_id"
    return [f"This {source} carries no {missing}, so the observations tab is empty."]


def import_omex_bytes(data: bytes, output_dir: str | None = None,
                      source: str = "archive") -> dict:
    """Load an archive's bytes, whatever delivered them.

    Split out from the upload route so the PhLynx inbox loads a study through the
    **same** code, rather than growing a second importer beside it: an archive
    that arrived over localhost has to behave exactly like one that was dropped,
    and the way to guarantee that is for there to be one body.
    """
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
    import_warnings: list[str] = []
    source_suffix: str | None = None
    source_bytes: bytes | None = None
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
        source_suffix, source_bytes = ".mmt", mmt_bytes
        protocol = _protocol_from_mmt(mmt_bytes, only_name, out_dir)
        raw_by_name = {Path(only_name).stem + ".cellml": cellml_bytes}
        parts = {**parts, "master": Path(only_name).stem + ".cellml"}
    elif len(raw_by_name) == 1 and easyml_import.wants_easyml(
        parts["master"] or "", next(iter(raw_by_name.values()))
    ):
        only_name = parts["master"]
        easyml_bytes = raw_by_name[only_name]
        try:
            read = easyml_import.import_easyml(
                easyml_bytes, filename=only_name, out_dir=out_dir
            )
        except easyml_import.EasyMLImportError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        converted_from = only_name
        source_suffix, source_bytes = ".model", easyml_bytes
        protocol = _protocol_from_easyml(read, only_name, out_dir)
        import_warnings = list(read["warnings"])
        raw_by_name = {Path(only_name).stem + ".cellml": read["cellml"]}
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
    if source_bytes is not None and source_suffix is not None:
        _save_model_source(model_id, source_suffix, source_bytes)
    _models[model_id] = _ModelRecord(
        model_id, path, meta, _remember_prefix(model_id, converted_from or parts["master"]))
    # The archive itself, whole. Everything CUFLynx does not understand -- the
    # manifest, SED-ML, `simulation.json`, PhLynx's `flow.json` -- has to come
    # back untouched when the study is sent on (#287), and it cannot come back
    # from four extracted roles.
    _models[model_id].archive_path = _save_model_archive(model_id, data)

    # Everything the import could not do, in the user's words rather than in
    # silence: a part that was never found, a check that could not run, editor
    # state that could not be kept. The study still loads -- but an empty tab
    # must never be the only thing that says so.
    load_warnings: list[str] = list(import_warnings)

    result = {
        "model_id": model_id,
        "warnings": load_warnings,
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
            obs_path = _study_file(_models[model_id], "_obs_data.json")
            obs_path.write_bytes(blob)
            _models[model_id].obs_path = obs_path
            result["obs_data"] = {
                "filename": name,
                **parsed.summary(),
                "data_items": parsed.data_items,
                "prediction_items": parsed.prediction_items,
                "protocol_info": parsed.protocol_info,
            }
            load_warnings.extend(parsed.warnings)
        except (ValueError, ObsDataError) as exc:
            result["obs_data"] = {"filename": name, "error": str(exc)}

    if parts["params"]:
        name, blob = parts["params"]
        try:
            entries = parse_params_for_id(blob, meta.initial_values)
            _models[model_id].params_path = _save_params_file(_models[model_id], blob)
            result["params_for_id"] = {
                "filename": name,
                "params": [e.as_dict() for e in entries],
            }
        except ParamsForIdError as exc:
            result["params_for_id"] = {"filename": name, "error": str(exc)}

    for cfg_name, blob in parts.get("phlynx_state") or []:
        # Beside the model in `generated_models/<prefix>/`, not among the run
        # outputs: this is PhLynx's editor state for that model, not a result of
        # anything. Same layout the export bundle uses, so the archive round-trips
        # into a folder CA already understands.
        saved = omex_import.save_module_config(
            blob, _model_dir(out_dir, _record_prefix(_models[model_id])), cfg_name
        )
        # The first one kept is the one reported: the field predates PhLynx
        # splitting its workspace across two files, and a client that reads it is
        # asking "was the layout kept", not "where is each part".
        if result["module_config_path"] is None:
            result["module_config_path"] = saved
        # Only when there was somewhere to put it. With no outputs directory this
        # copy is simply not made, and nothing is lost: the archive is kept whole
        # (`_save_model_archive`) and `omex_export` re-emits every member it did
        # not understand byte-for-byte, so PhLynx's state still round-trips. A
        # banner on every import until a directory is set would be noise -- and a
        # banner nobody reads is how the real failure below gets missed.
        if out_dir and saved is None:
            load_warnings.append(
                f"PhLynx's {cfg_name} could not be kept beside the model: it is not valid "
                "JSON, or the directory could not be written. The study loaded, and the "
                "archive still round-trips."
            )

    # An obs_data in the archive is the author's own and always wins; only when
    # there is none does the .mmt's protocol become the study's protocol.
    if result["obs_data"] is None and protocol and protocol.get("obs_data"):
        try:
            parsed = parse_obs_data(protocol["obs_data"])
        except (ValueError, ObsDataError) as exc:
            # The fallback failing is not the archive's fault, but it is the
            # difference between an observations tab with a protocol in it and an
            # empty one -- which is exactly the kind of thing that used to happen
            # without a word.
            load_warnings.append(
                f"The protocol in {converted_from or 'the .mmt'} could not be used as an "
                f"obs_data: {exc}"
            )
            parsed = None
        else:
            load_warnings.extend(parsed.warnings)
        if parsed is not None:
            _models[model_id].obs_data = parsed
            obs_path = _study_file(_models[model_id], "_obs_data.json")
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

    # Last, so that an archive whose observations came from its own .mmt protocol
    # is not told it carries none: the slot is what matters, not where it was
    # filled from.
    if result["obs_data"] is None:
        load_warnings.extend(_no_obs_data_warning(parts, source))

    return result


class PhlynxSendRequest(BaseModel):
    model_id: str
    #: Which parameter values are written into the model on the way out.
    #: ``current`` -- the sliders, ``best_fit`` -- the last calibration,
    #: ``as_imported`` -- none, the model exactly as CUFLynx holds it.
    source: str = "current"
    values: dict[str, float] = Field(default_factory=dict)  # for source="current"
    output_dir: str = ""  # for source="best_fit"
    #: Return the archive as a file instead of base64, for the size fallback: a
    #: browser silently drops an over-long URL, so past a point the send becomes
    #: a download the user drops into PhLynx by hand.
    download: bool = False


#: Base64 longer than this is not worth putting in a URL fragment -- browsers
#: differ on where they truncate and none of them says so. Above it the frontend
#: offers the archive as a file instead.
PHLYNX_URL_LIMIT = 1_500_000


@app.post("/api/phlynx/send")
def phlynx_send(req: PhlynxSendRequest):
    """Build the study as a COMBINE archive for PhLynx (#290).

    The archive is assembled here rather than in the browser so there is one
    writer, and so the frontend keeps assuming nothing about a local backend: it
    receives base64 and does ``window.open``.

    Every member of the archive the study was loaded from is returned
    byte-for-byte except the model (flattened, with the chosen values
    substituted) and params_for_id (refreshed from the study). obs_data is never
    replaced, only added when the archive has none. See :mod:`omex_export`.
    """
    record = _get_model(req.model_id)
    if record.path.suffix.lower() != ".cellml":
        raise HTTPException(
            status_code=422,
            detail="only a CellML model can be sent to PhLynx, which is a CellML builder",
        )

    if req.source == "current":
        values = dict(req.values)
    elif req.source == "best_fit":
        out_dir = _user_func_base_dir(req.output_dir)
        best = ca_run_history.best_param_values(out_dir or "")["params"] if out_dir else {}
        if not best:
            raise HTTPException(
                status_code=422,
                detail="no calibration best fit was found in the outputs directory",
            )
        values = best
    elif req.source == "as_imported":
        values = {}
    else:
        raise HTTPException(status_code=422, detail=f"unknown source {req.source!r}")

    _validate_param_keys(values)

    stem = _record_prefix(record, "study")
    archive = record.archive_path.read_bytes() if record.archive_path else None
    try:
        blob, report = omex_export.build_archive(
            cellml_text=record.path.read_text(encoding="utf-8"),
            values=values,
            source_archive=archive,
            obs_bytes=record.obs_path.read_bytes() if record.obs_path else None,
            obs_name=f"{stem}_obs_data.json",
            params_bytes=record.params_path.read_bytes() if record.params_path else None,
            params_name=f"{stem}_params_for_id{record.params_path.suffix}"
            if record.params_path
            else "params_for_id.json",
        )
    except omex_export.OmexExportError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"could not read the study: {exc}") from exc

    filename = f"{stem}.omex"
    if req.download:
        return Response(
            content=blob,
            media_type="application/zip",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    encoded = base64.b64encode(blob).decode("ascii")
    return {
        "base64": encoded,
        "bytes": len(blob),
        "too_large": len(encoded) > PHLYNX_URL_LIMIT,
        "limit": PHLYNX_URL_LIMIT,
        "filename": filename,
        "members": report["members"],
        "member_count": len(report["members"]),
        "updated": report["updated"],
        # Named, never silently dropped: a parameter that could not be written
        # into the model is a value PhLynx will not see.
        "unresolved": report["unresolved"],
        # Written, but into a component PhLynx does not read parameter changes
        # back out of (#287) -- so the value travels and is ignored.
        "outside_parameters": report["outside_parameters"],
    }


#: A delivered archive is a payload from a web page, so it is capped well below
#: anything a real study reaches. The point is that an accidental or hostile
#: multi-gigabyte post cannot sit in this process's memory.
MAX_INBOX_BYTES = 64 * 1024 * 1024


@app.post("/api/inbox")
async def deliver_to_inbox(request: Request) -> dict:
    """Accept a study from PhLynx and **stage** it for the user to confirm (#287).

    Deliberately not an import. CORS stops a page reading our responses; it does
    not stop it sending, so anything running in the user's browser can reach this.
    Staging is what makes the confirmation in the UI possible -- a route that
    imported on arrival would have nothing left to ask about.

    The archive is validated far enough to fail *here* rather than at accept time,
    so PhLynx learns it sent something unreadable instead of the user learning it
    a minute later.
    """
    data = await request.body()
    if not data:
        raise HTTPException(status_code=422, detail="no archive in the request body")
    if len(data) > MAX_INBOX_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"archive is larger than the {MAX_INBOX_BYTES // (1024 * 1024)} MB limit",
        )
    try:
        omex_import.unpack(data)
    except omex_import.OmexImportError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    origin = request.headers.get("origin") or "an unknown page"
    filename = _display_stem(request.query_params.get("filename") or "", "study") + ".omex"
    return inbox.deliver(data, origin, filename)


@app.get("/api/inbox")
def peek_inbox() -> dict:
    """What is waiting, if anything -- metadata only, never the archive.

    Polled by the UI, which has no push channel: the frontend deliberately assumes
    no local backend, and giving it one for this would be a bigger change than the
    feature.
    """
    return {"pending": inbox.peek()}


@app.post("/api/inbox/accept")
def accept_inbox(output_dir: str | None = Query(default=None)) -> dict:
    """Load the staged study, returning exactly what ``/api/omex/upload`` returns.

    Same body, so the frontend feeds it into the same `model-loaded` /
    `obs-data-loaded` / `params-loaded` flow a dropped archive takes -- there is
    one importer (:func:`import_omex_bytes`) and one set of emits, not a second
    pair that can drift from the first.
    """
    pending = inbox.take()
    if pending is None:
        raise HTTPException(status_code=404, detail="nothing is waiting in the inbox")
    result = import_omex_bytes(pending.data, output_dir)
    return {**result, "delivered_from": pending.origin}


@app.post("/api/inbox/reject")
def reject_inbox() -> dict:
    """Discard without importing."""
    return {"discarded": inbox.clear()}


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


@app.get("/api/models/{model_id}/source")
def get_model_source(model_id: str, config_outputs_dir: str = "") -> FileResponse:
    """The file the user wrote, for a model that has one (#91 follow-up).

    Two kinds of model do. An ``external_python`` model **is** its ``.py``; a
    Myokit ``.mmt`` and an EasyML ``.model`` are kept beside the CellML they were
    converted into at import. A plain CellML model has none: it is edited in
    PhLynx, so this 404s
    rather than answering with the flattened document CUFLynx generated, which is
    not a file the user has ever seen.

    Served as ``text/plain`` **inline**: a browser tab showing the source. This
    is no longer what the Edit button does — that opens the file in the user's
    own editor — but it is what a remote or headless deployment *can* do, and
    reading your own model is worth a route on its own.

    ``config_outputs_dir`` makes it show the same file the editor would: once a
    study copy exists under the outputs directory, that copy is the model, and
    answering with the uploaded original would show a version the user has since
    edited away from.

    The model id is validated before anything is joined onto a path — the same
    rule ``solver_plots`` follows; a client string never becomes a path segment.
    """
    if not _SAFE_MODEL_ID.match(str(model_id or "")):
        raise HTTPException(status_code=404, detail="model source not found")
    record = _get_model(model_id)
    path = _model_source_path(model_id)
    if path is None:
        raise HTTPException(
            status_code=404,
            detail="this model has no source file to show — a CellML model is edited in PhLynx",
        )
    base_dir = _user_func_base_dir(config_outputs_dir)
    if base_dir:
        study_copy = study_model_source_path(path.suffix, base_dir)
        if study_copy.is_file():
            path = study_copy
    stem = _record_prefix(record, model_id)
    return FileResponse(
        path,
        media_type="text/plain; charset=utf-8",
        filename=f"{stem}{path.suffix}",
        content_disposition_type="inline",
    )


class ModelEditRequest(BaseModel):
    #: The study's outputs directory. Required in practice — see the route: the
    #: editable copy has to live somewhere the study keeps, and there is no
    #: sensible temp-dir default for a file the user is about to invest work in.
    config_outputs_dir: str = ""


@app.post("/api/models/{model_id}/edit")
def edit_model_source(model_id: str, req: ModelEditRequest) -> dict:
    """Put the model's source under the outputs directory and open it for editing.

    Three things happen, in this order, and the order is the point:

    1. **The source is copied into the study**, at
       ``<outputs>/user_funcs/user_model.<py|mmt>`` — beside the funcs, which is
       already where an external_python model's ``.py`` is kept and what CA is
       handed as ``external_model_path``. Only when it is not there yet: once it
       exists it *is* the file, and re-pressing Edit must never overwrite the
       work the last press produced.
    2. **For a ``.py``, that copy becomes the model that runs.**
       ``resolve_model_path`` prefers it over the uploaded temp file, on every
       tier — the live engine, the sim worker, and the calibration / sensitivity
       / UQ runners. Editing a file nothing executes would be a worse trap than
       the TTL-pruned temp copy this replaces.
    3. **It is opened in the user's editor** (``$VISUAL`` / ``$EDITOR``, else the
       platform's default handler). A launch that cannot happen — headless
       server, no handler — is reported as ``opened: false`` with a reason, not
       as a failure: the caller still knows where the file is, which is most of
       what was wanted.

    A ``.mmt`` is copied and opened the same way but is **not** what runs: it was
    converted to CellML at import, so ``runs`` comes back false and the caller
    says the edited file has to be dropped back in to take effect.

    Editing changes the *program*, not CUFLynx's idea of it: a ``.py`` whose
    ``parameters`` or ``output_names`` change needs a re-upload for the sliders
    to follow, because those were read by AST at the door.
    """
    if not _SAFE_MODEL_ID.match(str(model_id or "")):
        raise HTTPException(status_code=404, detail="model source not found")
    record = _get_model(model_id)
    source = _model_source_path(model_id)
    if source is None:
        raise HTTPException(
            status_code=404,
            detail="this model has no source file to edit — a CellML model is edited in PhLynx",
        )

    base_dir = _user_func_base_dir(req.config_outputs_dir)
    if not base_dir:
        # Deliberately a refusal rather than a temp-dir fallback: an edited model
        # dropped in the same pruned scratch directory this change exists to get
        # out of would be lost, and silently.
        raise HTTPException(
            status_code=422,
            detail="no outputs directory is set, so there is nowhere to keep an "
            "editable copy of the model. Choose an outputs directory first, then "
            "press Edit source again.",
        )

    target = study_model_source_path(source.suffix, base_dir)
    try:
        if not target.is_file():
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(source.read_bytes())
    except OSError as exc:
        raise HTTPException(
            status_code=422,
            detail=f"could not write the model into {target.parent}: {exc}",
        ) from exc

    launch = editor_launch.open_in_editor(target)
    return {
        "path": str(target),
        "filename": f"{_record_prefix(record, model_id)}{source.suffix}",
        "opened": bool(launch["opened"]),
        "editor": launch["editor"],
        "reason": launch["reason"],
        # Whether this copy is the one CUFLynx runs. True for external_python;
        # false for a .mmt or an EasyML .model, each of which is the source of a
        # CellML that runs instead.
        "runs": source.suffix == ".py",
    }


@app.get("/api/models/{model_id}/solver_plots/{token}/{index}.png")
def get_solver_plot(model_id: str, token: str, index: str) -> FileResponse:
    """One figure an external_python model drew for itself during a run.

    The URL is handed out by the simulate / protocol response's ``solver_plots``
    entries; ``token`` is that run's counter, so each run's images are a distinct
    resource the browser may cache forever. An unknown token or index is a 404
    — the run may simply have been pruned (only the last two are kept).

    All three path segments are validated as ids/integers before anything is
    joined onto a path; the client never supplies a path component directly.
    """
    path = solver_plots.plot_file(model_id, token, index)
    if path is None:
        raise HTTPException(status_code=404, detail="solver plot not found")
    return FileResponse(path, media_type="image/png")


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
    # The states, by default -- they are what a model is usually watched by. An
    # external_python model declares no states (it integrates itself), so for one
    # of those the default is its declared output_names instead; without this
    # "just simulate it" would ask for nothing and draw an empty plot.
    outputs = req.outputs or record.meta.odes or record.meta.algebraic
    # Resolved up front rather than beside the cost below: it also decides which
    # copy of an external_python model is the one that runs.
    output_dir = _user_func_base_dir(req.config_outputs_dir)
    try:
        # Resolve the path for the backend the *live* run will actually use: the
        # engine falls back when the configured format cannot run in-process
        # (#122), and a model generated for one backend is not readable by
        # another -- a generated .py handed to Myokit fails as invalid XML.
        live_type, live_solver, _fell_back = engine.live_backend()
        model_path = resolve_model_path(
            str(record.path), live_type, model_id=req.model_id, output_dir=output_dir
        )
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
    # See simulate(): also decides which copy of an external_python model runs.
    output_dir = _user_func_base_dir(req.config_outputs_dir)
    try:
        live_type, live_solver, _fell_back = engine.live_backend()
        model_path = resolve_model_path(
            str(record.path), live_type, model_id=req.model_id, output_dir=output_dir
        )
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


def _solver_cost_at(record, params: dict, *, model_id, model_path, protocol_info,
                    output_dir, sim_time: float, pre_time: float,
                    outputs: list[str] | None = None) -> dict | None:
    """A solver run at ``params``, scored -- the cost the Output plots show.

    Deliberately the *same* run the run routes make, down to which outputs are
    requested: a superset would score observables the panel's cost does not, and
    anything built on this -- a differenced gradient (#188), a best-fit
    comparison against the emulator (#333) -- would then belong to a different
    number.
    """
    if protocol_info is not None:
        wanted = outputs or (record.meta.odes + record.meta.algebraic)
        result = engine.run_protocol(
            model_id=model_id,
            model_path=model_path,
            protocol_info=protocol_info,
            params=params,
            outputs=_with_obs_operands(wanted, record),
        )
        return _protocol_run_cost(record, result, protocol_info, output_dir)
    result = engine.simulate(
        model_id=model_id,
        model_path=model_path,
        params=params,
        sim_time=sim_time,
        pre_time=pre_time,
        outputs=outputs or record.meta.odes,
    )
    return _single_run_cost(record, result, output_dir)


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
        model_path = resolve_model_path(
            str(record.path), live_type, model_id=req.model_id, output_dir=output_dir
        )
    except Exception as exc:  # noqa: BLE001 - a model that cannot be resolved owes a reason
        raise HTTPException(
            status_code=500, detail=engine.describe_exception(exc)
        ) from exc

    # Deliberately the *same* run each endpoint above makes, down to which
    # outputs are requested: a superset would score observables the panel's cost
    # does not, and the gradient would then belong to a different number.
    def cost_at(params: dict) -> dict | None:
        return _solver_cost_at(
            record, params,
            model_id=req.model_id,
            model_path=model_path,
            protocol_info=protocol_info,
            output_dir=output_dir,
            sim_time=req.sim_time,
            pre_time=req.pre_time,
            outputs=req.outputs,
        )

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


class CostAtParamsRequest(BaseModel):
    model_id: str
    # Physical model values, the way the sliders hand them to a run.
    params: dict[str, float] = Field(default_factory=dict)
    # The same point written as theta -- one value per params_for_id row, at a
    # modifier's anchor rather than its expansion (#208). The emulator was
    # trained on theta and must be given theta; `params` alone would feed a
    # modifier's expanded value to a surrogate that has never seen one. Absent
    # means "they are the same", which is true of a study with no modifiers.
    analysis_params: dict[str, float] | None = None
    sim_time: float = 10.0
    pre_time: float = 0.0
    outputs: list[str] | None = None
    protocol_info: dict | None = None
    config_outputs_dir: str | None = None


@app.post("/api/cost_at_params")
def cost_at_params(req: CostAtParamsRequest) -> dict:
    """One parameter set, scored twice: by the model and by the emulator (#333).

    The question this answers is the calibration one -- a fit found on the
    surrogate reports a cost and per-observable errors that describe the
    *emulator's* features, and there was no way to see what the model says at the
    same parameters. Both sides come back from a single request so that they are
    unarguably the same point: one ``params`` in, one theta derived from it, no
    opportunity for the two to be asked at different slider values.

    Both are scored through the one CA-backed path (``obs_cost``), so the
    difference between them is the surrogate's error and nothing else. The
    emulator side is absent -- never an error -- when there is no bundle, when it
    cannot be loaded, or when its features cannot be matched to the obs_data.
    """
    record = _get_model(req.model_id)
    _validate_param_keys(req.params)
    if record.obs_data is None:
        raise HTTPException(
            status_code=422,
            detail="no obs_data is loaded, so there is nothing to score against",
        )

    output_dir = _user_func_base_dir(req.config_outputs_dir)
    protocol_info = req.protocol_info
    if protocol_info is None:
        protocol_info = record.obs_data.protocol_info

    try:
        live_type, _live_solver, _fell_back = engine.live_backend()
        model_path = resolve_model_path(
            str(record.path), live_type, model_id=req.model_id, output_dir=output_dir
        )
        cost = _solver_cost_at(
            record, req.params,
            model_id=req.model_id,
            model_path=model_path,
            protocol_info=protocol_info,
            output_dir=output_dir,
            sim_time=req.sim_time,
            pre_time=req.pre_time,
            outputs=req.outputs,
        )
    except SimulationError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 - anything else still owes a reason
        raise HTTPException(
            status_code=500, detail=engine.describe_exception(exc)
        ) from exc

    return {
        "cost": cost,
        "emulator_cost": _emulator_cost_at(
            record,
            req.model_id,
            req.analysis_params if req.analysis_params is not None else req.params,
            req.config_outputs_dir,
            output_dir,
            protocol_info,
        ),
    }


def _emulator_cost_at(record, model_id: str, params: dict, config_outputs_dir,
                      output_dir, protocol_info=None) -> dict | None:
    """The emulator's cost at ``params``, or None if it cannot answer.

    Every reason it might not -- no bundle, a bundle trained on a different
    parameter set, no autoemulate in the configured interpreter -- is a silence
    rather than a failure: the solver's cost is still correct and complete, and
    an error banner over a missing second opinion would be worse than not
    offering one.
    """
    try:
        emu_dir = _emulator_dir_for(model_id, {"config_outputs_dir": config_outputs_dir or ""})
        metadata = ca_run_history.emulator_metadata(emu_dir)
        if metadata is None:
            return None
        theta = _emulator_theta(model_id, params, metadata)
        prediction = engine.emulator_predict(emu_dir, theta)
    except Exception:  # noqa: BLE001 - an emulator that cannot answer says nothing
        return None
    # This caller wants the cost alone: the Analysis toggle's legend says which source
    # is displayed, so an absent emulator cost there means "no emulator side to show"
    # rather than a state needing explanation.
    return _emulator_feature_cost(record, prediction, output_dir, protocol_info)[0]


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
        obs_path = _study_file(_models[model_id], "_obs_data.json")
        obs_path.write_text(json.dumps(obj), encoding="utf-8")
        _models[model_id].obs_path = obs_path

    saved_path = _save_edited_copy(
        output_dir, filename, json.dumps(obj, indent=1).encode("utf-8")
    )

    return {
        "model_id": model_id,
        # Where Save put the dated copy, so the panel can say it (#215).
        "saved_path": saved_path,
        # What could not be checked, or is written in a vocabulary on its way
        # out. The document loaded; these are the things a silent load hid.
        "warnings": parsed.warnings,
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


# --- Add from dataset: build obs_data from raw recordings -------------------
# Six thin routes over `obs_extract`, which is a package rather than a module so
# it can be lifted into its own repository. Each body parses, delegates and maps
# ObsExtractError to a 422; keeping them this thin is what keeps the directory
# movable. There is no APIRouter in this codebase and six endpoints is not the
# reason to introduce one.


class ObsExtractScanRequest(BaseModel):
    root: str
    recurse: bool = True
    suffixes: list[str] | None = None
    exclude: list[str] = []
    #: Per case_name reader settings from a config being re-scanned, so a .npy
    #: whose sample rate the user supplied stays readable across a rescan.
    reader_opts: dict = {}
    #: When given, the scan also suggests a model binding from its variables.
    model_id: str = ""


class ObsExtractConfigRequest(BaseModel):
    config: dict
    output_dir: str = ""
    filename: str = "obs_extraction_config.json"


class ObsExtractRunRequest(BaseModel):
    config: dict
    output_dir: str = ""
    model_id: str = ""


def _obs_extract_dir(output_dir: str) -> str:
    """Where this extraction's artefacts go.

    The outputs directory the user already chose, falling back to the config dir
    the way ``_save_edited_copy`` does -- so a Save never silently loses work
    because no directory was set.
    """
    base = _user_func_base_dir(output_dir) or str(settings_store.config_dir())
    return base


@app.get("/api/obs_extract/formats")
def obs_extract_formats() -> dict:
    """Which recording formats this install can read, and what is missing."""
    return {"formats": obs_extract.available_formats()}


@app.post("/api/obs_extract/scan")
def obs_extract_scan(req: ObsExtractScanRequest) -> dict:
    """Discover recordings under a directory, grouped and probed.

    In-request rather than a job: the probe reads headers only, and the full
    488-file reference corpus scans in a few seconds.
    """
    try:
        found = obs_extract.discover(
            req.root, recurse=req.recurse,
            suffixes=tuple(req.suffixes or obs_extract.SUPPORTED_SUFFIXES),
            exclude=tuple(req.exclude), reader_opts=req.reader_opts)
    except obs_extract.ObsExtractError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    if req.model_id:
        record = _get_model(req.model_id)
        meta = record.meta
        found["suggested_binding"] = obs_extract.suggested_binding({
            "params": meta.params, "odes": meta.odes, "algebraic": meta.algebraic,
            "all_names": meta.all_names, "units": meta.units,
        }).to_dict()
    return found


@app.get("/api/obs_extract/config")
def obs_extract_load_config(path: str) -> dict:
    """Read a saved extraction config.

    The one place a client-supplied path is read here. Same posture as
    ``/api/fs/list``, which is already a localhost filesystem tool: the suffix is
    checked so a mis-click cannot try to parse an arbitrary file as JSON.
    """
    if not str(path).lower().endswith(".json"):
        raise HTTPException(status_code=422,
                            detail="an extraction config must be a .json file")
    try:
        return {"config": obs_extract.config.load(path), "path": path}
    except obs_extract.ObsExtractError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/api/obs_extract/config")
def obs_extract_save_config(req: ObsExtractConfigRequest) -> dict:
    """Save the decisions, so the afternoon spent making them happens once."""
    name = os.path.basename(req.filename or "obs_extraction_config.json")
    path = os.path.join(_obs_extract_dir(req.output_dir), name)
    try:
        obs_extract.config.save(req.config, path)
    except obs_extract.ObsExtractError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"path": path}


@app.post("/api/obs_extract/run")
def obs_extract_run(req: ObsExtractRunRequest) -> dict:
    """Start an extraction. The per-sweep log is the product, so this is a job."""
    variables = None
    if req.model_id:
        meta = _get_model(req.model_id).meta
        variables = {"params": meta.params, "odes": meta.odes,
                     "algebraic": meta.algebraic, "all_names": meta.all_names,
                     "units": meta.units}
    output_dir = _obs_extract_dir(req.output_dir)
    try:
        job_id = obs_extract_jobs.start(
            req.config,
            operation_funcs=get_operation_funcs(_user_func_base_dir(req.output_dir)),
            variables=variables, output_dir=output_dir,
            cuflynx_version=__version__)
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except obs_extract.ObsExtractError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"job_id": job_id, "output_dir": output_dir}


@app.get("/api/obs_extract/{job_id}/status")
def obs_extract_status(job_id: str, offset: int = 0) -> dict:
    status = obs_extract_jobs.status(job_id, offset)
    if status is None:
        raise HTTPException(status_code=404, detail="extraction job not found")
    return status


@app.post("/api/obs_extract/{job_id}/cancel")
def obs_extract_cancel(job_id: str) -> dict:
    if not obs_extract_jobs.cancel(job_id):
        raise HTTPException(status_code=404, detail="extraction job not found")
    return {"cancelled": True}


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
    # user_funcs also carries a *module* kind (an external_python model's solver
    # class), which travels with a study like a func but is not one: it has no
    # list of top-level defs to list, validate or template. FUNC_KINDS is the
    # editor's own list, so a module kind can never acquire these routes.
    assert _kind in FUNC_KINDS, f"{_kind} is not an editable func kind"
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


@app.get("/api/outputs/load")
def load_outputs_directory(
    dir: str = Query(...),
    file_prefix: str | None = Query(default=None),
    obs_path: str | None = Query(default=None),
    run_dir: str | None = Query(default=None),
) -> dict:
    """Everything a finished run left in an outputs directory (#255, #256).

    The panels are filled by job polls, so today they only fill for a run
    started in this session -- a run produced by cuflynx-param-id, by a
    generated run_pipeline.py, or by this app yesterday, is invisible even
    though every file is there.

    Tolerant on purpose: a folder with a calibration and no UQ is a perfectly
    ordinary folder, so what could not be read is returned in ``missing`` rather
    than raising, and ``found`` says which panels have something to show.
    """
    return load_outputs.load_outputs(dir, file_prefix, obs_path, run_dir)


class OpenStudyRequest(BaseModel):
    #: The outputs directory the user picked, the same one `/api/outputs/load` reads.
    dir: str
    #: Which run's snapshots to open, when the directory holds several. Omitted
    #: takes the one `/api/outputs/load` chose.
    run_dir: str | None = None
    #: The loaded model's name, when there is one, for resolving `<prefix>_calibrated`.
    file_prefix: str | None = None


def _adopt_study_solver_info(solver_info) -> list[str]:
    """Apply a study's recorded solver settings to the engine. Returns what to report.

    Same shape, and the same handling, as the ``solver_info`` in a saved config: ``dt`` is
    folded in beside the solver's own keys and popped out here. Unsupported keys are
    dropped rather than rejected, because a manifest written by a newer pipeline must not
    stop an older app from opening the study at all.

    Reported rather than applied quietly. Adopting dt = 1e-4 where the app was at 0.01
    makes every subsequent simulation a hundred times slower, and a user watching that
    happen deserves the reason.
    """
    if not isinstance(solver_info, dict) or not solver_info:
        return []
    notes, si = [], dict(solver_info)
    if "dt" in si:
        try:
            new_dt = float(si.pop("dt"))
        except (TypeError, ValueError):
            new_dt = None
        if new_dt and new_dt > 0 and new_dt != engine.dt:
            notes.append(
                f"Solver dt set to {new_dt:g} s (was {engine.dt:g} s), as this study was "
                f"run. At the coarser step the output grid misses the fast parts of a "
                f"trace; simulations will be slower."
            )
            engine.dt = new_dt
    solver = si.pop("solver", None)
    if isinstance(solver, str) and solver and solver != engine.solver:
        notes.append(f"Solver set to {solver}, as this study was run.")
        engine.solver = solver
    si.pop("method", None)          # CA's own spelling of the solver; not a solver_info key here
    if si:
        engine.solver_info = {**getattr(engine, "solver_info", {}), **si}
        notes.append("Solver options taken from the study: "
                     + ", ".join(f"{k}={v}" for k, v in sorted(si.items())) + ".")
    return notes


def _finest_scored_obs_dt(obs_data_path) -> float | None:
    """The smallest ``obs_dt`` among series an obs_data will actually score, or None.

    This is the constraint CA enforces before it will compare a run against the data: the
    solver output has to be at least as finely sampled as the series it is resampled onto.
    Read from the obs_data itself rather than from the study manifest, which does not
    record a timestep, and rather than from a user_inputs.yaml, which a finished run
    directory need not contain.

    Only *scored* series count, matching CA: a zero-weighted item is dropped from the cost
    and an empty one has nothing to compare, so neither can require anything of dt.
    SN_full ships eight empty zero-weighted series at 1e-4, and honouring those would drop
    the app's timestep a hundredfold to satisfy placeholders that are never read.

    Unreadable is None, not an error: opening a study whose obs_data has moved should
    still show the results that are there.
    """
    if not obs_data_path or not os.path.isfile(str(obs_data_path)):
        return None
    try:
        with open(obs_data_path) as handle:
            document = json.load(handle)
    except (OSError, ValueError):
        return None
    items = document.get("data_items") if isinstance(document, dict) else document
    if not isinstance(items, list):
        return None
    finest = None
    for item in items:
        if not isinstance(item, dict) or item.get("data_type") != "series":
            continue
        try:
            if float(item.get("weight", 1.0)) == 0.0:
                continue
        except (TypeError, ValueError):
            pass
        value = item.get("value")
        if not isinstance(value, list) or not value:
            continue
        try:
            obs_dt = float(item["obs_dt"])
        except (KeyError, TypeError, ValueError):
            continue
        if obs_dt > 0 and (finest is None or obs_dt < finest):
            finest = obs_dt
    return finest


@app.post("/api/outputs/study")
def open_study_from_outputs(req: OpenStudyRequest) -> dict:
    """Load the model, obs_data and params_for_id a finished run was made from.

    ``/api/outputs/load`` reports the study's files as *paths*, which fill no
    panel: a user who reopened a directory got its results and an empty
    Parameters tab, and had to find the same three files on disk and drop them
    in by hand -- after which the results they had just loaded were replaced by
    an empty study.

    The three files are packed into an archive in memory and handed to
    :func:`import_omex_bytes`, rather than loaded by a second copy of the same
    logic. A study opened from a directory then behaves exactly like one dropped
    as a .omex or delivered by PhLynx -- same parsing, same warnings, same
    response shape -- which is the only way the three stay in step.
    """
    found = load_outputs.load_outputs(req.dir, req.file_prefix, run_dir=req.run_dir)
    if found.get("error"):
        raise HTTPException(status_code=422, detail=found["error"])

    study = found.get("study") or {}
    members: dict[str, bytes] = {}
    unreadable: list[str] = []

    def take(path, name):
        if not path:
            return
        try:
            members[name] = Path(path).read_bytes()
        except OSError as exc:
            unreadable.append(f"{os.path.basename(path)} ({exc.strerror or exc})")

    model_path = study.get("model")
    if model_path:
        take(model_path, os.path.basename(model_path))
    take(study.get("obs_data"), "study_obs_data.json")
    take(study.get("params_for_id"), "study_params_for_id.json")

    if not members.get(os.path.basename(model_path or "")):
        raise HTTPException(
            status_code=422,
            detail=(
                f"no model to open in {req.dir}: a study needs the CellML the run was "
                f"made from, under generated_models/<prefix>/ or as <prefix>_calibrated.cellml"
                + (f". Could not read: {'; '.join(unreadable)}" if unreadable else "")
            ),
        )

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, blob in members.items():
            zf.writestr(name, blob)

    result = import_omex_bytes(buf.getvalue(), req.dir, source="run directory")
    # Where each part came from, so the UI can say which run it reopened rather
    # than implying the user dropped these files.
    result["study_paths"] = {
        "model": model_path,
        "obs_data": study.get("obs_data"),
        "params_for_id": study.get("params_for_id"),
        "run_dir": found.get("run_dir"),
    }
    result["model_is_calibrated"] = bool(study.get("model_is_calibrated"))

    # Solve the study the way the study was solved. Opening a directory used to adopt
    # nothing, so a run configured for dt = 1e-4 with CVODE bounded by MaximumStep = 1e-4
    # was reopened at the app's DEFAULT_DT of 0.01 and no maximum step.
    #
    # For a spiking model that is not cosmetic. dt is the *output* interval, so a 10 ms
    # grid samples straight over every 1-4 ms action potential: the trace comes back
    # showing only the voltage between spikes and appears to sit at about -20 mV. Nothing
    # is wrong with the resting potential or the parameters, and there is nothing on
    # screen to say so.
    # From the manifest rather than from ``study``, which is a narrower projection built for
    # the panels and carries only the study's file paths.
    manifest = found.get("manifest") or {}
    adopted = _adopt_study_solver_info(
        manifest.get("solver_info") or study.get("solver_info"))
    for note in adopted:
        result["warnings"].append(note)

    # A study written before the manifest recorded solver settings has none, so fall back
    # to what its obs_data requires: CA will not compare a solver output against series
    # sampled finer than it. Only if the manifest did not already say.
    if not adopted:
        required = _finest_scored_obs_dt(study.get("obs_data"))
        if required is not None and required < engine.dt:
            result["warnings"].append(
                f"Solver dt lowered from {engine.dt:g} s to {required:g} s: this study's "
                f"series data is sampled that finely, and a coarser solver output cannot "
                f"be compared against it. Simulations will be slower."
            )
            engine.dt = required

    if unreadable:
        result["warnings"].append(
            "Some of the run's inputs could not be read: " + "; ".join(unreadable)
        )
    return result


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


def _save_params_file(record: "_ModelRecord", data: bytes | str) -> Path:
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
    # Named after the study, like the obs_data beside it -- see `_study_file`.
    path = _study_file(record, f"_params_for_id{suffix}")
    path.write_bytes(raw)
    other_suffix = ".csv" if suffix == ".json" else ".json"
    _study_file(record, f"_params_for_id{other_suffix}").unlink(missing_ok=True)
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
        _models[model_id].params_path = _save_params_file(_models[model_id], data)

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
        # The *configured* outputs dir, not the temp fallback below it: that is
        # where the study keeps its own copy of an external_python model, and the
        # runner must resolve the same file the live engine does.
        "model_path": resolve_model_path(
            str(record.path), engine.model_type, model_id=req.model_id,
            output_dir=configured or None,
        ),
        # Translated at the boundary: an older CA parses only its own spelling
        # of the model_type and exits on anything else (solver_options.
        # MODEL_TYPE_ALIASES). Inside CUFLynx it stays canonical.
        "model_type": ca_model_type(engine.model_type),
        "solver": engine.solver,
        "solver_info": dict(engine.solver_info),
        "obs_path": str(record.obs_path),
        "params_path": str(record.params_path),
        # CUFLynx-authored operation/cost funcs, saved under the output dir; CA
        # loads them from these paths (CA #303).
        "operation_funcs_external_path": user_func_path("operation", configured or None),
        "cost_funcs_external_path": user_func_path("cost", configured or None),
        "output_dir": output_dir,
        "file_prefix": _record_prefix(record),
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
        # Evaluate the trained emulator instead of the solver when the
        # Emulator tab's tick box is on (CA #333).
        **_emulator_run_config(req.model_id, req.settings),
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
    # cellml + CVODE_myokit), exactly like the calibration menu.
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
    # FSA surfaces for cellml + CVODE_myokit and AD for casadi_python. The
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
        # The *configured* outputs dir, not the temp fallback below it: that is
        # where the study keeps its own copy of an external_python model, and the
        # runner must resolve the same file the live engine does.
        "model_path": resolve_model_path(
            str(record.path), engine.model_type, model_id=req.model_id,
            output_dir=configured or None,
        ),
        # Translated at the boundary: an older CA parses only its own spelling
        # of the model_type and exits on anything else (solver_options.
        # MODEL_TYPE_ALIASES). Inside CUFLynx it stays canonical.
        "model_type": ca_model_type(engine.model_type),
        "solver": engine.solver,
        "solver_info": dict(engine.solver_info),
        "obs_path": str(record.obs_path),
        "params_path": str(record.params_path),
        # CUFLynx-authored operation/cost funcs, saved under the output dir; CA
        # loads them from these paths (CA #303).
        "operation_funcs_external_path": user_func_path("operation", configured or None),
        "cost_funcs_external_path": user_func_path("cost", configured or None),
        "output_dir": output_dir,
        "file_prefix": _record_prefix(record),
        "num_cores": num_cores,
        "python": python_path,
        "settings": req.settings,
        "best_params": best_params,
        "current_params": req.current_params,
        # Global random seed (Settings popup); None => non-deterministic run.
        "seed": _analysis_seed,
        # Evaluate the trained emulator instead of the solver when the Emulator
        # tab's tick box is on (CA #333).
        **_emulator_run_config(req.model_id, req.settings),
    }
    try:
        job_id = sensitivity.start(config)
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"job_id": job_id}


class EmulatorTrainRequest(BaseModel):
    model_id: str
    settings: dict = Field(default_factory=dict)


class EmulatorPredictRequest(BaseModel):
    model_id: str
    params: dict = Field(default_factory=dict)
    settings: dict = Field(default_factory=dict)


def _emulator_dir_for(model_id: str, settings: dict) -> str:
    """Where this study's emulator lives, from the same rule the trainer uses.

    Derived rather than remembered, so a training run and a later analysis agree
    without a second setting to keep in step, and so an emulator trained by CA's
    own script into the same outputs directory is found here too.
    """
    record = _get_model(model_id)
    configured = (settings.get("config_outputs_dir") or "").strip()
    output_dir = configured or str(UPLOAD_DIR / f"emu_{model_id}")
    prefix = _record_prefix(record)
    obs = str(record.obs_path) if record.obs_path else None
    conventional = ca_run_history.emulator_dir(output_dir, prefix, obs)
    if ca_run_history.emulator_metadata(conventional) is not None:
        return conventional
    # The convention is the right question while *this* app is the thing training: both
    # sides derive the path and neither has to pass it. It is only a guess for a directory
    # someone else produced -- `emulator_settings.emulator_dir` is a setting, and the
    # conventional name embeds the obs file. So the Analysis panel would list the emulator
    # it had found while this route, asking the convention, answered "no trained emulator
    # for this study; train one in the Emulator tab" about the very bundle on screen.
    #
    # Same search the loader uses, so what was found is what gets predicted with.
    found = ca_run_history.find_emulator_dir(output_dir, prefix, obs)
    # Falls back to the conventional path rather than None so a genuinely absent emulator
    # still reports the place it was expected, which is what makes that message actionable.
    return found or conventional


def _emulator_run_config(model_id: str, settings: dict) -> dict:
    """Config keys that put an analysis run on the trained emulator, or ``{}``.

    Lifted out of the panel's settings here rather than sent as a path by the
    frontend: the location is derived from the outputs directory by one rule
    (:func:`ca_run_history.emulator_dir`), and deriving it in one place is what
    stops a run training into one directory and reading from another.

    Refuses up front when the tick box is on and nothing is trained — the run
    would otherwise start, compile the model, and fail inside CA.
    """
    if not settings.get("use_emulator"):
        return {}
    emu_dir = _emulator_dir_for(model_id, settings)
    if ca_run_history.emulator_metadata(emu_dir) is None:
        raise HTTPException(
            status_code=422,
            detail="'use the emulator' is on but no emulator has been trained for "
            "this study; train one in the Emulator tab first",
        )
    return {
        "use_emulator": True,
        "emulator_dir": emu_dir,
        "emulator_settings": {
            k: v
            for k, v in settings.items()
            if k in ("min_r2", "out_of_bounds", "fd_rel_step")
        },
    }


@app.get("/api/emulator/defaults")
def emulator_defaults() -> dict:
    """The emulator settings form, from CA's ``emulation`` schema entry (#333).

    ``supported`` is False on a circulatory_autogen that predates emulators, so
    the tab can say so plainly instead of rendering an empty form. ``models`` is a
    runtime registry (whatever autoemulate has registered), not a schema, so an
    empty list means "type a name" rather than "there are none".

    ``available`` / ``interpreter`` / ``unavailable_reason`` are what the tab needs
    to stop degrading in silence. Emulation needs autoemulate in the interpreter
    that would *train*, and that is an optional extra with heavy dependencies: a
    user who pointed Settings at a conda env built for FEniCSx watched the model
    dropdown become a free-text box and had no way to learn why. The panel turns
    orange and shows ``unavailable_reason`` alone -- so the reason is a complete,
    actionable sentence, not a code the client has to phrase.

    One probe answers both ``models`` and ``available`` (``solver_options`` owns it,
    and caches it per interpreter + CA dir), so polling this costs no subprocess and
    the menu can never disagree with the explanation beside it.
    """
    meta = get_analysis_options().get("emulation", {})
    # The interpreter that will train is the one that knows what it can train with.
    probe = emulator_availability(emulator.python)
    return {
        "supported": bool(meta.get("options")),
        "label": meta.get("label", "Emulator"),
        "enable_flag": meta.get("enable_flag"),
        "use_flag": meta.get("use_flag"),
        "options": meta.get("options", []),
        "models": probe["models"],
        "available": probe["available"],
        "interpreter": probe["interpreter"],
        "unavailable_reason": probe["unavailable_reason"],
    }


@app.get("/api/emulator/info")
def emulator_info(model_id: str, config_outputs_dir: str = "") -> dict:
    """The trained emulator for this study, if there is one.

    Read on load and after a run so the tab can show what is available -- and,
    above all, its held-out R2 per feature. Absent is not an error: most studies
    have no emulator, and the tab's job is then to offer to train one.
    """
    emu_dir = _emulator_dir_for(model_id, {"config_outputs_dir": config_outputs_dir})
    metadata = ca_run_history.emulator_metadata(emu_dir)
    # The held-out points travel with the metadata rather than behind a second
    # route: they are what the Analysis view draws, and a caller that has one
    # without the other can only show half the picture.
    points = ca_run_history.emulator_error_points(emu_dir) if metadata else None
    return {
        "emulator_dir": emu_dir,
        "metadata": metadata,
        "error_points": points,
        # Whether `emulator_settings.reuse_samples` could run here. Its own key
        # rather than inferred from `metadata`: CA needs the saved samples *as
        # well as* the metadata, so a bundle can be perfectly usable and still
        # have nothing to refit.
        "reusable": ca_run_history.emulator_reusable(emu_dir),
    }


@app.post("/api/emulator/train")
def emulator_train(req: EmulatorTrainRequest) -> dict:
    record = _get_model(req.model_id)
    if record.obs_path is None or record.params_path is None:
        raise HTTPException(
            status_code=422,
            detail="training an emulator requires both an obs_data.json and a "
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
    if configured and not os.path.isabs(configured):
        raise HTTPException(
            status_code=422, detail="config_outputs_dir must be an absolute path"
        )
    output_dir = configured or str(UPLOAD_DIR / f"emu_{req.model_id}")
    config = {
        # See the calibration config: the configured outputs dir decides which
        # copy of an external_python model every tier runs.
        "model_path": resolve_model_path(
            str(record.path), engine.model_type, model_id=req.model_id,
            output_dir=configured or None,
        ),
        # Translated at the boundary: an older CA parses only its own spelling
        # of the model_type and exits on anything else (solver_options.
        # MODEL_TYPE_ALIASES). Inside CUFLynx it stays canonical.
        "model_type": ca_model_type(engine.model_type),
        "solver": engine.solver,
        "solver_info": dict(engine.solver_info),
        "obs_path": str(record.obs_path),
        "params_path": str(record.params_path),
        "operation_funcs_external_path": user_func_path("operation", configured or None),
        "cost_funcs_external_path": user_func_path("cost", configured or None),
        "output_dir": output_dir,
        "file_prefix": _record_prefix(record),
        "num_cores": int(req.settings.get("num_cores", 1) or 1),
        "python": python_path,
        "settings": req.settings,
        "seed": _analysis_seed,
    }
    try:
        job_id = emulator.start(config)
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"job_id": job_id}


@app.get("/api/emulator/{job_id}/status")
def emulator_status(job_id: str, offset: int = 0) -> dict:
    status = emulator.status(job_id, offset)
    if status is None:
        raise HTTPException(status_code=404, detail="emulator job not found")
    return status


@app.post("/api/emulator/{job_id}/cancel")
def emulator_cancel(job_id: str) -> dict:
    if not emulator.cancel(job_id):
        raise HTTPException(status_code=404, detail="emulator job not found")
    return {"ok": True}


@app.post("/api/emulator/predict")
def emulator_predict(req: EmulatorPredictRequest) -> dict:
    """The emulator's predicted features at the current slider values (#333).

    Drawn beside the model's own features so the two can be read against the
    ground truth in the same plot -- which is the only way to see, while moving a
    parameter, whether the surrogate still agrees with the model there.

    Values are returned keyed by the emulator's own feature labels; the caller
    matches them to its data_items rather than assuming a shared ordering.

    ``cost`` rides along: what those predicted features cost against the loaded
    obs_data, scored by the same CA path ``/api/simulate`` scores the solver's
    features with (#333). Here rather than on the run routes because this request
    is already made every time the parameters settle -- so the panel gets both
    numbers without a second round trip, and, since both are asked for at the
    same slider values, they are two costs of one parameter set rather than two
    parameter sets. None when there is no obs_data or CA cannot score it.
    """
    record = _get_model(req.model_id)
    emu_dir = _emulator_dir_for(req.model_id, req.settings)
    metadata = ca_run_history.emulator_metadata(emu_dir)
    if metadata is None:
        raise HTTPException(
            status_code=404,
            detail="no trained emulator for this study; train one in the Emulator tab",
        )
    theta = _emulator_theta(req.model_id, req.params, metadata)
    try:
        result = engine.emulator_predict(emu_dir, theta)
    except SimulationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    result["cost"], result["cost_unavailable"] = _emulator_feature_cost(
        record, result, _user_func_base_dir(req.settings.get("config_outputs_dir"))
    )
    return result


def _emulator_feature_cost(record, prediction: dict, output_dir,
                           protocol_info=None) -> dict | None:
    """What an emulator's predicted features cost, through CA's own cost path.

    The prediction is matched to the data_items by circulatory_autogen's feature
    labels -- the same rule the Output plots' overlay matches by, and the labels
    the bundle was trained with -- and then scored by exactly the code that
    scores a solver run (:func:`obs_cost.evaluate_features`). Two numbers meant
    to be read against each other cannot come from two implementations.

    Quiet on failure: an emulator that cannot be scored (no obs_data, an
    obs_data CA cannot parse, a bundle whose labels no longer match) leaves the
    solver's cost exactly as it was and simply offers no second number.
    """
    labels = prediction.get("labels") or []
    values = prediction.get("values") or []
    if not labels or len(labels) != len(values):
        return None, "the emulator returned no usable predictions"
    why: list[str] = []
    try:
        cost = obs_cost.evaluate_features(
            dict(zip(labels, values)),
            _obs_data_document(record, protocol_info),
            output_dir,
            dt=engine.dt,
            why=why,
        )
    except Exception:  # noqa: BLE001 - a cost is never worth failing the prediction over
        return None, "circulatory_autogen could not score the emulator's prediction"
    if cost is not None:
        return cost, None
    # Why, not just "no". The predicted features still draw their dotted overlay, so a
    # silent None left the user with lines on the plot and no number beside them and
    # nothing to act on -- which is exactly how this was reported.
    return None, (why[0] if why else "the emulator's prediction could not be scored")


def _emulator_theta(model_id: str, params: dict, metadata: dict) -> list:
    """The theta vector for the emulator, ordered as it was trained.

    A params_for_id row is one calibrated variable that may write several model
    constants (#193), and the emulator carries one value per row. The sliders are
    keyed by qname, so the value is taken from the row's first member -- the same
    anchor CUFLynx uses everywhere else for a grouped row.
    """
    record = _get_model(model_id)
    entries = parse_params_for_id(Path(record.params_path).read_bytes())
    theta = []
    for entry in entries:
        value = None
        for qname in entry.qnames or [entry.qname]:
            if qname in params:
                value = float(params[qname])
                break
        if value is None:
            # No slider for this row (it was never added, or the study changed):
            # its own initial value is the honest stand-in, and the midpoint of
            # its range when it has none.
            value = entry.initial_value
            if value is None:
                value = 0.5 * (float(entry.min) + float(entry.max))
        theta.append(float(value))
    expected = len(metadata.get("param_entry_labels") or [])
    if expected and len(theta) != expected:
        raise HTTPException(
            status_code=409,
            detail=f"this emulator was trained on {expected} parameters but the "
            f"study now has {len(theta)}; retrain it",
        )
    return theta


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
        # The *configured* outputs dir, not the temp fallback below it: that is
        # where the study keeps its own copy of an external_python model, and the
        # runner must resolve the same file the live engine does.
        "model_path": resolve_model_path(
            str(record.path), engine.model_type, model_id=req.model_id,
            output_dir=configured or None,
        ),
        # Translated at the boundary: an older CA parses only its own spelling
        # of the model_type and exits on anything else (solver_options.
        # MODEL_TYPE_ALIASES). Inside CUFLynx it stays canonical.
        "model_type": ca_model_type(engine.model_type),
        "solver": engine.solver,
        "solver_info": dict(engine.solver_info),
        "obs_path": str(record.obs_path),
        "params_path": str(record.params_path),
        # CUFLynx-authored operation/cost funcs, saved under the output dir; CA
        # loads them from these paths (CA #303).
        "operation_funcs_external_path": user_func_path("operation", configured or None),
        "cost_funcs_external_path": user_func_path("cost", configured or None),
        "output_dir": output_dir,
        "file_prefix": _record_prefix(record),
        "num_cores": int(req.settings.get("num_cores", 1) or 1),
        "python": python_path,
        "settings": req.settings,
        "best_params": best_params,
        # Global random seed (Settings popup); None => non-deterministic run.
        "seed": _analysis_seed,
        # Evaluate the trained emulator instead of the solver when the
        # Emulator tab's tick box is on (CA #333).
        **_emulator_run_config(req.model_id, req.settings),
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


@app.get("/api/uq/{job_id}/posterior-predictive")
def uq_posterior_predictive(job_id: str) -> dict:
    """Posterior draws pushed back through the model, against the measurements.

    Everything is returned in units of each measurement's own standard
    deviation, so observables on different scales share one axis and "inside the
    error bar" means the same distance for all of them. ``available: false``
    when the run predates the check or the engine could not run it -- that is
    not an error, it is a run that was not scored.
    """
    payload = uq.posterior_predictive(job_id)
    if payload is None:
        raise HTTPException(status_code=404, detail="UQ job not found")
    return payload


@app.get("/api/uq/{job_id}/progress")
def uq_progress(job_id: str) -> dict:
    """The chain so far, as the three views the Progress tab draws (#244).

    Separate from /status on purpose: status is polled for log lines at a rate suited to text,
    and the chain is the largest thing a run produces. Keeping them apart lets the client ask
    for each at the rate it is worth.
    """
    progress = uq.progress(job_id)
    if progress is None:
        raise HTTPException(status_code=404, detail="UQ job not found")
    return progress


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
