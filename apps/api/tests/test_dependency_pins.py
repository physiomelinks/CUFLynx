"""Guards on dependency pins whose absence breaks the app only once *shipped*.

The v0.1.0 desktop build shipped with the python / casadi_python backends broken:
`apps/api/pyproject.toml` said `libcellml>=0.6.3` with no upper bound, so the CI
build machine's fresh `pip install` resolved **0.7.0**. libcellml 0.7 changed its
generated Python code (it no longer emits `VARIABLE_COUNT`), and
circulatory_autogen's `PythonGenerator._extract_generated_metadata` still reads
that symbol — so every model generation died with `KeyError: 'VARIABLE_COUNT'`.

Nothing caught it: developer machines had 0.6.3 pinned by history, so running from
source worked, and CI's unit tier doesn't import libcellml at all. Only the frozen
binary — built from a clean resolve — was broken.

These tests are deliberately in the *unit* tier (no libcellml import required), so
they run everywhere the build does.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

PYPROJECT = Path(__file__).resolve().parents[1] / "pyproject.toml"


def _requirement(name: str) -> str:
    """The raw requirement string for ``name`` from our [project] dependencies."""
    text = PYPROJECT.read_text()
    match = re.search(rf'^\s*"({re.escape(name)}[^"]*)"', text, re.MULTILINE)
    assert match, f"{name} is not declared in {PYPROJECT}"
    return match.group(1)


def test_libcellml_has_an_upper_bound_below_0_7():
    """libcellml 0.7 removed VARIABLE_COUNT from its generated code, which CA's
    PythonGenerator still requires. Without this bound, a clean install resolves
    0.7.0 and the python / casadi_python backends break in the packaged app."""
    req = _requirement("libcellml")
    assert "<0.7" in req, (
        f"libcellml must be pinned below 0.7 (got {req!r}). 0.7 drops VARIABLE_COUNT "
        "from the generated Python code and circulatory_autogen's PythonGenerator "
        "still reads it -> KeyError: 'VARIABLE_COUNT' on every model generation."
    )


def test_libcellml_pin_matches_circulatory_autogen():
    """CA pins `libcellml>=0.6.3,<0.7.0` for this exact reason. Our bundle *is* the
    environment CA runs in, so the two constraints have to agree — if CA relaxes its
    bound after migrating, this is the other place to update."""
    assert _requirement("libcellml") == "libcellml>=0.6.3,<0.7.0"


# Every package circulatory_autogen imports on the *simulation* path. The live
# engine imports CA in-process, so these must exist in whatever environment runs
# the app — including the frozen bundle, which is built from a clean install of
# pyproject.toml. casadi was missing, and because dev machines have it installed
# for CA anyway, only the CI-built binary was broken: the casadi_python backend
# died with "CasADi solver requested but CasADi is not available".
#
# CA's *analysis* deps (emcee, SALib, nevergrad, mpi4py, matplotlib, ...) are
# deliberately absent: those run in the user's own interpreter via the subprocess
# runners, never in-process.
SIMULATION_PATH_DEPS = [
    "numpy",
    "scipy",
    "pandas",
    "myokit",
    "libcellml",
    "casadi",
    "sympy",
    "pyyaml",
    "ruamel.yaml",
    "rdflib",
    "pint",
]


@pytest.mark.parametrize("package", SIMULATION_PATH_DEPS)
def test_simulation_path_dependency_is_declared(package):
    """A CA simulation-path import that we don't declare will be absent from the
    packaged app — and present on every dev machine, so nobody notices."""
    _requirement(package)  # asserts it appears in [project] dependencies


@pytest.mark.integration
def test_installed_libcellml_still_emits_variable_count():
    """The pin is only worth anything if the *installed* libcellml honours it.

    Catches an environment that satisfied the pin at resolve time but drifted since
    (or was installed by something that ignored it, e.g. conda).
    """
    libcellml = pytest.importorskip("libcellml")
    version = libcellml.versionString()
    major, minor = (int(p) for p in version.split(".")[:2])
    assert (major, minor) < (0, 7), (
        f"installed libcellml is {version}; CA's PythonGenerator needs <0.7 "
        "(0.7 no longer emits VARIABLE_COUNT)"
    )
