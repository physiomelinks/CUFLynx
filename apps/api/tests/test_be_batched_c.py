"""BE_batched_c: libcuflynx's generated-C solver for CellML, offered and run through CUFLynx.

It builds each model with CMake and a C compiler the first time it runs it, reads
``MaximumStep`` (unbounded by default) and ``MaximumNumberOfSteps`` (per block over the
whole run) differently from CVODE, and drives protocol traces like CVODE_myokit.

The unit tier fakes CA; the integration tier needs a CA that has BE_batched_c (libcuflynx
with libCellML 0.7.1), CMake and a C compiler, and skips otherwise.
"""

from __future__ import annotations

import ast
import copy
import json
import re
import types
from pathlib import Path

import numpy as np
import pytest

import calibration_runner
import compiler_check
import emulator_runner
import engine as engine_mod
import sensitivity_runner
import solver_info_defaults
import solver_options as so
import uq_runner
from conftest import (
    LV_MODEL_PATH,
    SN_MODEL_PATH,
    SN_OBS_DATA_PATH,
    set_ca_module,
    upload_model,
)

API_DIR = Path(__file__).resolve().parents[1]
SPEC = API_DIR.parents[1] / "packaging" / "cuflynx.spec"

# BE_batched_c's entries in CA's SOLVER_INFO_FIELDS, and CVODE_myokit's for contrast.
BE_FIELDS = [
    {"name": "rtol", "type": "float", "default": 1e-4},
    {"name": "atol", "type": "float", "default": 1e-8},
    {"name": "MaximumStep", "type": "float", "default": None},
    {"name": "MaximumNumberOfSteps", "type": "int", "default": 10_000_000},
    {"name": "threads", "type": "int", "default": 1},
    {"name": "jacobian", "type": "enum", "default": "exact", "choices": ["exact", "finite_difference"]},
]
MYOKIT_FIELDS = [
    {"name": "MaximumStep", "type": "float", "default": 0.001},
    {"name": "rtol", "type": "float", "default": 1e-6},
    {"name": "atol", "type": "float", "default": 1e-8},
]


@pytest.fixture
def fake_fields(monkeypatch):
    set_ca_module(
        monkeypatch,
        "parsers.PrimitiveParsers",
        types.SimpleNamespace(SOLVER_INFO_FIELDS={"BE_batched_c": BE_FIELDS, "CVODE_myokit": MYOKIT_FIELDS}),
    )


# ---------------------------------------------------------------------------
# Solver options: offered with its methods and fields, withheld without its tools
# ---------------------------------------------------------------------------
def _schema_with_batched_c():
    return {
        "model_types": ["cellml", "python"],
        "solvers_by_model_type": {
            "cellml": ["CVODE_opencor", "CVODE_myokit", "BE_batched_c"],
            "python": ["solve_ivp"],
        },
        "methods_by_solver": {
            "CVODE_opencor": ["CVODE"],
            "CVODE_myokit": ["CVODE"],
            "BE_batched_c": ["backward_euler", "trbdf2"],
            "solve_ivp": ["RK45"],
        },
        "stiff_suitable_methods": {"CVODE_myokit": ["CVODE"], "BE_batched_c": ["backward_euler", "trbdf2"]},
        "default_solver_by_model_type": {"cellml": "CVODE_opencor", "python": "solve_ivp"},
        "solver_info_fields_by_solver": {"CVODE_myokit": MYOKIT_FIELDS, "BE_batched_c": BE_FIELDS},
    }


