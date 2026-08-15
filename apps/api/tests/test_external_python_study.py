"""external_python as a *format*: the solver menu, the live-backend rules, the
files a study carries, and what the analysis run configs say about it.

Unit tier throughout. The CA-present arm is exercised against a CA-shaped schema
literal rather than the sibling checkout, the way test_solver_options does it, so
the coverage does not depend on which machine runs the suite.
"""

import json
from pathlib import Path

import pytest

import engine as engine_mod
import main
import solver_options as so
import user_funcs
from py_model_meta import parse_py_model

FIXTURE = Path(__file__).resolve().parent / "data" / "heat1d_external_model.py"

# CA's SOLVER_SCHEMA as it advertises the external backend, trimmed to what
# _build_options reads. The solver_info descriptor is CA's own: a `dict`-typed
# `user_config`, which is why the form builder has to render one dict field.
CA_SCHEMA_WITH_EXTERNAL = {
    "model_types": ["cellml_only", "python", "casadi_python", "external_python"],
    "solvers_by_model_type": {
        "cellml_only": ["CVODE_myokit"],
        "python": ["solve_ivp"],
        "casadi_python": ["casadi_integrator"],
        "external_python": ["external"],
    },
    "methods_by_solver": {
        "CVODE_myokit": ["CVODE"],
        "solve_ivp": ["RK45"],
        "casadi_integrator": ["bdf"],
        "external": ["external"],
    },
    "default_solver_by_model_type": {
        "cellml_only": "CVODE_myokit",
        "python": "solve_ivp",
        "casadi_python": "casadi_integrator",
        "external_python": "external",
    },
    "solver_info_fields_by_solver": {
        "CVODE_myokit": [{"name": "MaximumStep", "type": "float", "default": 0.001}],
        "external": [
            {"name": "user_config", "type": "dict", "default": None, "required": False},
        ],
    },
}

BOTH_SCHEMAS = pytest.mark.parametrize(
    "schema",
    [so.FALLBACK_SOLVER_SCHEMA, CA_SCHEMA_WITH_EXTERNAL],
    ids=["fallback (no CA: the packaged cold start)", "CA schema"],
)


# ---------------------------------------------------------------------------
# The solver menu
# ---------------------------------------------------------------------------
@BOTH_SCHEMAS
def test_external_python_is_offered_with_its_one_solver(schema):
    opts = so._build_options(schema, {"max": True})
    assert "external_python" in opts["model_formats"]
    assert opts["solvers_by_format"]["external_python"] == ["external"]
    assert opts["default_solver_by_format"]["external_python"] == "external"
    assert opts["methods_by_solver"]["external"] == ["external"]


@BOTH_SCHEMAS
def test_user_config_is_the_external_solvers_one_setting(schema):
    """It is a dict, and the other dict-typed solver_info fields are dropped as
    unrenderable -- but dropping this one would leave the format unconfigurable
    *and* make check_solver_info reject the key CA expects."""
    opts = so._build_options(schema, {"max": True})
    fields = {f["key"]: f for f in opts["solver_info_schema"]["external"]}
    assert fields["user_config"]["type"] == "json"
    assert "dt" in fields


def test_check_solver_info_accepts_user_config(monkeypatch):
    monkeypatch.setattr(
        so, "get_solver_options",
        lambda *a, **k: so._build_options(so.FALLBACK_SOLVER_SCHEMA, {"max": True}),
    )
    so.check_solver_info("external", {"user_config": {"mesh": "fine"}, "dt": 0.01})
    assert so.filter_solver_info("external", {"user_config": {}, "nonsense": 1}) == {
        "user_config": {}
    }


def test_the_fallback_schema_still_offers_external_python_when_ca_is_absent(monkeypatch):
    """The packaged app's cold start: no CA directory chosen yet, and the format
    a user reaches for first (having been handed a .py) must still be on the
    menu."""
    def _boom():
        raise ImportError("no circulatory_autogen")

    monkeypatch.setattr(so, "_introspect_solver_schema", _boom)
    so.reset_cache()
    opts = so.get_solver_options(refresh=True)
    assert "external_python" in opts["model_formats"]
    so.reset_cache()


# ---------------------------------------------------------------------------
# Live-backend rules
# ---------------------------------------------------------------------------
def test_external_python_has_a_backend_probe():
    assert engine_mod._BACKEND_MODULE["external_python"] == "numpy"


def test_external_python_is_neither_a_fallback_source_nor_a_target():
    """A substitute backend would run a *different model*, not merely a different
    integrator -- so the preview must fail rather than quietly show another
    model's plot."""
    formats = {fmt for fmt, _solver in engine_mod._LIVE_FALLBACKS}
    assert "external_python" not in formats
    assert "external" not in {solver for _fmt, solver in engine_mod._LIVE_FALLBACKS}


def test_live_backend_never_substitutes_for_external_python(monkeypatch):
    monkeypatch.setattr(engine_mod, "backend_importable", lambda _t: False)
    engine_mod.engine.model_type = "external_python"
    engine_mod.engine.solver = "external"
    assert engine_mod.engine.live_backend() == ("external_python", "external", None)


