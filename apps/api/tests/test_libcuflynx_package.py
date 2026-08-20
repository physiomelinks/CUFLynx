"""CUFLynx against an **installed** libcuflynx, rather than a checkout of it.

``apps/api/pyproject.toml`` has declared ``libcuflynx>=0.4.0`` as an ordinary dependency
since CA #452 renamed the distribution, and ``[emulation]`` raises that to
``libcuflynx[emulation,uq]>=0.4.1``. Nothing has ever checked it.

Every CI job resolves CA a different way instead: ``backend-unit`` and its Windows twin
check out ``physiomelinks/circulatory_autogen`` at a pinned commit and point
``CIRCULATORY_AUTOGEN_SRC`` at its ``src``; the ``packaging`` job installs with
``--no-deps`` on purpose. So the declared dependency is never installed, and the layout
under test is a **checkout on sys.path** -- which is not the layout users get and not the
layout the frozen app ships.

The difference is not cosmetic. ``ca_imports.installed_package_available()`` exists
precisely because "importable" and "installed" are different questions: ``ensure_ca_path``
inserts a configured checkout's ``src`` permanently, so once any directory has been used
``libcuflynx`` stays importable for the life of the process. A checkout answers for an
install and everything downstream -- the first-run prompt, the emulator's model list, the
version floor -- is decided against the wrong thing.

These tests skip when no installed libcuflynx is present, so they cost the existing
checkout-based jobs nothing. **A skipped test is a green job**, though, which is the
whole failure mode this suite keeps meeting, so the CI job that exists to run them sets
``CUFLYNX_REQUIRE_INSTALLED_LIBCUFLYNX=1`` and the skip becomes an error.
"""
from __future__ import annotations

import importlib.metadata as metadata
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

API_DIR = Path(__file__).resolve().parents[1]
PYPROJECT = API_DIR / "pyproject.toml"

REQUIRED = os.environ.get(
    "CUFLYNX_REQUIRE_INSTALLED_LIBCUFLYNX", "").strip() not in ("", "0")


def _installed_version():
    """The version of libcuflynx as an installed distribution, or None.

    ``importlib.metadata`` rather than ``libcuflynx.__version__``: a checkout on
    ``sys.path`` supplies the latter and not the former, which is exactly the
    distinction being drawn.
    """
    try:
        return metadata.version("libcuflynx")
    except metadata.PackageNotFoundError:
        return None


def _is_a_checkout(module) -> bool:
    """Whether *module* was imported from ``<repo>/src/libcuflynx``.

    Same test ``ca_imports.installed_package_available`` applies, kept independent of it
    on purpose -- a bug in that helper is one of the things this file should catch.
    """
    origin = getattr(module, "__file__", None)
    if not origin:
        return False
    return Path(origin).resolve().parent.parent.name == "src"


if _installed_version() is None:
    if REQUIRED:  # pragma: no cover - CI misconfiguration
        raise RuntimeError(
            "CUFLYNX_REQUIRE_INSTALLED_LIBCUFLYNX is set but libcuflynx is not installed "
            "as a distribution. These tests would have skipped silently, which reads as a "
            "pass -- check the install step in the workflow."
        )
    pytestmark = pytest.mark.skip(
        reason="libcuflynx is not installed as a distribution (only a checkout, or absent)"
    )


# ---------------------------------------------------------------------------
# The declared dependency is real
# ---------------------------------------------------------------------------
def _declared_floor() -> str:
    """The minimum libcuflynx version pyproject.toml asks for.

    Parsed rather than restated so bumping the pin cannot leave this test asserting the
    old number, and so the test fails loudly if the dependency is ever dropped.
    """
    text = PYPROJECT.read_text()
    matches = re.findall(r'"libcuflynx(?:\[[^\]]*\])?>=([0-9][^",]*)"', text)
    assert matches, (
        "apps/api/pyproject.toml no longer declares a libcuflynx>=X dependency. If that "
        "is deliberate, this whole file should go with it."
    )
    return sorted(matches, key=lambda v: [int(p) for p in re.findall(r"\d+", v)])[0]


def _as_tuple(version: str):
    return tuple(int(p) for p in re.findall(r"\d+", version)[:3])


def test_the_installed_version_satisfies_the_declared_floor():
    installed = _installed_version()
    floor = _declared_floor()
    assert _as_tuple(installed) >= _as_tuple(floor), (
        f"libcuflynx {installed} is installed but apps/api/pyproject.toml asks for "
        f">={floor}. CI resolving an older one than the app declares means the app is "
        f"being tested against an engine it says it does not support."
    )


def test_libcuflynx_imports_from_an_install_not_a_checkout():
    """The layout under test must be the one users get.

    Without this the job could pass while quietly importing a checkout that happens to be
    on sys.path -- which is precisely what every other CI job here does.
    """
    import libcuflynx

    assert not _is_a_checkout(libcuflynx), (
        f"libcuflynx resolved to a checkout at {libcuflynx.__file__}. This job exists to "
        f"test the installed package; a checkout on sys.path answering for it makes the "
        f"whole job meaningless."
    )


