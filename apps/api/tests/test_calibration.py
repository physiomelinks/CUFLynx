"""Tests for the calibration job manager + endpoints.

Unit tests inject a fake runner script (no Myokit). The integration test runs a
short real genetic-algorithm calibration on the 3compartment model.
"""

import json
import math
import os
import sys
import time

import pytest

import calibration as calibration_mod
from conftest import (
    LV_MODEL_PATH,
    LV_OBS_DATA_PATH,
    LV_PARAMS_CSV_PATH,
    RESOURCES_DIR,
    upload_model,
)

C3_MODEL_PATH = RESOURCES_DIR / "3compartment_flat.cellml"
C3_OBS_DATA_PATH = RESOURCES_DIR / "3compartment_obs_data.json"
C3_PARAMS_CSV_PATH = RESOURCES_DIR / "3compartment_params_for_id.csv"


FAKE_RUNNER = """
import json, sys
from pathlib import Path
cfg = json.loads(Path(sys.argv[1]).read_text())
print("starting fake calibration", flush=True)
print("generation 0 cost: 1.0", flush=True)
Path(cfg["output_dir"], "results.json").write_text(
    json.dumps({"params": {"a/x": 1.5, "a/y": 2.0}, "cost": 0.25}))
print("best cost: 0.25", flush=True)
print("__CALIBRATION_DONE__", flush=True)
"""

SLOW_RUNNER = """
import sys, time
print("starting slow", flush=True)
time.sleep(30)
"""

# Writes history CSVs into a <case_type> subdir (like circulatory_autogen) before
# finishing, so the progress endpoint has something to parse.
HISTORY_RUNNER = """
import json, os, sys
from pathlib import Path
cfg = json.loads(Path(sys.argv[1]).read_text())
sub = Path(cfg["output_dir"], "genetic_algorithm_model_obs")
sub.mkdir(parents=True, exist_ok=True)
(sub / "best_cost_history.csv").write_text(
    "1.0, 2.0, 3.0\\n0.5, 0.7, 0.9\\n")
(sub / "best_param_vals_history.csv").write_text(
    "a x,a y\\n0.10, 0.20\\n0.30, 0.40\\n")
Path(cfg["output_dir"], "results.json").write_text(
    json.dumps({"params": {"a/x": 1.5}, "cost": 0.5}))
print("__CALIBRATION_DONE__", flush=True)
"""


def _install_runner(tmp_path, src) -> str:
    path = tmp_path / "fake_runner.py"
    path.write_text(src)
    calibration_mod.calibration.runner_path = str(path)
    return str(path)


def _setup_model_obs_params(client, model_path, obs_path, params_path) -> str:
    """Upload model + obs_data + params_for_id and return model_id."""
    model_id = upload_model(client, model_path)["model_id"]
    obs = json.loads(obs_path.read_text())
    r = client.post(
        "/api/obs_data/upload", json={"model_id": model_id, "obs_data": obs}
    )
    assert r.status_code == 200, r.text
    with open(params_path, "rb") as fh:
        r = client.post(
            f"/api/params_for_id/upload?model_id={model_id}",
            files={"file": (params_path.name, fh, "text/csv")},
        )
    assert r.status_code == 200, r.text
    return model_id


def _wait(client, job_id, timeout=15):
    offset = 0
    lines: list[str] = []
    deadline = time.time() + timeout
    while time.time() < deadline:
        s = client.get(f"/api/calibration/{job_id}/status?offset={offset}").json()
        lines += s["lines"]
        offset = s["next_offset"]
        if s["state"] != "running":
            return s, lines
        time.sleep(0.05)
    raise AssertionError(f"calibration did not finish; lines:\n" + "\n".join(lines))


# ---------------------------------------------------------------------------
# Unit tier (fake runner)
# ---------------------------------------------------------------------------
def test_calibration_defaults(client):
    body = client.get("/api/calibration/defaults").json()
    assert body["param_id_method"] == "genetic_algorithm"
    # methods are introspected from CA's PARAM_ID_METHODS schema (fallback list
    # when CA lacks it): a list of {value, label, gradient_based, ...}.
    methods = body["methods"]
    assert any(m["value"] == "CMA-ES" for m in methods)
    assert all({"value", "label", "gradient_based"} <= set(m) for m in methods)
    assert body["num_cores"] == 1
    # pre_time / sim_time come from obs_data protocol_info (#13)
    assert "pre_time" not in body and "sim_time" not in body


