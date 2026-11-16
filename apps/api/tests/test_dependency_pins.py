"""Guards on dependency pins whose absence breaks the app only once *shipped*.

The frozen desktop bundle is built from a clean resolve of `apps/api/pyproject.toml`,
while developer machines keep whatever they installed before. A pin that disagrees
with what circulatory_autogen needs therefore breaks only the shipped binary
(v0.1.0 shipped with libcellml resolved past CA's bound and every Python-backend
generation failing). CI's unit tier doesn't import libcellml at all.

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


def test_libcellml_floor_is_0_7_2():
    """libcuflynx's generators need libcellml 0.7.2: 0.7.1 drops an imported unit's
    exponent, so models with imported units fail with a units mismatch. The bundle is
    built from a clean resolve of pyproject.toml, so the floor is what ships."""
    assert _requirement("libcellml") == "libcellml>=0.7.2"


def test_libcellml_pin_matches_circulatory_autogen():
    """Our bundle *is* the environment CA runs in, so the two constraints have to agree.
    Read from the CA checkout the unit tier pins (``CIRCULATORY_AUTOGEN_SRC``)."""
    import os

    src = os.environ.get("CIRCULATORY_AUTOGEN_SRC")
    ca_pyproject = Path(src).parent / "pyproject.toml" if src else None
    if not ca_pyproject or not ca_pyproject.is_file():
        pytest.skip("no circulatory_autogen checkout (CIRCULATORY_AUTOGEN_SRC)")
    match = re.search(r'"(libcellml[^"]*)"', ca_pyproject.read_text())
    assert match, f"no libcellml requirement in {ca_pyproject}"
    assert _requirement("libcellml") == match.group(1)


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
def test_installed_libcellml_meets_the_floor():
    """The pin is only worth anything if the *installed* libcellml honours it.

    Catches an environment that satisfied the pin at resolve time but drifted since
    (or was installed by something that ignored it, e.g. conda).
    """
    libcellml = pytest.importorskip("libcellml")
    version = libcellml.versionString()
    parts = tuple(int(p) for p in re.findall(r"\d+", version)[:3])
    assert parts >= (0, 7, 2), f"installed libcellml is {version}; libcuflynx needs >=0.7.2"


def _core_dependencies() -> list[str]:
    """The ``[project] dependencies`` list only — not the optional extras.

    That distinction is the whole point of the test below: a package named anywhere
    in the file satisfies :func:`_requirement`, including the ``[analysis]`` extra
    that a source install never installs.
    """
    text = PYPROJECT.read_text()
    block = re.search(r"^dependencies = \[(.*?)^\]", text, re.MULTILINE | re.DOTALL)
    assert block, f"could not find the [project] dependencies list in {PYPROJECT}"
    return re.findall(r'"([^"]+)"', block.group(1))


@pytest.mark.parametrize("package", ["numdifftools", "scikit-learn"])
def test_in_process_cost_gradient_deps_are_core_not_analysis(package):
    """The analytic cost gradient runs *in-process*, so its imports are core deps.

    ``cost_gradient.py`` imports CA's ``param_id.paramID``, which reaches an
    unguarded ``import numdifftools`` in CA's ``utilities/utility_funcs.py``, and then
    scikit-learn. Both were declared only in the ``[analysis]`` extra — the
    *subprocess* tier — so ``scripts/install.py`` (``pip install -e ".[dev]"``) built
    an environment in which the analytic gradient could never be computed.

    It fails silently, which is why this is a metadata test rather than a smoke test:
    the route catches ``GradientUnavailable`` and differences instead, so the app keeps
    working and merely returns a gradient that is ~10x slower and, for a parameter the
    cost barely depends on, of arbitrary sign. Nothing turns red; the numbers just get
    worse. Measured on a clean venv, ``/api/cost_sensitivity`` returned
    ``analytic: false`` with ``fallback_reason: "No module named 'numdifftools'"``.
    """
    names = {re.split(r"[<>=!~;\[ ]", dep)[0] for dep in _core_dependencies()}
    assert package in names, (
        f"{package} must be a core dependency: the in-process cost gradient imports it "
        "through circulatory_autogen. Declaring it only in [analysis] leaves a source "
        "install silently differencing every cost gradient."
    )


# ---------------------------------------------------------------------------
# Build-time constraints (as opposed to what the app runs against)
# ---------------------------------------------------------------------------
REPO_ROOT = Path(__file__).resolve().parents[3]
BUILD_CONSTRAINTS = REPO_ROOT / "build-constraints.txt"
WORKFLOWS = REPO_ROOT / ".github" / "workflows"


def _run_steps() -> list[tuple[str, str]]:
    """``(workflow, run script)`` for every workflow step that runs a shell script.

    Parsed as YAML rather than scanned as text: the interesting property is "this
    *step* installs the extra and this *same step* passes the flag", and a regex
    over the file cannot tell a step's script from the comment above the next one.
    """
    import yaml

    out = []
    for path in sorted(WORKFLOWS.glob("*.yml")):
        doc = yaml.safe_load(path.read_text()) or {}
        for job in (doc.get("jobs") or {}).values():
            for step in job.get("steps") or []:
                script = step.get("run")
                if isinstance(script, str):
                    out.append((path.name, script))
    return out


def _installs_of_the_full_extra() -> list[tuple[str, str]]:
    """Steps whose pip install reaches the ``full`` extra.

    Matched on the extras list rather than on a literal, because release.yml builds
    it with a matrix expression (``,full`` appended conditionally) and integration.yml
    takes it from ``matrix.extras``.
    """
    return [
        (workflow, " ".join(script.split()))
        for workflow, script in _run_steps()
        if "pip install" in script and ("full]" in script or "matrix.extras" in script)
    ]


def test_the_build_constraints_file_holds_the_cython_ceiling():
    """`harmonic` has no cp310 wheel, so CI and the Linux `-full` release build
    compile its sdist. Cython 3.3.0 made an implicit double->long assignment an
    error and harmonic 1.3.0's `model_legacy.pyx:433` does exactly that, which
    broke `pip install -e ".[...,full]"` outright. harmonic's own build-system
    requires an unpinned Cython, so the ceiling can only come from here."""
    assert BUILD_CONSTRAINTS.is_file(), f"{BUILD_CONSTRAINTS} is missing"
    assert re.search(r"^Cython<3\.3", BUILD_CONSTRAINTS.read_text(), re.M), (
        "the Cython ceiling is what keeps the `full` extra installable on 3.10"
    )


def test_every_full_install_passes_the_build_constraints():
    """A workflow that installs `[full]` without the constraint file builds
    harmonic against whatever Cython PyPI serves that day. That is not a
    hypothetical: it is how this broke, hours after Cython 3.3.0 was published,
    on a branch that had touched nothing related."""
    installs = _installs_of_the_full_extra()
    assert installs, "no workflow pip install reaching [full] was found -- has the matrix moved?"
    missing = [(wf, cmd) for wf, cmd in installs if "--build-constraint" not in cmd]
    assert not missing, (
        "these installs reach the `full` extra without --build-constraint:\n"
        + "\n".join(f"  {wf}: {cmd}" for wf, cmd in missing)
    )


def test_the_full_installs_upgrade_pip_first():
    """`--build-constraint` is a pip 25.1+ option, and a runner image ships
    whatever pip it ships. Passing it to an older pip is a "no such option"
    failure, i.e. the same red build by a different route."""
    for workflow, script in _run_steps():
        if "--build-constraint" not in script:
            continue
        assert "pip install --upgrade pip" in script, (
            f"{workflow}: a step passes --build-constraint without upgrading pip "
            f"first:\n{script.strip()[:400]}"
        )
