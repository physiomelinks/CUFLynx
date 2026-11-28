"""The coupling contract that keeps ``obs_extract`` movable.

``obs_extract`` is the only package in a directory of flat modules, and it is one
because it is a candidate to move to its own repository. That only stays true
while it does not reach into the rest of the app. The temptation is real and
small each time -- one import of ``settings_store`` to find a directory, one of
``engine`` to get the loaded model -- and each one welds the package a little
more firmly in place.

So the contract is a test rather than a comment. It reads the package's own
imports with ``ast``; a convenient import added later fails here, with the reason,
instead of being noticed the day someone tries to move the directory.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

PACKAGE = Path(__file__).resolve().parents[1] / "obs_extract"

#: What obs_extract is allowed to import from apps/api, and why each is here.
ALLOWED = {
    "obs_options",   # CA's operation registry and kwargs schemas
    "obs_data",      # the obs_data validator, so output is checked by CA
    "ca_imports",    # the one supported route to circulatory_autogen
    "solver_plots",  # *path* helpers only -- never its pyplot figure saving
}

#: Named so the failure message can say what is wrong rather than just "not
#: allowed". These are the ones that would actually weld the package in place.
FORBIDDEN_REASONS = {
    "main": "the FastAPI app; obs_extract is called by routes, it does not know them",
    "engine": "the live simulation tier; extraction reads files, it does not simulate",
    "calibration": "a job manager for a different subsystem",
    "runtime_paths": "resolves paths for the frozen app; obs_extract takes directories as arguments",
    "settings_store": "the user's config; every directory must arrive as an argument",
    "uq": "a different subsystem",
    "sensitivity": "a different subsystem",
    "emulator": "a different subsystem",
}


def _local_module_names() -> set[str]:
    """Every flat module in ``apps/api`` -- what "the rest of the app" means."""
    return {p.stem for p in PACKAGE.parent.glob("*.py") if p.stem != "__init__"}


def _imports(path: Path) -> set[str]:
    """Top-level module names imported by one file, relative imports excluded."""
    tree = ast.parse(path.read_text(), filename=str(path))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                found.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            # level > 0 is a relative import: within the package, always fine.
            if node.level == 0 and node.module:
                found.add(node.module.split(".")[0])
    return found


def test_obs_extract_imports_nothing_it_should_not():
    local = _local_module_names()
    offenders: list[str] = []
    for path in sorted(PACKAGE.rglob("*.py")):
        for name in sorted(_imports(path)):
            if name not in local or name in ALLOWED:
                continue
            reason = FORBIDDEN_REASONS.get(name, "not on the allow-list")
            offenders.append(f"{path.name} imports {name} -- {reason}")
    assert not offenders, (
        "obs_extract may only import "
        + ", ".join(sorted(ALLOWED))
        + " from apps/api, so the directory can be moved to its own repository. "
        + "Found:\n  " + "\n  ".join(offenders)
    )


def test_the_package_is_importable_without_the_app():
    """Importing obs_extract must not drag in FastAPI or the model registry.

    A subprocess, because ``main`` is already imported by the time most tests
    run -- so asserting on ``sys.modules`` in-process would prove nothing.
    """
    import subprocess
    import sys

    code = (
        "import sys; import obs_extract; "
        "bad = [m for m in ('main', 'engine', 'fastapi') if m in sys.modules]; "
        "print(','.join(bad))"
    )
    out = subprocess.run(
        [sys.executable, "-c", code], cwd=str(PACKAGE.parent),
        capture_output=True, text=True, check=True)
    assert out.stdout.strip() == "", (
        f"importing obs_extract pulled in {out.stdout.strip()}")


def test_the_allow_list_matches_the_documented_contract():
    """The docstring in ``__init__`` is what a reader trusts; keep it true."""
    doc = (PACKAGE / "__init__.py").read_text()
    for name in ALLOWED:
        assert name in doc, f"{name} is allowed here but not named in __init__.py"
    for name in ("main", "engine", "calibration", "runtime_paths", "settings_store"):
        assert name in doc, f"{name} is refused here but not named in __init__.py"
