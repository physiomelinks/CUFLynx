"""The integration tier's run check (.github/scripts/assert_tests_ran.py): a floor on how
many tests ran, and named tests that must have run."""
import importlib.util
import pathlib
import re

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]
SCRIPT = REPO_ROOT / ".github" / "scripts" / "assert_tests_ran.py"
TESTS = pathlib.Path(__file__).resolve().parent


def _script():
    spec = importlib.util.spec_from_file_location("assert_tests_ran", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


JUNIT = """<?xml version="1.0" encoding="utf-8"?>
<testsuites><testsuite name="pytest" tests="3" skipped="1" failures="0" errors="0">
<testcase classname="tests.test_a" name="test_ran"/>
<testcase classname="tests.test_a" name="test_also_ran[x]"/>
<testcase classname="tests.test_a" name="test_skipped"><skipped message="no CA"/></testcase>
</testsuite></testsuites>
"""


@pytest.mark.parametrize("required, code", [
    ([], 0),
    (["test_ran", "test_also_ran[x]"], 0),
    (["test_skipped"], 1),        # skipped
    (["test_absent"], 1),         # not collected
])
def test_a_required_test_must_have_run(tmp_path, required, code):
    junit = tmp_path / "junit.xml"
    junit.write_text(JUNIT)
    argv = [a for name in required for a in ("--require", name)]
    assert _script().main(["--min-ran", "2", *argv, str(junit)]) == code


def test_every_test_ci_requires_exists():
    """A renamed test would leave CI requiring one that is never collected."""
    workflow = (REPO_ROOT / ".github" / "workflows" / "integration.yml").read_text()
    required = re.findall(r"--require (\w+)", workflow)
    assert required
    defined = set()
    for path in TESTS.glob("test_*.py"):
        defined.update(re.findall(r"^def (test_\w+)\(", path.read_text(), re.M))
    assert sorted(set(required) - defined) == []