def _force_mpiexec(monkeypatch, present: bool):
    """Pin mpiexec discovery so build_command tests don't depend on the host
    actually having (or lacking) an MPI runtime."""
    path = "/usr/bin/mpiexec" if present else None
    monkeypatch.setattr(
        calibration_mod.shutil,
        "which",
        lambda name, *a, **k: path if name == "mpiexec" else None,
    )


# The fake environment has to look executable to shutil.which, and that is
# platform-specific. On Windows which() only ever considers candidates carrying a
# PATHEXT extension (it builds `[cmd + ext for ext in PATHEXT]`), so an
# extension-less "mpiexec" file is invisible there — and a real Windows venv puts
# its executables in Scripts/, not bin/. Building the fixture the POSIX way made
# this test fail on windows-latest for a reason that has nothing to do with the
# behaviour under test.
_EXE = ".exe" if sys.platform == "win32" else ""
_BINDIR = "Scripts" if sys.platform == "win32" else "bin"


def _fake_env(tmp_path, name, *, with_mpiexec, symlink_python=False):
    """A fake interpreter environment: <name>/<bindir>/python, optionally with its
    own mpiexec beside it (as `pip install mpi4py mpich` produces).

    ``symlink_python`` reproduces a real venv, where ``bin/python`` is a symlink
    to the interpreter the venv was created from rather than a regular file. That
    detail is load-bearing: resolving the symlink walks out of the environment.
    """
    bindir = tmp_path / name / _BINDIR
    bindir.mkdir(parents=True)
    python = bindir / f"python{_EXE}"
    if symlink_python:
        # the "base" interpreter the venv was created from, in a different dir
        base_bin = tmp_path / f"{name}_base" / _BINDIR
        base_bin.mkdir(parents=True)
        base = base_bin / f"python{_EXE}"
        base.write_text("#!/bin/sh\n")
        base.chmod(0o755)
        python.symlink_to(base)
    else:
        python.write_text("#!/bin/sh\n")
        python.chmod(0o755)
    if with_mpiexec:
        mpi = bindir / f"mpiexec{_EXE}"
        mpi.write_text("#!/bin/sh\n")
        mpi.chmod(0o755)
    return str(python)


def _own_mpiexec(tmp_path, name):
    """The launcher _fake_env put inside that environment."""
    return str(tmp_path / name / _BINDIR / f"mpiexec{_EXE}")


def test_resolve_mpiexec_prefers_the_interpreters_own_launcher(tmp_path):
    """The launcher must come from the selected interpreter's environment, not
    PATH. Mixing them is what aborts every rank with 'unsupported PMI version
    PMIx' (a system Open MPI mpiexec launching a venv's MPICH-linked mpi4py).

    No mocking: the venv's mpiexec is a real file, so this fails against the old
    `shutil.which("mpiexec")` behaviour, which would return the system one.
    """
    python = _fake_env(tmp_path, "venv", with_mpiexec=True)
    own = _own_mpiexec(tmp_path, "venv")
    found = calibration_mod.resolve_mpiexec(python)
    assert found is not None, "the interpreter's own launcher should have been found"
    # samefile, not string equality: resolve_mpiexec derives the directory via
    # Path.resolve(), which on Windows may normalise case or expand an 8.3 short
    # path, so the strings can differ while naming the same file.
    assert os.path.samefile(found, own)
    # And it is genuinely different from whatever PATH would have given us.
    on_path = calibration_mod.shutil.which("mpiexec")
    assert on_path is None or not os.path.samefile(found, on_path)


