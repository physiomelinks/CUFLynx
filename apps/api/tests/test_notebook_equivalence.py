"""CUFLynx and CA's tutorial-notebook route are two ways to do the same thing, and give the
same results.

The notebook (``generation_and_calibration_test.ipynb``) builds a ``CVS0DParamID`` with
``init_from_dict`` and feeds it through setters: an ``ObsDataCreator`` dict, a list of
``vessel_name``/``param_name`` entries, a user operation registered as a function. CUFLynx
uploads files and hands them to its runner subprocesses. A calibration workflow writes the
same inputs as files and runs them through libcuflynx's workflow runner. Given the same
inputs and solver settings, all of them must agree to the last bit or close to it:

- the best-fit parameters exactly (the seeded GA visits the same points);
- the best cost, the best-fit traces and the Sobol outputs and indices within
  ``REL_TOL`` relative;
- the calibrated CellML's parameter values: CUFLynx's ``calibrated_cellml`` against CA's
  ``write_calibrated_model`` (the values, not the bytes: the two writers serialise
  differently).

Every route is given the solver settings explicitly (``MaximumStep``, ``rtol``, ``atol``
and ``dt``), so a difference in the routes' *defaults* cannot hide a difference in what
they run. ``test_default_settings_agree`` is the variant that leaves them to each route's
defaults; it waits for CA's fix/route-consistency, which makes those defaults one rule.

The model is the flat 3compartment CellML (no generation) for the notebook and CUFLynx
routes. A calibration workflow's target is a module array, so the workflow route
generates 3compartment from CA's module array and parameters
(``tests/data/3compartment_module_array``), and the notebook route is run again on the
flat model the workflow generated, to compare with it.
"""

from __future__ import annotations

import importlib
import inspect
import json
import math
import os
import sys
import textwrap
import time
from pathlib import Path

import numpy as np
import pytest

pytestmark = pytest.mark.integration

TESTS_DIR = Path(__file__).resolve().parent
RESOURCES_DIR = TESTS_DIR.parents[2] / "resources"
MODEL = RESOURCES_DIR / "3compartment_flat.cellml"
MODULE_ARRAY_DIR = TESTS_DIR / "data" / "3compartment_module_array"

SEED = 1234
REL_TOL = 1e-12
PREFIX = "3compartment"

# The window: no offline_pre_time, so a calibrated initial state (q_lv_init) is allowed.
PRE_TIME = 4.0
SIM_TIME = 1.0
DT = 0.01
# Set on every route, and deliberately not any route's default.
SOLVER_INFO = {"solver": "CVODE_myokit", "MaximumStep": 0.002, "rtol": 1e-7, "atol": 1e-9}

GA_CALLS = 56
OPTIMISER = {
    "num_calls_to_function": GA_CALLS,
    "cost_convergence": 0.001,
    "max_patience": 10,
    "cost_type": "MSE",
}
SOBOL_SAMPLES = 8

# The notebook's legacy params_for_id entries.
PARAMS_FOR_ID = [
    {"vessel_name": "global", "param_name": "q_lv_init", "min": 200e-6, "max": 1500e-6,
     "name_for_plotting": "q_{sbv}"},
    {"vessel_name": "aortic_root", "param_name": "C", "min": 1e-9, "max": 5e-8,
     "name_for_plotting": "C_{ao}"},
]


def my_extra_feature(pressure, series_output=False):
    if series_output:
        return pressure
    half_idx = len(pressure) // 2
    return pressure[half_idx]


my_extra_feature.series_to_constant = True

#: the same operation as CUFLynx's funcs editor saves it: the marker as its decorator
OPERATION_SOURCE = "@series_to_constant\n" + textwrap.dedent(inspect.getsource(my_extra_feature))


def _ca(name, attr):
    from ca_imports import ca_from

    return ca_from(name, attr)


