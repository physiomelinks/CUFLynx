"""Compiler detection must answer for the machine, not for $PATH.

The bug these pin, in one line: on macOS ``/usr/bin/cc``, ``/usr/bin/gcc`` and
``/usr/bin/clang`` are ``xcrun`` shims shipped by the OS and are **always**
present, so a ``shutil.which`` check said "compiler present" on a machine with no
toolchain at all. The app then offered CVODE_myokit and the run died much later,
inside distutils, with

    DistutilsExecError: command '/usr/bin/clang' failed with exit code 1

and a packaged Mac app has no console to print the reason to.

These tests are hermetic -- they build their own fake shims rather than asking the
host anything -- so they run on every platform in the ordinary unit tier. The
half they cannot cover is whether *real* macOS behaves as described; that is what
the `no-toolchain` state in .github/workflows/mac-extended.yml is for.
"""

from __future__ import annotations

import platform
import sys

import pytest

import compiler_check


def _fake_compiler(tmp_path, name: str, exit_code: int):
    """A stand-in for macOS's /usr/bin/clang: on PATH, and exiting as told."""
    path = tmp_path / name
    if sys.platform == "win32":
        path = path.with_suffix(".bat")
        path.write_text(f"@echo off\r\nexit /b {exit_code}\r\n")
    else:
        path.write_text(
            "#!/bin/sh\n"
            "echo 'xcode-select: note: No developer tools were found' >&2\n"
            f"exit {exit_code}\n"
        )
        path.chmod(0o755)
    return path


@pytest.fixture(autouse=True)
def _uncached():
    """has_cpp_compiler is lru_cached, so every test must start from cold."""
    compiler_check.has_cpp_compiler.cache_clear()
    yield
    compiler_check.has_cpp_compiler.cache_clear()


@pytest.mark.skipif(
    platform.system() == "Windows", reason="the posix branch is what is under test"
)
def test_a_compiler_that_is_on_path_but_does_not_run_is_not_a_compiler(
    tmp_path, monkeypatch
):
    """The exact macOS shape: which() finds it, running it fails. This is the
    assertion that would have caught the reported bug before it shipped."""
    shim = _fake_compiler(tmp_path, "clang", exit_code=1)
    monkeypatch.setattr(
        compiler_check.shutil, "which", lambda name: str(shim) if name == "clang" else None
    )

    assert compiler_check.has_cpp_compiler() is False


@pytest.mark.skipif(
    platform.system() == "Windows", reason="the posix branch is what is under test"
)
def test_a_compiler_that_runs_is_a_compiler(tmp_path, monkeypatch):
    """The other half: detection must not have become uselessly pessimistic."""
    shim = _fake_compiler(tmp_path, "cc", exit_code=0)
    monkeypatch.setattr(
        compiler_check.shutil, "which", lambda name: str(shim) if name == "cc" else None
    )

    assert compiler_check.has_cpp_compiler() is True


@pytest.mark.skipif(
    platform.system() == "Windows", reason="the posix branch is what is under test"
)
def test_no_compiler_on_path_at_all(monkeypatch):
    monkeypatch.setattr(compiler_check.shutil, "which", lambda name: None)

    assert compiler_check.has_cpp_compiler() is False


@pytest.mark.skipif(
    platform.system() == "Windows", reason="the posix branch is what is under test"
)
def test_a_broken_first_candidate_does_not_mask_a_working_later_one(
    tmp_path, monkeypatch
):
    """`cc` and `clang` can disagree -- a Homebrew gcc alongside macOS's dead
    shims, say. Finding one that works is the question, not testing only the
    first."""
    broken = _fake_compiler(tmp_path, "cc", exit_code=1)
    working = _fake_compiler(tmp_path, "gcc", exit_code=0)
    found = {"cc": str(broken), "gcc": str(working)}
    monkeypatch.setattr(compiler_check.shutil, "which", found.get)

    assert compiler_check.has_cpp_compiler() is True


@pytest.mark.skipif(
    platform.system() == "Windows", reason="the posix branch is what is under test"
)
def test_a_compiler_binary_that_vanishes_is_not_an_error(monkeypatch):
    """which() can win a race it then loses. Detection reports absence; it must
    never raise into GET /api/config.

    Windows-skipped like its siblings, and for a reason worth stating: there
    ``has_cpp_compiler`` takes the MSVC branch, which only *looks* for cl.exe and
    never runs it — so patching ``which`` to name a missing file makes that branch
    answer True quite correctly, and the test would be asserting the opposite of
    what the code means. Running the compiler is a POSIX-branch concern."""
    monkeypatch.setattr(
        compiler_check.shutil, "which", lambda name: "/definitely/not/here/cc"
    )

    assert compiler_check.has_cpp_compiler() is False


def test_status_offers_the_compiler_free_backends_when_there_is_none(monkeypatch):
    """The status dict is what the in-app banner renders, and the point of the
    banner is that CVODE_myokit is the *only* thing lost."""
    monkeypatch.setattr(compiler_check.shutil, "which", lambda name: None)
    monkeypatch.setattr(compiler_check, "_has_msvc", lambda: False)

    status = compiler_check.compiler_status()

    assert status["present"] is False
    assert status["hint"]
    assert "CVODE_myokit" in status["affects"]
    formats = {b["generated_model_format"] for b in status["alternatives"]}
    assert formats == {"python", "casadi_python"}


def test_status_is_quiet_when_a_compiler_works(monkeypatch):
    monkeypatch.setattr(compiler_check, "has_cpp_compiler", lambda: True)

    status = compiler_check.compiler_status()

    assert status == {"present": True, "hint": "", "affects": "", "alternatives": []}


@pytest.mark.skipif(
    platform.system() != "Darwin", reason="describes macOS's own /usr/bin shims"
)
def test_macos_really_does_ship_the_shims_this_all_exists_for():
    """Not a behaviour test -- a claim check. Every comment here rests on these
    three existing unconditionally on macOS; if that ever stops being true, the
    reasoning should be revisited rather than silently inherited."""
    import shutil

    assert shutil.which("clang") == "/usr/bin/clang"