@pytest.mark.skipif(sys.platform == "win32", reason="venvs on Windows copy python.exe, not symlink it")
def test_resolve_mpiexec_finds_a_venvs_own_launcher_through_the_python_symlink(tmp_path):
    """Regression: a real venv's bin/python is a SYMLINK to the interpreter it was
    created from, so Path(exe).resolve() walks out of the venv:

        <venv>/bin/python -> <base>/bin/python

    Searching only the resolved directory found the *base* environment (in
    practice /usr/bin, i.e. the system Open MPI launcher) while the venv's own
    mpiexec sat unused beside the symlink -- the exact launcher/runtime mismatch
    this function exists to prevent. `pip install mpi4py mpich` into a venv is the
    documented fix path, so this made that path a no-op on Linux and macOS.

    The earlier tests missed it because the fixture wrote bin/python as a regular
    file, which makes .resolve() a no-op.
    """
    python = _fake_env(tmp_path, "venv", with_mpiexec=True, symlink_python=True)
    own = _own_mpiexec(tmp_path, "venv")
    found = calibration_mod.resolve_mpiexec(python)
    assert found is not None, "the venv's own launcher should have been found"
    assert os.path.samefile(found, own), (
        f"resolved to {found!r} instead of the venv's own {own!r} — "
        "the python symlink was followed out of the environment"
    )


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX symlink semantics")
def test_resolve_mpiexec_uses_the_symlink_target_dir_when_the_link_dir_has_none(tmp_path):
    """A symlink that lives outside the environment it points into (e.g.
    ~/bin/mypython -> <venv>/bin/python) must still find the target's launcher, so
    the resolved directory is searched second rather than dropped."""
    python = _fake_env(tmp_path, "venv2", with_mpiexec=True)
    link_dir = tmp_path / "elsewhere"
    link_dir.mkdir()
    link = link_dir / f"mypython{_EXE}"
    link.symlink_to(python)
    found = calibration_mod.resolve_mpiexec(str(link))
    assert found is not None
    assert os.path.samefile(found, _own_mpiexec(tmp_path, "venv2"))


def test_resolve_mpiexec_falls_back_to_path(tmp_path):
    """When the interpreter's env ships no launcher, fall back to PATH — this
    preserves the previous behaviour rather than regressing to no-MPI."""
    python = _fake_env(tmp_path, "bare", with_mpiexec=False)
    real = calibration_mod.shutil.which("mpiexec")
    assert calibration_mod.resolve_mpiexec(python) == real


def test_resolve_mpiexec_returns_none_when_nothing_found(tmp_path, monkeypatch):
    python = _fake_env(tmp_path, "none", with_mpiexec=False)
    monkeypatch.setattr(calibration_mod.shutil, "which", lambda name, *a, **k: None)
    assert calibration_mod.resolve_mpiexec(python) is None


def test_resolve_mpiexec_prefers_the_bundled_launcher_when_frozen(tmp_path, monkeypatch):
    """Packaged app, no external interpreter: prefer the MPICH Hydra launcher
    bundled beside the app over a PATH mpiexec, so the launcher matches the
    bundle's MPICH runtime (a PATH Open MPI launcher would abort every rank with
    'unsupported PMI version PMIx'). The bundled launcher lives at
    <_MEIPASS>/mpi/bin/mpiexec.hydra (see packaging/cuflynx.spec)."""
    meipass = tmp_path / "_MEIPASS"
    launcher = meipass / "mpi" / "bin" / f"mpiexec.hydra{_EXE}"
    launcher.parent.mkdir(parents=True)
    launcher.write_text("#!/bin/sh\n")
    launcher.chmod(0o755)
    monkeypatch.setattr(calibration_mod.shutil, "which", lambda name, *a, **k: "/usr/bin/mpiexec")

    import runtime_paths as rp
    monkeypatch.setattr(rp.sys, "frozen", True, raising=False)
    monkeypatch.setattr(rp.sys, "_MEIPASS", str(meipass), raising=False)

    # None => packaged default. Must pick the bundled launcher, not the PATH one.
    assert calibration_mod.resolve_mpiexec(None) == str(launcher)


def test_resolve_mpiexec_frozen_without_a_bundled_launcher_falls_back_to_path(tmp_path, monkeypatch):
    """A frozen platform build with no bundled launcher (macOS Intel / Windows have
    no MPICH wheel) must still fall back to PATH rather than break."""
    meipass = tmp_path / "_MEIPASS"
    meipass.mkdir()  # no mpi/bin/ inside
    monkeypatch.setattr(calibration_mod.shutil, "which", lambda name, *a, **k: "/usr/bin/mpiexec")
    import runtime_paths as rp
    monkeypatch.setattr(rp.sys, "frozen", True, raising=False)
    monkeypatch.setattr(rp.sys, "_MEIPASS", str(meipass), raising=False)
    assert calibration_mod.resolve_mpiexec(None) == "/usr/bin/mpiexec"