def test_live_backend_still_falls_back_for_the_other_formats(monkeypatch):
    monkeypatch.setattr(
        engine_mod, "backend_importable", lambda t: t == "python",
    )
    engine_mod.engine.model_type = "casadi_python"
    engine_mod.engine.solver = "casadi_integrator"
    model_type, solver, fell_back = engine_mod.engine.live_backend()
    assert (model_type, solver, fell_back) == ("python", "solve_ivp", "casadi_python")


def test_a_helper_that_cannot_be_built_names_the_interpreter_setting():
    """Building an external helper *imports the user's module*, so its failures
    are import failures -- and in-process that import happens somewhere the user
    never installed anything."""
    def _explode(**_kwargs):
        raise ModuleNotFoundError("No module named 'fenics'")

    engine_mod.engine.model_type = "external_python"
    engine_mod.engine.solver = "external"
    engine_mod.engine.helper_factory = _explode
    with pytest.raises(engine_mod.SimulationError) as exc:
        engine_mod.engine.simulate(
            model_id="m1", model_path="/tmp/user_model.py", params={},
            sim_time=1.0, pre_time=0.0, outputs=[],
        )
    message = str(exc.value)
    assert "fenics" in message
    assert "Settings" in message


def test_the_same_failure_for_cellml_says_nothing_about_interpreters():
    def _explode(**_kwargs):
        raise RuntimeError("could not compile")

    engine_mod.engine.helper_factory = _explode
    with pytest.raises(engine_mod.SimulationError) as exc:
        engine_mod.engine.simulate(
            model_id="m1", model_path="/tmp/x.cellml", params={},
            sim_time=1.0, pre_time=0.0, outputs=[],
        )
    assert "external_python" not in str(exc.value)


# ---------------------------------------------------------------------------
# Study travel: the fourth user_funcs kind
# ---------------------------------------------------------------------------
def test_the_model_kind_is_not_an_editable_func_kind():
    assert "model" not in user_funcs.FUNC_KINDS
    assert set(user_funcs.FUNC_KINDS) == {"operation", "cost", "modifier"}


@pytest.mark.parametrize("path", ["/api/model_funcs", "/api/module_funcs"])
def test_the_funcs_editor_has_no_route_for_the_model_kind(client, path):
    assert client.get(path).status_code == 404


def test_the_editor_refuses_the_model_kind_directly():
    """A solver class is not a list of top-level defs; the listing, the
    validation and the renderer all assume it is."""
    for call in (
        lambda: user_funcs.read_user_funcs("model"),
        lambda: user_funcs.save_user_func("model", None, "def f():\n    pass\n"),
        lambda: user_funcs.delete_user_func("model", "f"),
    ):
        with pytest.raises(user_funcs.UserFuncError) as exc:
            call()
        assert "module" in str(exc.value)


def test_external_paths_carries_the_model_under_cas_config_key(tmp_path):
    assert "external_model_path" not in user_funcs.external_paths(str(tmp_path))
    saved = user_funcs.save_model_module(FIXTURE.read_bytes(), str(tmp_path))
    assert Path(saved).name == "user_model.py"
    paths = user_funcs.external_paths(str(tmp_path))
    assert paths["external_model_path"] == saved
    # Verbatim: it is the user's program, and the study reproduces *that*.
    assert Path(saved).read_bytes() == FIXTURE.read_bytes()


def test_include_modules_false_withholds_a_stale_model(tmp_path):
    """user_model.py is whatever .py was uploaded last under this output dir and
    outlives a switch to a CellML model, so the export asks for it explicitly."""
    user_funcs.save_model_module(FIXTURE.read_bytes(), str(tmp_path))
    assert "external_model_path" not in user_funcs.external_paths(
        str(tmp_path), include_modules=False
    )


def test_uploading_a_py_stores_it_beside_the_user_funcs(client, tmp_path):
    resp = client.post(
        f"/api/models/upload?output_dir={tmp_path}",
        files={"file": ("heat1d.py", FIXTURE.read_bytes(), "text/x-python")},
    )
    assert resp.status_code == 200, resp.text
    assert (tmp_path / "user_funcs" / "user_model.py").read_bytes() == FIXTURE.read_bytes()


# ---------------------------------------------------------------------------
# Analysis run configs
# ---------------------------------------------------------------------------
def _external_model_with_study(client, tmp_path):
    """Upload the .py and give the record the obs/params an analysis needs.

    Attached directly rather than through their upload routes: what is under test
    is the *model* half of the config, and the obs/params halves have their own
    tests that should not have to be satisfied twice.
    """
    model_id = client.post(
        "/api/models/upload",
        files={"file": ("heat1d.py", FIXTURE.read_bytes(), "text/x-python")},
    ).json()["model_id"]
    obs = tmp_path / "obs_data.json"
    obs.write_text(json.dumps({"data_items": []}))
    params = tmp_path / "params_for_id.csv"
    params.write_text("vessel_name,param_name,param_type,min,max\nheat,k,constant,0.1,2.0\n")
    record = main._models[model_id]
    record.obs_path = obs
    record.params_path = params
    return model_id


