"""Tests for the runtime CA-directory + backend-solver config endpoints."""

import os

import engine as engine_mod
import solver_options as solver_options_mod


def test_get_config_shape(client):
    body = client.get("/api/config").json()
    assert {"ca_dir", "ca_src", "ca_exists"} <= set(body)


def test_config_reports_mpiexec_availability(client, monkeypatch):
    """The UI warns before a num_cores>1 run silently drops to a single core, so
    /api/config must report whether a launcher is available for the current
    interpreter (resolved the same way the run does)."""
    import main

    monkeypatch.setattr(main, "resolve_mpiexec", lambda python: "/usr/bin/mpiexec")
    assert client.get("/api/config").json()["mpiexec_available"] is True

    monkeypatch.setattr(main, "resolve_mpiexec", lambda python: None)
    assert client.get("/api/config").json()["mpiexec_available"] is False


def test_config_exposes_backend_solver_capabilities(client):
    body = client.get("/api/config").json()
    # Settings UI + AD gating metadata.
    assert {
        "generated_model_format",
        "solver",
        "solver_info",
        "model_formats",
        "solvers_by_format",
        "solver_info_schema",
        "ad_available",
    } <= set(body)
    assert body["generated_model_format"] == "cellml_only"
    assert body["ad_available"] is False  # cellml_only never offers AD


