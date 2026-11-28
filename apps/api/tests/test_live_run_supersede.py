"""A newer live run stops an older one that is still running, and a live run can
be cancelled.

The worker serves one request at a time. A model whose live run takes minutes (an
enterocyte's whole cell: 1100 s of model time at MaximumStep 0.001, ~230 s) used
to hold every later click behind it, so the plots of the next model clicked said
"Simulating..." for minutes. A live run that has waited ``SUPERSEDE_AFTER`` behind
another live run now stops it; the stopped request answers 409 ``superseded``.
"""

from __future__ import annotations

import sys
import textwrap
import threading
import time

import engine as engine_mod
import pytest
import sim_worker
from conftest import LV_MODEL_PATH, upload_model
from sim_worker import SimWorker, WorkerGone, WorkerStopped, worker_settings

SETTINGS = worker_settings(
    ca_src="", dt=0.01, model_type="cellml", solver="CVODE_myokit", solver_info={}
)


@pytest.fixture
def sleepy_worker(tmp_path, monkeypatch):
    """A worker that sleeps ``sleep`` seconds before answering each request."""
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
                time.sleep(float(msg.get("sleep") or 0))
                wire.write(json.dumps({
                    "id": msg.get("id"), "ok": True,
                    "result": {"model_id": msg.get("model_id"), "pid": os.getpid()},
                    "captured": "",
                }) + "\\n")
                wire.flush()
            '''
        )
    )
    monkeypatch.setattr(sim_worker, "runner_path", lambda name: script)
    monkeypatch.setattr(sim_worker, "SUPERSEDE_AFTER", 0.3)
    return script


def _in_thread(fn, *args, **kwargs):
    """Run ``fn`` on a thread; returns (thread, box) with box["value"] or box["error"]."""
    box = {}

    def run():
        try:
            box["value"] = fn(*args, **kwargs)
        except BaseException as exc:  # noqa: BLE001 - handed back to the test
            box["error"] = exc

    t = threading.Thread(target=run, daemon=True)
    t.start()
    time.sleep(0.15)  # let it reach the worker before the next request
    return t, box


def test_a_newer_live_run_stops_a_long_one(sleepy_worker):
    worker = SimWorker(sys.executable, SETTINGS, timeout=30)
    worker.start()
    try:
        t, box = _in_thread(worker.call, "run_protocol", live=True, model_id="whole_cell", sleep=20)
        started = time.monotonic()
        with pytest.raises(WorkerGone):
            worker.call("run_protocol", live=True, model_id="channel", sleep=0)
        t.join(5)
        assert isinstance(box.get("error"), WorkerStopped)
        assert box["error"].reason == "superseded"
        assert time.monotonic() - started < 5, "the newer run must not wait for the old one"
        assert not worker.alive
    finally:
        worker.stop()


def test_a_short_live_run_finishes_and_keeps_the_worker(sleepy_worker):
    """Stopping the worker throws away every compiled model, so a run that ends
    within the grace period is waited for."""
    worker = SimWorker(sys.executable, SETTINGS, timeout=30)
    worker.start()
    try:
        t, box = _in_thread(worker.call, "run_protocol", live=True, model_id="a", sleep=0.1)
        second = worker.call("run_protocol", live=True, model_id="b", sleep=0)
        t.join(5)
        assert box["value"]["ok"] is True
        assert second["result"]["pid"] == box["value"]["result"]["pid"]
        assert worker.alive
    finally:
        worker.stop()


def test_a_live_run_does_not_stop_other_work(sleepy_worker):
    """A cost-sensitivity loop or an emulator prediction is not a live run: a
    live run waits for it rather than stopping it."""
    worker = SimWorker(sys.executable, SETTINGS, timeout=30)
    worker.start()
    try:
        t, box = _in_thread(worker.call, "simulate", model_id="a", sleep=0.8)
        assert worker.call("run_protocol", live=True, model_id="b", sleep=0)["ok"] is True
        t.join(5)
        assert box["value"]["ok"] is True
        assert worker.alive
    finally:
        worker.stop()


def test_a_live_run_can_be_cancelled(sleepy_worker):
    worker = SimWorker(sys.executable, SETTINGS, timeout=30)
    worker.start()
    try:
        assert worker.interrupt("cancelled") is False, "nothing in flight"
        t, box = _in_thread(worker.call, "run_protocol", live=True, model_id="a", sleep=20)
        assert worker.interrupt("cancelled") is True
        t.join(5)
        assert isinstance(box.get("error"), WorkerStopped)
        assert box["error"].reason == "cancelled"
    finally:
        worker.stop()


@pytest.fixture
def engine_with_worker(sleepy_worker, monkeypatch):
    monkeypatch.setattr(engine_mod, "_interpreter_prefix", lambda p: "/pretend/venv")
    engine_mod.engine.worker_python = sys.executable
    yield engine_mod.engine
    engine_mod.engine.worker_python = None
    engine_mod.engine.reset()


def test_the_engine_restarts_the_worker_for_the_newer_run(engine_with_worker):
    eng = engine_with_worker
    t, box = _in_thread(eng._worker_call, "run_protocol", {}, live=True, model_id="whole_cell", sleep=20)
    reply = eng._worker_call("run_protocol", {}, live=True, model_id="channel", sleep=0)
    t.join(5)
    assert reply["result"]["model_id"] == "channel"
    assert isinstance(box.get("error"), engine_mod.LiveRunStopped)
    assert box["error"].reason == "superseded"
    assert eng._worker is not None and eng._worker.alive


def test_cancel_live_stops_the_run_in_flight(engine_with_worker):
    eng = engine_with_worker
    assert eng.cancel_live() is False
    t, box = _in_thread(eng._worker_call, "run_protocol", {}, live=True, model_id="a", sleep=20)
    assert eng.cancel_live() is True
    t.join(5)
    assert box["error"].reason == "cancelled"
    # The next run gets a fresh worker.
    assert eng._worker_call("run_protocol", {}, live=True, model_id="a", sleep=0)["result"]


def test_a_stopped_live_run_answers_409_naming_why(client, monkeypatch):
    model_id = upload_model(client, LV_MODEL_PATH)["model_id"]

    def stopped(**kwargs):
        assert kwargs.get("live") is True, "the plots' run must be marked live"
        raise engine_mod.LiveRunStopped("superseded")

    monkeypatch.setattr(engine_mod.engine, "run_protocol", stopped)
    resp = client.post(
        "/api/protocol/run",
        json={"model_id": model_id, "params": {}, "protocol_info": {"sim_times": [[1.0]], "pre_times": [0.0]}},
    )
    assert resp.status_code == 409, resp.text
    assert resp.json()["detail"]["stopped"] == "superseded"


def test_cancel_without_a_worker_says_why_not(client):
    body = client.post("/api/simulate/cancel").json()
    assert body["cancelled"] is False
    assert "Settings" in body["reason"]