@pytest.mark.parametrize("python", [None, "", "   "])
def test_resolve_mpiexec_tolerates_a_missing_interpreter(python, monkeypatch):
    """Regression: default_python() returns None in the packaged app ("no external
    interpreter -- run in the bundle"), and that None flows straight into
    resolve_mpiexec. `os.sep in None` raised TypeError, so any num_cores>1
    sensitivity/calibration/UQ run failed with an HTTP 500 instead of falling back.

    With no interpreter there is no environment to resolve a launcher from, so the
    correct answer is PATH -- the pre-existing behaviour."""
    monkeypatch.setattr(
        calibration_mod.shutil, "which",
        lambda name, *a, **k: "/usr/bin/mpiexec" if name == "mpiexec" else None,
    )
    assert calibration_mod.resolve_mpiexec(python) == "/usr/bin/mpiexec"


@pytest.mark.parametrize(
    "module_name", ["calibration", "sensitivity", "uq"],
)
def test_build_command_with_no_interpreter_does_not_raise(module_name, monkeypatch):
    """All three managers share the launcher lookup, so all three regressed
    together. Drives build_command (not just the helper) with the packaged-app
    state -- no configured interpreter, num_cores>1 -- and asserts it returns a
    command rather than raising, which is what surfaced as the 500."""
    import importlib

    mod = importlib.import_module(module_name)
    monkeypatch.setattr(
        calibration_mod.shutil, "which",
        lambda name, *a, **k: "/usr/bin/mpiexec" if name == "mpiexec" else None,
    )
    manager = next(
        obj for obj in vars(mod).values()
        if hasattr(obj, "build_command") and hasattr(obj, "python")
    )
    monkeypatch.setattr(manager, "python", None)  # packaged app: no external default
    cmd = manager.build_command({"num_cores": 2}, "/tmp/cfg.json")
    assert isinstance(cmd, list) and cmd
    assert cmd[0] == "/usr/bin/mpiexec" and cmd[1:3] == ["-n", "2"]


def test_build_command_single_vs_mpiexec(monkeypatch):
    _force_mpiexec(monkeypatch, present=True)
    mgr = calibration_mod.CalibrationManager()
    single = mgr.build_command({"num_cores": 1}, "/tmp/c.json")
    assert "mpiexec" not in single[0]
    assert single[-2:] == [mgr.runner_path, "/tmp/c.json"]

    parallel = mgr.build_command({"num_cores": 4}, "/tmp/c.json")
    assert "mpiexec" in parallel[0]
    assert parallel[1] == "-n" and parallel[2] == "4"
    assert parallel[-2:] == [mgr.runner_path, "/tmp/c.json"]


def test_build_command_uses_selected_python(monkeypatch):
    _force_mpiexec(monkeypatch, present=True)
    mgr = calibration_mod.CalibrationManager()
    cmd = mgr.build_command({"num_cores": 1, "python": "/custom/py"}, "/tmp/c.json")
    assert cmd[0] == "/custom/py"
    mpi = mgr.build_command({"num_cores": 2, "python": "/custom/py"}, "/tmp/c.json")
    assert "mpiexec" in mpi[0]
    assert "/custom/py" in mpi


def test_build_command_falls_back_to_single_core_without_mpiexec(monkeypatch):
    """num_cores>1 with no mpiexec must yield a single-core command (no mpiexec),
    not a command that launches a non-existent 'mpiexec' (-> 500)."""
    _force_mpiexec(monkeypatch, present=False)
    mgr = calibration_mod.CalibrationManager()
    cmd = mgr.build_command({"num_cores": 4}, "/tmp/c.json")
    assert "mpiexec" not in " ".join(cmd)
    assert "-n" not in cmd
    assert cmd[-2:] == [mgr.runner_path, "/tmp/c.json"]


def test_calibration_pythons_lists_interpreters(client):
    body = client.get("/api/calibration/pythons").json()
    assert "default" in body
    assert isinstance(body["pythons"], list)
    for p in body["pythons"]:
        assert {"path", "version", "ready", "missing"} <= set(p)