def test_set_backend_solver_roundtrips(client):
    resp = client.post(
        "/api/config",
        json={
            "generated_model_format": "python",
            "solver": "solve_ivp",
            "solver_info": {"method": "BDF", "rtol": 1e-6},
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["generated_model_format"] == "python"
    assert body["solver"] == "solve_ivp"
    assert body["solver_info"]["method"] == "BDF"
    assert body["ad_available"] is False  # python (not casadi_python)
    # Stored on the engine (live sim) + exported for subprocess runners.
    assert engine_mod.engine.model_type == "python"
    assert engine_mod.engine.solver == "solve_ivp"
    assert os.environ["CUFLYNX_MODEL_TYPE"] == "python"
    assert os.environ["CUFLYNX_SOLVER"] == "solve_ivp"


def test_dt_in_solver_info_sets_engine_dt(client):
    """dt is edited in the solver-settings form but is engine-level: it must be
    pulled out of solver_info and applied to engine.dt (and echoed back)."""
    resp = client.post(
        "/api/config",
        json={
            "generated_model_format": "casadi_python",
            "solver": "casadi_integrator",
            "solver_info": {"method": "semi_implicit_euler", "dt": 0.005},
        },
    )
    assert resp.status_code == 200, resp.text
    assert engine_mod.engine.dt == 0.005
    # dt is not kept as a solver_info key (passed separately to the solver)...
    assert "dt" not in engine_mod.engine.solver_info
    # ...but is surfaced in the payload's solver_info for the UI.
    assert resp.json()["solver_info"]["dt"] == 0.005


def test_set_backend_solver_rejects_incompatible_solver(client):
    # solve_ivp is for python models; CVODE_myokit is cellml-only.
    resp = client.post(
        "/api/config",
        json={"generated_model_format": "python", "solver": "CVODE_myokit"},
    )
    assert resp.status_code == 422


def test_set_unknown_format_422(client):
    resp = client.post("/api/config", json={"generated_model_format": "bogus_format"})
    assert resp.status_code == 422


def test_config_passes_casadi_ad_source_through_for_client_side_gating(client, monkeypatch):
    """/api/config is model-agnostic, so it can't evaluate the per-model
    requires_all_differentiable gate (which ops the loaded obs_data uses). It must
    therefore pass the CasADi AD source through *with its flag* even when the
    whole-registry all_differentiable is False — the client gates it against its
    in-use differentiability. Regression: gating on the coarse whole-registry flag
    here hid AD for every casadi_python model."""
    monkeypatch.setattr(
        solver_options_mod,
        "_introspect_differentiable",
        lambda: {"max": True, "calc_spike_period": False},  # registry NOT all-differentiable
    )
    solver_options_mod.reset_cache()
    body = client.post(
        "/api/config",
        json={"generated_model_format": "casadi_python", "solver": "casadi_integrator"},
    ).json()
    assert body["all_differentiable"] is False
    ad = [s for s in body["gradient_sources"] if s["value"] == "AD"]
    assert ad and ad[0]["requires_all_differentiable"] is True
    solver_options_mod.reset_cache()


def test_ad_available_when_casadi_python_and_all_ops_differentiable(client, monkeypatch):
    """When casadi_python is chosen and every operation is @differentiable, the
    payload reports ad_available so the UI can offer AD local-SA + sp_minimize."""
    monkeypatch.setattr(
        solver_options_mod, "_introspect_differentiable", lambda: {"max": True, "min": True}
    )
    solver_options_mod.reset_cache()
    body = client.post(
        "/api/config",
        json={"generated_model_format": "casadi_python", "solver": "casadi_integrator"},
    ).json()
    assert body["all_differentiable"] is True
    assert body["ad_available"] is True
    solver_options_mod.reset_cache()


def test_ad_unavailable_when_an_op_is_not_differentiable(client, monkeypatch):
    monkeypatch.setattr(
        solver_options_mod,
        "_introspect_differentiable",
        lambda: {"max": True, "calc_spike_period": False},
    )
    solver_options_mod.reset_cache()
    body = client.post(
        "/api/config",
        json={"generated_model_format": "casadi_python", "solver": "casadi_integrator"},
    ).json()
    assert body["all_differentiable"] is False
    assert body["ad_available"] is False
    solver_options_mod.reset_cache()


def test_engine_forwards_format_and_solver_to_helper_factory():
    """The engine threads its model_type/solver/solver_info into the helper
    factory (so live sims use the configured backend) — no CA needed."""
    captured = {}

    class _Dummy:
        def reset_and_clear(self):
            pass

        def update_times(self, *a, **k):
            pass

        def set_param_vals(self, *a, **k):
            pass

        def run(self):
            return True

        def get_time(self, include_pre_time=False):
            return [0.0, 1.0]

        def get_results(self, variables, flatten=False):
            return [[0.0, 1.0]]

    eng = engine_mod.engine
    eng.model_type = "casadi_python"
    eng.solver = "casadi_integrator"

    def _factory(**kwargs):
        captured.update(kwargs)
        return _Dummy()

    eng.helper_factory = _factory
    out = eng.simulate("m", "/tmp/model.py", {}, 1.0, 0.0, ["a/b"])
    assert out["outputs"]["a/b"] == [0.0, 1.0]
    assert captured["model_type"] == "casadi_python"
    assert captured["solver"] == "casadi_integrator"
    assert captured["solver_info"] == eng.solver_info


def test_set_config_repo_dir_appends_src(client, tmp_path):
    (tmp_path / "src").mkdir()
    resp = client.post("/api/config", json={"ca_dir": str(tmp_path)})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["ca_src"] == str(tmp_path / "src")
    assert body["ca_dir"] == str(tmp_path)
    assert body["ca_exists"] is True


def test_set_config_src_dir_used_directly(client, tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    resp = client.post("/api/config", json={"ca_dir": str(src)})
    assert resp.status_code == 200, resp.text
    assert resp.json()["ca_src"] == str(src)


def test_set_config_invalid_dir_422(client):
    assert client.post("/api/config", json={"ca_dir": "/no/such/dir"}).status_code == 422


def test_set_config_blank_resets_to_default(client, tmp_path):
    (tmp_path / "src").mkdir()
    client.post("/api/config", json={"ca_dir": str(tmp_path)})
    # Blank clears the override; ca_dir falls back to the sibling default.
    body = client.post("/api/config", json={"ca_dir": ""}).json()
    assert str(tmp_path) not in body["ca_src"]


# ---------------------------------------------------------------------------
# Regression: a config POST that doesn't mention ca_dir must not clear it.
#
# The Settings popup saves solver choices with a payload carrying no ca_dir, and
# ca_dir used to default to "" == "reset to default". From source that looked
# harmless (the default is the sibling clone), but in the packaged app there is
# no sibling: changing any solver setting silently dropped the user's CA dir, and
# every non-Myokit backend then died with "No module named 'generators'".
# ---------------------------------------------------------------------------
def test_solver_only_post_preserves_the_ca_dir(client, tmp_path):
    ca = tmp_path / "circulatory_autogen"
    (ca / "src").mkdir(parents=True)
    client.post("/api/config", json={"ca_dir": str(ca)})

    # Exactly what the Settings popup sends when only the solver changes.
    body = client.post(
        "/api/config",
        json={
            "generated_model_format": "python",
            "solver": "solve_ivp",
            "solver_info": {"method": "RK45", "dt": 0.01},
        },
    ).json()

    assert body["ca_dir"] == str(ca)
    assert body["ca_exists"] is True
    assert os.environ["CIRCULATORY_AUTOGEN_SRC"] == str(ca / "src")


def test_explicit_empty_ca_dir_still_resets_to_the_default(client, tmp_path):
    ca = tmp_path / "circulatory_autogen"
    (ca / "src").mkdir(parents=True)
    client.post("/api/config", json={"ca_dir": str(ca)})

    client.post("/api/config", json={"ca_dir": ""})

    assert "CIRCULATORY_AUTOGEN_SRC" not in os.environ


def test_frozen_app_reports_no_ca_dir_instead_of_guessing_a_sibling(monkeypatch):
    """Frozen, __file__ is inside the bundle, so the sibling guess would produce
    nonsense like '/circulatory_autogen'. Report "not configured" instead."""
    monkeypatch.delenv("CIRCULATORY_AUTOGEN_SRC", raising=False)
    monkeypatch.setattr(engine_mod, "is_frozen", lambda: True)

    assert engine_mod._circulatory_autogen_src() == ""


def test_unconfigured_ca_is_never_put_on_sys_path(monkeypatch):
    """"" on sys.path means the CWD — which would import whatever happens to be
    next to wherever the user launched the app."""
    import sys

    monkeypatch.setattr(engine_mod, "_circulatory_autogen_src", lambda: "")
    before = list(sys.path)
    engine_mod._ensure_ca_on_path()
    assert sys.path == before


def test_ca_exists_is_false_when_unconfigured(client, monkeypatch):
    """Regression: frozen + unconfigured, _circulatory_autogen_src() returns "",
    and Path("").is_dir() is True (empty path -> cwd). ca_exists must still be
    False so the packaged app prompts for a CA dir instead of silently proceeding
    and failing the first run with 'No module named generators'."""
    import main as main_mod

    monkeypatch.setattr(main_mod, "_circulatory_autogen_src", lambda: "")
    body = client.get("/api/config").json()
    assert body["ca_src"] == ""
    assert body["ca_exists"] is False
