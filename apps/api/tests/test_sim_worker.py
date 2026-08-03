"""Live simulation runs in the interpreter you picked (issue #167).

It used to run in-process, in whatever interpreter started the app, while
calibration/sensitivity/UQ ran in the one chosen in Settings -- so "switch
Python" was true of half the app. These tests are mostly about the seams that
made that hard to see: which interpreter is actually used, when the worker is
replaced, and whether a dead worker explains itself.

The worker is exercised with a stub script wherever the point is the plumbing,
so the fast tier can cover it without Myokit or circulatory_autogen.
"""

from __future__ import annotations

import json
import subprocess
import sys
import textwrap
from pathlib import Path

import engine as engine_mod
import pytest
import sim_worker
from sim_worker import SimWorker, WorkerError, worker_settings

SETTINGS = worker_settings(
    ca_src="", dt=0.01, model_type="cellml_only", solver="CVODE_myokit", solver_info={}
)


@pytest.fixture
def stub_worker(tmp_path, monkeypatch):
    """A worker script that speaks the protocol without needing a solver.

    Echoes back the request so a test can assert what crossed the pipe, which is
    the only way to prove the request was built from the right settings.
    """
    script = tmp_path / "sim_worker_runner.py"
    script.write_text(
        textwrap.dedent(
            '''
            import json, os, sys
            wire = os.fdopen(os.dup(sys.stdout.fileno()), "w")
            sys.stdout = sys.stderr
            for line in sys.stdin:
                if not line.strip():
                    continue
                msg = json.loads(line)
                if msg.get("op") == "shutdown":
                    break
                wire.write(json.dumps({
                    "id": msg.get("id"), "ok": True,
                    "result": {"echo": msg, "python": sys.executable},
                    "captured": "",
                }) + "\\n")
                wire.flush()
            '''
        )
    )
    monkeypatch.setattr(sim_worker, "runner_path", lambda name: script)
    return script


# ---------------------------------------------------------------------------
# The wire
# ---------------------------------------------------------------------------
def test_it_starts_and_answers(stub_worker):
    worker = SimWorker(sys.executable, SETTINGS)
    worker.start()
    try:
        assert worker.alive
        reply = worker.call("simulate", model_id="m")
        assert reply["ok"] is True
        assert reply["result"]["echo"]["op"] == "simulate"
    finally:
        worker.stop()


def test_it_runs_in_the_interpreter_it_was_given(stub_worker):
    """The whole point. The worker reports its own sys.executable, and it has to
    be the one asked for rather than the one serving the API."""
    worker = SimWorker(sys.executable, SETTINGS)
    worker.start()
    try:
        reply = worker.call("ping")
        assert reply["result"]["python"] == sys.executable
    finally:
        worker.stop()


def test_the_settings_reach_the_worker(stub_worker):
    settings = worker_settings(
        ca_src="/somewhere/src", dt=0.5, model_type="python", solver="solve_ivp",
        solver_info={"MaximumStep": 0.1},
    )
    worker = SimWorker(sys.executable, settings)
    worker.start()
    try:
        # configure is sent at start-up, so the echo of the next call is not it;
        # ask again to see what the worker was told.
        reply = worker.call("configure", **settings)
        assert reply["result"]["echo"]["ca_src"] == "/somewhere/src"
        assert reply["result"]["echo"]["solver"] == "solve_ivp"
    finally:
        worker.stop()


def test_requests_are_numbered(stub_worker):
    worker = SimWorker(sys.executable, SETTINGS)
    worker.start()
    try:
        first = worker.call("ping")["result"]["echo"]["id"]
        second = worker.call("ping")["result"]["echo"]["id"]
        assert second > first
    finally:
        worker.stop()


def test_stop_is_safe_to_call_twice(stub_worker):
    worker = SimWorker(sys.executable, SETTINGS)
    worker.start()
    worker.stop()
    worker.stop()
    assert not worker.alive


