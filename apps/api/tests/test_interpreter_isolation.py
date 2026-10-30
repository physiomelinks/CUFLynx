"""The chosen interpreter must not survive the test that chose it.

``POST /api/config`` points every job manager at an interpreter. Tests configure
one that does not exist -- ``/venv/bin/python`` -- to check the *plumbing*, and
without a restore every later test in the session then tries to spawn it. That
is not hypothetical: it has happened twice, first for the sensitivity manager
and then for the emulator one, each time because a manager was added after the
list in ``conftest._analysis_pythons`` and never added to it.

CI cannot catch it, which is why it is worth a test rather than vigilance: the
unit and integration tiers run as separate sessions, and the test that sets the
interpreter is in one while the tests that spawn it are in the other. Only a
whole-suite run locally shows it.

So the list is checked against what is actually there rather than trusted.
"""

from __future__ import annotations

import importlib
from pathlib import Path

import pytest

from conftest import _analysis_pythons, _set_analysis_pythons

API_DIR = Path(__file__).resolve().parents[1]

#: Not a manager: the engine stores its interpreter under a different name, and
#: it is already covered by the restore list explicitly.
_ENGINE = ('engine', 'worker_python')


def _job_managers():
    """Every ``<module>.<module>`` object holding a ``python``, found on disk.

    Discovered rather than listed. A list is exactly what was wrong both times.
    """
    found = {}
    for path in sorted(API_DIR.glob('*.py')):
        name = path.stem
        try:
            module = importlib.import_module(name)
        except Exception:  # noqa: BLE001 - not every module imports standalone
            continue
        manager = getattr(module, name, None)
        if manager is not None and hasattr(manager, 'python') and not isinstance(manager, type):
            found[name] = manager
    return found


def test_there_are_job_managers_to_find():
    """Guards the discovery itself: an empty sweep would make the next test vacuous."""
    assert set(_job_managers()) >= {'calibration', 'sensitivity', 'uq', 'emulator'}


def test_every_job_manager_is_restored_between_tests():
    """Set them all to a sentinel, restore, and check none of them kept it."""
    managers = _job_managers()
    before = _analysis_pythons()
    try:
        for manager in managers.values():
            manager.python = '/nonexistent/sentinel/python'
        _set_analysis_pythons(before)
        leaked = [
            name for name, manager in managers.items()
            if manager.python == '/nonexistent/sentinel/python'
        ]
        assert not leaked, (
            f'{", ".join(leaked)} would keep an interpreter chosen by an earlier test. '
            f'Add it to conftest._analysis_pythons and _set_analysis_pythons.'
        )
    finally:
        _set_analysis_pythons(before)


def test_the_engines_worker_interpreter_is_restored_too():
    """Live simulation is set from the same choice, so it leaks the same way."""
    module = importlib.import_module(_ENGINE[0])
    engine = getattr(module, _ENGINE[0])
    before = _analysis_pythons()
    try:
        setattr(engine, _ENGINE[1], '/nonexistent/sentinel/python')
        _set_analysis_pythons(before)
        assert getattr(engine, _ENGINE[1]) != '/nonexistent/sentinel/python'
    finally:
        _set_analysis_pythons(before)


@pytest.mark.parametrize('name', ['calibration', 'sensitivity', 'uq', 'emulator'])
def test_configuring_an_interpreter_reaches_that_manager(client, name):
    """The other half: the restore is only worth having because the config
    really does set these.

    A real interpreter, because the route checks that it exists -- the tests that
    use a fake one have to fake ``os.path.isfile`` alongside it, and that is not
    what is being checked here.
    """
    import sys

    module = importlib.import_module(name)
    manager = getattr(module, name)
    resp = client.post('/api/config', json={'python_path': sys.executable})
    assert resp.status_code == 200, resp.text
    assert manager.python == sys.executable
