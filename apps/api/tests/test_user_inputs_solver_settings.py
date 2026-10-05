"""A libcuflynx user_inputs.yaml's solver settings are adopted, and used.

A study is solved the way its author solved it: the yaml's ``solver``,
``solver_info`` (``MaximumStep`` ...) and ``dt`` become the app's -- from inside an
.omex, from a reopened run directory, or from the Settings picker -- for the live
plot and for every calibration / sensitivity / UQ / emulator run. Before this the
yaml was carried byte-for-byte and never read, so a study calibrated by CA with
``MaximumStep: 1e-4`` was recalibrated here at CA's 1e-3 default and dt 0.01.
"""

from __future__ import annotations

import io
import json
import os
import time
import zipfile

import pytest

import calibration as calibration_mod
import main
import omex_import
import settings_store
from conftest import (
    LV_MODEL_PATH,
    LV_OBS_DATA_PATH,
    LV_PARAMS_CSV_PATH,
    WRITE_CA_RESULTS_SRC,
)
from engine import engine

USER_INPUTS = """\
model_type: cellml
file_prefix: lv
dt: 0.005
solver_info:
  solver: CVODE_myokit
  method: CVODE
  MaximumStep: 0.0001
  MaximumNumberOfSteps: 1000000
"""


@pytest.fixture(autouse=True)
def solver_state(tmp_path, monkeypatch):
    """The engine and the env are process-wide: put back whatever a test adopts."""
    monkeypatch.setenv("CUFLYNX_CONFIG_DIR", str(tmp_path / "cfg"))
    for var in ("CUFLYNX_MODEL_TYPE", "CUFLYNX_SOLVER", "CUFLYNX_SOLVER_INFO"):
        monkeypatch.setenv(var, os.environ.get(var, ""))
    before = (engine.dt, engine.solver, engine.model_type, dict(engine.solver_info))
    engine.dt, engine.solver = 0.01, "CVODE_myokit"
    engine.solver_info = {"MaximumStep": 0.001}
    yield
    engine.dt, engine.solver, engine.model_type = before[:3]
    engine.solver_info = before[3]


def _archive(**extra) -> bytes:
    members = {
        "lv.cellml": LV_MODEL_PATH.read_bytes(),
        "lv_obs_data.json": LV_OBS_DATA_PATH.read_bytes(),
        "lv_params_for_id.csv": LV_PARAMS_CSV_PATH.read_bytes(),
        **extra,
    }
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in members.items():
            zf.writestr(name, data)
    return buf.getvalue()


def _upload(client, data: bytes, **params):
    resp = client.post("/api/omex/upload", params=params,
                       files={"file": ("study.omex", data, "application/zip")})
    assert resp.status_code == 200, resp.text
    return resp.json()


# ---------------------------------------------------------------------------
# Recognition
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("name, hit", [
    ("CA_volume_control_user_inputs.yaml", True),
    ("user_inputs_260101.yml", True),
    ("bundle/user_inputs.yaml", True),
    ("user_inputs.json", False),
    ("inputs.yaml", False),
])
def test_a_user_inputs_yaml_is_recognised_by_name(name, hit):
    assert omex_import.is_user_inputs_name(name) is hit


def test_unpack_returns_the_yaml():
    parts = omex_import.unpack(_archive(**{"x_user_inputs.yaml": USER_INPUTS}))
    assert parts["user_inputs"] == ("x_user_inputs.yaml", USER_INPUTS.encode())
    assert parts["roles"]["user_inputs"] == ["x_user_inputs.yaml"]


def test_reading_takes_cas_keys_and_nothing_else():
    si, notes = main._solver_settings_from_user_inputs(USER_INPUTS.encode(), "u.yaml")
    assert notes == []
    assert si == {"solver": "CVODE_myokit", "method": "CVODE", "MaximumStep": 0.0001,
                  "MaximumNumberOfSteps": 1000000, "dt": 0.005}


def test_a_legacy_top_level_solver_is_read():
    si, _ = main._solver_settings_from_user_inputs(b"solver: CVODE_myokit\ndt: 0.002\n", "u.yaml")
    assert si == {"solver": "CVODE_myokit", "dt": 0.002}


@pytest.mark.parametrize("blob", [b"solver_info: [unclosed", b"- just\n- a list\n"])
def test_an_unreadable_yaml_is_a_note_not_an_exception(blob):
    si, notes = main._solver_settings_from_user_inputs(blob, "u.yaml")
    assert si is None and "u.yaml" in notes[0]


# ---------------------------------------------------------------------------
# Adoption: filtering, notes, persistence
# ---------------------------------------------------------------------------
def test_an_unsupported_key_is_dropped_with_a_note_never_rejected():
    notes, adopted = main._adopt_solver_settings(
        {"solver": "CVODE_myokit", "MaximumStep": 1e-4, "MaximumNumberOfSteps": 10**6}, "u.yaml")
    assert engine.solver_info["MaximumStep"] == pytest.approx(1e-4)
    assert "MaximumNumberOfSteps" not in engine.solver_info
    assert adopted["ignored"] == ["MaximumNumberOfSteps"]
    assert any("Ignored MaximumNumberOfSteps" in n for n in notes)