# ---------------------------------------------------------------------------
# Failure: a worker that dies must say why
# ---------------------------------------------------------------------------
def test_a_worker_that_cannot_start_names_the_interpreter(monkeypatch, stub_worker):
    worker = SimWorker("/no/such/python", SETTINGS)
    with pytest.raises(WorkerError, match="/no/such/python"):
        worker.start()


def test_a_worker_that_dies_on_import_reports_what_it_printed(tmp_path, monkeypatch):
    """The failure this most needs to explain: an interpreter without myokit.
    Presented as an empty pipe it is undiagnosable; the stderr tail is the fix."""
    script = tmp_path / "sim_worker_runner.py"
    script.write_text('import sys\nsys.stderr.write("ModuleNotFoundError: no myokit\\n")\n')
    monkeypatch.setattr(sim_worker, "runner_path", lambda name: script)

    worker = SimWorker(sys.executable, SETTINGS)
    with pytest.raises(WorkerError, match="no myokit"):
        worker.start()


def test_a_worker_that_dies_mid_request_is_reported_not_hung(tmp_path, monkeypatch):
    script = tmp_path / "sim_worker_runner.py"
    script.write_text(
        textwrap.dedent(
            '''
            import json, os, sys
            wire = os.fdopen(os.dup(sys.stdout.fileno()), "w")
            sys.stdout = sys.stderr
            line = sys.stdin.readline()          # the configure at start-up
            msg = json.loads(line)
            wire.write(json.dumps({"id": msg.get("id"), "ok": True, "result": {}}) + "\\n")
            wire.flush()
            sys.stderr.write("worker fell over\\n")
            raise SystemExit(3)
            '''
        )
    )
    monkeypatch.setattr(sim_worker, "runner_path", lambda name: script)

    worker = SimWorker(sys.executable, SETTINGS)
    worker.start()
    with pytest.raises(WorkerError, match="fell over"):
        worker.call("simulate", model_id="m")


# ---------------------------------------------------------------------------
# When a worker is reused, and when it is replaced
# ---------------------------------------------------------------------------
def test_the_same_settings_reuse_the_worker():
    worker = SimWorker("/usr/bin/python3", SETTINGS)
    assert worker.matches("/usr/bin/python3", dict(SETTINGS))


@pytest.mark.parametrize(
    "change",
    [
        {"ca_src": "/elsewhere"},
        {"dt": 0.5},
        {"model_type": "python"},
        {"solver": "solve_ivp"},
        {"solver_info": {"MaximumStep": 0.2}},
    ],
    ids=["ca_dir", "dt", "model_type", "solver", "solver_info"],
)
def test_any_setting_change_means_a_new_worker(change):
    """Restarted rather than reconfigured: CA caches its modules on first import,
    so a mid-life change could not fully take effect -- which is the bug this
    design exists to remove."""
    worker = SimWorker("/usr/bin/python3", SETTINGS)
    assert not worker.matches("/usr/bin/python3", {**SETTINGS, **change})


def test_a_different_interpreter_means_a_new_worker():
    worker = SimWorker("/usr/bin/python3", SETTINGS)
    assert not worker.matches("/other/python3", dict(SETTINGS))


# ---------------------------------------------------------------------------
# The engine's side: when a worker is used at all
# ---------------------------------------------------------------------------
def test_no_interpreter_means_no_worker():
    """The frozen app's default, and what a user who never opens Settings gets."""
    engine_mod.engine.worker_python = None
    assert engine_mod.engine._acquire_worker() is None


def test_this_interpreter_means_no_worker():
    """A worker to reach the interpreter already running would cost a process and
    a model compile and change nothing about what is importable."""
    engine_mod.engine.worker_python = sys.executable
    try:
        assert engine_mod.engine._acquire_worker() is None
    finally:
        engine_mod.engine.worker_python = None


def test_a_venv_is_not_this_interpreter_even_when_the_binary_is_shared(tmp_path, monkeypatch):
    """A venv's bin/python is usually a symlink to the base interpreter, so
    comparing resolved paths makes a venv look like the interpreter it was made
    from -- and the venv is the whole point, since that is where the user put the
    packages they picked it for. The same mistake once hid venvs from the picker.
    """
    monkeypatch.setattr(engine_mod, "_PREFIX_CACHE", {})
    monkeypatch.setattr(engine_mod, "_interpreter_prefix", lambda p: "/some/venv")
    assert engine_mod._is_this_interpreter("/some/venv/bin/python") is False