def test_calibration_invalid_python_returns_422(client, tmp_path):
    _install_runner(tmp_path, FAKE_RUNNER)
    model_id = _setup_model_obs_params(
        client, LV_MODEL_PATH, LV_OBS_DATA_PATH, LV_PARAMS_CSV_PATH
    )
    resp = client.post(
        "/api/calibration/run",
        json={"model_id": model_id, "settings": {"python_path": "/no/such/python"}},
    )
    assert resp.status_code == 422


def test_calibration_streams_and_completes(client, tmp_path):
    _install_runner(tmp_path, FAKE_RUNNER)
    model_id = _setup_model_obs_params(
        client, LV_MODEL_PATH, LV_OBS_DATA_PATH, LV_PARAMS_CSV_PATH
    )
    resp = client.post("/api/calibration/run", json={"model_id": model_id, "settings": {}})
    assert resp.status_code == 200, resp.text
    job_id = resp.json()["job_id"]

    status, lines = _wait(client, job_id)
    assert status["state"] == "done", lines
    assert status["best_params"] == {"a/x": 1.5, "a/y": 2.0}
    assert status["cost"] == 0.25
    assert any("generation 0 cost" in ln for ln in lines)


def test_calibration_requires_obs_and_params_422(client, tmp_path):
    _install_runner(tmp_path, FAKE_RUNNER)
    model_id = upload_model(client, LV_MODEL_PATH)["model_id"]  # no obs/params
    resp = client.post("/api/calibration/run", json={"model_id": model_id, "settings": {}})
    assert resp.status_code == 422


def test_calibration_unknown_model_404(client):
    resp = client.post("/api/calibration/run", json={"model_id": "nope", "settings": {}})
    assert resp.status_code == 404


def test_calibration_busy_returns_409(client, tmp_path):
    _install_runner(tmp_path, SLOW_RUNNER)
    model_id = _setup_model_obs_params(
        client, LV_MODEL_PATH, LV_OBS_DATA_PATH, LV_PARAMS_CSV_PATH
    )
    r1 = client.post("/api/calibration/run", json={"model_id": model_id, "settings": {}})
    assert r1.status_code == 200
    r2 = client.post("/api/calibration/run", json={"model_id": model_id, "settings": {}})
    assert r2.status_code == 409
    # cancel the running job
    job_id = r1.json()["job_id"]
    assert client.post(f"/api/calibration/{job_id}/cancel").json()["cancelled"] is True


def test_calibration_status_unknown_job_404(client):
    assert client.get("/api/calibration/nope/status").status_code == 404


def test_read_history_parses_subdir_and_tolerates_partial(tmp_path):
    sub = tmp_path / "genetic_algorithm_model_obs"
    sub.mkdir()
    # Two full generations plus a partially-flushed final row.
    (sub / "best_cost_history.csv").write_text(
        "0.9, 1.0, 1.1\n0.4, 0.6, 0.8\n0.3, 0.3"  # last line shorter, still parses
    )
    (sub / "best_param_vals_history.csv").write_text(
        "global q_lv_init,aortic_root C\n0.75, 0.30\n1.00, 0.29\n1.00,"  # trailing
    )
    hist = calibration_mod._read_history(str(tmp_path))
    assert hist["param_names"] == ["global q_lv_init", "aortic_root C"]
    # cost: all rows parse (variable width tolerated); best is column 0.
    assert [row[0] for row in hist["cost_history"]] == [0.9, 0.4, 0.3]
    # params: only full-width rows kept (trailing "1.00," has wrong width).
    assert hist["param_history"] == [[0.75, 0.30], [1.00, 0.29]]


def test_read_history_missing_files_returns_empty(tmp_path):
    hist = calibration_mod._read_history(str(tmp_path))
    assert hist == {
        "param_names": [],
        "cost_history": [],
        "param_history": [],
        "start_costs": [],
        "start_params": {"param_names": [], "starts": []},
    }