def test_a_solver_this_format_does_not_offer_is_kept_out():
    """OpenCOR's is never surfaced in CUFLynx, so a yaml naming it cannot select it."""
    notes, adopted = main._adopt_solver_settings({"solver": "CVODE_opencor"}, "u.yaml")
    assert engine.solver == "CVODE_myokit"
    assert adopted["solver"] == "CVODE_myokit"
    assert any("CVODE_opencor" in n and "kept CVODE_myokit" in n for n in notes)


def test_adopted_settings_persist_like_a_settings_change(monkeypatch):
    resets = []
    monkeypatch.setattr(engine, "reset", lambda: resets.append(1))
    main._adopt_solver_settings({"MaximumStep": 1e-4, "dt": 0.005}, "u.yaml")

    assert resets, "cached helpers key on solver, not solver_info: they must be dropped"
    assert json.loads(os.environ["CUFLYNX_SOLVER_INFO"])["MaximumStep"] == pytest.approx(1e-4)
    saved = settings_store.load()
    assert saved["solver_info"]["MaximumStep"] == pytest.approx(1e-4)
    assert saved["solver_info"]["dt"] == pytest.approx(0.005)


def test_settings_already_in_force_are_not_re_persisted(monkeypatch):
    resets = []
    monkeypatch.setattr(engine, "reset", lambda: resets.append(1))
    notes, adopted = main._adopt_solver_settings(
        {"solver": "CVODE_myokit", "MaximumStep": 0.001, "dt": 0.01}, "u.yaml")
    assert resets == []
    assert adopted["solver_info"] == {"MaximumStep": 0.001}


# ---------------------------------------------------------------------------
# The three ways in: archive, run directory, picker
# ---------------------------------------------------------------------------
def test_an_archives_user_inputs_are_adopted_and_reported(client):
    body = _upload(client, _archive(**{"lv_user_inputs.yaml": USER_INPUTS}))

    assert body["solver_settings"] == {
        "solver": "CVODE_myokit",
        "solver_info": {"MaximumStep": 0.0001},
        "dt": 0.005,
        "source": "lv_user_inputs.yaml",
        "ignored": ["MaximumNumberOfSteps"],
    }
    assert engine.dt == pytest.approx(0.005)
    assert engine.solver_info["MaximumStep"] == pytest.approx(1e-4)
    assert any("MaximumNumberOfSteps" in w for w in body["warnings"])
    # And the config the Settings dialog re-reads says the same.
    cfg = client.get("/api/config").json()
    assert cfg["solver_info"]["MaximumStep"] == pytest.approx(1e-4)
    assert cfg["solver_info"]["dt"] == pytest.approx(0.005)


def test_an_archive_without_one_changes_nothing(client):
    body = _upload(client, _archive())
    assert body["solver_settings"] is None
    assert engine.dt == 0.01 and engine.solver_info == {"MaximumStep": 0.001}


def test_a_broken_yaml_in_an_archive_is_a_warning(client):
    body = _upload(client, _archive(**{"user_inputs.yaml": "solver_info: [unclosed"}))
    assert body["model_id"] and body["solver_settings"] is None
    assert any("user_inputs.yaml" in w and "YAML" in w for w in body["warnings"])


def test_a_run_directorys_user_inputs_are_adopted(client, tmp_path):
    from test_load_outputs import write_study

    out = tmp_path / "out"
    out.mkdir()
    write_study(out)
    (out / "user_inputs_260824.yaml").write_text(USER_INPUTS)

    resp = client.post("/api/outputs/study", json={"dir": str(out)})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["solver_settings"]["solver_info"] == {"MaximumStep": 0.0001}
    assert body["solver_settings"]["source"] == "user_inputs_260824.yaml"
    assert engine.dt == pytest.approx(0.005)


def test_the_picker_route_adopts_and_returns_the_config(client):
    resp = client.post("/api/user_inputs/upload",
                       files={"file": ("my_user_inputs.yaml", USER_INPUTS, "application/x-yaml")})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["solver_settings"]["solver_info"] == {"MaximumStep": 0.0001}
    assert body["solver_settings"]["dt"] == pytest.approx(0.005)
    # The config payload itself, so the dialog re-renders from the response.
    assert body["solver"] == "CVODE_myokit"
    assert body["solver_info"]["MaximumStep"] == pytest.approx(1e-4)
    assert any("MaximumNumberOfSteps" in w for w in body["warnings"])


@pytest.mark.parametrize("blob", ["solver_info: [unclosed", "file_prefix: only_layout\n"])
def test_the_picker_route_refuses_a_file_with_no_solver_settings(client, blob):
    resp = client.post("/api/user_inputs/upload",
                       files={"file": ("u.yaml", blob, "application/x-yaml")})
    assert resp.status_code == 422
    assert engine.solver_info == {"MaximumStep": 0.001}