def _obs_data() -> dict:
    """The notebook's ObsDataCreator dict: a mean item and one user operation."""
    creator = _ca("utilities.obs_data_helpers", "ObsDataCreator")()
    creator.add_protocol_info([PRE_TIME], [[SIM_TIME]], {})
    creator.add_data_item({
        "data_item_name": "P aortic root mean", "trace_name_for_plotting": "P_{aoMean}",
        "operands": ["aortic_root/u"], "operation": "mean", "unit": "Pa",
        "value": 12700.0, "std": 200, "obs_dt": DT,
    })
    creator.add_data_item({
        "data_item_name": "P aortic root half time", "trace_name_for_plotting": "P_{aoHalf}",
        "operands": ["aortic_root/u"], "operation": "my_extra_feature", "unit": "Pa",
        "value": 12600.0, "std": 200,
    })
    return json.loads(json.dumps(creator.get_obs_data_dict()))


# ---------------------------------------------------------------------------
# The routes
# ---------------------------------------------------------------------------
def _notebook_route(out: Path, obs: dict, model: Path = MODEL,
                    solver_info: dict | None = SOLVER_INFO) -> dict:
    """The notebook's cells, in process: init_from_dict and setters. ``solver_info=None``
    leaves the solver settings and ``dt`` at get_default_inp_data_dict's defaults."""
    CVS0DParamID = _ca("param_id.paramID", "CVS0DParamID")
    SensitivityAnalysis = _ca("sensitivity_analysis.sensitivityAnalysis", "SensitivityAnalysis")
    get_default_inp_data_dict = _ca("utilities.utility_funcs", "get_default_inp_data_dict")

    out.mkdir(parents=True)
    inp = get_default_inp_data_dict(PREFIX, f"{PREFIX}_parameters.json", str(MODULE_ARRAY_DIR))
    inp.update({
        # The notebook generates the model; here it is the fixture, named the way a kept
        # model is (SensitivityAnalysis.init_from_dict parses the dict again).
        "DEBUG": True, "model_path": str(model), "generated_model_path": str(model),
        "model_type": "cellml",
        "param_id_output_dir": str(out / "param_id"),
        "sim_time": SIM_TIME, "pre_time": PRE_TIME,
    })
    if solver_info is not None:
        inp.update({"dt": DT, "solver_info": dict(solver_info)})

    t0 = time.monotonic()
    sa_dir = out / "sensitivity"
    sa_dir.mkdir()
    sa_options = {"method": "sobol", "sample_type": "saltelli", "num_samples": SOBOL_SAMPLES,
                  "output_dir": str(sa_dir), "seed": SEED}
    sa = SensitivityAnalysis.init_from_dict(inp)
    sa.add_user_operation_func(my_extra_feature)
    sa.set_ground_truth_data(obs)
    sa.set_params_for_id(PARAMS_FOR_ID)
    sa.set_sa_options(sa_options)
    np.random.seed(SEED)
    sa.run_sensitivity_analysis(sa_options)
    t_sa = time.monotonic() - t0

    t0 = time.monotonic()
    param_id = CVS0DParamID.init_from_dict(inp)
    param_id.add_user_operation_func(my_extra_feature)
    param_id.set_ground_truth_data(obs)
    param_id.set_params_for_id(PARAMS_FOR_ID)
    param_id.set_param_id_method("genetic_algorithm")
    param_id.set_optimiser_options({**OPTIMISER, "seed": SEED})
    param_id.run()
    param_id.simulate_with_best_param_vals()
    # CA's calibrated-model writer (libcuflynx #650); None on a libcuflynx without it.
    calibrated = (param_id.write_calibrated_model(output_dir=str(out))
                  if hasattr(param_id, "write_calibrated_model") else None)
    t_cal = time.monotonic() - t0

    names = param_id.get_param_names()
    best = [float(v) for v in param_id.get_best_param_vals()]
    return {
        "params": {q: v for row, v in zip(names, best) for q in row},
        "cost": float(param_id.param_id.best_cost),
        "solver_info": dict(param_id.solver_info),
        "calibrated_model": calibrated,
        "dirs": {"calibration": out / "param_id", "sensitivity": sa_dir},
        "seconds": {"sensitivity": t_sa, "calibration": t_cal},
    }


