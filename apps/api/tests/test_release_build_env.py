"""The release build's environment, which only a tagged build would otherwise test.

`.github/workflows/release.yml` runs on a tag, so a mistake in the "Build
executable" step's `env:` is discovered after the tag and the release page exist.
Both of the entries pinned here were learned that way.

The one this file was written for: `MPI4PY_RC_INITIALIZE: "0"` skips `MPI_Init`
while PyInstaller analyses the bundle, which the Linux runners need (MPICH's UCX
transport intermittently fails to come up and kills the isolated child). What it
leaves behind is mpi4py imported with MPI *not open*, and in that state every MPI
routine except `MPI_Initialized`/`MPI_Finalized` is erroneous. MS-MPI and MPICH
both answer by printing

    Attempting to use an MPI routine before initializing MPI

and killing the process -- not raising, so nothing can catch it.

PyInstaller imports every collected package into ONE isolated child. mpi4py.futures
loads MPI uninitialised, and once #269 put libcuflynx in the bundle the same child
went on to import `libcuflynx.solver_wrappers` -> `aadc_python_solver_helper` ->
`PrimitiveParsers`, whose module scope reads `rank = mpi_utils.rank()`. That read
killed the child, and the v0.4.0 Windows build failed twice with

    SubprocessDiedError: Isolated subprocess crashed while importing package
    'libcuflynx.solver_wrappers'

Windows has no UCX and so never needed the workaround -- every release up to
v0.1.1 initialised MS-MPI in that child and passed.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

WORKFLOW = Path(__file__).resolve().parents[3] / ".github" / "workflows" / "release.yml"


def _build_step_env() -> dict:
    """The `env:` of the step that runs PyInstaller."""
    workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    steps = workflow["jobs"]["build"]["steps"]
    for step in steps:
        if step.get("name") == "Build executable":
            return step.get("env", {})
    pytest.fail(f"no 'Build executable' step in {WORKFLOW}")


def _resolve(value: str, runner_os: str) -> str:
    """Evaluate the one GitHub expression form used here: `cond && a || b`.

    Deliberately tiny. The point is to read the *value each runner gets*, so a
    later edit that keeps the expression but flips the branches is still caught.
    """
    expr = re.fullmatch(r"\$\{\{\s*(.+?)\s*\}\}", str(value).strip())
    if not expr:
        return str(value)
    match = re.fullmatch(
        r"runner\.os\s*==\s*'(\w+)'\s*&&\s*'([^']*)'\s*\|\|\s*'([^']*)'", expr.group(1))
    assert match, f"unrecognised expression {value!r}; extend _resolve or simplify it"
    named, when_true, when_false = match.groups()
    return when_true if runner_os == named else when_false


def _matrix_runners() -> list:
    workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    return [entry["os"] for entry in workflow["jobs"]["build"]["strategy"]["matrix"]["include"]]


def test_windows_is_still_built():
    """Everything below is vacuous if no Windows runner is in the matrix."""
    assert any(name.startswith("windows") for name in _matrix_runners())


def test_the_windows_build_does_not_skip_mpi_init():
    """Skipping it leaves MPI loaded-but-unopened, and libcuflynx reads its rank at
    import -- MS-MPI kills the isolated child, and PyInstaller reports it as
    'crashed while importing libcuflynx.solver_wrappers'."""
    value = _build_step_env().get("MPI4PY_RC_INITIALIZE")
    assert value is not None, (
        "MPI4PY_RC_INITIALIZE has gone from the build step; the Linux runners need "
        "it set to 0 (see the module docstring)")
    assert _resolve(value, "Windows") != "0", (
        f"the Windows build resolves MPI4PY_RC_INITIALIZE={_resolve(value, 'Windows')!r}. "
        "0 means mpi4py loads MPI without opening it, and the PyInstaller analysis "
        "child dies reading libcuflynx's rank.")


def test_the_linux_workaround_is_kept():
    """The other half: MPICH's UCX init took out 2 of 3 Linux release builds, and
    the fix for Windows must not quietly undo the fix for Linux."""
    value = _build_step_env().get("MPI4PY_RC_INITIALIZE")
    assert _resolve(value, "Linux") == "0"
    assert _resolve(value, "macOS") == "0"
