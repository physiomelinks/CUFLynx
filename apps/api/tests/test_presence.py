"""The browser-fallback shell exits once its last tab has gone.

Regression for the packaged Linux app outliving its window: with no GTK/Qt
bindings it opens the system browser instead, and used to wait on an Event that
nothing ever set -- so closing the tab left the server, and its /tmp/_MEI*
directory, running until logout.
"""

from __future__ import annotations

import importlib.util
import sys
import threading
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import main
from presence import Presence

DESKTOP_APP = Path(__file__).resolve().parents[2] / "desktop" / "app.py"


class Clock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def p(clock):
    return Presence(stale_after=150, grace=15, first_contact=300, clock=clock)


def test_waits_for_a_first_tab_then_gives_up(p, clock):
    clock.t += 299
    assert not p.abandoned()
    clock.t += 2
    assert p.abandoned()


def test_open_tab_keeps_it_alive(p, clock):
    p.beat("a")
    clock.t += 140
    assert not p.abandoned()


def test_closing_the_last_tab_ends_it_after_the_grace(p, clock):
    p.beat("a")
    clock.t += 5
    p.leave("a")
    clock.t += 14
    assert not p.abandoned()
    clock.t += 2
    assert p.abandoned()


def test_a_reload_is_not_a_close(p, clock):
    p.beat("a")
    p.leave("a")
    clock.t += 3
    p.beat("b")  # the reloaded page
    clock.t += 60
    assert not p.abandoned()


def test_one_tab_leaving_does_not_end_another(p, clock):
    p.beat("a")
    p.beat("b")
    p.leave("a")
    clock.t += 100  # b is throttled in the background but not stale
    assert not p.abandoned()


def test_a_tab_that_vanishes_silently_goes_stale(p, clock):
    p.beat("a")
    clock.t += 150 + 14
    assert not p.abandoned()
    clock.t += 2
    assert p.abandoned()


def test_routes_feed_the_shared_tracker(monkeypatch, p):
    monkeypatch.setattr(main, "presence", p)
    client = TestClient(main.app)
    assert client.post("/api/presence/beat", params={"client": "x"}).status_code == 200
    assert p._seen.keys() == {"x"}
    assert client.post("/api/presence/leave", params={"client": "x"}).status_code == 200
    assert p._seen == {}
    assert client.post("/api/presence/beat").status_code == 422


@pytest.fixture(scope="module")
def shell():
    spec = importlib.util.spec_from_file_location("cuflynx_desktop_shell_presence", DESKTOP_APP)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class FakeServer:
    should_exit = False


def test_shell_stops_the_server_once_abandoned(shell):
    calls = iter([False, False, True])

    class Fake:
        def abandoned(self):
            return next(calls)

    server = FakeServer()
    done = threading.Event()
    t = threading.Thread(
        target=lambda: (shell.wait_until_abandoned(server, Fake(), poll=0.001), done.set()),
        daemon=True,
    )
    t.start()
    assert done.wait(5), "the shell never returned -- the old Event().wait() hang"
    assert server.should_exit


def test_no_browser_mode_waits_forever():
    """The hang was an Event nobody sets; it must not come back in any branch."""
    assert "Event().wait()" not in DESKTOP_APP.read_text()