def _wait(client, kind: str, job_id: str, timeout_s: float = 300) -> dict:
    deadline = time.monotonic() + timeout_s
    offset, lines = 0, []
    while time.monotonic() < deadline:
        resp = client.get(f"/api/{kind}/{job_id}/status?offset={offset}")
        assert resp.status_code == 200, resp.text
        status = resp.json()
        offset = status["next_offset"]
        lines += status["lines"]
        if status["state"] in ("done", "error", "cancelled"):
            assert status["state"] == "done", (
                f"{kind}: {status.get('error')}; log tail: {lines[-20:]}")
            return status
        time.sleep(0.5)
    pytest.fail(f"{kind} did not finish in {timeout_s}s; log tail: {lines[-20:]}")


def _cuflynx_route(out: Path, obs: dict, solver_info: dict | None) -> dict:
    """The CUFLynx app: uploads, the funcs editor, /api/calibration/run and
    /api/sensitivity/run with the real runner subprocesses."""
    from fastapi.testclient import TestClient

    import main

    client = TestClient(main.app)
    work = out / "work"
    work.mkdir(parents=True)

    config = {"generated_model_format": "cellml", "solver": "CVODE_myokit", "seed": SEED}
    if solver_info is not None:
        config["solver_info"] = {**{k: v for k, v in solver_info.items() if k != "solver"},
                                 "dt": DT}
    resp = client.post("/api/config", json=config)
    assert resp.status_code == 200, resp.text

    resp = client.post("/api/models/upload",
                       files={"file": (MODEL.name, MODEL.read_bytes(), "application/xml")})
    assert resp.status_code == 200, resp.text
    model_id = resp.json()["model_id"]
    resp = client.post(f"/api/obs_data/upload?model_id={model_id}", json=obs)
    assert resp.status_code == 200, resp.text
    # The params_for_id file CUFLynx stores: the same entries in CA's JSON document form.
    doc = {"version": 1, "params": [
        {"targets": [f"{e['vessel_name']}/{e['param_name']}"], "min": e["min"],
         "max": e["max"], "name_for_plotting": e["name_for_plotting"]} for e in PARAMS_FOR_ID]}
    resp = client.post(
        f"/api/params_for_id/upload?model_id={model_id}",
        files={"file": (f"{PREFIX}_params_for_id.json", json.dumps(doc).encode(),
                        "application/json")})
    assert resp.status_code == 200, resp.text
    resp = client.post("/api/operation_funcs",
                       json={"name": "", "source": OPERATION_SOURCE, "output_dir": str(work)})
    assert resp.status_code == 200, resp.text

    common = {"DEBUG": True, "num_cores": 1, "python_path": sys.executable,
              "config_outputs_dir": str(work)}
    if solver_info is not None:
        common["dt"] = DT

    t0 = time.monotonic()
    resp = client.post("/api/sensitivity/run", json={"model_id": model_id, "settings": {
        **common, "method": "sobol", "sample_type": "saltelli",
        "num_samples": SOBOL_SAMPLES}})
    assert resp.status_code == 200, resp.text
    _wait(client, "sensitivity", resp.json()["job_id"])
    t_sa = time.monotonic() - t0

    # The Sobol files, before the calibration writes beside them.
    sa_dir = out / "sensitivity"
    sa_dir.mkdir()
    for path in work.rglob("all_outputs_n*_Sobol_*.csv"):
        path.rename(sa_dir / path.name)

    t0 = time.monotonic()
    resp = client.post("/api/calibration/run", json={"model_id": model_id, "settings": {
        **common, "param_id_method": "genetic_algorithm", **OPTIMISER}})
    assert resp.status_code == 200, resp.text
    status = _wait(client, "calibration", resp.json()["job_id"])
    t_cal = time.monotonic() - t0

    return {
        "params": {k: float(v) for k, v in status["best_params"].items()},
        "cost": float(status["cost"]),
        "calibrated_model": status.get("calibrated_model_path"),
        "dirs": {"calibration": work, "sensitivity": sa_dir},
        "seconds": {"sensitivity": t_sa, "calibration": t_cal},
    }