@pytest.mark.parametrize(
    "route,manager",
    [
        ("/api/calibration/run", "calibration"),
        ("/api/sensitivity/run", "sensitivity"),
        # UQ is deliberately absent: its route refuses to start without a
        # completed calibration to reuse, which is a different test's subject.
        # It builds its config from the same three lines (main.py) as these two.
        ("/api/emulator/train", "emulator"),
    ],
)
def test_the_run_config_names_the_py_and_the_format(client, tmp_path, monkeypatch, route, manager):
    """Every analysis tier has to be told the same two things: that the model is
    external_python, and where the user's .py is. resolve_model_path answers the
    second, verbatim."""
    model_id = _external_model_with_study(client, tmp_path)
    engine_mod.engine.model_type = "external_python"
    engine_mod.engine.solver = "external"

    captured = {}
    monkeypatch.setattr(
        getattr(main, manager), "start", lambda config: captured.update(config) or "job-1"
    )
    resp = client.post(route, json={"model_id": model_id, "settings": {}})
    assert resp.status_code == 200, resp.text
    assert captured["model_type"] == "external_python"
    assert captured["solver"] == "external"
    assert captured["model_path"] == str(main.UPLOAD_DIR / f"{model_id}.py")


def test_the_export_bundle_carries_the_py_and_the_external_model_path(client, tmp_path):
    model_id = client.post(
        f"/api/models/upload?output_dir={tmp_path}",
        files={"file": ("heat1d.py", FIXTURE.read_bytes(), "text/x-python")},
    ).json()["model_id"]
    resp = client.post(
        "/api/export/pipeline",
        json={
            "model_id": model_id,
            "file_prefix": "heat1d",
            "config_outputs_dir": str(tmp_path),
        },
    )
    assert resp.status_code == 200, resp.text
    export_dir = Path(resp.json()["export_dir"])
    # The model where CA resolves model_path, keeping its own suffix: a .py
    # copied out as ".cellml" would be a file whose name lies about it.
    assert (export_dir / "generated_models" / "heat1d" / "heat1d.py").is_file()
    # ...and named to CA by its own config key, relative like every other resource.
    yaml_text = next(export_dir.glob("user_inputs_*.yaml")).read_text()
    assert "external_model_path: resources/user_model.py" in yaml_text
    assert (export_dir / "resources" / "user_model.py").is_file()


def test_a_cellml_export_carries_no_external_model_path(client, tmp_path):
    """A stale user_model.py from an earlier external study must not point the
    exported run at a model this study does not use."""
    from conftest import BG_MODEL_PATH

    user_funcs.save_model_module(FIXTURE.read_bytes(), str(tmp_path))
    model_id = client.post(
        f"/api/models/upload?output_dir={tmp_path}",
        files={"file": (BG_MODEL_PATH.name, BG_MODEL_PATH.read_bytes(), "application/xml")},
    ).json()["model_id"]
    resp = client.post(
        "/api/export/pipeline",
        json={"model_id": model_id, "file_prefix": "bg", "config_outputs_dir": str(tmp_path)},
    )
    assert resp.status_code == 200, resp.text
    export_dir = Path(resp.json()["export_dir"])
    yaml_text = next(export_dir.glob("user_inputs_*.yaml")).read_text()
    assert "external_model_path" not in yaml_text
    assert (export_dir / "generated_models" / "bg" / "bg.cellml").is_file()


def test_the_exported_script_absolutises_external_model_path(tmp_path):
    """The generated script matches the *funcs* keys by suffix; the model key
    does not share that suffix, so it is named alongside them -- otherwise the
    exported run would look for the model wherever it lived on the machine that
    produced the bundle."""
    import export_pipeline as ep
    import os

    script = tmp_path / "run_pipeline.py"
    script.write_text(ep.render_pipeline_script(), encoding="utf-8")
    ns = {"__name__": "exported_pipeline", "__file__": str(script)}
    exec(compile(script.read_text(), str(script), "exec"), ns)  # noqa: S102

    cfg = {
        "file_prefix": "heat1d",
        "model_file": "heat1d.py",
        "model_type": "external_python",
        "external_model_path": "resources/user_model.py",
    }
    inp = ns["build_inp_data_dict"](cfg, str(tmp_path))
    assert os.path.isabs(inp["external_model_path"])
    assert os.path.normpath(inp["external_model_path"]) == os.path.normpath(
        os.path.join(str(tmp_path), "resources", "user_model.py")
    )
    # And model_path still points at the bundled copy, with its own suffix.
    assert inp["model_path"].endswith(os.path.join("heat1d", "heat1d.py"))


def test_the_fixture_satisfies_the_contract_the_export_relies_on():
    meta = parse_py_model(FIXTURE.read_bytes())
    assert meta.name == "Heat1D"
    assert meta.params and meta.algebraic
