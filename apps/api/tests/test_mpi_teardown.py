"""A run that finished must not be thrown away by its own shutdown.

Reported on macOS: a completed calibration reported as failed with

    Abort(808576911): Fatal error in internal_Finalize: Other MPI error
    MPIDI_OFI_handle_cq_error(593): OFI poll failed
    (default nic=en5: Input/output error)

MPICH's MPI_Finalize aborted while flushing its network queue -- long after
every rank had finished and results.json was on disk. The managers gated purely
on the exit code, so the results were discarded and the user was told "runner
exited with code 808576911".

Two halves: stop treating a teardown failure as a failed analysis, and stop
provoking it by keeping single-node MPI off the network entirely.
"""

from __future__ import annotations

import calibration as calibration_mod
import pytest
import runtime_paths
from calibration import finished_before_exiting, teardown_warning

DONE = "__CALIBRATION_DONE__"
FAIL = "__CALIBRATION_FAILED__"


# ---------------------------------------------------------------------------
# Which non-zero exits are forgivable
# ---------------------------------------------------------------------------
def test_the_done_marker_means_the_work_was_finished():
    lines = ["Starting genetic_algorithm", DONE, "Abort(808576911): Fatal error"]
    assert finished_before_exiting(lines, DONE, FAIL) is True


def test_a_failed_run_is_still_a_failed_run():
    """The FAIL marker wins: this forgives the epilogue, not a crash."""
    lines = ["Starting", FAIL, "Traceback (most recent call last):"]
    assert finished_before_exiting(lines, DONE, FAIL) is False


def test_a_run_that_died_before_finishing_is_not_forgiven():
    """No marker at all -- e.g. killed mid-run -- must stay an error, or a
    crashed analysis would read as a successful one."""
    lines = ["Starting genetic_algorithm", "Segmentation fault"]
    assert finished_before_exiting(lines, DONE, FAIL) is False


def test_the_warning_names_the_code_and_quotes_the_output():
    msg = teardown_warning(808576911, ["...", DONE, "Abort(808576911): Fatal error"])

    assert "completed" in msg and "saved" in msg
    assert "808576911" in msg
    assert "Abort(808576911)" in msg  # the tail, so the cause is visible


# ---------------------------------------------------------------------------
# The manager keeps the results and reports the teardown
# ---------------------------------------------------------------------------
def _finalized_job(tmp_path, code, lines, results=True):
    job = calibration_mod.CalibrationJob("j", str(tmp_path), None, None)
    job.lines = list(lines)
    if results:
        (tmp_path / "results.json").write_text('{"params": {"a/x": 1.5}, "cost": 0.25}')
    calibration_mod.calibration._finalize(job, code)
    return job


def test_a_finished_run_survives_a_non_zero_exit(tmp_path):
    job = _finalized_job(tmp_path, 808576911, ["running", DONE, "Abort(808576911)"])

    assert job.state == "done"
    assert job.best_params == {"a/x": 1.5}
    assert job.cost == 0.25
    # Not silent: the run stands, and the shutdown failure is still reported.
    assert job.warning and "808576911" in job.warning
    assert not job.error


def test_a_clean_run_carries_no_warning(tmp_path):
    job = _finalized_job(tmp_path, 0, ["running", DONE])

    assert job.state == "done"
    assert job.warning is None


def test_a_run_that_never_finished_is_still_an_error(tmp_path):
    """Results on disk from an *earlier* run must not rescue a crashed one."""
    job = _finalized_job(tmp_path, 1, ["running", "Segmentation fault"])

    assert job.state == "error"
    assert "exited with code 1" in job.error


# ---------------------------------------------------------------------------
# Not provoking it in the first place
# ---------------------------------------------------------------------------
def test_single_node_runs_keep_mpi_on_loopback(monkeypatch):
    """Every analysis CUFLynx launches is single-node, so libfabric has no
    business picking a real NIC that can go away mid-run."""
    for var in ("FI_PROVIDER", "FI_TCP_IFACE"):
        monkeypatch.delenv(var, raising=False)

    env = runtime_paths.runner_launch_env(None)

    assert env["FI_PROVIDER"] == "tcp"
    assert env["FI_TCP_IFACE"] in ("lo0", "lo")


def test_the_users_own_mpi_settings_win(monkeypatch):
    """Defaults only: a deliberate provider choice, or a genuinely multi-node
    setup, must not be overridden."""
    monkeypatch.setenv("FI_PROVIDER", "verbs")
    monkeypatch.setenv("FI_TCP_IFACE", "eth0")

    env = runtime_paths.runner_launch_env(None)

    assert env["FI_PROVIDER"] == "verbs"
    assert env["FI_TCP_IFACE"] == "eth0"


@pytest.mark.parametrize("platform,iface", [("darwin", "lo0"), ("linux", "lo")])
def test_the_loopback_interface_is_named_per_platform(monkeypatch, platform, iface):
    """macOS calls it lo0, Linux calls it lo; naming the wrong one would leave
    libfabric with nothing to bind."""
    for var in ("FI_PROVIDER", "FI_TCP_IFACE"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(runtime_paths.sys, "platform", platform)

    assert runtime_paths.runner_launch_env(None)["FI_TCP_IFACE"] == iface