def test_batched_c_is_offered_with_its_methods_and_fields(monkeypatch):
    monkeypatch.setattr(compiler_check, "unavailable_solvers", lambda: {})
    opts = so._build_options(_schema_with_batched_c(), {})

    assert opts["solvers_by_format"]["cellml"] == ["CVODE_myokit", "BE_batched_c"]
    assert opts["methods_by_solver"]["BE_batched_c"] == ["backward_euler", "trbdf2"]
    fields = {f["key"]: f for f in opts["solver_info_schema"]["BE_batched_c"]}
    assert set(fields) == {"method", "dt", "rtol", "atol", "MaximumStep", "MaximumNumberOfSteps", "threads", "jacobian"}
    assert fields["method"]["options"] == ["backward_euler", "trbdf2"]
    assert fields["jacobian"]["options"] == ["exact", "finite_difference"]
    assert fields["MaximumStep"]["default"] is None  # unbounded: the form seeds nothing
    assert fields["MaximumNumberOfSteps"]["default"] == 10_000_000


def test_batched_c_is_withheld_without_its_tools(monkeypatch):
    monkeypatch.setattr(compiler_check, "unavailable_solvers", lambda: {"BE_batched_c": "no CMake"})
    opts = so._build_options(_schema_with_batched_c(), {})

    assert opts["solvers_by_format"]["cellml"] == ["CVODE_myokit"]
    assert opts["default_solver_by_format"]["cellml"] == "CVODE_myokit"


def test_settings_accept_its_fields(monkeypatch):
    """check_solver_info reads the same schema, so every field BE_batched_c declares is accepted."""
    monkeypatch.setattr(compiler_check, "unavailable_solvers", lambda: {})
    opts = so._build_options(_schema_with_batched_c(), {})
    monkeypatch.setattr(so, "get_solver_options", lambda refresh=False: opts)

    so.check_solver_info(
        "BE_batched_c",
        {"method": "trbdf2", "rtol": 1e-6, "atol": 1e-9, "MaximumStep": 0.01,
         "MaximumNumberOfSteps": 100, "threads": 2, "jacobian": "finite_difference"},
    )
    with pytest.raises(ValueError, match="max_step"):
        so.check_solver_info("BE_batched_c", {"max_step": 0.1})


# ---------------------------------------------------------------------------
# Step defaults: the solver's own, not CVODE's
# ---------------------------------------------------------------------------
def test_declared_step_defaults(fake_fields):
    assert solver_info_defaults.declared_step_defaults("BE_batched_c") == {
        "MaximumStep": None, "MaximumNumberOfSteps": 10_000_000,
    }
    # A solver that declares a cap keeps the defaults it runs with now.
    assert solver_info_defaults.declared_step_defaults("CVODE_myokit") == {}
    assert solver_info_defaults.with_declared_step_defaults("BE_batched_c", {"MaximumStep": 0.01}) == {
        "MaximumStep": 0.01, "MaximumNumberOfSteps": 10_000_000,
    }


def test_declared_step_defaults_without_ca(monkeypatch):
    set_ca_module(monkeypatch, "parsers.PrimitiveParsers", None)
    assert solver_info_defaults.with_declared_step_defaults("BE_batched_c", {"rtol": 1e-6}) == {"rtol": 1e-6}


@pytest.mark.parametrize(
    "solver_info_for",
    [
        calibration_runner._solver_info_from_config,
        sensitivity_runner._solver_info_from_config,
        uq_runner._solver_info,
        emulator_runner._solver_info_from_config,
    ],
    ids=["calibration", "sensitivity", "uq", "emulator"],
)
def test_runners_do_not_put_cvode_step_defaults_on_batched_c(fake_fields, solver_info_for):
    """The runners cap MaximumStep at 1e-4 for CVODE, and CA then fills MaximumNumberOfSteps
    with 5000: BE_batched_c would take 10 000 steps per second and stop after 5000."""
    si = solver_info_for({"solver": "BE_batched_c", "solver_info": {"rtol": 1e-6}}, {})
    assert si["MaximumStep"] is None
    assert si["MaximumNumberOfSteps"] == 10_000_000
    assert si["rtol"] == 1e-6

    assert solver_info_for({"solver": "CVODE_myokit", "solver_info": {}}, {})["MaximumStep"] == 0.0001


