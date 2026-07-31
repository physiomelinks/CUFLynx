"""The version number must be one number, everywhere it is written down.

v0.1.7 was tagged, built and published while the app reported ``0.1.0`` and
Windows reported ``0.1.2``. Nothing was broken by it, which is exactly why it
survived four releases: a wrong version is invisible until someone tries to say
which build they are running. Two of the four copies are now derived from
``version.py``; these tests cover the two that cannot be.
"""

import json
import re
from pathlib import Path

import pytest

from version import __version__

REPO = Path(__file__).resolve().parents[3]
API = REPO / "apps" / "api"


def test_the_version_is_a_release_number():
    assert re.fullmatch(r"\d+\.\d+\.\d+", __version__), __version__


def test_the_frontend_package_agrees():
    # package.json cannot import Python, so this is the only thing holding it to
    # the same number.
    pkg = json.loads((REPO / "apps" / "web" / "package.json").read_text())
    assert pkg["version"] == __version__


def test_the_windows_resource_agrees():
    # PyInstaller embeds this in the .exe; it is what Windows shows under
    # Properties -> Details, and what an antivirus heuristic reads.
    text = (REPO / "packaging" / "version_info.txt").read_text()
    major, minor, patch = __version__.split(".")
    assert f"filevers=({major}, {minor}, {patch}, 0)" in text
    assert f"prodvers=({major}, {minor}, {patch}, 0)" in text
    assert f"StringStruct('FileVersion', '{__version__}.0')" in text
    assert f"StringStruct('ProductVersion', '{__version__}.0')" in text


def test_the_windows_resource_states_the_licence_the_repo_actually_uses():
    # It claimed MIT while LICENSE is Apache-2.0 -- a licence statement compiled
    # into a shipped binary, so worth a test rather than a memory.
    text = (REPO / "packaging" / "version_info.txt").read_text()
    licence = (REPO / "LICENSE").read_text()
    assert "Apache License" in licence
    assert "Apache-2.0" in text
    assert "MIT" not in text


def test_pyproject_takes_its_version_from_version_py():
    # If someone re-adds a literal `version = "..."` here, the single source is
    # no longer single.
    text = (API / "pyproject.toml").read_text()
    assert 'dynamic = ["version"]' in text
    assert '[tool.hatch.version]\npath = "version.py"' in text
    assert not re.search(r'^version = "', text, re.M)


@pytest.mark.integration
def test_the_built_package_reports_the_same_version():
    # Proves the hatch indirection resolves, rather than trusting the config.
    hatchling = pytest.importorskip("hatchling")  # noqa: F841
    from hatchling.metadata.core import ProjectMetadata  # noqa: PLC0415
    from hatchling.plugin.manager import PluginManager  # noqa: PLC0415

    meta = ProjectMetadata(str(API), PluginManager())
    assert meta.version == __version__
