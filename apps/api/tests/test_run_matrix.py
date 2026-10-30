"""Every analysis CUFLynx can launch, in serial and in parallel.

CUFLynx offers four analyses that take a processor count -- calibration, sensitivity,
UQ and emulator training -- and each one builds its own command in its own
``*Manager.build_command``. The four are near-identical copies of the same eight lines,
which is the situation this file exists for: **four independent chances to silently stop
being parallel.**

The silence is the point. Every one of them ends with

    mpiexec = resolve_mpiexec(python)
    if mpiexec is None:
        _warn_no_mpiexec(num_cores)
        return base            # <-- a one-core run, reported as success

which is correct behaviour on a machine with no MPI (a user on Windows should get a
slow run, not an HTTP 500) and a silent failure in CI, where a "parallel" job that
quietly ran on one core passes green having tested nothing. That is the same shape as a
skipped test reading as a pass, and it is why the assertions below check the *command*
rather than only the outcome.

Two tiers, as everywhere else in this suite:

- **Unit** -- no Myokit, no MPI runtime needed. Table-driven over the four managers,
  discovered by inspection rather than listed, so a fifth analysis cannot be added
  without being covered. Runs in the ordinary ``backend-unit`` job.
- **Integration** -- marked ``integration``, and actually runs the analyses on the
  3compartment model at one core and at four. Needs myokit, libcellml, an installed
  libcuflynx and a real MPI. Runs in the ``integration-*`` jobs added alongside this
  file; before them, nothing in CUFLynx CI ran ``-m integration`` at all.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time

import pytest

import calibration as calibration_mod
import emulator as emulator_mod
import sensitivity as sensitivity_mod
import uq as uq_mod
from conftest import RESOURCES_DIR, upload_model

C3_MODEL_PATH = RESOURCES_DIR / "3compartment_flat.cellml"
C3_OBS_DATA_PATH = RESOURCES_DIR / "3compartment_obs_data.json"
C3_PARAMS_CSV_PATH = RESOURCES_DIR / "3compartment_params_for_id.csv"

#: The processor count the matrix uses for its parallel arm. Four rather than two
#: because four is what a user actually types, and because two ranks hide anything that
#: only shows up when the pool is larger than the trivial case.
PARALLEL_CORES = 4


# ---------------------------------------------------------------------------
# The table, discovered rather than restated
# ---------------------------------------------------------------------------
def _managers():
    """Every analysis manager that accepts a processor count.

    Discovered by asking each module for its singleton and checking it has a
    ``build_command``, so adding a fifth analysis puts it in this table automatically.
    Listing them by hand is how a new analysis ships with no parallel coverage and
    nobody notices -- the same reasoning as CA's table-driven entry-point test.
    """
    found = []
    for label, module in (
        ("calibration", calibration_mod),
        ("sensitivity", sensitivity_mod),
        ("uq", uq_mod),
        ("emulator", emulator_mod),
    ):
        manager = getattr(module, label, None)
        if manager is not None and hasattr(manager, "build_command"):
            found.append((label, manager))
    return found


MANAGERS = _managers()
MANAGER_IDS = [label for label, _ in MANAGERS]


def test_every_analysis_module_exposes_a_manager_with_build_command():
    """The discovery above must actually find all four.

    Without this, a rename turns every parametrised test below into zero tests and the
    file passes having checked nothing -- the failure mode this suite keeps hitting.
    """
    assert sorted(MANAGER_IDS) == ["calibration", "emulator", "sensitivity", "uq"], (
        f"expected four analysis managers, discovered {sorted(MANAGER_IDS)}. If an "
        f"analysis was renamed or added, update _managers() -- do not delete this "
        f"assertion, it is what stops the parametrised tests silently emptying."
    )


# ---------------------------------------------------------------------------
# Unit tier: the command, not the outcome
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("label,manager", MANAGERS, ids=MANAGER_IDS)
def test_one_core_runs_without_a_launcher(label, manager, tmp_path, monkeypatch):
    """num_cores=1 must not involve mpiexec at all."""
    monkeypatch.setattr(manager, "python", sys.executable, raising=False)
    cmd = manager.build_command({"num_cores": 1}, str(tmp_path / "config.json"))
    assert "mpiexec" not in " ".join(cmd), f"{label} used a launcher for a one-core run: {cmd}"


@pytest.mark.parametrize("label,manager", MANAGERS, ids=MANAGER_IDS)
def test_more_than_one_core_really_launches_mpiexec(label, manager, tmp_path, monkeypatch):
    """num_cores=N must produce ``mpiexec -n N ...`` when a launcher exists.

    The assertion is on the argv, deliberately. Asserting only that the run finished
    would pass just as happily against the silent one-core fallback, which is the
    regression this test exists to catch.
    """
    fake_launcher = tmp_path / "mpiexec"
    fake_launcher.write_text("#!/bin/sh\nexec \"$@\"\n")
    fake_launcher.chmod(0o755)
    module = sys.modules[type(manager).__module__]
    monkeypatch.setattr(
        module, "resolve_mpiexec", lambda python: str(fake_launcher), raising=True)
    cmd = manager.build_command(
        {"num_cores": PARALLEL_CORES}, str(tmp_path / "config.json"))
    assert cmd[0] == str(fake_launcher), f"{label} did not use the launcher: {cmd}"
    assert cmd[1:3] == ["-n", str(PARALLEL_CORES)], (
        f"{label} built {cmd[:3]}, expected the launcher then -n {PARALLEL_CORES}"
    )


@pytest.mark.parametrize("label,manager", MANAGERS, ids=MANAGER_IDS)
def test_a_missing_launcher_degrades_to_one_core_rather_than_erroring(
    label, manager, tmp_path, monkeypatch, capsys
):
    """No mpiexec must mean a slow run, not an HTTP 500 -- and it must say so.

    This behaviour is correct and deliberate (a Windows user with no MPI should still
    get a result), so it is pinned rather than removed. What it must not do is stay
    quiet: the warning is the only signal that a four-core request became a one-core
    run, and CI relies on it.
    """
    module = sys.modules[type(manager).__module__]
    monkeypatch.setattr(module, "resolve_mpiexec", lambda python: None, raising=True)
    cmd = manager.build_command(
        {"num_cores": PARALLEL_CORES}, str(tmp_path / "config.json"))
    assert "mpiexec" not in " ".join(cmd), f"{label} kept a launcher it could not find: {cmd}"
    warned = capsys.readouterr().err
    assert "mpiexec" in warned and str(PARALLEL_CORES) in warned, (
        f"{label} fell back to one core without warning. Silence here is how a "
        f"'parallel' CI job passes having run serially. Got: {warned!r}"
    )


# ---------------------------------------------------------------------------
# Integration tier: the analyses actually run, at one core and at four
# ---------------------------------------------------------------------------
def _mpi_available() -> bool:
    """A real launcher *and* a real mpi4py, from the same environment.

    Both halves matter: a PATH mpiexec from a different MPI than mpi4py bound aborts
    every rank at MPI_Init, which is the failure resolve_mpiexec() exists to avoid.
    """
    if shutil.which("mpiexec") is None:
        return False
    try:
        import mpi4py  # noqa: F401
    except ImportError:
        return False
    return True


requires_mpi = pytest.mark.skipif(
    not _mpi_available(), reason="no mpiexec + mpi4py in this environment")


def _setup_model_obs_params(client) -> str:
    model_id = upload_model(client, C3_MODEL_PATH)["model_id"]
    obs = json.loads(C3_OBS_DATA_PATH.read_text())
    resp = client.post(
        "/api/obs_data/upload", json={"model_id": model_id, "obs_data": obs})
    assert resp.status_code == 200, resp.text
    with open(C3_PARAMS_CSV_PATH, "rb") as fh:
        resp = client.post(
            f"/api/params_for_id/upload?model_id={model_id}",
            files={"file": (C3_PARAMS_CSV_PATH.name, fh, "text/csv")},
        )
    assert resp.status_code == 200, resp.text
    return model_id


def _wait(client, kind: str, job_id: str, timeout: float):
    offset, lines = 0, []
    deadline = time.time() + timeout
    while time.time() < deadline:
        state = client.get(f"/api/{kind}/{job_id}/status?offset={offset}").json()
        lines += state.get("lines", [])
        offset = state.get("next_offset", offset)
        if state.get("state") != "running":
            return state, lines
        time.sleep(0.1)
    raise AssertionError(
        f"{kind} job {job_id} did not finish within {timeout}s. Output:\n"
        + "\n".join(lines[-60:])
    )


# `recorded_commands` lives in conftest.py: the emulator's own parallel test needs it
# too, and a fixture defined in this module is not visible from another one.

def _assert_parallelism(seen, num_cores):
    """The command actually built must match the parallelism that was asked for."""
    assert seen, "no command was built at all -- the run never reached build_command"
    cmd = seen[-1]
    if num_cores > 1:
        assert "-n" in cmd and str(num_cores) in cmd, (
            f"asked for {num_cores} cores but the launched command was serial: {cmd}. "
            f"A run that quietly drops to one core still finishes and still reports "
            f"success, which is why this is asserted on the argv."
        )
    else:
        assert "mpiexec" not in " ".join(cmd), f"a one-core run used a launcher: {cmd}"


@pytest.mark.integration
@pytest.mark.parametrize("num_cores", [1, PARALLEL_CORES], ids=["one-core", "four-cores"])
def test_local_sensitivity_stays_single_process_whatever_num_cores_says(
    client, requires_simulation, num_cores, recorded_commands
):
    """Local (finite-difference) SA must run in one process even when asked for four.

    This is deliberate, not a limitation to route around: ``main.py`` forces
    ``num_cores = 1`` when ``method == "local"``, because only Sobol parallelises -- it
    splits sample evaluation across ranks, while a local FD sweep has nothing to fan out.

    Worth pinning precisely *because* it is surprising. The UI happily accepts a
    processor count here, so the natural assumption -- which this test made in its first
    draft, and which the argv guard caught -- is that asking for four gets four. If that
    override is ever removed by accident, a local SA would start launching ranks that
    duplicate each other's work and write over each other's output.

    The genuine parallel end-to-end coverage is
    ``test_calibration_runs_serial_and_parallel``: the GA really does split population
    evaluation across ranks, and it is the analysis the reported crash occurred in.
    """
    model_id = _setup_model_obs_params(client)
    settings = {
        "method": "local",
        "gradient_method": "FD",
        # About the model's current values, so no calibration is needed first. Omitting
        # this turns a four-second test into a multi-minute one.
        "nominal": "current",
        "rel_step": 0.05,
        "dt": 0.01,
        "num_cores": num_cores,
    }
    # Nested under "settings": the endpoint reads req.settings, so a flat body is
    # accepted with a 200 and every value silently ignored.
    resp = client.post(
        "/api/sensitivity/run", json={"model_id": model_id, "settings": settings})
    assert resp.status_code == 200, resp.text

    state, lines = _wait(client, "sensitivity", resp.json()["job_id"], timeout=900)
    joined = "\n".join(lines)
    assert state["state"] == "done", (
        f"local sensitivity asked for {num_cores} core(s) ended in state "
        f"{state['state']}:\n" + joined[-3000:]
    )
    assert state.get("indices"), f"no sensitivity indices returned:\n{joined[-2000:]}"
    assert "MPI_ABORT" not in joined, f"a rank aborted:\n{joined[-3000:]}"
    assert "Traceback" not in joined, f"a rank raised:\n{joined[-3000:]}"
    # One process in both arms -- that is the property under test.
    _assert_parallelism(recorded_commands, 1)


@pytest.mark.integration
@pytest.mark.parametrize("num_cores", [1, PARALLEL_CORES], ids=["serial", "parallel"])
def test_calibration_runs_serial_and_parallel(client, requires_simulation, num_cores,
                                              recorded_commands):
    """A real genetic-algorithm calibration at one core and at four.

    This is the configuration the reported crash was in: a four-rank 3compartment
    calibration launched from CUFLynx, which died on three of four ranks during
    ``import corner``. Nothing in either repo's CI ran a multi-rank calibration through
    the app, so there was nothing to catch it.

    The GA is the right analysis for the parallel arm because it is the one that
    genuinely uses the ranks -- it splits population evaluation across them, exactly as
    circulatory_autogen's ``run_param_id.sh`` does -- so a four-core run exercises the
    rank fan-out rather than merely starting four processes.

    Kept short on purpose (30 evaluations, DEBUG for a small population), matching
    ``test_calibration_3compartment_genetic_algorithm``. The question here is whether the
    parallel arm *works*, not whether it converges; convergence is CA's to test.
    """
    if num_cores > 1 and not _mpi_available():
        pytest.skip("no mpiexec + mpi4py in this environment")
    model_id = _setup_model_obs_params(client)
    settings = {
        "param_id_method": "genetic_algorithm",
        "num_calls_to_function": 30,
        "DEBUG": True,
        "dt": 0.01,
        "num_cores": num_cores,
    }
    resp = client.post(
        "/api/calibration/run", json={"model_id": model_id, "settings": settings})
    assert resp.status_code == 200, resp.text
    job_id = resp.json()["job_id"]

    state, lines = _wait(client, "calibration", job_id, timeout=900)
    joined = "\n".join(lines)
    assert state["state"] == "done", (
        f"calibration on {num_cores} core(s) ended in state {state['state']}:\n"
        + joined[-3000:]
    )
    # The failure being guarded against killed a rank and then the job. Both markers
    # matter: a run can be reported done while a rank died, and a rank can raise without
    # aborting.
    assert "MPI_ABORT" not in joined, f"a rank aborted:\n{joined[-3000:]}"
    assert "Traceback" not in joined, f"a rank raised:\n{joined[-3000:]}"
    assert state.get("cost") is not None, f"no cost was produced:\n{joined[-2000:]}"
    _assert_parallelism(recorded_commands, num_cores)