def test_live_runs_get_the_solvers_step_defaults_and_a_build_cache(fake_fields, client, monkeypatch, tmp_path):
    """The protocol path builds CA's ProtocolRunner, which fills CVODE's 0.001 / 5000 into
    whatever is absent: the engine hands it the solver's own defaults first, and the folder
    under the user's cache to keep the C builds in."""
    monkeypatch.setattr(engine_mod, "solver_build_cache_dir", lambda solver: tmp_path / solver)
    seen = {}

    def runner_factory(**kwargs):
        seen.update(kwargs)
        raise RuntimeError("stop here")

    engine_mod.engine.runner_factory = runner_factory
    engine_mod.engine.solver = "BE_batched_c"
    engine_mod.engine.solver_info = {"method": "trbdf2", "rtol": 1e-6}
    model_id = upload_model(client, SN_MODEL_PATH)["model_id"]
    protocol_info = {"pre_times": [0.0], "sim_times": [[1.0]], "params_to_change": {}}
    client.post("/api/protocol/run", json={
        "model_id": model_id, "protocol_info": protocol_info, "params": {}, "outputs": ["soma_SN/V"],
    })

    assert seen["solver"] == "BE_batched_c"
    assert seen["solver_info"] == {
        "method": "trbdf2", "rtol": 1e-6, "MaximumStep": None, "MaximumNumberOfSteps": 10_000_000,
        "build_dir": str(tmp_path / "BE_batched_c"),
    }
    # Applied to the run only: the settings stay what the user chose.
    assert engine_mod.engine.solver_info == {"method": "trbdf2", "rtol": 1e-6}


def test_the_sim_worker_gets_the_same_solver_info(fake_fields, monkeypatch, tmp_path):
    """With an interpreter chosen in Settings, the run is built in the worker from what
    the engine sends it."""
    monkeypatch.setattr(engine_mod, "solver_build_cache_dir", lambda solver: tmp_path / solver)
    engine_mod.engine.solver = "BE_batched_c"
    engine_mod.engine.solver_info = {"rtol": 1e-6}
    assert engine_mod.engine._worker_settings()["solver_info"] == {
        "rtol": 1e-6, "MaximumStep": None, "MaximumNumberOfSteps": 10_000_000,
        "build_dir": str(tmp_path / "BE_batched_c"),
    }


def test_cvode_myokit_runs_are_unchanged(fake_fields):
    engine_mod.engine.solver = "CVODE_myokit"
    engine_mod.engine.solver_info = {"MaximumStep": 0.001, "rtol": 1e-6}
    assert engine_mod.engine.run_solver_info("CVODE_myokit") == {"MaximumStep": 0.001, "rtol": 1e-6}


def test_build_cache_is_under_the_user_cache(monkeypatch, tmp_path):
    import runtime_paths

    monkeypatch.setattr(runtime_paths, "user_cache_base", lambda *_a: tmp_path)
    assert runtime_paths.solver_build_cache_dir("BE_batched_c") == (
        tmp_path / "CUFLynx" / "solver-builds" / "BE_batched_c"
    )


# ---------------------------------------------------------------------------
# Failure messages point at BE_batched_c's own settings and tools
# ---------------------------------------------------------------------------
def test_step_budget_hint_names_maximum_number_of_steps():
    hint = engine_mod._failure_hint(
        "BE_batched_c simulation failed: 1 block(s); block 0 (a/x, ...) stopped at t=3.9: "
        "too many steps (MaximumNumberOfSteps)."
    )
    assert "raise MaximumNumberOfSteps" in hint


@pytest.mark.parametrize("reason", [
    "Model0dError: cmake not found: install CMake (in a conda environment: ...)",
    "BatchedBuildError: cmake --build /x/build --target batched -j failed:\nerror: ...",
])
def test_build_failure_hint_names_cmake(reason):
    hint = engine_mod._failure_hint(reason)
    assert "CMake" in hint and "CVODE_myokit" in hint


# ---------------------------------------------------------------------------
# Packaging
# ---------------------------------------------------------------------------
def _spec_tuple(name: str) -> tuple:
    match = re.search(rf"^{name} = (\(.*?\))$", SPEC.read_text(), re.MULTILINE | re.DOTALL)
    assert match, f"{name} not found in {SPEC}"
    return ast.literal_eval(match.group(1))