def test_read_multistart_costs_demuxes_interleaved_rows(tmp_path):
    """CA appends `start_idx, iteration, cost` rows interleaved across MPI ranks;
    _read_multistart_costs must group by start and order by iteration so each
    start becomes one cost-vs-iteration curve for the Progress plot."""
    sub = tmp_path / "sp_minimize_model_obs"
    sub.mkdir()
    (sub / "multi_start_cost_history.csv").write_text(
        "0,0,1.5\n1,0,2.0\n2,0,3.0\n0,1,1.2\n1,1,1.1\n0,2,1.0\n"
    )
    assert calibration_mod._read_multistart_costs(str(tmp_path)) == [
        [1.5, 1.2, 1.0],
        [2.0, 1.1],
        [3.0],
    ]
    # And it's surfaced by _read_history for the progress endpoint.
    assert calibration_mod._read_history(str(tmp_path))["start_costs"] == [
        [1.5, 1.2, 1.0],
        [2.0, 1.1],
        [3.0],
    ]


def test_read_multistart_costs_absent_returns_empty(tmp_path):
    """GA / single-start runs write no multi_start_cost_history.csv -> []."""
    sub = tmp_path / "genetic_algorithm_model_obs"
    sub.mkdir()
    (sub / "best_cost_history.csv").write_text("0.9\n0.4\n")
    assert calibration_mod._read_multistart_costs(str(tmp_path)) == []


def test_read_multistart_params_demuxes_interleaved_rows(tmp_path):
    """CA writes a header naming the params then `start_idx, iteration, <vals>`
    rows interleaved across MPI ranks; _read_multistart_params must name the
    params and group each start's rows into one [iteration][param] matrix."""
    sub = tmp_path / "sp_minimize_model_obs"
    sub.mkdir()
    (sub / "multi_start_param_vals_history.csv").write_text(
        "start_idx, iteration, well x, well y\n"
        "0, 0, 1.2, 3.4\n"
        "1, 0, 2.2, 4.4\n"
        "0, 1, 1.0, 3.0\n"
        "1, 1, 1.9, 4.0\n"
    )
    res = calibration_mod._read_multistart_params(str(tmp_path))
    assert res == {
        "param_names": ["well x", "well y"],
        "starts": [
            [[1.2, 3.4], [1.0, 3.0]],
            [[2.2, 4.4], [1.9, 4.0]],
        ],
    }
    # Surfaced by _read_history for the progress endpoint.
    assert calibration_mod._read_history(str(tmp_path))["start_params"] == res


def test_read_multistart_params_tolerates_partial_and_absent(tmp_path):
    """Partial mid-write rows (wrong width) are skipped; an absent file yields
    empty names/starts (GA / single-start runs)."""
    assert calibration_mod._read_multistart_params(str(tmp_path)) == {
        "param_names": [],
        "starts": [],
    }
    sub = tmp_path / "sp_minimize_model_obs"
    sub.mkdir()
    (sub / "multi_start_param_vals_history.csv").write_text(
        "start_idx, iteration, well x, well y\n"
        "0, 0, 1.2, 3.4\n"
        "0, 1, 1.0"  # partially-flushed final row: wrong width, skipped
    )
    assert calibration_mod._read_multistart_params(str(tmp_path)) == {
        "param_names": ["well x", "well y"],
        "starts": [[[1.2, 3.4]]],
    }


def test_find_history_prefers_the_most_recent_match(tmp_path):
    """A reused output_dir can hold a previous run's history in another method
    subdir; _read_history must follow the freshest one, not an arbitrary match,
    or a second run's live plot shows stale data that never changes."""
    old = tmp_path / "genetic_algorithm_model_run1"
    old.mkdir()
    (old / "best_cost_history.csv").write_text("9.9\n8.8\n")
    time.sleep(0.02)
    new = tmp_path / "sp_minimize_model_run2"
    new.mkdir()
    (new / "best_cost_history.csv").write_text("5.0\n4.0\n3.0\n")

    assert calibration_mod._find_history_file(str(tmp_path), "best_cost_history.csv") == str(
        new / "best_cost_history.csv"
    )
    assert [r[0] for r in calibration_mod._read_history(str(tmp_path))["cost_history"]] == [5.0, 4.0, 3.0]


