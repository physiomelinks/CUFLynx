"""CI must not depend on a circulatory_autogen branch that can be deleted.

The integration PR (#297) temporarily pointed every CA reference at CA's `integration/next`
so the pair could be tested together, with "revert before merge" on each. That revert did not
happen, and `main` shipped with CI depending on a **merged topic branch** — one that routine
post-merge cleanup deletes. Nothing would have failed at merge time; the build would simply
have started failing later, at a checkout step, for a reason unrelated to whatever was pushed.

So: a CA reference may be a full commit SHA (reproducible, and what the deliberate pin wants)
or a long-lived branch. Never a topic branch.

Parsed as YAML rather than grepped, because `ref:` appears under several different actions and
only the circulatory_autogen ones are ours to constrain.
"""
import pathlib
import re

import yaml

REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]
WORKFLOW_DIR = REPO_ROOT / ".github" / "workflows"

CA_REPO = "physiomelinks/circulatory_autogen"

#: Branches that are not going anywhere. Anything else must be a SHA.
PERMANENT_REFS = {"master", "main"}

_SHA = re.compile(r"^[0-9a-f]{40}$")


def _ca_checkout_refs():
    """``(workflow, job, ref)`` for every actions/checkout of circulatory_autogen."""
    found = []
    for path in sorted(WORKFLOW_DIR.glob("*.yml")):
        doc = yaml.safe_load(path.read_text()) or {}
        for job_name, job in (doc.get("jobs") or {}).items():
            for step in job.get("steps") or []:
                with_ = step.get("with") or {}
                if with_.get("repository") == CA_REPO:
                    found.append((path.name, job_name, str(with_.get("ref", "")).strip()))
    return found


def _ca_git_installs():
    """``(workflow, job, ref)`` for every ``pip install ...@<ref>`` of circulatory_autogen."""
    found = []
    pattern = re.compile(re.escape(CA_REPO) + r"@([^\"'\s]+)")
    for path in sorted(WORKFLOW_DIR.glob("*.yml")):
        doc = yaml.safe_load(path.read_text()) or {}
        for job_name, job in (doc.get("jobs") or {}).items():
            for step in job.get("steps") or []:
                for ref in pattern.findall(step.get("run") or ""):
                    found.append((path.name, job_name, ref))
    return found


def test_every_ca_reference_is_a_sha_or_a_permanent_branch():
    """The guard proper. A merged topic branch is the dangerous case: it works right up until
    someone tidies it away, and then fails somewhere that does not mention it."""
    bad = [
        f"{wf}:{job} -> {ref!r}"
        for wf, job, ref in _ca_checkout_refs() + _ca_git_installs()
        if ref and not (_SHA.match(ref) or ref in PERMANENT_REFS)
    ]
    assert not bad, (
        "these reference circulatory_autogen by something other than a commit SHA or a "
        f"permanent branch {sorted(PERMANENT_REFS)}: {bad}. A topic branch here breaks CI "
        "whenever it is deleted."
    )


def test_the_unit_tier_pins_a_sha_rather_than_tracking_a_branch():
    """The unit tier's own comment says the pin is bumped deliberately, so upstream drift
    cannot break CUFLynx CI. Tracking a branch -- even `master` -- gives that up silently."""
    drifting = [
        f"{wf}:{job} -> {ref!r}"
        for wf, job, ref in _ca_checkout_refs()
        if ref and not _SHA.match(ref)
    ]
    assert not drifting, (
        f"these check CA out at a moving ref: {drifting}. The unit tier pins a commit on "
        "purpose -- see the comment beside it."
    )


def test_no_revert_before_merge_marker_survives():
    """`INTEGRATION WIRING -- revert before merge` reached `main` once already. If the phrase
    is in a workflow, something temporary was merged."""
    offenders = [
        p.name for p in sorted(WORKFLOW_DIR.glob("*.yml"))
        if "revert before merge" in p.read_text().lower()
    ]
    assert not offenders, (
        f"these workflows still carry a 'revert before merge' marker: {offenders}"
    )


