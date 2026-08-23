"""The port the desktop shell listens on, which is now part of a contract (#287).

PhLynx hands a study to a running CUFLynx by posting to it on localhost, and a
browser cannot discover a random port. So the shell has to *try* the agreed range
first. It also must not insist on it: a user with something else on 8787 should
still get a working app, minus deliveries.

Both halves are load-bearing and neither is obvious from reading the function, so
they are pinned here. Imported by path because `apps/desktop` is not a package and
the shell is executed as a script.
"""

from __future__ import annotations

import importlib.util
import socket
import sys
from pathlib import Path

import pytest

import inbox as inbox_mod

DESKTOP_APP = Path(__file__).resolve().parents[2] / "desktop" / "app.py"


@pytest.fixture(scope="module")
def shell():
    """`apps/desktop/app.py`, loaded under a name that cannot collide with `main`."""
    spec = importlib.util.spec_from_file_location("cuflynx_desktop_shell", DESKTOP_APP)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _hold(port: int):
    """Occupy a port for a test, so the walk has something to walk past."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", port))
    s.listen(1)
    return s


def test_the_preferred_port_is_used_when_it_is_free(shell):
    port, reachable = shell.choose_port()
    assert reachable is True
    assert port in inbox_mod.RECEIVE_PORTS


def test_the_range_is_walked_when_the_preferred_port_is_taken(shell):
    held = _hold(inbox_mod.PREFERRED_PORT)
    try:
        port, reachable = shell.choose_port()
    finally:
        held.close()
    assert reachable is True
    assert port != inbox_mod.PREFERRED_PORT
    assert port in inbox_mod.RECEIVE_PORTS


def test_exhausting_the_range_still_yields_a_working_app(shell):
    """The failure mode this avoids: refusing to start over a convenience. The
    caller is told deliveries are unavailable and everything else carries on."""
    held = [_hold(p) for p in inbox_mod.RECEIVE_PORTS]
    try:
        port, reachable = shell.choose_port()
    finally:
        for s in held:
            s.close()
    assert reachable is False
    assert port not in inbox_mod.RECEIVE_PORTS
    assert port > 0


def test_a_taken_port_is_reported_as_taken(shell):
    held = _hold(inbox_mod.PREFERRED_PORT)
    try:
        assert shell._port_is_free(inbox_mod.PREFERRED_PORT) is False
    finally:
        held.close()
    assert shell._port_is_free(inbox_mod.PREFERRED_PORT) is True


def test_the_shell_reads_the_range_from_the_one_definition(shell):
    """Not a copy: a range that drifted between the API and the shell would leave
    the app listening somewhere PhLynx never looks."""
    assert shell._receive_ports() is inbox_mod.RECEIVE_PORTS