def _workflow_route(out: Path, obs: dict) -> dict:
    """A calibration workflow made with libcuflynx's WorkflowBuilder, run by its runner."""
    # Namespaced only: calibration_workflow is newer than CA's flat layout, so it is not
    # one of ca_imports.CA_PACKAGES and there is no second spelling to try.
    try:
        builder_mod = importlib.import_module("libcuflynx.calibration_workflow.builder")
    except ModuleNotFoundError:
        pytest.skip("this libcuflynx has no calibration_workflow.WorkflowBuilder (CA #652)")

    wf = builder_mod.WorkflowBuilder(
        "nb_equiv", builder_mod.module_array(
            str(MODULE_ARRAY_DIR / f"{PREFIX}_module_array.csv"),
            str(MODULE_ARRAY_DIR / f"{PREFIX}_parameters.json")),
        directory=out / "workflow",
        # debug_UQ_options: a DEBUG calibration step reads it unconditionally until CA
        # fix/route-consistency (KeyError otherwise); empty, as no step here runs UQ.
        settings={"dt": DT, "DEBUG": True, "solver_info": dict(SOLVER_INFO),
                  "debug_UQ_options": {}})
    wf.add_operation(my_extra_feature)
    wf.add_sensitivity("screen", obs_data=obs, params_for_id=PARAMS_FOR_ID, sa_options={
        "method": "sobol", "sample_type": "saltelli", "num_samples": SOBOL_SAMPLES,
        "seed": SEED})
    wf.add_calibration("fit", obs_data=obs, params_for_id=PARAMS_FOR_ID, settings={
        "param_id_method": "genetic_algorithm", "optimiser_options": {**OPTIMISER, "seed": SEED}})

    t0 = time.monotonic()
    run_dir = out / "run"
    result = wf.run(output_dir=run_dir, progress=False)
    seconds = time.monotonic() - t0
    assert result.get("complete"), result

    fit = json.loads((run_dir / "fit" / "step_result.json").read_text())
    # a calibrated record names its module (CA #716; ``vessel`` on a CA before it)
    params = {f"{c['module'] if 'module' in c else c['vessel']}/{c['param']}":
              float(c["value"]) for c in fit["calibrated"]}
    flat = sorted((run_dir / "fit" / "model").rglob("*_flat.cellml"))
    assert len(flat) == 1, flat
    return {
        "params": params,
        "cost": float(fit["best_cost"]),
        "flat_model": flat[0],
        "dirs": {"calibration": run_dir / "fit", "sensitivity": run_dir / "screen"},
        "seconds": {"workflow": seconds},
    }


# ---------------------------------------------------------------------------
# Comparisons
# ---------------------------------------------------------------------------
def _one(directory: Path, pattern: str) -> Path:
    found = sorted(Path(directory).rglob(pattern))
    assert len(found) == 1, f"expected one {pattern} under {directory}, found {found}"
    return found[0]


def _assert_traces_equal(a_dir: Path, b_dir: Path) -> None:
    a = np.load(_one(a_dir, "all_outputs_with_best_param_vals_exp_0.npz"), allow_pickle=True)
    b = np.load(_one(b_dir, "all_outputs_with_best_param_vals_exp_0.npz"), allow_pickle=True)
    keys = sorted(set(a.files) & set(b.files))
    assert any("aortic_root" in k for k in keys), keys
    for key in keys:
        x, y = a[key], b[key]
        if x.dtype.kind not in "fi":
            continue
        assert x.shape == y.shape, key
        np.testing.assert_allclose(y, x, rtol=REL_TOL, atol=0, err_msg=key)


