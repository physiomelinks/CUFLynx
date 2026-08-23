"""The MPI environment CI runs under, which only a bad runner would otherwise find.

Every job in `integration.yml` launches `mpiexec`, and the Azure VMs GitHub hands
out are not uniform: some present a MANA NIC with verbs support. UCX probes it,
cannot open it, and takes `MPI_Init` down with it before a single test runs:

    ib_iface.c:1316 UCX ERROR mana_0: iface ... failed to create UD QP ...
        failed: Operation not supported
    Abort(941770127): Fatal error in internal_Init_thread ... MPIDI_UCX_init_local

Because it depends on which VM the job lands on, it presents as an intermittent
red build unrelated to whatever was pushed -- which is the worst kind to diagnose
twice. `release.yml` already learned this the expensive way (the sibling "invalid
bandwidth 0.00" form took out 2 of 3 release builds) and pins `UCX_TLS`; these
tests are here so `integration.yml` cannot quietly lose it, and so a *new* MPI job
cannot be added outside the setting's reach.

Deliberately in the unit tier: it is a fact about a text file, and it needs to run
everywhere, including the machines that have no MPI at all.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

WORKFLOWS = Path(__file__).resolve().parents[3] / ".github" / "workflows"
INTEGRATION = WORKFLOWS / "integration.yml"

#: Enough for two ranks on one VM, and short of anything UCX has to probe a device
#: for. Matches what release.yml pins, so there is one string to remember.
EXPECTED_UCX_TLS = "tcp,self"


def _workflow(path: Path) -> dict:
    return yaml.safe_load(path.read_text()) or {}


def test_the_integration_workflow_keeps_ucx_off_the_fabric():
    doc = _workflow(INTEGRATION)
    assert doc.get("env", {}).get("UCX_TLS") == EXPECTED_UCX_TLS, (
        "integration.yml must set UCX_TLS at the workflow level: without it a runner "
        "whose VM exposes an RDMA device aborts MPI_Init before any test runs, and the "
        "failure looks like whatever was pushed rather than like the runner it is."
    )


def test_the_setting_is_workflow_wide_not_per_job():
    """A per-job setting is the version of this fix that rots: the next job to
    launch mpiexec is added without it and the flake comes back, on that job only."""
    doc = _workflow(INTEGRATION)
    assert "UCX_TLS" in (doc.get("env") or {}), "UCX_TLS is not set workflow-wide"
    for name, job in (doc.get("jobs") or {}).items():
        job_env = job.get("env") or {}
        assert "UCX_TLS" not in job_env, (
            f"job {name!r} overrides UCX_TLS locally; the workflow-level setting is "
            "what covers jobs nobody has written yet"
        )


@pytest.mark.parametrize("workflow", ["integration.yml", "release.yml"])
def test_every_workflow_that_touches_mpi_pins_it(workflow):
    """Both halves of the same lesson. release.yml pins it on the build step (where
    PyInstaller's isolated child imports mpi4py); integration.yml pins it workflow-
    wide (where mpiexec is actually launched). Either losing it is a returning flake."""
    text = (WORKFLOWS / workflow).read_text()
    assert "UCX_TLS" in text, f"{workflow} launches or imports MPI without pinning UCX_TLS"
