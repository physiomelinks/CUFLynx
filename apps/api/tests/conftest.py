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
import emulator as emulator_mod  # noqa: E402
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


def all_mmt_fixtures():
    """Every .mmt under resources/: the two at the top plus the third-party set.

    rglob rather than a fixed list, so a model dropped in later is covered by the
    parametrised sweeps without anyone remembering to extend them. Shared so the
    import tests and the protocol tests cannot drift onto different model sets.
    """
    return sorted(RESOURCES_DIR.rglob("*.mmt"))


def all_easyml_fixtures():
    """Every EasyML .model under resources/.

    Same rglob rule as the .mmt sweep and for the same reason. The set is
    smaller on purpose: openCARP's own model library is under the openCARP
    Academic Public License and is not redistributable from here, and a Myokit
    *export* of one of the third-party .mmt files next door would be a
    derivative work of a file this repository only aggregates. So what is here
    is written for this repository -- see resources/hodgkin_huxley_1952.model.
    """
    return sorted(RESOURCES_DIR.rglob("*.model"))


# ---------------------------------------------------------------------------
# Simulation-dependency gating
# ---------------------------------------------------------------------------
def ca_module_spellings(name: str) -> tuple[str, str]:
    """Both spellings of a circulatory_autogen module (CA #437).

    CA moved its modules under a ``libcuflynx.`` namespace and CUFLynx resolves
    either (:mod:`ca_imports`), preferring the namespaced one. A test that
    injected a fake under only the flat name would therefore be *ignored* the
    moment the developer's CA directory is a namespaced checkout — the resolver
    would find the real module first — so tests name both.
    """
    return (name, f"libcuflynx.{name}")


def set_ca_module(monkeypatch, name: str, value) -> None:
    """Register ``value`` as CA module ``name`` in both layouts, for the test only.

    ``value=None`` is the idiom for "make importing this raise ImportError".
    """
    for spelling in ca_module_spellings(name):
        monkeypatch.setitem(sys.modules, spelling, value)


def running_against_installed_ca_only() -> bool:
    """True when CA comes from an installed package with no checkout configured.

    The arrangement `.github/workflows/integration.yml` runs in, and the one a user who
    ran `pip install` and configured nothing is in. Every other CI job checks CA out and
    points CIRCULATORY_AUTOGEN_SRC at it, so this is False there.

    Used to scope `xfail` markers to failures that only manifest in this arrangement, so
    a test that legitimately passes against a checkout is not marked broken everywhere.
    """
    return not Path(engine_mod._circulatory_autogen_src()).is_dir()


def _simulation_deps_available() -> bool:
    """Whether a real CellML simulation can run here.

    "A CA *directory* exists" used to be the whole question, because a sibling checkout
    on PYTHONPATH was the only way CUFLynx ever found circulatory_autogen. Since CA #452
    it is not: ``apps/api/pyproject.toml`` depends on ``libcuflynx>=0.4.0``, the frozen
    app bundles one, and a user who pip-installed it and configured no directory is a
    supported -- indeed the default -- arrangement.

    Requiring the directory made the whole integration tier unreachable in exactly that
    arrangement: every test skipped, and pytest exits 0 when everything skips, so a job
    running the tier against an installed package would pass having run none of it. That
    is precisely what `.github/workflows/integration.yml` exists to do, so this has to
    accept both.

    ``installed_package_available()`` rather than a bare import, deliberately: it is the
    same distinction ``ca_imports`` draws for the app itself. ``ensure_ca_path`` inserts a
    configured checkout's ``src`` permanently, so "libcuflynx is importable" stays true
    for the life of the process after any directory has been used -- and would report CA
    present when the setting has since been cleared.
    """
    src = Path(engine_mod._circulatory_autogen_src())
    if not src.is_dir():
        from ca_imports import installed_package_available

        if not installed_package_available():
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
def requires_ca():
    """For assertions about CA's *schema* verdict on an obs_data document.

    Lighter than ``requires_simulation``: CA's parser needs neither Myokit nor
    libCellML, only a reachable circulatory_autogen. Without one the validation
    degrades to CUFLynx's structural checks, so these assertions do not apply.
    """
    import obs_data as obs_mod

    if obs_mod._ca_parser() is None:
        pytest.skip("circulatory_autogen not importable; schema validation degrades")


