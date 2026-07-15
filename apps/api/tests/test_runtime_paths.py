"""Tests for frozen-bundle (PyInstaller) path + interpreter resolution.

The desktop build freezes the API into a single executable. The failure these
tests guard against is subtle and severe: in a frozen app ``sys.executable`` is
the *bundle*, so the old ``subprocess.Popen([sys.executable, runner.py, ...])``
would relaunch the whole GUI instead of running a calibration — recursively.
"""

from __future__ import annotations

import sys

import pytest

import calibration as calibration_mod
import compiler_check
import runtime_paths
import sensitivity as sensitivity_mod
import uq as uq_mod
from runtime_paths import NO_PYTHON_ERROR


@pytest.fixture
def frozen(monkeypatch, tmp_path):
    """Make runtime_paths believe it's running inside a PyInstaller bundle."""
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)
    monkeypatch.delenv("CUFLYNX_PYTHON", raising=False)
    return tmp_path


# ---------------------------------------------------------------------------
# is_frozen / resource_path / frontend_dist
# ---------------------------------------------------------------------------
def test_not_frozen_when_running_from_source():
    assert runtime_paths.is_frozen() is False


def test_frozen_detected(frozen):
    assert runtime_paths.is_frozen() is True


def test_resource_path_resolves_into_the_bundle_when_frozen(frozen):
    assert runtime_paths.resource_path("calibration_runner.py") == (
        frozen / "calibration_runner.py"
    )


def test_resource_path_resolves_next_to_the_api_from_source():
    # From source the runner sits beside the module that spawns it.
    assert runtime_paths.resource_path("calibration_runner.py").is_file()


def test_runner_path_is_in_a_subdir_when_frozen(frozen):
    """Runners must NOT be at the bundle root: that dir holds the app's numpy, and
    the external interpreter would import it (via sys.path[0]) instead of its own.
    A 'runners' subdir keeps the bundle's packages off the runner's path."""
    p = runtime_paths.runner_path("sensitivity_runner.py")
    assert p == frozen / "runners" / "sensitivity_runner.py"
    assert p.parent != frozen  # the whole point: not the bundle root


def test_runner_path_resolves_beside_the_api_from_source():
    assert runtime_paths.runner_path("calibration_runner.py").is_file()


def test_frontend_dist_points_into_the_bundle_when_frozen(frozen):
    assert runtime_paths.frontend_dist() == frozen / "web" / "dist"


def test_frontend_dist_points_at_the_web_app_from_source():
    assert runtime_paths.frontend_dist().name == "dist"
    assert runtime_paths.frontend_dist().parent.name == "web"


# ---------------------------------------------------------------------------
# default_python — the core hazard
# ---------------------------------------------------------------------------
def test_default_python_is_the_serving_interpreter_from_source(monkeypatch):
    monkeypatch.delenv("CUFLYNX_PYTHON", raising=False)
    assert runtime_paths.default_python() == sys.executable


def test_default_python_is_none_when_frozen(frozen):
    """The bundle must never be offered as an interpreter: running it would
    relaunch the desktop app instead of the runner script."""
    assert runtime_paths.default_python() is None


def test_cuflynx_python_env_var_overrides(monkeypatch, frozen):
    monkeypatch.setenv("CUFLYNX_PYTHON", "/opt/ca-venv/bin/python")
    assert runtime_paths.default_python() == "/opt/ca-venv/bin/python"


def test_candidate_pythons_never_include_the_frozen_bundle(frozen, monkeypatch):
    """sys.executable (the bundle) must not leak into the interpreter picker."""
    monkeypatch.setattr(sys, "executable", "/nonexistent/CUFLynx.AppImage")
    assert "/nonexistent/CUFLynx.AppImage" not in calibration_mod._candidate_python_paths()


# ---------------------------------------------------------------------------
# The three job managers refuse to run with no interpreter (rather than
# re-executing the bundle, or crashing on a None in the argv list).
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "manager_cls",
    [
        calibration_mod.CalibrationManager,
        sensitivity_mod.SensitivityManager,
        uq_mod.UQManager,
    ],
)
def test_build_command_errors_clearly_without_an_interpreter(manager_cls):
    manager = manager_cls()
    manager.python = None  # what default_python() yields in the packaged app
    with pytest.raises(RuntimeError, match="no Python interpreter selected"):
        manager.build_command({}, "/tmp/config.json")
    assert "circulatory_autogen" in NO_PYTHON_ERROR


