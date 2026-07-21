"""Shared pytest fixtures for the CUFLynx backend tests."""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

# Make the app package importable (apps/api on sys.path).
API_DIR = Path(__file__).resolve().parents[1]
if str(API_DIR) not in sys.path:
    sys.path.insert(0, str(API_DIR))

# Point the settings store at a throwaway dir *before* importing main, which
# restores persisted settings at import time. Otherwise the suite would inherit
# (and overwrite) the developer's real ~/.config/cuflynx/config.json — e.g. their
# saved ca_dir would leak in and change which CA the tests resolve.
os.environ["CUFLYNX_CONFIG_DIR"] = tempfile.mkdtemp(prefix="cuflynx-test-config-")

import calibration as calibration_mod  # noqa: E402
import engine as engine_mod  # noqa: E402
import main  # noqa: E402
import model_codegen as model_codegen_mod  # noqa: E402
import sensitivity as sensitivity_mod  # noqa: E402
import solver_options as solver_options_mod  # noqa: E402
import uq as uq_mod  # noqa: E402

# Repo-root resources (apps/api/tests -> parents[3] == repo root).
RESOURCES_DIR = Path(__file__).resolve().parents[3] / "resources"
BG_MODEL_PATH = RESOURCES_DIR / "BG_MWC_Huang-Peskin_SS.cellml"
LV_MODEL_PATH = RESOURCES_DIR / "Lotka_Volterra_forced.cellml"
LV_OBS_DATA_PATH = RESOURCES_DIR / "Lotka_Volterra_obs_data.json"
LV_PARAMS_CSV_PATH = RESOURCES_DIR / "Lotka_Volterra_params_for_id.csv"
SN_MODEL_PATH = RESOURCES_DIR / "SN_simple_flat.cellml"
SN_OBS_DATA_PATH = RESOURCES_DIR / "SN_simple_obs_data.json"
SN_PARAMS_CSV_PATH = RESOURCES_DIR / "SN_simple_params_for_id.csv"


# ---------------------------------------------------------------------------
# Simulation-dependency gating
# ---------------------------------------------------------------------------
def _simulation_deps_available() -> bool:
    src = Path(engine_mod._circulatory_autogen_src())
    if not src.is_dir():
        return False
    try:
        import libcellml  # noqa: F401
        import myokit  # noqa: F401
    except ImportError:
        return False
    return True


@pytest.fixture(scope="session")
def simulation_deps_available() -> bool:
    return _simulation_deps_available()


@pytest.fixture
def requires_simulation(simulation_deps_available: bool):
    if not simulation_deps_available:
        pytest.skip("myokit / libcellml / circulatory_autogen not available")


@pytest.fixture
def requires_casadi(requires_simulation):
    """For the casadi_python backend (generated model + CasADi AD)."""
    try:
        import casadi  # noqa: F401
    except ImportError:
        pytest.skip("casadi not available")


# ---------------------------------------------------------------------------
# App + state isolation
# ---------------------------------------------------------------------------
def _reset_backend_solver():
    """Restore the engine's backend-solver selection to defaults."""
    engine_mod.engine.model_type = engine_mod.DEFAULT_MODEL_TYPE
    engine_mod.engine.solver = engine_mod.DEFAULT_SOLVER
    engine_mod.engine.solver_info = dict(engine_mod.DEFAULT_SOLVER_INFO)
    import os

    for var in ("CUFLYNX_MODEL_TYPE", "CUFLYNX_SOLVER", "CUFLYNX_SOLVER_INFO"):
        os.environ.pop(var, None)
    solver_options_mod.reset_cache()
    model_codegen_mod.reset_cache()


def _analysis_pythons() -> tuple:
    return (
        calibration_mod.calibration.python,
        sensitivity_mod.sensitivity.python,
        uq_mod.uq.python,
    )


def _set_analysis_pythons(pythons: tuple) -> None:
    (
        calibration_mod.calibration.python,
        sensitivity_mod.sensitivity.python,
        uq_mod.uq.python,
    ) = pythons


@pytest.fixture(autouse=True)
def reset_app_state():
    """Reset the model registry and engine caches/factories between tests."""
    import os

    _ca_src_before = os.environ.get("CIRCULATORY_AUTOGEN_SRC")
    # The job managers are module-level singletons, so a test that repoints their
    # interpreter (directly, or via POST /api/config) would otherwise leak a bogus
    # python into every later test — which spawns runners with it.
    _pythons_before = _analysis_pythons()
    main._models.clear()
    engine_mod.engine.reset()
    engine_mod.engine.helper_factory = engine_mod._default_helper_factory
    engine_mod.engine.runner_factory = engine_mod._default_runner_factory
    _reset_backend_solver()
    calibration_mod.calibration.reset()
    calibration_mod.calibration.runner_path = calibration_mod.RUNNER_PATH
    uq_mod.uq.reset()
    uq_mod.uq.runner_path = uq_mod.RUNNER_PATH
    yield
    main._models.clear()
    engine_mod.engine.reset()
    engine_mod.engine.helper_factory = engine_mod._default_helper_factory
    engine_mod.engine.runner_factory = engine_mod._default_runner_factory
    _reset_backend_solver()
    calibration_mod.calibration.reset()
    calibration_mod.calibration.runner_path = calibration_mod.RUNNER_PATH
    uq_mod.uq.reset()
    uq_mod.uq.runner_path = uq_mod.RUNNER_PATH
    _set_analysis_pythons(_pythons_before)
    # Restore CIRCULATORY_AUTOGEN_SRC so a /api/config test doesn't leak.
    if _ca_src_before is None:
        os.environ.pop("CIRCULATORY_AUTOGEN_SRC", None)
    else:
        os.environ["CIRCULATORY_AUTOGEN_SRC"] = _ca_src_before


@pytest.fixture
def client() -> TestClient:
    return TestClient(main.app)


# ---------------------------------------------------------------------------
# Fakes for the unit tier (no Myokit)
# ---------------------------------------------------------------------------
class FakeHelper:
    """Records set_param_vals and returns fixed-length arrays."""

    def __init__(self, n: int = 5, **_kwargs):
        self.n = n
        self.set_param_calls: list[tuple] = []
        self.reset_called = False

    def reset_and_clear(self):
        self.reset_called = True

    def update_times(self, *_args, **_kwargs):
        pass

    def set_param_vals(self, names, vals):
        self.set_param_calls.append((list(names), list(vals)))

    def run(self):
        return True

    def get_time(self, include_pre_time=False):
        return [float(i) for i in range(self.n)]

    def get_results(self, variables, flatten=False):
        series = [float(i) * 2.0 for i in range(self.n)]
        return [series]


@pytest.fixture
def fake_helper():
    """Install a FakeHelper factory on the engine and return the live instance."""
    helper = FakeHelper()
    engine_mod.engine.helper_factory = lambda **kwargs: helper
    return helper


def upload_model(client: TestClient, path: Path) -> dict:
    """Helper: upload a CellML file and return the JSON metadata response."""
    with open(path, "rb") as fh:
        resp = client.post(
            "/api/models/upload",
            files={"file": (path.name, fh, "application/xml")},
        )
    assert resp.status_code == 200, resp.text
    return resp.json()


def upload_bundle(client: TestClient, paths):
    """Helper: upload a multi-file CellML bundle (main + sisters) via the ``files``
    field, returning the raw response so error cases can assert on it too."""
    files = [("files", (p.name, p.read_bytes(), "application/xml")) for p in paths]
    return client.post("/api/models/upload", files=files)