# --- The pin against the engine floor the app declares -------------------------------
#
# The guards above ask whether the pin is *stable*. This one asks whether it is *current
# enough*, which is a different failure and had a far longer fuse.
#
# `apps/api/pyproject.toml` moved its floor to `libcuflynx>=0.7.3` -- deliberately, because
# a 0.7.x app writes obs_data an older engine refuses (see the comment beside the floor).
# The `backend-unit` pin stayed at the v0.7.1 merge commit. So for a month the unit tier
# ran every test against a pairing pip itself would refuse to resolve, nothing in CI said
# so, and the only thing that noticed was the weekly `ca-pin-freshness` job in
# integration.yml -- which reports "N releases behind the newest", not "older than our own
# floor", and only on a schedule nobody is watching on a Monday morning.
#
# A SHA carries no version, so the pin line records which release it is in a trailing
# `# libcuflynx vX.Y.Z` marker and this reads it back. That keeps the check offline and
# deterministic: it runs in the unit tier, on every pull request, with no network and no
# GitHub API.

_PYPROJECT = REPO_ROOT / "apps" / "api" / "pyproject.toml"

#: `ref: <sha>  # libcuflynx v0.7.3`
_PINNED_RELEASE = re.compile(
    r"^\s*ref:\s*([0-9a-f]{40})\s*#\s*libcuflynx\s+v(\d+(?:\.\d+)*)\s*$", re.MULTILINE
)

#: `"libcuflynx>=0.7.3"` and `"libcuflynx[emulation,uq]>=0.7.3"` alike.
_DECLARED_FLOOR = re.compile(r"libcuflynx(?:\[[^\]]*\])?\s*>=\s*(\d+(?:\.\d+)*)")


def _version(text):
    return tuple(int(part) for part in text.split("."))


def _declared_libcuflynx_floor():
    """The highest `libcuflynx>=` floor in the app's metadata -- the real floor, since pip
    resolves against all of them at once."""
    floors = _DECLARED_FLOOR.findall(_PYPROJECT.read_text())
    assert floors, f"no `libcuflynx>=` requirement found in {_PYPROJECT}"
    return max(floors, key=_version)


def _pinned_release_markers():
    """``{sha: version}`` for every CA checkout pin that names its release."""
    ci = (WORKFLOW_DIR / "ci.yml").read_text()
    return {sha: version for sha, version in _PINNED_RELEASE.findall(ci)}


def test_every_ca_checkout_pin_names_the_release_it_is():
    """Without the marker the check below has nothing to read, and the pin silently stops
    being verifiable offline -- which is how it drifted two releases in the first place."""
    markers = _pinned_release_markers()
    unmarked = [
        f"{wf}:{job} -> {ref}"
        for wf, job, ref in _ca_checkout_refs()
        if _SHA.match(ref) and ref not in markers
    ]
    assert not unmarked, (
        f"these pin a CA commit with no `# libcuflynx vX.Y.Z` marker beside it: {unmarked}. "
        "A bare SHA carries no version, so nothing offline can tell whether the pin is "
        "older than the `libcuflynx>=` floor in apps/api/pyproject.toml."
    )


def test_the_pin_is_not_older_than_the_libcuflynx_floor():
    """The guard proper. CI must not test the app against an engine the app's own metadata
    says is too old to install -- the state `main` shipped in for a month."""
    floor = _declared_libcuflynx_floor()
    stale = {
        sha: version
        for sha, version in _pinned_release_markers().items()
        if _version(version) < _version(floor)
    }
    assert not stale, (
        f"apps/api/pyproject.toml requires libcuflynx>={floor}, but ci.yml pins CA at "
        f"{ {sha[:8]: f'v{v}' for sha, v in stale.items()} }. The unit tier would run "
        f"against an engine pip would refuse to install alongside this app. Bump the "
        f"`ref:` in ci.yml to the v{floor} (or newer) merge commit and update its marker."
    )