def test_spec_collects_the_sources_batched_c_reads_at_run_time():
    """BE_batched_c digests its own generator's source to key its build cache; in the
    bundle a module's __file__ names a .py beside the frozen module, which exists only
    if collected."""
    batched = pytest.importorskip("libcuflynx.generators.cpp.batched")
    root = Path(batched.__file__).resolve().parents[2]  # .../libcuflynx
    read = {Path(batched.__file__).resolve(), Path(batched.kernel_ir.__file__).resolve()}
    assert {p.relative_to(root).as_posix() for p in read} <= set(_spec_tuple("_SOURCES_READ_AT_RUN_TIME"))


def test_spec_collects_the_batched_c_templates():
    pytest.importorskip("libcuflynx.generators.cpp.batched")
    hooks = pytest.importorskip("PyInstaller.utils.hooks")
    collected = {Path(src).name for src, _dest in hooks.collect_data_files("libcuflynx")}
    assert {"batched_kernels.c.j2", "batched_runtime.c.j2", "batched_CMakeLists.txt.j2"} <= collected


def test_spec_ships_the_runners_local_imports():
    """A runner runs as a file in another interpreter: every app module it imports must
    ship beside it in runners/."""
    block = re.search(r"for runner in \((.*?)\n\):", SPEC.read_text(), re.DOTALL).group(1)
    shipped = set(re.findall(r'"(\w+)\.py"', block))
    local = {p.stem for p in API_DIR.glob("*.py")}
    for runner in ("calibration_runner", "sensitivity_runner", "uq_runner", "emulator_runner"):
        tree = ast.parse((API_DIR / f"{runner}.py").read_text())
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported |= {a.name.split(".")[0] for a in node.names}
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                imported.add(node.module.split(".")[0])
        assert (imported & local) <= shipped, f"{runner} imports unshipped {sorted((imported & local) - shipped)}"


# ---------------------------------------------------------------------------
# Integration: real CA, CMake and a C compiler
# ---------------------------------------------------------------------------
@pytest.fixture
def requires_batched_c(requires_simulation):
    try:
        from ca_imports import ca_from

        schema = ca_from("parsers.PrimitiveParsers", "SOLVER_SCHEMA")
    except ImportError:
        pytest.skip("circulatory_autogen not importable")
    if "BE_batched_c" not in schema["solvers_by_model_type"].get("cellml", []):
        pytest.skip("this circulatory_autogen has no BE_batched_c")
    if compiler_check.unavailable_solvers():
        pytest.skip("BE_batched_c needs CMake and a C compiler")


@pytest.fixture(scope="module")
def build_cache(tmp_path_factory):
    """One build folder for the module, so each model is compiled once."""
    return tmp_path_factory.mktemp("batched-c-builds")


@pytest.fixture
def batched_c_builds_in(build_cache, monkeypatch):
    monkeypatch.setattr(engine_mod, "solver_build_cache_dir", lambda solver: build_cache / solver, raising=False)
    return build_cache / "BE_batched_c"


def _configure(client, solver: str, solver_info: dict) -> None:
    resp = client.post("/api/config", json={
        "generated_model_format": "cellml", "solver": solver, "solver_info": solver_info,
    })
    assert resp.status_code == 200, resp.text


@pytest.mark.integration
def test_ca_offers_batched_c_through_solver_options(requires_batched_c):
    from ca_imports import ca_from

    fields = ca_from("parsers.PrimitiveParsers", "SOLVER_INFO_FIELDS")["BE_batched_c"]
    opts = so.get_solver_options(refresh=True)
    assert "BE_batched_c" in opts["solvers_by_format"]["cellml"]
    assert opts["methods_by_solver"]["BE_batched_c"] == ["backward_euler", "trbdf2"]
    keys = {f["key"] for f in opts["solver_info_schema"]["BE_batched_c"]}
    assert {f["name"] for f in fields} | {"method", "dt"} == keys
    # Both methods are L-stable, so the stiff filter keeps them.
    assert so.supported_methods("BE_batched_c", ["backward_euler", "trbdf2"], stiff=True) == [
        "backward_euler", "trbdf2",
    ]