def _sobol_tables(directory: Path):
    import pandas as pd

    return {kind: pd.read_csv(_one(directory, f"all_outputs_n*_Sobol_{kind}.csv"))
            for kind in ("samples", "outputs", "indices")}


def _assert_sobol_equal(a_dir: Path, b_dir: Path) -> None:
    a, b = _sobol_tables(a_dir), _sobol_tables(b_dir)
    for kind in a:
        x, y = a[kind], b[kind]
        assert list(x.columns) == list(y.columns), kind
        xn, yn = x.select_dtypes("number").to_numpy(), y.select_dtypes("number").to_numpy()
        assert xn.shape == yn.shape and xn.size, kind
        np.testing.assert_allclose(yn, xn, rtol=REL_TOL, atol=0, err_msg=kind)
        other = x.select_dtypes(exclude="number")
        assert other.equals(y.select_dtypes(exclude="number")), kind


def _cellml_values(path) -> dict:
    """``{qname: value}`` of every literal constant in a CellML file, read by Myokit."""
    import myokit.formats.cellml as cellml

    model = cellml.CellMLImporter().model(str(path))
    return {v.qname(): float(v.eval()) for v in model.variables(const=True, deep=True)
            if v.is_literal()}


def _assert_close(a: float, b: float, what: str) -> None:
    assert math.isclose(a, b, rel_tol=REL_TOL, abs_tol=0), f"{what}: {a!r} != {b!r}"


# ---------------------------------------------------------------------------
# The runs, once for the module
# ---------------------------------------------------------------------------
@pytest.fixture(scope="module")
def routes(simulation_deps_available, tmp_path_factory):
    if not simulation_deps_available:
        pytest.skip("myokit / libcellml / circulatory_autogen not available")
    from ca_imports import ensure_ca_path

    ensure_ca_path()
    root = tmp_path_factory.mktemp("notebook_equivalence")
    # libcuflynx's default directories (generated models, outputs) go under the run's.
    saved_user_dir = os.environ.get("CUFLYNX_USER_DIR")
    os.environ["CUFLYNX_USER_DIR"] = str(root / "user_dir")
    obs = _obs_data()
    runs = {
        "notebook": _notebook_route(root / "notebook", obs),
        "cuflynx": _cuflynx_route(root / "cuflynx", obs, SOLVER_INFO),
    }
    for name, run in runs.items():
        print(f"{name}: {run['seconds']}")
    yield runs
    if saved_user_dir is None:
        os.environ.pop("CUFLYNX_USER_DIR", None)
    else:
        os.environ["CUFLYNX_USER_DIR"] = saved_user_dir


@pytest.fixture(scope="module")
def workflow_run(routes, tmp_path_factory):
    """The workflow, and the notebook route again on the model the workflow generated.

    The 3compartment the module array generates is not the flat fixture (that one carries
    calibrated values and an older heart), so the notebook route is rerun on the
    workflow's own flattened model for this comparison."""
    root = tmp_path_factory.mktemp("notebook_equivalence_wf")
    obs = _obs_data()
    run = _workflow_route(root, obs)
    notebook = _notebook_route(root / "notebook", obs, run["flat_model"])
    print(f"workflow: {run['seconds']}; notebook on its model: {notebook['seconds']}")
    return run, notebook


def test_the_notebook_ran_with_the_solver_settings_given(routes):
    """The comparison means something only if the notebook route ran what it was given."""
    effective = routes["notebook"]["solver_info"]
    for key, value in SOLVER_INFO.items():
        assert effective.get(key) == value, (key, effective)


def test_cuflynx_calibration_matches_the_notebook(routes):
    nb, cuf = routes["notebook"], routes["cuflynx"]
    assert cuf["params"] == nb["params"]
    _assert_close(cuf["cost"], nb["cost"], "best cost")
    _assert_traces_equal(nb["dirs"]["calibration"], cuf["dirs"]["calibration"])