@pytest.fixture
def requires_easyml():
    """For the EasyML reader, which is circulatory_autogen's alone.

    There is no local fallback -- a second reader of a format this implicit would
    not agree with the first for long -- so without CA these tests have nothing
    to exercise. The tests about the *no-CA* behaviour itself force CA away and
    always run.

    Myokit is checked too, and separately from ``requires_simulation``: the
    reader builds a ``myokit.Model``, so the unit tier -- which installs neither
    Myokit nor libCellML -- cannot run these even with CA present. libCellML is
    *not* required, because reading an EasyML file never touches it.
    """
    import easyml_import

    if easyml_import._ca_parser("parsers.EasyMLParsers") is None:
        pytest.skip("libcuflynx.parsers.EasyMLParsers not importable")
    pytest.importorskip("myokit")


@pytest.fixture
def requires_obs_data_helpers():
    """For ``fill_protocol_info``, which lives in CA's obs_data helpers (CA #496).

    A different CA module from the readers, and a different vintage: it moved
    there after the .mmt reader did. Needs no Myokit -- putting a key in an
    obs_data document is not a simulation -- so this asks only for the module.
    """
    import mmt_protocol

    if mmt_protocol._ca_fill_protocol_info() is None:
        pytest.skip("libcuflynx.utilities.obs_data_helpers has no fill_protocol_info")


@pytest.fixture
def requires_myokit_parser():
    """Likewise for the Myokit reader, which moved into circulatory_autogen."""
    import myokit_import

    if myokit_import._ca_parser() is None:
        pytest.skip("libcuflynx.parsers.MyokitParsers not importable")
    pytest.importorskip("myokit")


@pytest.fixture
def requires_casadi(requires_simulation):
    """For the casadi_python backend (generated model + CasADi AD)."""
    try:
        import casadi  # noqa: F401
    except ImportError:
        pytest.skip("casadi not available")


def _params_csv_converter_available() -> bool:
    """Whether CA's params_for_id CSV converter is importable.

    The CSV -> JSON conversion is CA's alone (no local fallback), so tests
    about *CSV semantics* have nothing to exercise without it. Tests about the
    no-CA behavior itself force CA away and always run.
    """
    try:
        from ca_imports import ca_from, ensure_ca_path

        ensure_ca_path()
        ObsAndParamDataParser = ca_from(
            "parsers.PrimitiveParsers", "ObsAndParamDataParser")

        return hasattr(ObsAndParamDataParser, "params_for_id_csv_to_json")
    except Exception:  # noqa: BLE001
        return False


@pytest.fixture
def requires_params_csv():
    if not _params_csv_converter_available():
        pytest.skip("circulatory_autogen's params_for_id CSV converter not available")


# ---------------------------------------------------------------------------
# App + state isolation
# ---------------------------------------------------------------------------
def _reset_backend_solver():
    """Restore the engine's backend-solver selection to defaults."""
    engine_mod.engine.model_type = engine_mod.DEFAULT_MODEL_TYPE
    engine_mod.engine.solver = engine_mod.DEFAULT_SOLVER
    import os

    for var in ("CUFLYNX_MODEL_TYPE", "CUFLYNX_SOLVER", "CUFLYNX_SOLVER_INFO"):
        os.environ.pop(var, None)
    solver_options_mod.reset_cache()
    model_codegen_mod.reset_cache()
    # After the cache drop, so a test that repointed the CA directory doesn't
    # leave the next one seeded from the previous CA's schema. Reset to unseeded
    # rather than to a value: the seed is CA's to state (#200), and re-reading it
    # here would only be a second place for it to be stale.
    engine_mod.engine._solver_info = None