def test_the_running_environment_is_recognised(monkeypatch):
    monkeypatch.setattr(engine_mod, "_PREFIX_CACHE", {})
    monkeypatch.setattr(engine_mod, "_interpreter_prefix", lambda p: sys.prefix)
    assert engine_mod._is_this_interpreter("/anything") is True


def test_an_unaskable_interpreter_is_not_assumed_to_be_ours(monkeypatch):
    """If the probe fails we cannot know, and guessing "same" would silently keep
    live simulation where it was -- the failure mode being fixed here."""
    monkeypatch.setattr(engine_mod, "_PREFIX_CACHE", {})
    monkeypatch.setattr(engine_mod, "_interpreter_prefix", lambda p: "")
    assert engine_mod._is_this_interpreter("/broken/python") is False


def test_a_worker_that_will_not_start_fails_loudly_rather_than_falling_back(monkeypatch):
    """Silently running somewhere other than where the user asked is exactly the
    confusion this change exists to end, so a broken choice is an error."""
    engine_mod.engine.worker_python = "/no/such/python"
    monkeypatch.setattr(engine_mod, "_interpreter_prefix", lambda p: "/no/such")
    try:
        with pytest.raises(engine_mod.SimulationError, match="/no/such/python"):
            engine_mod.engine._acquire_worker()
    finally:
        engine_mod.engine.worker_python = None


def test_reset_stops_the_worker(stub_worker, monkeypatch):
    monkeypatch.setattr(engine_mod, "_interpreter_prefix", lambda p: "/pretend/venv")
    engine_mod.engine.worker_python = sys.executable
    try:
        worker = engine_mod.engine._acquire_worker()
        assert worker is not None and worker.alive
        engine_mod.engine.reset()
        assert not worker.alive
    finally:
        engine_mod.engine.worker_python = None
        engine_mod.engine.reset()


def test_changing_the_solver_replaces_the_worker(stub_worker, monkeypatch):
    monkeypatch.setattr(engine_mod, "_interpreter_prefix", lambda p: "/pretend/venv")
    engine_mod.engine.worker_python = sys.executable
    try:
        first = engine_mod.engine._acquire_worker()
        engine_mod.engine.solver = "solve_ivp"
        second = engine_mod.engine._acquire_worker()
        assert second is not first
        assert not first.alive
    finally:
        engine_mod.engine.worker_python = None
        engine_mod.engine.reset()


# ---------------------------------------------------------------------------
# The real worker, against a real model
# ---------------------------------------------------------------------------
@pytest.mark.integration
def test_a_real_worker_simulates_the_same_as_in_process(client, requires_simulation, tmp_path):
    """Moving the work into another process must not move the answer."""
    from conftest import LV_MODEL_PATH, upload_model

    body = upload_model(client, LV_MODEL_PATH)
    request = {
        "model_id": body["model_id"],
        "params": {},
        "sim_time": 2.0,
        "pre_time": 0.0,
        "outputs": ["Lotka_Volterra_module/x"],
    }

    engine_mod.engine.worker_python = None
    in_process = client.post("/api/simulate", json=request)
    assert in_process.status_code == 200, in_process.text

    # sys.executable is this environment, so force the worker path explicitly
    # rather than relying on a second interpreter existing on the machine.
    engine_mod.engine.reset()
    engine_mod.engine.worker_python = sys.executable
    try:
        import engine as e

        original = e._is_this_interpreter
        e._is_this_interpreter = lambda _p: False
        try:
            via_worker = client.post("/api/simulate", json=request)
        finally:
            e._is_this_interpreter = original
    finally:
        engine_mod.engine.worker_python = None

    assert via_worker.status_code == 200, via_worker.text
    a = in_process.json()["outputs"]["Lotka_Volterra_module/x"]
    b = via_worker.json()["outputs"]["Lotka_Volterra_module/x"]
    assert len(a) == len(b)
    assert max(abs(x - y) for x, y in zip(a, b)) < 1e-9