def test_cuflynx_sobol_matches_the_notebook(routes):
    _assert_sobol_equal(routes["notebook"]["dirs"]["sensitivity"],
                        routes["cuflynx"]["dirs"]["sensitivity"])


def test_calibrated_cellml_values_match_cas_writer(routes):
    """CUFLynx's calibrated_cellml and CA's write_calibrated_model write the same values:
    the best fit into the calibrated parameters, and every other constant unchanged."""
    nb, cuf = routes["notebook"], routes["cuflynx"]
    if nb["calibrated_model"] is None:
        pytest.skip("this libcuflynx has no CVS0DParamID.write_calibrated_model (CA #650)")
    assert cuf["calibrated_model"]
    ca_values = _cellml_values(nb["calibrated_model"])
    cuf_values = _cellml_values(cuf["calibrated_model"])
    original = _cellml_values(MODEL)
    assert set(cuf_values) == set(original)

    def by_name(values):
        # CA's writer flattens the model, so a component may be renamed; the variable's
        # own name and its value are what must survive.
        out = {}
        for qname, value in values.items():
            out.setdefault(qname.split(".")[-1], set()).add(value)
        return out

    assert by_name(ca_values) == by_name(cuf_values)
    for qname, value in nb["params"].items():
        component, name = qname.split("/")
        hits = [v for q, v in cuf_values.items()
                if q.split(".")[-1] in (name, f"{name}_{component}")]
        assert value in hits, (qname, value, hits)


def test_workflow_matches_the_notebook(workflow_run):
    wf, nb = workflow_run
    assert wf["params"] == nb["params"]
    _assert_close(wf["cost"], nb["cost"], "best cost")
    _assert_traces_equal(nb["dirs"]["calibration"], wf["dirs"]["calibration"])
    _assert_sobol_equal(nb["dirs"]["sensitivity"], wf["dirs"]["sensitivity"])


def _ca_defaults_are_one_rule() -> bool:
    """Whether CA states one set of CVODE_myokit defaults: its get_solver_info_default and the
    SOLVER_INFO_FIELDS CUFLynx reads agree (CA fix/route-consistency)."""
    from ca_imports import ca_import

    parsers = ca_import("parsers.PrimitiveParsers")
    fields = {f["name"]: f.get("default")
              for f in parsers.SOLVER_INFO_FIELDS.get("CVODE_myokit", [])}
    stated = parsers.get_solver_info_default("cellml")
    return all(stated.get(k) == fields.get(k) for k in ("rtol", "atol", "MaximumStep"))


def test_default_settings_agree(simulation_deps_available, tmp_path, monkeypatch):
    """The same comparison with no solver settings given: each route's own defaults."""
    if not simulation_deps_available:
        pytest.skip("myokit / libcellml / circulatory_autogen not available")
    from ca_imports import ensure_ca_path

    ensure_ca_path()
    if not _ca_defaults_are_one_rule():
        pytest.skip(
            "waits for circulatory_autogen fix/route-consistency (one max-step rule, one rtol "
            "default): until the libcuflynx under test has it, the notebook route and CUFLynx "
            "fill different solver defaults")
    monkeypatch.setenv("CUFLYNX_USER_DIR", str(tmp_path / "user_dir"))
    obs = _obs_data()
    nb = _notebook_route(tmp_path / "notebook", obs, solver_info=None)
    cuf = _cuflynx_route(tmp_path / "cuflynx", obs, None)
    assert cuf["params"] == nb["params"]
    _assert_close(cuf["cost"], nb["cost"], "best cost")
    _assert_traces_equal(nb["dirs"]["calibration"], cuf["dirs"]["calibration"])
    _assert_sobol_equal(nb["dirs"]["sensitivity"], cuf["dirs"]["sensitivity"])
