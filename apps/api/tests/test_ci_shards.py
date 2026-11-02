"""The CI shards in tests/ci_shards.json, and the --ci-shard selection in conftest.py.

A shard that names a test which no longer exists, or a workflow matrix that leaves a group
out, would stop tests running in CI without anything failing. These checks fail instead.
"""
import ast
import pathlib
import re

import pytest
import yaml

from conftest import ci_shard_group_of, load_ci_shards, select_ci_shard

API_DIR = pathlib.Path(__file__).resolve().parents[1]
WORKFLOW = API_DIR.parents[1] / ".github" / "workflows" / "integration.yml"
SHARDS = load_ci_shards()


def _test_functions(path: pathlib.Path) -> set:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return {node.name for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name.startswith("test")}


def _all_test_nodeids() -> list:
    """``tests/<file>.py::<function>`` for every test function in the suite."""
    found = []
    for path in sorted((API_DIR / "tests").glob("test_*.py")):
        rel = path.relative_to(API_DIR).as_posix()
        found += [f"{rel}::{name}" for name in sorted(_test_functions(path))]
    return found


def _sharded_jobs() -> list:
    """``(job, suite, shards)`` for every job of integration.yml that runs --ci-shard."""
    jobs = yaml.safe_load(WORKFLOW.read_text())["jobs"]
    found = []
    for name, job in jobs.items():
        commands = "\n".join(step.get("run", "") for step in job.get("steps", []))
        match = re.search(r'--ci-shard "([\w-]+):\$\{\{ matrix\.shard \}\}"', commands)
        if match:
            found.append((name, match.group(1), job["strategy"]["matrix"]["shard"]))
    return found


@pytest.mark.parametrize("suite", sorted(SHARDS))
def test_every_entry_names_an_existing_test(suite):
    for group, entries in SHARDS[suite].items():
        assert entries, f"{suite}:{group} is empty"
        for entry in entries:
            path, _, function = entry.partition("::")
            assert (API_DIR / path).is_file(), f"{suite}:{group}: no file {path}"
            if function:
                assert function in _test_functions(API_DIR / path), \
                    f"{suite}:{group}: {path} defines no {function}"


@pytest.mark.parametrize("suite", sorted(SHARDS))
def test_no_entry_is_in_two_groups(suite):
    groups = SHARDS[suite]
    assert "rest" not in groups, "'rest' is every test no group names; it cannot be a group"
    owner = {}
    for group, entries in groups.items():
        for entry in entries:
            for other in (entry, entry.partition("::")[0]):
                assert owner.get(other) in (None, group), \
                    f"{entry} is in {owner[other]} and {group}"
            owner[entry] = group


def test_the_simulation_tier_is_sharded():
    assert [suite for _, suite, _ in _sharded_jobs()] == ["simulation"]
    assert set(SHARDS) == {"simulation"}


@pytest.mark.parametrize("job, suite, matrix", _sharded_jobs(), ids=str)
def test_every_test_lands_in_exactly_one_shard_of_the_job(job, suite, matrix):
    """Each test of the suite is kept by exactly one of the job's shards: none dropped,
    none run twice."""
    shards = [s.split(",") for s in matrix]
    nodeids = _all_test_nodeids()
    assert nodeids
    for nodeid in nodeids:
        group = ci_shard_group_of(SHARDS[suite], nodeid)
        keeping = [s for s in shards if group in s]
        assert len(keeping) == 1, f"{nodeid} ({group}) is kept by {len(keeping)} shards of {job}"
    groups = [g for s in shards for g in s]
    assert sorted(groups) == sorted([*SHARDS[suite], "rest"]), \
        f"{job} runs {groups}; suite {suite} is {sorted(SHARDS[suite])} plus rest"


# --- select_ci_shard ---------------------------------------------------------

class _Item:
    def __init__(self, nodeid):
        self.nodeid = nodeid


class _Config:
    def __init__(self, spec):
        self.spec = spec
        self.deselected = []
        config = self

        class _Hook:
            @staticmethod
            def pytest_deselected(items):
                config.deselected += items
        self.hook = _Hook()

    def getoption(self, name):
        assert name == "--ci-shard"
        return self.spec


FAKE = {"s": {"a": ["tests/test_a.py"], "b": ["tests/test_b.py::test_one"]}}
NODEIDS = ["tests/test_a.py::test_x", "tests/test_b.py::test_one[1]",
           "tests/test_b.py::test_one_more", "tests/test_c.py::test_y"]


def _select(spec):
    items = [_Item(n) for n in NODEIDS]
    config = _Config(spec)
    select_ci_shard(config, items, shards=FAKE)
    return [i.nodeid for i in items], [i.nodeid for i in config.deselected]


def test_no_shard_keeps_everything():
    assert _select(None) == (NODEIDS, [])


def test_the_shards_of_a_suite_partition_the_collected_tests():
    kept = {spec: _select(f"s:{spec}")[0] for spec in ("a", "b", "rest")}
    assert kept["a"] == ["tests/test_a.py::test_x"]
    # A function entry keeps its parametrizations, not a function whose name only starts with it.
    assert kept["b"] == ["tests/test_b.py::test_one[1]"]
    assert kept["rest"] == ["tests/test_b.py::test_one_more", "tests/test_c.py::test_y"]
    assert sorted(sum(kept.values(), [])) == sorted(NODEIDS)


def test_several_groups_in_one_shard():
    kept, deselected = _select("s:a,rest")
    assert kept == ["tests/test_a.py::test_x", "tests/test_b.py::test_one_more",
                    "tests/test_c.py::test_y"]
    assert deselected == ["tests/test_b.py::test_one[1]"]


@pytest.mark.parametrize("spec", ["nope:a", "s", "s:", "s:zzz"])
def test_a_bad_spec_is_a_usage_error(spec):
    with pytest.raises(pytest.UsageError):
        _select(spec)
