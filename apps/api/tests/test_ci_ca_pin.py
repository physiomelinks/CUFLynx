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
