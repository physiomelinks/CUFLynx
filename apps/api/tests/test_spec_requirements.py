"""Everything the bundle requires must be declared as a dependency.

`packaging/cuflynx.spec` fails the build when a package it collects is absent from the
build environment — deliberately, because v0.1.0 shipped without casadi when a dev machine
had it and the CI machine did not. That guard works, but it fires *during a tagged release
build*, which is the worst place to learn about it: the tag exists, the release page exists,
and nothing can be attached to it.

That is exactly how v0.4.0 went out with no binaries. `libcuflynx` was added to the spec and
installed by hand here, but never declared, so it was present on one laptop and nowhere else,
and all four runners failed.

This is the same check, moved to where it costs nothing: read the spec's `_REQUIRED` list and
the declared dependencies, and compare. It runs in the existing unit job, needs no PyInstaller
and no build.
"""
import ast
import pathlib
import re

import pytest

_REPO = pathlib.Path(__file__).resolve().parents[3]
_SPEC = _REPO / "packaging" / "cuflynx.spec"
_PYPROJECT = _REPO / "apps" / "api" / "pyproject.toml"

#: Import name -> distribution that provides it, where they differ.
_DISTRIBUTION_FOR = {
    "yaml": "pyyaml",
    "sklearn": "scikit-learn",
    "webview": "pywebview",
    "PIL": "pillow",
}


def _spec_required() -> set[str]:
    """The spec's `_REQUIRED` tuple, read rather than executed (it imports PyInstaller)."""
    tree = ast.parse(_SPEC.read_text(encoding="utf-8"), filename=str(_SPEC))
    names: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        if not any(getattr(t, "id", None) in ("_REQUIRED", "_ANALYSIS_PKGS") for t in node.targets):
            continue
        for element in ast.walk(node.value):
            if isinstance(element, ast.Constant) and isinstance(element.value, str):
                names.add(element.value)
    return names


def _declared_distributions() -> set[str]:
    """Every distribution named in pyproject, base and extras alike, normalised."""
    text = _PYPROJECT.read_text(encoding="utf-8")
    # Requirement strings, minus version specifiers, markers and extras.
    found = set()
    for raw in re.findall(r'"([A-Za-z0-9._+\[\]-]+(?:[<>=!~;][^"]*)?)"', text):
        name = re.split(r"[<>=!~;\[]", raw, 1)[0].strip()
        if name:
            found.add(name.lower().replace("_", "-"))
    return found


@pytest.mark.unit
def test_the_sweep_reads_both_files():
    """Guard the guard: a bad path or a renamed variable makes this vacuous."""
    assert _SPEC.is_file(), _SPEC
    assert _PYPROJECT.is_file(), _PYPROJECT
    required = _spec_required()
    assert "libcuflynx" in required, "the spec no longer requires libcuflynx — has it moved?"
    assert len(required) > 10, required
    assert len(_declared_distributions()) > 10


@pytest.mark.unit
def test_every_package_the_bundle_requires_is_declared():
    declared = _declared_distributions()
    missing = sorted(
        name for name in _spec_required()
        if _DISTRIBUTION_FOR.get(name, name).lower().replace("_", "-").replace(".", "-")
        not in declared
        and _DISTRIBUTION_FOR.get(name, name).lower().replace("_", "-") not in declared
    )
    assert not missing, (
        f"packaging/cuflynx.spec requires {missing}, which apps/api/pyproject.toml does not "
        f"declare. The spec fails the build when one is absent — and it fails it during a "
        f"tagged release, after the tag and the release page already exist. Declare it, so a "
        f"fresh build environment gets it."
    )