@pytest.mark.integration
def test_the_worker_script_is_shipped_for_an_external_interpreter():
    """It is executed as a file by a Python that is not this one, so it must be
    on disk beside the other runners -- not importable only as a frozen module."""
    from runtime_paths import runner_path

    assert Path(runner_path("sim_worker_runner.py")).is_file()


@pytest.mark.integration
def test_the_worker_script_runs_standalone():
    """No imports from the app: in the packaged build the app's modules are
    frozen into the bundle and an external interpreter cannot reach them."""
    from runtime_paths import runner_path

    proc = subprocess.run(
        [sys.executable, str(runner_path("sim_worker_runner.py"))],
        input=json.dumps({"id": 1, "op": "ping"}) + "\n",
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    reply = json.loads(proc.stdout.strip().splitlines()[0])
    assert reply["ok"] is True and reply["id"] == 1


# ---------------------------------------------------------------------------
# Hung, as opposed to dead
# ---------------------------------------------------------------------------
def test_a_worker_that_stops_responding_is_killed_rather_than_waited_on(tmp_path, monkeypatch):
    """A worker can hang without dying -- an AADC licence check that cannot reach
    the network sits at 0% CPU indefinitely, which is how this was found. A
    blocking read would hold the engine lock with it and take every later
    simulation down too, so the read has a deadline and the worker is stopped.
    """
    script = tmp_path / "sim_worker_runner.py"
    script.write_text(
        textwrap.dedent(
            '''
            import json, os, sys, time
            wire = os.fdopen(os.dup(sys.stdout.fileno()), "w")
            sys.stdout = sys.stderr
            line = sys.stdin.readline()          # configure
            msg = json.loads(line)
            wire.write(json.dumps({"id": msg.get("id"), "ok": True, "result": {}}) + "\\n")
            wire.flush()
            sys.stderr.write("about to hang\\n")
            sys.stderr.flush()
            time.sleep(300)                      # never replies
            '''
        )
    )
    monkeypatch.setattr(sim_worker, "runner_path", lambda name: script)

    worker = SimWorker(sys.executable, SETTINGS, timeout=2.0)
    worker.start()
    try:
        with pytest.raises(WorkerError, match="stopped responding"):
            worker.call("simulate", model_id="m")
        assert not worker.alive, "a hung worker must be stopped, not left running"
    finally:
        worker.stop()


def test_the_hang_message_carries_what_the_worker_last_said(tmp_path, monkeypatch):
    script = tmp_path / "sim_worker_runner.py"
    script.write_text(
        textwrap.dedent(
            '''
            import json, os, sys, time
            wire = os.fdopen(os.dup(sys.stdout.fileno()), "w")
            sys.stdout = sys.stderr
            msg = json.loads(sys.stdin.readline())
            wire.write(json.dumps({"id": msg.get("id"), "ok": True, "result": {}}) + "\\n")
            wire.flush()
            sys.stderr.write("AADC License check: contacting server\\n")
            sys.stderr.flush()
            time.sleep(300)
            '''
        )
    )
    monkeypatch.setattr(sim_worker, "runner_path", lambda name: script)

    worker = SimWorker(sys.executable, SETTINGS, timeout=2.0)
    worker.start()
    try:
        with pytest.raises(WorkerError, match="License check"):
            worker.call("simulate", model_id="m")
    finally:
        worker.stop()


def test_a_slow_but_answering_worker_is_not_killed(tmp_path, monkeypatch):
    """The timeout is a "something is stuck" bound, not a performance one: a
    first compile is legitimately slow and must not be cut off."""
    script = tmp_path / "sim_worker_runner.py"
    script.write_text(
        textwrap.dedent(
            '''
            import json, os, sys, time
            wire = os.fdopen(os.dup(sys.stdout.fileno()), "w")
            sys.stdout = sys.stderr
            for line in sys.stdin:
                if not line.strip():
                    continue
                msg = json.loads(line)
                if msg.get("op") == "shutdown":
                    break
                time.sleep(0.4)
                wire.write(json.dumps({"id": msg.get("id"), "ok": True, "result": {}}) + "\\n")
                wire.flush()
            '''
        )
    )
    monkeypatch.setattr(sim_worker, "runner_path", lambda name: script)

    worker = SimWorker(sys.executable, SETTINGS, timeout=5.0)
    worker.start()
    try:
        assert worker.call("simulate", model_id="m")["ok"] is True
        assert worker.alive
    finally:
        worker.stop()


# ---------------------------------------------------------------------------
# Stray output on the wire (issue #175)
#
# The child claims fd 1 for the protocol and points sys.stdout at stderr, which
# stops CA's *Python* prints reaching the wire. It does not stop a native
# library: AADC writes "AADC LicenseSpring exception encountered: ..." to the
# descriptor, it landed between the request and the reply, and the run failed
# with "sent something that is not a reply" while the real reply sat behind it.
# ---------------------------------------------------------------------------
def _chatty_worker(tmp_path, monkeypatch, noise: str):
    """A worker that prints ``noise`` on the wire before every reply."""
    script = tmp_path / "sim_worker_runner.py"
    script.write_text(
        textwrap.dedent(
            f'''
            import json, os, sys
            wire = os.fdopen(os.dup(sys.stdout.fileno()), "w")
            sys.stdout = sys.stderr
            for line in sys.stdin:
                if not line.strip():
                    continue
                msg = json.loads(line)
                if msg.get("op") == "shutdown":
                    break
                wire.write({noise!r} + "\\n")
                wire.write(json.dumps({{
                    "id": msg.get("id"), "ok": True, "result": {{"ran": True}},
                }}) + "\\n")
                wire.flush()
            '''
        )
    )
    monkeypatch.setattr(sim_worker, "runner_path", lambda name: script)
    return script


def test_a_library_banner_on_the_wire_does_not_lose_the_reply(tmp_path, monkeypatch):
    _chatty_worker(tmp_path, monkeypatch, "AADC LicenseSpring exception encountered: no seat")
    worker = SimWorker(sys.executable, SETTINGS)
    worker.start()  # the configure reply already had to survive the banner
    try:
        assert worker.call("ping")["result"] == {"ran": True}
    finally:
        worker.stop()


def test_stray_output_is_kept_for_diagnosis_rather_than_dropped(tmp_path, monkeypatch):
    """Skipping it silently would trade one undiagnosable failure for another."""
    _chatty_worker(tmp_path, monkeypatch, "AADC LicenseSpring exception encountered: no seat")
    worker = SimWorker(sys.executable, SETTINGS)
    worker.start()
    try:
        worker.call("ping")
        assert any("LicenseSpring" in line for line in worker._stderr)
    finally:
        worker.stop()


def test_a_native_write_to_the_descriptor_cannot_reach_the_wire():
    """The cause rather than the symptom. Reassigning sys.stdout only redirects
    Python writes; a C library writes to file descriptor 1 and lands on the wire
    regardless, which is how AADC's banner got in. os.write bypasses Python's
    stream objects the same way, so it stands in for the native call.

    In a subprocess because the module claims fd 1 at import: importing it here
    would redirect pytest's own stdout."""
    api_dir = str(Path(__file__).resolve().parents[1])
    probe = (
        "import os, sys; sys.path.insert(0, {!r}); import sim_worker_runner as r; "
        "os.write(1, b'BANNER\\n'); r._WIRE.write('REPLY\\n'); r._WIRE.flush()"
    ).format(api_dir)
    proc = subprocess.run(
        [sys.executable, "-c", probe], capture_output=True, text=True, timeout=60
    )
    assert "REPLY" in proc.stdout, proc.stderr
    assert "BANNER" not in proc.stdout  # the wire stayed clean
    assert "BANNER" in proc.stderr  # and the banner was not simply lost
