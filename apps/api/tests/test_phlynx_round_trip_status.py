"""The PhLynx round trip's status issue (`.github/scripts/phlynx_round_trip_status.py`)."""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[3] / ".github" / "scripts" / "phlynx_round_trip_status.py"
_spec = importlib.util.spec_from_file_location("phlynx_round_trip_status", SCRIPT)
status = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(status)

C1, C2 = "a" * 40, "b" * 40
P1, P2 = "c" * 40, "d" * 40


def _comment(cuflynx, phlynx, ref="main", passed=True):
    return status.comment_body(cuflynx=cuflynx, phlynx=phlynx, ref=ref, passed=passed,
                               run_url="https://github.com/physiomelinks/CUFLynx/actions/runs/1")


def test_a_comment_names_its_pair_and_is_read_back():
    body = _comment(C1, P1, passed=False)
    assert "**failed**" in body and P1[:7] in body and C1[:7] in body
    assert status.last_main_pair([body]) == (C1, P1)


def test_the_nightly_run_skips_only_the_pair_main_tested_last():
    comments = [_comment(C1, P1), _comment(C1, P2, passed=False)]
    assert not status.should_run(comments, C1, P2)
    # either repo moving runs it, and so does an issue with no runs
    assert status.should_run(comments, C2, P2)
    assert status.should_run(comments, C1, P1)
    assert status.should_run([], C1, P1)
    assert status.should_run(["a person's comment"], C1, P1)


def test_a_run_of_another_ref_is_not_the_cache():
    # a deploy run tests a production commit; the nightly run compares with main's
    comments = [_comment(C1, P1), _comment(C2, P2, ref=P2)]
    assert status.last_main_pair(comments) == (C1, P1)
    assert status.should_run(comments, C2, P2)


@pytest.mark.parametrize("passed, ref, current, expected", [
    (False, "main", "closed", "open"),
    (False, P1, "closed", "open"),       # a production deploy that fails reopens it
    (True, "main", "open", "closed"),
    (True, P1, "open", "open"),          # only main's pass says main is fixed
    (True, P1, "closed", "closed"),
])
def test_a_failure_opens_the_issue_and_only_a_pass_of_main_closes_it(passed, ref, current,
                                                                      expected):
    assert status.new_state(passed=passed, ref=ref, current=current) == expected
