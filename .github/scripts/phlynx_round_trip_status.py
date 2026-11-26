"""The PhLynx round trip's status issue, on PhLynx (CUFLynx #418).

One issue, reused: every reported run comments on it with both commits and the run,
a failure reopens it and a passing run of PhLynx ``main`` closes it. The nightly run
reads the last ``main`` pair back from those comments and skips when neither repo
has moved.

    python phlynx_round_trip_status.py check --repo physiomelinks/PhLynx \\
        --cuflynx <sha> --phlynx <sha>
    python phlynx_round_trip_status.py report --repo physiomelinks/PhLynx \\
        --cuflynx <sha> --phlynx <sha> --ref main --result success --run-url <url>

``check`` writes ``run=true|false`` to ``$GITHUB_OUTPUT`` (or stdout). Both call the
GitHub API through ``gh``, so ``GH_TOKEN`` must be able to read and write PhLynx's
issues.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys

TITLE = "CUFLynx round trip status"
#: the PhLynx branch whose runs close the issue and set the skip cache
MAIN = "main"
_MARKER = re.compile(
    r"<!-- cuflynx-round-trip ref=(?P<ref>\S+) cuflynx=(?P<cuflynx>[0-9a-f]+) "
    r"phlynx=(?P<phlynx>[0-9a-f]+) result=(?P<result>\w+) -->")


def last_main_pair(comments: list[str]) -> tuple[str, str] | None:
    """The (CUFLynx, PhLynx) commits of the newest ``main`` run reported in
    ``comments`` (oldest first), or None when there is none."""
    for body in reversed(comments):
        for match in _MARKER.finditer(body or ""):
            if match["ref"] == MAIN:
                return match["cuflynx"], match["phlynx"]
    return None


def should_run(comments: list[str], cuflynx: str, phlynx: str) -> bool:
    """False only when the newest ``main`` run tested exactly this pair."""
    return last_main_pair(comments) != (cuflynx, phlynx)


def comment_body(*, cuflynx: str, phlynx: str, ref: str, passed: bool, run_url: str) -> str:
    verdict = "passed" if passed else "**failed**"
    marker = (f"<!-- cuflynx-round-trip ref={ref} cuflynx={cuflynx} phlynx={phlynx} "
              f"result={'success' if passed else 'failure'} -->")
    return (f"The CUFLynx round trip {verdict} for PhLynx `{ref}` at {phlynx[:7]} "
            f"with CUFLynx {cuflynx[:7]}: {run_url}\n\n{marker}")


def new_state(*, passed: bool, ref: str, current: str) -> str:
    """The issue's state after a run: a failure opens it, a pass of ``main`` closes
    it, and a pass of anything else leaves it as it was."""
    if not passed:
        return "open"
    return "closed" if ref == MAIN else current


def _gh(*args: str, body: dict | None = None):
    cmd = ["gh", "api", *args]
    if body is not None:
        cmd += ["--input", "-"]
    out = subprocess.run(cmd, input=json.dumps(body) if body is not None else None,
                         capture_output=True, text=True, check=True).stdout
    return json.loads(out) if out.strip() else None


def _find_issue(repo: str) -> dict | None:
    for page in range(1, 11):
        issues = _gh(f"repos/{repo}/issues?state=all&per_page=100&page={page}&sort=created"
                     f"&direction=asc")
        for issue in issues:
            if issue["title"] == TITLE and "pull_request" not in issue:
                return issue
        if len(issues) < 100:
            return None
    return None


def _comments(repo: str, number: int) -> list[str]:
    bodies = []
    for page in range(1, 51):
        batch = _gh(f"repos/{repo}/issues/{number}/comments?per_page=100&page={page}")
        bodies += [c["body"] for c in batch]
        if len(batch) < 100:
            return bodies
    return bodies


def _output(**values: str) -> None:
    lines = "".join(f"{k}={v}\n" for k, v in values.items())
    path = os.environ.get("GITHUB_OUTPUT")
    if path:
        with open(path, "a") as f:
            f.write(lines)
    sys.stdout.write(lines)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("check", "report"):
        p = sub.add_parser(name)
        p.add_argument("--repo", required=True)
        p.add_argument("--cuflynx", required=True)
        p.add_argument("--phlynx", required=True)
        if name == "report":
            p.add_argument("--ref", required=True)
            p.add_argument("--result", required=True)
            p.add_argument("--run-url", required=True)
    args = parser.parse_args(argv)

    issue = _find_issue(args.repo)
    comments = _comments(args.repo, issue["number"]) if issue else []
    if args.command == "check":
        run = should_run(comments, args.cuflynx, args.phlynx)
        if not run:
            print(f"PhLynx {args.phlynx[:7]} with CUFLynx {args.cuflynx[:7]} was tested last; "
                  f"nothing has changed")
        _output(run=str(run).lower())
        return 0

    passed = args.result == "success"
    if issue is None:
        issue = _gh(f"repos/{args.repo}/issues", body={
            "title": TITLE,
            "body": "The CUFLynx -> PhLynx -> CUFLynx round trip, run by CUFLynx's "
                    "`phlynx-round-trip.yml`: nightly against `main`, and before every "
                    "production deploy. Open while it fails; each run comments below.",
        })
    _gh(f"repos/{args.repo}/issues/{issue['number']}/comments", body={
        "body": comment_body(cuflynx=args.cuflynx, phlynx=args.phlynx, ref=args.ref,
                             passed=passed, run_url=args.run_url)})
    state = new_state(passed=passed, ref=args.ref, current=issue["state"])
    if state != issue["state"]:
        _gh(f"repos/{args.repo}/issues/{issue['number']}", "-X", "PATCH",
            body={"state": state})
    print(f"{issue['html_url']}: {state}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
