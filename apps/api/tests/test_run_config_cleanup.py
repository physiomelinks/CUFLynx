"""The runner's config file is gone before the job says it finished.

``write_run_config`` puts a runner's payload in a private temp dir and hands the path
over as ``argv[1]``; ``clear_run_config`` removes it once the process has exited. Both
live in ``calibration.py`` and all four managers share them, so they share this ordering
too — which is why this is parametrised rather than written once against calibration.

The ordering is not cosmetic. ``_finalize`` publishes the job's terminal state, and that
is the instant a client stops polling and starts asserting: ``_wait`` in
``test_calibration.py`` returns the moment ``state != "running"``. With the cleanup
*after* the finalise there was a window — a couple of bytecodes wide, but a thread
switch is enough — in which a job reported ``done`` while its temp dir was still on
disk. ``test_the_run_config_never_lands_in_the_outputs_dir`` lost that race on a loaded
ubuntu runner and turned `main` red
(https://github.com/physiomelinks/CUFLynx/actions/runs/35799011240).

Ordering it the other way is also simply more correct: ``wait()`` has already returned,
so no MPI rank can still be reading the file, and a ``_finalize`` that raises no longer
leaks the temp dir.
"""

import os

import calibration as calibration_mod
import emulator as emulator_mod
import pytest
import sensitivity as sensitivity_mod
import uq as uq_mod

#: ``(module, manager, job factory)`` for every tier that spawns a runner.
MANAGERS = [
    pytest.param(
        calibration_mod,
        lambda: calibration_mod.calibration,
        lambda out: calibration_mod.CalibrationJob("job", out),
        id="calibration",
    ),
    pytest.param(
        emulator_mod,
        lambda: emulator_mod.emulator,
        lambda out: emulator_mod.EmulatorJob("job", out),
        id="emulator",
    ),
    pytest.param(
        sensitivity_mod,
        lambda: sensitivity_mod.sensitivity,
        lambda out: sensitivity_mod.SensitivityJob("job", out),
        id="sensitivity",
    ),
    pytest.param(
        uq_mod,
        lambda: uq_mod.uq,
        lambda out: uq_mod.UQJob("job", out),
        id="uq",
    ),
]


class _FinishedProc:
    """A runner that has already exited, with nothing left on its pipe."""

    returncode = 0
    stdout: list = []

    def wait(self):
        return 0


@pytest.mark.parametrize("module, manager_of, job_of", MANAGERS)
def test_the_run_config_is_removed_before_the_job_is_finalised(
    module, manager_of, job_of, monkeypatch, tmp_path
):
    manager = manager_of()
    job = job_of(str(tmp_path))
    job.proc = _FinishedProc()
    job.config_path = calibration_mod.write_run_config({"output_dir": str(tmp_path)}, "c.json")
    assert os.path.isfile(job.config_path), "precondition: the config was written"

    events = []
    real_clear = module.clear_run_config

    def spy_clear(path):
        events.append("cleared")
        real_clear(path)

    def spy_finalize(finalised_job, code):
        # What the assertion is really about: by the time a terminal state can be
        # published, the plumbing is already gone.
        events.append(
            "finalised with the config still present"
            if os.path.exists(finalised_job.config_path)
            else "finalised"
        )

    monkeypatch.setattr(module, "clear_run_config", spy_clear)
    monkeypatch.setattr(type(manager), "_finalize", staticmethod(spy_finalize))

    manager._reader(job)

    assert events == ["cleared", "finalised"], (
        f"{module.__name__}: expected the config to be cleared before the job is "
        f"finalised, got {events}"
    )
    assert not os.path.exists(job.config_path)
    assert not os.path.isdir(os.path.dirname(job.config_path))