def test_clear_progress_history_removes_stale_history_but_keeps_results(tmp_path):
    """Regression: a calibration run reusing an output_dir must clear the previous
    run's progress-history CSVs so its live plots start fresh (CA appends to them
    and never truncates). Final results must be left intact."""
    sub = tmp_path / "genetic_algorithm_model_run1"
    sub.mkdir()
    (sub / "best_cost_history.csv").write_text("9.9\n8.8\n7.7\n")
    (sub / "best_param_vals_history.csv").write_text("a,b\n0.1,0.2\n")
    (sub / "results.json").write_text('{"cost": 0.1}')  # a real result -> keep
    (tmp_path / "best_cost_history.csv").write_text("1.0\n")  # also the direct copy

    # Before: stale history is read.
    assert calibration_mod._read_history(str(tmp_path))["cost_history"]

    calibration_mod._clear_progress_history(str(tmp_path))

    # History cleared everywhere; results preserved.
    assert calibration_mod._read_history(str(tmp_path))["cost_history"] == []
    assert not (sub / "best_cost_history.csv").exists()
    assert not (sub / "best_param_vals_history.csv").exists()
    assert not (tmp_path / "best_cost_history.csv").exists()
    assert (sub / "results.json").exists()  # final result untouched


def test_calibration_progress_endpoint(client, tmp_path):
    _install_runner(tmp_path, HISTORY_RUNNER)
    model_id = _setup_model_obs_params(
        client, LV_MODEL_PATH, LV_OBS_DATA_PATH, LV_PARAMS_CSV_PATH
    )
    resp = client.post("/api/calibration/run", json={"model_id": model_id, "settings": {}})
    job_id = resp.json()["job_id"]
    _wait(client, job_id)

    prog = client.get(f"/api/calibration/{job_id}/progress").json()
    assert prog["param_names"] == ["a x", "a y"]
    assert [row[0] for row in prog["cost_history"]] == [1.0, 0.5]
    assert prog["param_history"] == [[0.10, 0.20], [0.30, 0.40]]


def test_run_clears_stale_progress_history_from_a_reused_outputs_dir(client, tmp_path):
    """End-to-end regression for the reported bug: a second calibration into a
    reused (user-configured) outputs dir must not show the first run's stale
    plots. start() clears the history before launching, so a poll right after
    the run starts reads no leftover data."""
    outdir = tmp_path / "outputs"
    stale = outdir / "genetic_algorithm_model_run1"
    stale.mkdir(parents=True)
    (stale / "best_cost_history.csv").write_text("9.9\n8.8\n7.7\n")

    _install_runner(tmp_path, SLOW_RUNNER)  # launches but writes no history
    model_id = _setup_model_obs_params(
        client, LV_MODEL_PATH, LV_OBS_DATA_PATH, LV_PARAMS_CSV_PATH
    )
    resp = client.post(
        "/api/calibration/run",
        json={"model_id": model_id, "settings": {"config_outputs_dir": str(outdir)}},
    )
    assert resp.status_code == 200
    try:
        # The stale history is gone the moment the run starts.
        assert calibration_mod._read_history(str(outdir))["cost_history"] == []
        assert client.get(f"/api/calibration/{resp.json()['job_id']}/progress").json()[
            "cost_history"
        ] == []
    finally:
        client.post(f"/api/calibration/{resp.json()['job_id']}/cancel")


def test_calibration_progress_unknown_job_404(client):
    assert client.get("/api/calibration/nope/progress").status_code == 404


def test_calibration_honors_config_outputs_dir(client, tmp_path):
    _install_runner(tmp_path, FAKE_RUNNER)
    out = tmp_path / "my_outputs"
    model_id = _setup_model_obs_params(
        client, LV_MODEL_PATH, LV_OBS_DATA_PATH, LV_PARAMS_CSV_PATH
    )
    resp = client.post(
        "/api/calibration/run",
        json={"model_id": model_id, "settings": {"config_outputs_dir": str(out)}},
    )
    assert resp.status_code == 200, resp.text
    status, _ = _wait(client, resp.json()["job_id"])
    assert status["state"] == "done"
    # Runner wrote results.json into the configured dir (proves it was used).
    assert (out / "results.json").exists()