@pytest.mark.parametrize(
    "manager_cls",
    [
        calibration_mod.CalibrationManager,
        sensitivity_mod.SensitivityManager,
        uq_mod.UQManager,
    ],
)
def test_build_command_uses_the_configured_interpreter(manager_cls):
    manager = manager_cls()
    manager.python = None
    cmd = manager.build_command({"python": "/opt/ca-venv/bin/python"}, "/tmp/config.json")
    assert cmd[0] == "/opt/ca-venv/bin/python"
    assert cmd[1] == "-u"
    assert cmd[-1] == "/tmp/config.json"


# ---------------------------------------------------------------------------
# Compiler detection (surfaced by GET /api/config so the UI can warn up front)
# ---------------------------------------------------------------------------
def test_compiler_status_is_quiet_when_a_compiler_is_present(monkeypatch):
    monkeypatch.setattr(compiler_check, "has_cpp_compiler", lambda: True)
    status = compiler_check.compiler_status()
    assert status["present"] is True
    assert status["hint"] == ""
    assert status["affects"] == ""
    assert status["alternatives"] == []


def test_missing_compiler_names_what_breaks_and_what_still_works(monkeypatch):
    """A missing compiler is a limitation, not a fatal error: only the Myokit
    backend JIT-compiles, so the UI must be able to point at the alternatives."""
    monkeypatch.setattr(compiler_check, "has_cpp_compiler", lambda: False)
    status = compiler_check.compiler_status()

    assert status["present"] is False
    assert status["hint"]  # a per-OS install instruction
    assert "CVODE_myokit" in status["affects"]

    # The compiler-free backends, exactly as named in CA's solver schema.
    formats = {a["generated_model_format"] for a in status["alternatives"]}
    solvers = {a["solver"] for a in status["alternatives"]}
    assert formats == {"python", "casadi_python"}
    assert solvers == {"solve_ivp", "casadi_integrator"}
    assert all(a["label"] for a in status["alternatives"])


def test_config_route_exposes_compiler_status_and_packaged_flag(client):
    body = client.get("/api/config").json()
    assert {"present", "hint", "affects", "alternatives"} == set(body["cpp_compiler"])
    assert body["packaged"] is False  # tests run from source, never frozen


# ---------------------------------------------------------------------------
# subprocess_env — the runner must NOT inherit the bundle's loader paths
# ---------------------------------------------------------------------------
def test_subprocess_env_is_untouched_when_not_frozen(monkeypatch):
    monkeypatch.setenv("LD_LIBRARY_PATH", "/whatever")
    assert runtime_paths.subprocess_env().get("LD_LIBRARY_PATH") == "/whatever"


def test_subprocess_env_restores_pyinstaller_originals(frozen, monkeypatch):
    """Frozen, LD_LIBRARY_PATH points at the bundle; PyInstaller saved the
    caller's value in LD_LIBRARY_PATH_ORIG. The runner must get the original,
    or its own numpy/OpenBLAS loads the bundle's libs and imports blow up."""
    monkeypatch.setenv("LD_LIBRARY_PATH", "/tmp/_MEIxxxx")  # bundle path
    monkeypatch.setenv("LD_LIBRARY_PATH_ORIG", "/usr/lib/original")
    env = runtime_paths.subprocess_env()
    assert env["LD_LIBRARY_PATH"] == "/usr/lib/original"


def test_subprocess_env_drops_bundle_loader_path_when_no_original(frozen, monkeypatch):
    """If the caller had no LD_LIBRARY_PATH, PyInstaller sets no _ORIG — the
    bundle value must be removed entirely, not left pointing at the bundle."""
    monkeypatch.setenv("LD_LIBRARY_PATH", "/tmp/_MEIxxxx")
    monkeypatch.delenv("LD_LIBRARY_PATH_ORIG", raising=False)
    assert "LD_LIBRARY_PATH" not in runtime_paths.subprocess_env()


def test_pythons_route_default_is_null_when_packaged(client, frozen, monkeypatch):
    """In the packaged app the client must force an explicit interpreter pick."""
    monkeypatch.setattr(calibration_mod, "list_python_interpreters", lambda refresh=False: [])
    assert client.get("/api/calibration/pythons").json()["default"] is None
