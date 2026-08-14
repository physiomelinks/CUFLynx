"""A run that finished must not be thrown away by its own shutdown.

Reported on macOS: a completed calibration reported as failed with

    Abort(808576911): Fatal error in internal_Finalize: Other MPI error
    MPIDI_OFI_handle_cq_error(593): OFI poll failed
    (default nic=en5: Input/output error)

MPICH's MPI_Finalize aborted while flushing its network queue -- long after
every rank had finished and the results were on disk. The managers gated purely
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
from conftest import write_ca_results

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
        # circulatory_autogen's own outputs, which is what the manager now reads
        # (#210). That makes this gate strictly more robust: it asks whether the
        # *run* wrote its results, not whether CUFLynx managed to serialise a
        # copy of them -- and the copy is what a teardown abort could interrupt.
        write_ca_results(tmp_path, [["a/x"]], [1.5], 0.25)
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
# ...and NOT by second-guessing libfabric
# ---------------------------------------------------------------------------
def test_the_mpi_environment_is_left_alone(monkeypatch):
    """CUFLynx must not pin libfabric's provider or interface.

    It used to: ``FI_PROVIDER=tcp`` plus ``FI_TCP_IFACE=lo0``/``lo``, to keep a
    single-node run off a real NIC that could vanish mid-run. That aborted
    ``MPI_Init`` on macOS -- "OFI call ep_enable failed (default nic=tcp: Bad
    file descriptor)" -- which is strictly worse than the fault it prevented: an
    init abort loses the run, while the teardown abort it was aimed at happens
    with the results already written.

    So the mitigation lives entirely in ``finished_before_exiting`` above, which
    is robust to any cause rather than to the one provider setting we guessed at.
    """
    for var in ("FI_PROVIDER", "FI_TCP_IFACE"):
        monkeypatch.delenv(var, raising=False)

    env = runtime_paths.runner_launch_env(None)

    assert "FI_PROVIDER" not in env
    assert "FI_TCP_IFACE" not in env


def test_the_users_own_mpi_settings_are_passed_through(monkeypatch):
    """Whatever the user set is theirs -- inherited, never rewritten."""
    monkeypatch.setenv("FI_PROVIDER", "verbs")
    monkeypatch.setenv("FI_TCP_IFACE", "eth0")

    env = runtime_paths.runner_launch_env(None)

    assert env["FI_PROVIDER"] == "verbs"
    assert env["FI_TCP_IFACE"] == "eth0"


# ---------------------------------------------------------------------------
# Probing an interpreter from inside the bundle
# ---------------------------------------------------------------------------
def _spawns(monkeypatch, func, *args):
    """Run ``func`` with subprocess.run stubbed, returning the env of each spawn."""
    seen = []

    class _Out:
        returncode = 0
        stdout = "Open MPI v4.1.2"
        stderr = ""

    def fake_run(cmd, **kwargs):
        seen.append(kwargs.get("env"))
        return _Out()

    monkeypatch.setattr(calibration_mod.subprocess, "run", fake_run)
    func(*args)
    return seen


@pytest.mark.parametrize(
    "probe, args",
    [
        ("_runtime_family", ("/somewhere/venv/bin/python",)),
        ("_launcher_family", ("/usr/bin/mpiexec",)),
    ],
)
def test_interpreter_probes_do_not_inherit_the_bundles_loader_path(monkeypatch, probe, args):
    """The MPI chip must be decided in the environment the *run* will use.

    PyInstaller points ``LD_LIBRARY_PATH`` at the unpacked bundle, and children
    inherit it. An external interpreter probed that way dlopens the **bundle's**
    ``libmpi`` (MPICH) rather than its own, so a venv whose mpi4py is built against
    the system Open MPI reports mpich, disagrees with the system launcher, and is
    told multi-core will not work -- while the run itself works fine, because it is
    spawned through ``runner_launch_env``, which strips those variables.

    Reported against the v0.3.0 desktop build: "MPI is working but the MPI tick
    mark isn't showing up". Reproduced exactly by running the probe with
    ``LD_LIBRARY_PATH`` set to the bundle dir -- runtime 'mpich' vs launcher
    'openmpi' -- and fixed by probing through ``subprocess_env``.
    """
    monkeypatch.setattr(calibration_mod, "is_frozen", lambda: True, raising=False)
    monkeypatch.setattr(runtime_paths, "is_frozen", lambda: True)
    monkeypatch.setenv("LD_LIBRARY_PATH", "/tmp/_MEIbundle")
    monkeypatch.delenv("LD_LIBRARY_PATH_ORIG", raising=False)

    envs = _spawns(monkeypatch, getattr(calibration_mod, probe), *args)

    assert envs, f"{probe} spawned nothing"
    for env in envs:
        assert env is not None, (
            f"{probe} inherited the parent environment; inside the bundle that is "
            "LD_LIBRARY_PATH pointing at the unpacked app")
        assert "LD_LIBRARY_PATH" not in env, (
            f"{probe} probed with the bundle's loader path, so an external "
            "interpreter would load the bundle's MPI instead of its own")