def test_calibration_config_outputs_dir_must_be_absolute(client, tmp_path):
    _install_runner(tmp_path, FAKE_RUNNER)
    model_id = _setup_model_obs_params(
        client, LV_MODEL_PATH, LV_OBS_DATA_PATH, LV_PARAMS_CSV_PATH
    )
    resp = client.post(
        "/api/calibration/run",
        json={"model_id": model_id, "settings": {"config_outputs_dir": "relative/dir"}},
    )
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Integration tier (real Myokit — short GA on 3compartment)
# ---------------------------------------------------------------------------
@pytest.mark.integration
def test_calibration_3compartment_genetic_algorithm(client, requires_simulation):
    model_id = _setup_model_obs_params(
        client, C3_MODEL_PATH, C3_OBS_DATA_PATH, C3_PARAMS_CSV_PATH
    )
    # No pre_time / sim_time: timing comes from the obs_data protocol_info (#13).
    settings = {
        "param_id_method": "genetic_algorithm",
        "num_calls_to_function": 30,
        "DEBUG": True,  # small population for a fast interactive-scale run
        "dt": 0.01,
    }
    resp = client.post(
        "/api/calibration/run", json={"model_id": model_id, "settings": settings}
    )
    assert resp.status_code == 200, resp.text
    job_id = resp.json()["job_id"]

    status, lines = _wait(client, job_id, timeout=600)
    assert status["state"] == "done", "\n".join(lines)

    best = status["best_params"]
    assert set(best) == {
        "global/q_lv_init",
        "aortic_root/C",
        "global/E_lv_A",
        "global/E_lv_B",
    }
    assert all(math.isfinite(v) for v in best.values())
    assert status["cost"] is not None and math.isfinite(status["cost"])


# --- issue #65: gradient descent can start from the current slider values ---
def test_apply_current_param_start_overrides_param_init():
    """_apply_current_param_start replaces CA's param_init (the sp_minimize x0,
    seeded from the model defaults) with the UI's current slider values, in
    param_id_info order, in the [value] shape CA reads x0 from. Params absent from
    the slider map keep their model-default init."""
    import calibration_runner

    class _Pid:
        param_id_info = {"param_names": [["a/x"], ["b/y"], ["c/z"]]}
        param_init = [[1.0], [2.0], [3.0]]  # model defaults

    class _CVS:
        param_id = _Pid()

    cvs = _CVS()
    calibration_runner._apply_current_param_start(cvs, {"a/x": 5.0, "c/z": 9.0})
    # a/x and c/z start from the sliders; b/y (not supplied) keeps its default.
    assert cvs.param_id.param_init == [[5.0], [2.0], [9.0]]


def test_apply_current_param_start_is_best_effort():
    """A malformed param_id must not raise (a start-point tweak can't abort a run)."""
    import calibration_runner

    class _Broken:
        param_id = object()  # no param_id_info -> AttributeError inside

    # Should swallow the error, not propagate.
    calibration_runner._apply_current_param_start(_Broken(), {"a/x": 1.0})


@pytest.mark.integration
def test_sp_minimize_starts_from_current_param_values(tmp_path, requires_simulation, capsys):
    """End-to-end (#65): with start_from_current, an sp_minimize run overrides the
    x0 (param_init) with the supplied current slider values before descending — the
    runner logs that it started from the current values for all params."""
    import calibration_runner
    import model_codegen

    # In-bounds values distinct from the model defaults (see the params_for_id CSV).
    current = {
        "global/q_lv_init": 800e-6,
        "aortic_root/C": 2e-8,
        "global/E_lv_A": 3e8,
        "global/E_lv_B": 2e7,
    }
    config = {
        "model_path": model_codegen.resolve_model_path(str(C3_MODEL_PATH), "cellml_only"),
        "model_type": "cellml_only",
        "solver": "CVODE_myokit",
        "solver_info": {"solver": "CVODE_myokit", "method": "CVODE"},
        "obs_path": str(C3_OBS_DATA_PATH),
        "params_path": str(C3_PARAMS_CSV_PATH),
        "output_dir": str(tmp_path / "out"),
        "file_prefix": "3compartment",
        "current_params": current,
        "settings": {
            "param_id_method": "sp_minimize",
            # cellml_only gradient descent needs an analytic gradient (Myokit FSA);
            # the start-point override itself is independent of the gradient source.
            "gradient_method": "FSA",
            "start_from_current": True,
            # Stop almost immediately so the test stays fast; the override happens
            # before the first evaluation regardless of the budget.
            "cost_convergence": 1e12,
            "num_calls_to_function": 5,
            "dt": 0.01,
        },
    }
    result = calibration_runner.run(config)
    out = capsys.readouterr().out
    assert "Starting gradient descent from current parameter values" in out
    assert "4/4 params overridden" in out
    assert isinstance(result.get("params"), dict)
