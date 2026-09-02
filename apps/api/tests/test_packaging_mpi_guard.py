"""A build must not be made against an MPI that cannot survive being frozen.

Issue #330: a locally built macOS app aborted on its first simulation with

    The MPI_Comm_dup() function was called before MPI_INIT was invoked
    Local abort before MPI_INIT completed

while the *downloaded* build of the same version worked. That message is
OpenMPI's, and the asymmetry is the diagnosis. CI installs ``.[analysis]``, which
brings the pip ``mpich`` wheel -- a single shared library. OpenMPI instead loads
its MCA components as plugins from ``<prefix>/lib/openmpi/*.so``, which
PyInstaller does not collect, so inside the bundle ``MPI_Init`` never completes
and the next MPI call aborts.

The trap is that the same OpenMPI works perfectly when running from source, so
nothing tells the developer until a user runs the binary. ``scripts/package.py``
now refuses that build; this pins the refusal, including that it stays quiet for
the environments where a build is legitimate.
"""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

import pytest

PACKAGE_SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "package.py"

OPENMPI_BANNER = (
    "Open MPI v4.1.5, package: Open MPI brew@Ventura Distribution, "
    "ident: 4.1.5, repo rev: v4.1.5, Feb 23, 2023"
)
MPICH_BANNER = "MPICH Version:\t4.1.2\nMPICH Release date:\tMon Jun 5 2023\n"


@pytest.fixture
def packager():
    """`scripts/package.py`, loaded by path -- `scripts/` is not a package."""
    spec = importlib.util.spec_from_file_location("cuflynx_packager", PACKAGE_SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    yield module
    sys.modules.pop(spec.name, None)


def _with_mpi(monkeypatch, banner: str | None, importable: bool = True):
    """Install a fake `mpi4py` reporting `banner` from Get_library_version()."""
    if not importable:
        monkeypatch.setitem(sys.modules, "mpi4py", None)  # import raises
        return
    mpi = types.SimpleNamespace(Get_library_version=lambda: banner)
    module = types.ModuleType("mpi4py")
    module.MPI = mpi
    monkeypatch.setitem(sys.modules, "mpi4py", module)
    monkeypatch.setitem(sys.modules, "mpi4py.MPI", mpi)


def test_an_open_mpi_build_is_refused(packager, monkeypatch, capsys):
    """The regression guard for #330 -- this build would ship broken."""
    _with_mpi(monkeypatch, OPENMPI_BANNER)

    with pytest.raises(SystemExit) as exc:
        packager.check_mpi_is_freezable()

    message = str(exc.value)
    # It must name the fix, not merely the fault: the whole point is that the
    # developer cannot see this failure any other way.
    assert "pip install mpich" in message
    assert "#330" in message


def test_an_mpich_build_is_allowed(packager, monkeypatch, capsys):
    """The environment the released binaries are actually built in."""
    _with_mpi(monkeypatch, MPICH_BANNER)

    packager.check_mpi_is_freezable()  # must not raise

    assert "MPICH" in capsys.readouterr().out


def test_no_mpi4py_is_not_a_build_failure(packager, monkeypatch, capsys):
    """A single-core bundle is a legitimate thing to build; MPI is optional."""
    _with_mpi(monkeypatch, None, importable=False)

    packager.check_mpi_is_freezable()  # must not raise

    assert "single-core" in capsys.readouterr().out


def test_an_unreadable_mpi_version_warns_rather_than_blocking(packager, monkeypatch, capsys):
    """Refusing to build because a probe misbehaved would be worse than the bug."""
    def _boom():
        raise RuntimeError("no MPI runtime")

    module = types.ModuleType("mpi4py")
    module.MPI = types.SimpleNamespace(Get_library_version=_boom)
    monkeypatch.setitem(sys.modules, "mpi4py", module)

    packager.check_mpi_is_freezable()  # must not raise

    assert "could not identify" in capsys.readouterr().out