def test_ca_imports_agrees_that_the_package_is_installed():
    """``installed_package_available()`` is what the app asks; it must say yes here.

    It drives the first-run prompt and the "is CA present at all" decision, so a wrong
    answer here is a wrong answer in the UI.
    """
    from ca_imports import installed_package_available

    assert installed_package_available() is True, (
        "libcuflynx is installed as a distribution but ca_imports does not think so"
    )


# ---------------------------------------------------------------------------
# The app resolves it with no CA directory configured
# ---------------------------------------------------------------------------
_RESOLVE_PROBE = """
import json, sys
sys.path.insert(0, {api_dir!r})
import ca_imports
ca_imports.reset_cache()
mod = ca_imports.ca_import("parsers.PrimitiveParsers")
print("RESULT " + json.dumps({{
    "resolved": ca_imports.resolved_name("parsers.PrimitiveParsers"),
    "file": getattr(mod, "__file__", None),
    "installed": ca_imports.installed_package_available(),
}}))
"""


def test_the_app_resolves_ca_with_no_directory_configured():
    """A user who installed libcuflynx and configured nothing must still get CA.

    That is the whole promise of depending on the package rather than on a checkout, and
    it is exercised nowhere: every existing job sets CIRCULATORY_AUTOGEN_SRC. Run in a
    subprocess with the variable stripped, because ``ensure_ca_path`` mutates
    ``sys.path`` and this session may already have a checkout on it.
    """
    env = {k: v for k, v in os.environ.items() if k != "CIRCULATORY_AUTOGEN_SRC"}
    env["CUFLYNX_CONFIG_DIR"] = env.get("CUFLYNX_CONFIG_DIR", "/nonexistent-cuflynx-config")
    proc = subprocess.run(
        [sys.executable, "-c", _RESOLVE_PROBE.format(api_dir=str(API_DIR))],
        env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        universal_newlines=True, timeout=300,
    )
    assert proc.returncode == 0, f"resolving CA with nothing configured failed:\n{proc.stdout}"
    line = [ln for ln in proc.stdout.splitlines() if ln.startswith("RESULT ")]
    assert line, f"probe printed no result:\n{proc.stdout}"
    import json as _json
    result = _json.loads(line[-1][len("RESULT "):])
    assert result["installed"] is True, result
    assert result["resolved"] == "libcuflynx.parsers.PrimitiveParsers", (
        f"with only an installed package present, CA should resolve under the "
        f"{'libcuflynx'!r} namespace; got {result['resolved']!r}"
    )
    assert result["file"] and Path(result["file"]).parent.parent.name != "src", (
        f"resolved to a checkout despite no CA directory being configured: {result['file']}"
    )


# ---------------------------------------------------------------------------
# The engine's own console commands
# ---------------------------------------------------------------------------
#: Declared by libcuflynx in its [project.scripts]. CUFLynx does not invoke these itself
#: (it runs runner scripts), but they are the contract a user follows from the docs, and
#: an install that cannot answer them is broken in a way nothing else here would notice.
ENTRY_POINTS = [
    "cuflynx-generate",
    "cuflynx-param-id",
    "cuflynx-sensitivity",
    "cuflynx-identifiability",
    "cuflynx-train-emulator",
    "cuflynx-plot",
]


def _console_script(command: str):
    """Locate *command* beside the running interpreter, then on PATH.

    Interpreter-first for the same reason ``calibration.resolve_mpiexec`` is: the
    scripts belonging to *this* environment are the ones that match *this*
    ``libcuflynx``, and a venv is very often used without being activated -- running
    ``venv/bin/python -m pytest`` puts nothing of the venv on PATH. Searching PATH alone
    made every one of these fail locally against a perfectly good install.
    """
    beside = Path(sys.executable).parent / command
    if beside.exists():
        return str(beside)
    return shutil.which(command)


@pytest.mark.parametrize("command", ENTRY_POINTS)
def test_the_engines_console_commands_answer(command):
    """Run the launcher setuptools wrote, as a program.

    Not ``python -m <module>``: that bypasses the generated launcher, which is the part
    that only exists in an installed layout and therefore the part a checkout-based job
    can never test.
    """
    path = _console_script(command)
    assert path, (
        f"{command} was found neither beside {sys.executable} nor on PATH. libcuflynx "
        f"is installed, so its console scripts should be too -- a missing one means a "
        f"broken [project.scripts] entry."
    )
    proc = subprocess.run(
        [path, "--help"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        universal_newlines=True, timeout=300,
    )
    assert proc.returncode == 0, f"{command} --help exited {proc.returncode}:\n{proc.stdout}"
    assert "usage:" in proc.stdout.lower(), f"{command} --help printed no usage:\n{proc.stdout}"