@pytest.mark.integration
def test_live_simulation_matches_cvode_myokit(client, requires_batched_c, batched_c_builds_in):
    model_id = upload_model(client, LV_MODEL_PATH)["model_id"]
    outputs = ["Lotka_Volterra_module/x", "Lotka_Volterra_module/y"]

    def simulate():
        resp = client.post("/api/simulate", json={
            "model_id": model_id, "params": {"Lotka_Volterra_module/alpha": 4.0},
            "sim_time": 5, "outputs": outputs,
        })
        assert resp.status_code == 200, resp.text
        body = resp.json()
        return np.array(body["time"]), {k: np.array(v) for k, v in body["outputs"].items()}

    _configure(client, "CVODE_myokit", {"rtol": 1e-8, "atol": 1e-10, "MaximumStep": 0.001})
    t_ref, ref = simulate()
    _configure(client, "BE_batched_c", {"method": "trbdf2", "rtol": 1e-7, "atol": 1e-10})
    t, got = simulate()

    np.testing.assert_allclose(t, t_ref)
    for key in outputs:
        assert np.max(np.abs(got[key] - ref[key])) < 1e-3 * np.ptp(ref[key]), key
    # Built under the cache folder CUFLynx chose, not the system temp folder.
    assert list(batched_c_builds_in.glob("batched_c_*"))


def _sn_protocol_v(client, model_id, protocol_info=None):
    payload = {"model_id": model_id, "params": {}, "outputs": ["soma_SN/V"]}
    if protocol_info is not None:
        payload["protocol_info"] = protocol_info
    resp = client.post("/api/protocol/run", json=payload)
    assert resp.status_code == 200, resp.text
    return [np.array(e["outputs"]["soma_SN/V"]) for e in resp.json()["experiments"]]


@pytest.mark.integration
@pytest.mark.parametrize(
    "solver_info, tolerance_mv",
    [
        # The default method at a tolerance where CVODE's step budget of 5000 used to stop
        # it 0.03 s into the first experiment.
        ({"rtol": 1e-6}, 1.0),
        ({"method": "trbdf2", "rtol": 1e-8}, 0.25),
    ],
    ids=["backward_euler", "trbdf2"],
)
def test_protocol_with_a_trace_matches_cvode_myokit(
    client, requires_batched_c, batched_c_builds_in, solver_info, tolerance_mv
):
    """SN_simple's third experiment drives soma_SN/I_in by the protocol trace ramp_port."""
    model_id = upload_model(client, SN_MODEL_PATH)["model_id"]
    obs = json.loads(SN_OBS_DATA_PATH.read_text())
    assert client.post("/api/obs_data/upload", json={"model_id": model_id, "obs_data": obs}).status_code == 200

    _configure(client, "CVODE_myokit", {"rtol": 1e-6, "atol": 1e-8, "MaximumStep": 0.001})
    ref = _sn_protocol_v(client, model_id)
    _configure(client, "BE_batched_c", solver_info)
    got = _sn_protocol_v(client, model_id)

    assert len(got) == 3
    for g, r in zip(got, ref):
        assert g.shape == r.shape
        assert np.max(np.abs(g - r)) < tolerance_mv

    # The trace is applied: without it the traced experiment changes and the others do not.
    zeroed = copy.deepcopy(obs["protocol_info"])
    for matrix in zeroed["params_to_change"].values():
        for row in matrix:
            for i, val in enumerate(row):
                if isinstance(val, str):
                    row[i] = 0.0
    untraced = _sn_protocol_v(client, model_id, zeroed)
    assert np.max(np.abs(got[2] - untraced[2])) > 0.05
    np.testing.assert_allclose(got[0], untraced[0])
