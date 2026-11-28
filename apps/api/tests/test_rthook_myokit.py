"""The PyInstaller runtime hook points Myokit at the bundle with paths Myokit can use.

Myokit 1.41 builds its C template paths as ``myokit.DIR_CFUNC / SOURCE_FILE``. A
string there fails every CellML simulation in the packaged app with
``TypeError: unsupported operand type(s) for /: 'str' and 'str'``, so the hook must
set Paths, which the older versions' ``os.path.join`` also takes.
"""

import os
import runpy
import sys
import types
from pathlib import Path

HOOK = Path(__file__).resolve().parents[3] / "packaging" / "rthook_myokit.py"


def test_hook_sets_myokit_dirs_that_join_with_slash_and_os_path(tmp_path, monkeypatch):
    fake = types.ModuleType("myokit")
    monkeypatch.setitem(sys.modules, "myokit", fake)
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)

    runpy.run_path(str(HOOK))

    sim = tmp_path / "myokit" / "_sim"
    assert fake.DIR_CFUNC / "cvodessim.c" == sim / "cvodessim.c"  # Myokit >= 1.41
    assert os.path.join(fake.DIR_CFUNC, "cvodessim.c") == str(sim / "cvodessim.c")  # older
    assert fake.DIR_DATA / "beeler-1977.mmt" == tmp_path / "myokit" / "_bin" / "beeler-1977.mmt"
    assert fake.DIR_MYOKIT == tmp_path / "myokit"
