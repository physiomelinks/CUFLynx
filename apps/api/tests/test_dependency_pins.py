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
# CA's *analysis* deps (emcee, SALib, nevergrad, mpi4py, matplotlib, ...) are ALSO
# bundled now (the self-contained app runs SA/calibration/UQ in its own
# interpreter, no external Python), declared in the [analysis] extra — see below.
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

# CA's analysis-path packages, declared in the [analysis] extra (pip names). The
# frozen build installs and bundles these so calibration/UQ/global-SA run in-app.
# Dropping any is a "breaks only once shipped" regression (the runner interpreter
# is the bundle), so guard them the same way as the simulation deps.
ANALYSIS_PATH_DEPS = [
    "matplotlib",
    "emcee",
    "corner",
    "SALib",
    "seaborn",
    "statsmodels",
    "schwimmbad",
    "nevergrad",
    "numdifftools",
    "scikit-learn",
    "tqdm",
    "mpi4py",
]


@pytest.mark.parametrize("package", SIMULATION_PATH_DEPS)
def test_simulation_path_dependency_is_declared(package):
    """A CA simulation-path import that we don't declare will be absent from the
    packaged app — and present on every dev machine, so nobody notices."""
    _requirement(package)  # asserts it appears in the pyproject dependencies


@pytest.mark.parametrize("package", ANALYSIS_PATH_DEPS)
def test_analysis_path_dependency_is_declared(package):
    """Same guard for the bundled analysis stack: a missing one breaks
    calibration/UQ/global-SA only in the shipped binary."""
    _requirement(package)


# The analysis runners (*_runner.py) are executed by an *external* interpreter, so
# any apps/api sibling module they import must be shipped beside them as a data
# file in the bundle — the external interpreter doesn't have cuflynx-api installed.
# local_sensitivity was missing, and a local sensitivity run died in the packaged
# app with "No module named 'local_sensitivity'" (invisible on a dev box, whose
# runner interpreter has an editable cuflynx-api on its path).
API_DIR = PYPROJECT.parent
SPEC = API_DIR.parents[1] / "packaging" / "cuflynx.spec"
RUNNERS = ("calibration_runner.py", "sensitivity_runner.py", "uq_runner.py")


def _sibling_module_imports(py_file: Path) -> set[str]:
    """Names imported in ``py_file`` that resolve to an apps/api sibling module."""
    import ast

    tree = ast.parse(py_file.read_text())
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names.add(node.module.split(".")[0])
        elif isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.name.split(".")[0])
    return {n for n in names if (API_DIR / f"{n}.py").is_file()}


def test_runner_sibling_imports_are_bundled():
    """Every apps/api module a runner imports must be listed as bundled data in
    the PyInstaller spec, or it's absent from the packaged app."""
    spec_text = SPEC.read_text()
    required: set[str] = set()
    for runner in RUNNERS:
        required |= _sibling_module_imports(API_DIR / runner)

    missing = [m for m in sorted(required) if f'"{m}.py"' not in spec_text]
    assert not missing, (
        f"runner(s) import apps/api modules not bundled by {SPEC.name}: {missing}. "
        "Add them to the data-file list next to the *_runner.py entries, or they "
        "won't exist for the external interpreter that runs the analysis."
    )


def test_runners_are_bundled_into_a_subdir_not_the_root():
    """The runner scripts must land in a 'runners' subdir, never the bundle root.

    At the root, the external interpreter's sys.path[0] would include the bundle's
    own numpy/scipy/... and import those instead of its own — crashing with
    'numpy.core.multiarray failed to import'. Extract the ACTUAL data-file dest of
    the runner-bundling loop and assert it, so the guard can't pass trivially.
    """
    import re

    spec_text = SPEC.read_text()
    dests = re.findall(r'datas\.append\(\(str\(API_DIR / runner\), "([^"]+)"\)\)', spec_text)
    assert dests, "could not locate the runner-bundling `datas.append` in the spec"
    for dest in dests:
        assert dest == "runners", (
            f"runners bundled to {dest!r}, must be the 'runners' subdir — bundling to "
            "'.' puts the app's numpy on the external interpreter's sys.path[0]."
        )


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