def _analysis_pythons() -> tuple:
    """Every place the chosen interpreter is stored.

    engine.worker_python belongs here: live simulation is set from the same
    choice as the three analysis managers (#167), so a test that changes the
    interpreter would otherwise leave the *engine* pointed at it and send every
    later test's simulate through a worker.

    So does the emulator manager, which was added after this list and left out
    of it. ``test_config`` configures ``/venv/bin/python`` -- a path chosen
    because it does not exist -- and every emulator test after it in the same
    session then tried to train with that, failing on a missing interpreter or
    on ranks that died at once. CI never saw it: it runs the unit and
    integration tiers as separate sessions, and the test that sets the
    interpreter is in one while the tests that spawn it are in the other.
    """
    return (
        calibration_mod.calibration.python,
        sensitivity_mod.sensitivity.python,
        uq_mod.uq.python,
        emulator_mod.emulator.python,
        engine_mod.engine.worker_python,
    )


def _set_analysis_pythons(pythons: tuple) -> None:
    (
        calibration_mod.calibration.python,
        sensitivity_mod.sensitivity.python,
        uq_mod.uq.python,
        emulator_mod.emulator.python,
        engine_mod.engine.worker_python,
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
    # The global analysis seed is module-level state on main; reset it so a test
    # that sets it via POST /api/config can't leak into the next test's run configs.
    main._analysis_seed = None
    main._models.clear()
    engine_mod.engine.reset()
    engine_mod.engine.helper_factory = engine_mod._default_helper_factory
    engine_mod.engine.runner_factory = engine_mod._default_runner_factory
    _reset_backend_solver()
    calibration_mod.calibration.reset()
    calibration_mod.calibration.runner_path = calibration_mod.RUNNER_PATH
    # sensitivity was the one manager not restored here, so a unit test's fake
    # runner (test_sensitivity_run._install_runner) leaked into every later
    # test: the "real Myokit" integration run spawned the leftover fake and
    # passed in 0.1s on its canned indices.
    sensitivity_mod.sensitivity.reset()
    sensitivity_mod.sensitivity.runner_path = sensitivity_mod.RUNNER_PATH
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
    sensitivity_mod.sensitivity.reset()
    sensitivity_mod.sensitivity.runner_path = sensitivity_mod.RUNNER_PATH
    uq_mod.uq.reset()
    uq_mod.uq.runner_path = uq_mod.RUNNER_PATH
    _set_analysis_pythons(_pythons_before)
    main._analysis_seed = None
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


#: What a finished calibration leaves behind, in circulatory_autogen's own
#: formats -- which is what the managers read (#210), so it is what a fake runner
#: has to produce.
#:
#: Held as source because three of the fakes are executed as a *subprocess* and
#: cannot import anything of ours, while two run in-process and want the
#: function. Defining the function from this same text keeps one copy: five test
#: modules were each hand-rolling it, four with their own copy of the reason.
WRITE_CA_RESULTS_SRC = '''
import csv
import numpy as np
from pathlib import Path


def write_ca_results(out_dir, param_names=(["a/x"],), values=(1.0,), cost=0.0):
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    np.save(str(out / "best_param_vals.npy"), np.array(values, dtype=float))
    np.save(str(out / "best_cost.npy"), np.array([cost], dtype=float))
    with open(out / "param_names.csv", "w", newline="") as fh:
        csv.writer(fh).writerows(param_names)
'''

exec(WRITE_CA_RESULTS_SRC, globals())  # noqa: S102 - one definition, two uses


@pytest.fixture
def recorded_commands(monkeypatch):
    """Record the argv each manager builds, so a run can be checked for being parallel.

    Without this the integration arms below assert only that the analysis *finished*,
    which a silently-serial run does just as well. Not hypothetical: the first draft of
    these tests posted their settings flat instead of under ``settings``, so the endpoint
    ignored ``num_cores`` entirely -- the "parallel" arm ran on one core and passed.
    Closing the loop on the argv is what makes the arm mean what it says.
    """
    seen = []

    def spy(manager):
        original = manager.build_command

        def wrapper(config, config_path):
            cmd = original(config, config_path)
            seen.append(cmd)
            return cmd

        monkeypatch.setattr(manager, "build_command", wrapper, raising=False)

    from test_run_matrix import MANAGERS  # noqa: PLC0415 - the table lives with its tests

    for _, manager in MANAGERS:
        spy(manager)
    return seen