# ---------------------------------------------------------------------------
# Runs get the engine's dt unless they ask for one
# ---------------------------------------------------------------------------
def test_a_run_without_dt_gets_the_engines():
    engine.dt = 0.005
    assert main._with_engine_dt({"DEBUG": True}) == {"DEBUG": True, "dt": 0.005}
    assert main._with_engine_dt({"dt": None})["dt"] == 0.005


def test_an_explicit_dt_still_wins():
    engine.dt = 0.005
    assert main._with_engine_dt({"dt": 0.02})["dt"] == 0.02


@pytest.mark.parametrize("route", ["calibration", "sensitivity", "uq"])
def test_the_defaults_offer_the_engines_dt(client, route):
    engine.dt = 0.005
    assert client.get(f"/api/{route}/defaults").json()["dt"] == pytest.approx(0.005)


# Prints what reached the runner: solver_info and the dt it was handed.
RECORDING_RUNNER = """
import json, sys
from pathlib import Path
""" + WRITE_CA_RESULTS_SRC + """
cfg = json.loads(Path(sys.argv[1]).read_text())
print("RUN_CONFIG " + json.dumps({"dt": cfg["settings"].get("dt"),
                                  "solver_info": cfg["solver_info"]}), flush=True)
write_ca_results(cfg["output_dir"], [["a/x"]], [1.0], 0.1)
print("__CALIBRATION_DONE__", flush=True)
"""


def _recorded_run_config(client, model_id, settings, tmp_path):
    runner = tmp_path / "recording_runner.py"
    runner.write_text(RECORDING_RUNNER)
    calibration_mod.calibration.runner_path = str(runner)
    resp = client.post("/api/calibration/run", json={"model_id": model_id, "settings": settings})
    assert resp.status_code == 200, resp.text
    job_id, offset, lines = resp.json()["job_id"], 0, []
    deadline = time.time() + 15
    while time.time() < deadline:
        s = client.get(f"/api/calibration/{job_id}/status?offset={offset}").json()
        lines += s["lines"]
        offset = s["next_offset"]
        if s["state"] != "running":
            break
        time.sleep(0.05)
    line = next(ln for ln in lines if ln.startswith("RUN_CONFIG "))
    return json.loads(line[len("RUN_CONFIG "):])


def test_a_calibration_after_import_gets_the_adopted_settings(client, tmp_path):
    body = _upload(client, _archive(**{"lv_user_inputs.yaml": USER_INPUTS}))
    cfg = _recorded_run_config(
        client, body["model_id"], {"config_outputs_dir": str(tmp_path / "o1")}, tmp_path)
    assert cfg["dt"] == pytest.approx(0.005)
    assert cfg["solver_info"]["MaximumStep"] == pytest.approx(1e-4)
    assert "MaximumNumberOfSteps" not in cfg["solver_info"]


def test_a_calibration_asking_for_its_own_dt_keeps_it(client, tmp_path):
    body = _upload(client, _archive(**{"lv_user_inputs.yaml": USER_INPUTS}))
    cfg = _recorded_run_config(
        client, body["model_id"], {"dt": 0.02, "config_outputs_dir": str(tmp_path / "o2")},
        tmp_path)
    assert cfg["dt"] == pytest.approx(0.02)


# ---------------------------------------------------------------------------
# Integration: a real calibration runs with the adopted MaximumStep
# ---------------------------------------------------------------------------
@pytest.mark.integration
def test_a_real_calibration_runs_with_the_archives_maximum_step(
    client, tmp_path, requires_simulation
):
    body = _upload(client, _archive(**{"lv_user_inputs.yaml": USER_INPUTS.replace(
        "dt: 0.005", "dt: 0.01")}))
    settings = {
        "param_id_method": "genetic_algorithm",
        "num_calls_to_function": 30,
        "DEBUG": True,
        "config_outputs_dir": str(tmp_path / "out"),
    }
    resp = client.post("/api/calibration/run",
                       json={"model_id": body["model_id"], "settings": settings})
    assert resp.status_code == 200, resp.text
    job_id, offset, lines = resp.json()["job_id"], 0, []
    deadline = time.time() + 600
    while time.time() < deadline:
        s = client.get(f"/api/calibration/{job_id}/status?offset={offset}").json()
        lines += s["lines"]
        offset = s["next_offset"]
        if s["state"] != "running":
            break
        time.sleep(0.2)
    assert s["state"] == "done", "\n".join(lines)
    # The runner's own line, printed with the dict it hands CVS0DParamID.
    solver_line = next(ln for ln in lines if ln.startswith("Solver: "))
    assert '"MaximumStep": 0.0001' in solver_line, solver_line
    assert "dt = 0.01" in solver_line
    assert "MaximumNumberOfSteps" not in solver_line
