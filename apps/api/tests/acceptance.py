"""Checks that must hold of a *running* CUFLynx, wherever it is running.

The same questions are worth asking of the app in this repo and of a built
executable: does the engine answer, does the shipped example load whole, is a
study reopenable from a directory it produced. Asking them twice, in two
codebases, is how the release ends up testing something subtly different from
what CI tests -- and the release is the one nobody re-runs after a change.

So the checks live here once, over a tiny transport, and both callers supply
their own:

* the test suite passes an adapter over FastAPI's ``TestClient`` (fast, in-process,
  runs on every push -- see ``test_acceptance_checks.py``);
* ``scripts/analysis_smoke.py`` passes one over HTTP to a launched binary, so the
  release exercises the same list against the artifact users download.

A check takes a transport and returns a short string describing what it found --
that string is what a caller prints, so a passing run says what it proved rather
than only that it passed. Failures raise ``AssertionError``.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

RESOURCES = Path(__file__).resolve().parents[3] / "resources"
EXAMPLE = RESOURCES / "3compartment.omex"


# ---------------------------------------------------------------------------
# The checks
# ---------------------------------------------------------------------------
def check_engine_vocabulary(app) -> str:
    """The engine answers what an obs_data may contain.

    Reaches CA's registries through whatever engine this app resolved -- an
    installed libcuflynx, a configured checkout, or the copy inside a bundle. An
    app that cannot answer this has no engine at all, which in a packaged build
    is the difference between "ships an engine" and "ships a UI".
    """
    options = app.get("/api/obs_data/options")
    operations = options.get("operations") or options.get("operation_funcs") or []
    assert len(operations) > 3, f"only {len(operations)} operations: {options}"
    assert any("mean" in str(op) for op in operations), operations
    return f"{len(operations)} operations"


def check_example_is_current(app) -> str:
    """The bundled example is served, uncacheable, and is the file in the repo.

    Uncacheable because the browser fetches it and posts the bytes back to the
    upload route, so a cached copy -- not the server -- would decide which version
    is imported. Byte-identical because an example that drifts from `resources/`
    is one nobody notices until it fails to load.
    """
    blob, headers = app.get_raw("/api/examples/3compartment")
    lower = {k.lower(): v for k, v in headers.items()}
    assert "no-cache" in lower.get("cache-control", ""), lower
    assert hashlib.sha256(blob).hexdigest() == hashlib.sha256(EXAMPLE.read_bytes()).hexdigest()
    return f"{len(blob)} bytes, no-cache, matches resources/"


def check_example_loads_whole(app) -> str:
    """One drop of the example gives back a whole study, with nothing to report.

    The example is a *file*, so it does not follow the engine's schema when that
    changes: CA #466 made `data_item_name` unique, which turns any example written
    before it into a study that loads with an empty observations tab.
    """
    body = app.upload("/api/omex/upload", "3compartment.omex", EXAMPLE.read_bytes())
    obs = body.get("obs_data") or {}
    assert not obs.get("error"), obs["error"]
    assert len(obs.get("data_items") or []) == 6, obs
    assert len((body.get("params_for_id") or {}).get("params") or []) == 4, body["params_for_id"]
    assert body.get("warnings") == [], body["warnings"]
    return f"{body.get('name')}: 6 obs items, 4 params, no warnings"


def check_old_obs_data_is_refused_with_the_fix(app, archive: bytes) -> str:
    """A pre-#466 obs_data is rejected, and the message names the migrator.

    The failure a user actually meets when they open a study written before the
    vocabulary split. The engine's complaint is about the *consequence* -- a
    duplicate name -- so the app adds the cause and the command that fixes it.
    """
    body = app.upload("/api/omex/upload", "old.omex", archive)
    error = (body.get("obs_data") or {}).get("error") or ""
    assert "Duplicate 'data_item_name'" in error, error[:300]
    assert "cuflynx-migrate-obs-data" in error, error[:400]
    return "refused, and names the migrator"


def check_directory_reopens(app, outputs_dir: str) -> str:
    """A finished outputs directory gives back its results *and* its study.

    Results without the study is half a reopen: the panels fill and the
    Parameters tab stays empty, and loading the files by hand afterwards clears
    what was just loaded.
    """
    found = app.get(f"/api/outputs/load?dir={app.quote(outputs_dir)}")
    assert not found.get("error"), found["error"]
    assert found.get("found"), f"nothing found in {outputs_dir}"
    assert found.get("missing") == [], found["missing"]

    study = app.post("/api/outputs/study", {"dir": outputs_dir})
    assert study.get("model_id"), study
    obs = study.get("obs_data") or {}
    assert obs.get("data_items"), f"study reopened without its obs_data: {obs}"
    return (f"{', '.join(found['found'])}; study {study.get('name')} with "
            f"{len(obs['data_items'])} obs items")


def check_simulates_on_every_backend(app, model_bytes: bytes, filename: str) -> str:
    """A real simulation through each generated-model format the app offers.

    Each has broken in a packaged build for a different reason -- a libcellml the
    engine could not use, casadi missing from the bundle, the Python headers Myokit
    needs to JIT-compile a model -- and only running one catches that.
    """
    up = app.upload("/api/models/upload", filename, model_bytes, field="file",
                    content_type="application/xml")
    counts = []
    for name, config in (
        ("python", {"generated_model_format": "python", "solver": "solve_ivp",
                    "solver_info": {"method": "RK45", "dt": 0.01}}),
        ("casadi_python", {"generated_model_format": "casadi_python",
                           "solver": "casadi_integrator",
                           "solver_info": {"method": "cvodes", "dt": 0.01}}),
        ("cellml", {"generated_model_format": "cellml", "solver": "CVODE_myokit",
                    "solver_info": {"method": "CVODE", "dt": 0.01}}),
    ):
        app.post("/api/config", config)
        out = app.post("/api/simulate", {"model_id": up["model_id"], "params": {},
                                         "sim_time": 2.0})
        points = len(out.get("time") or [])
        assert points > 1 and out.get("outputs"), f"{name}: {points} points"
        counts.append(f"{name}={points}")
    return ", ".join(counts) + " points"
