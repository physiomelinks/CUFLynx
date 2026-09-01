#!/usr/bin/env python3
"""Ask whether this machine can actually compile a CPython extension — and whether
CUFLynx's compiler detection agrees.

Background (the bug: ``command '/usr/bin/clang' failed with exit code 1``)
-------------------------------------------------------------------------
Only one backend compiles: ``CVODE_myokit`` turns each model into a native
CPython extension *at run time*, inside the frozen process, through setuptools'
``build_ext``. When that compile fails the user sees

    File "setuptools/_distutils/spawn.py", line 70, in spawn
    distutils.errors.DistutilsExecError: command '/usr/bin/clang' failed with exit code 1

with the compiler's own stderr nowhere in sight — a windowed macOS launch has no
console, so the one line that says *why* is lost.

``compiler_check.has_cpp_compiler()`` is meant to catch this at startup and
degrade to a compiler-free backend. On macOS it cannot, because it asks
``shutil.which``: ``/usr/bin/cc``, ``/usr/bin/gcc`` and ``/usr/bin/clang`` are
**always present** on macOS. They are ``xcrun`` shims shipped by the OS itself,
and they exit 1 with

    xcode-select: note: No developer tools were found

when no toolchain is installed behind them. So detection says "compiler present"
on a machine that cannot compile, and the failure surfaces later as the opaque
DistutilsExecError above.

There is a second, independent macOS-only route to the same traceback on a
machine that *does* have a working toolchain: the frozen interpreter carries the
**build machine's** ``_sysconfigdata``, and distutils reads ``CFLAGS`` from it.
If those flags name an ``-isysroot`` SDK that does not exist on the user's Mac
(a GitHub runner has Xcode.app's SDK; a typical user has only the Command Line
Tools' SDK, at a different path) CPython's ``_osx_support.compiler_fixup`` only
*warns* — it does not strip the flag — and clang then fails to find ``stdio.h``.
That one reproduces even with a healthy toolchain, which is why this probe
reports the sysconfig flags separately from the compile result.

Usage
-----
    python scripts/mac_toolchain_probe.py                   # report
    python scripts/mac_toolchain_probe.py --expect present  # and assert
    python scripts/mac_toolchain_probe.py --expect absent

``--expect`` asserts what a *real* compile does. Independently, the probe exits
non-zero whenever ``has_cpp_compiler()`` disagrees with that real compile: the
disagreement is the bug, and it is what the extended macOS workflow watches for.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import subprocess
import sys
import sysconfig
import tempfile
import textwrap
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps" / "api"))

# A trivial but *real* CPython extension. Trivial is the point: anything that
# fails here fails for a toolchain reason, never because of the source.
_EXT_SRC = textwrap.dedent(
    """
    #include <Python.h>
    static struct PyModuleDef m = {PyModuleDef_HEAD_INIT, "cuflynx_probe", NULL, -1, NULL};
    PyMODINIT_FUNC PyInit_cuflynx_probe(void) { return PyModule_Create(&m); }
    """
)


def _run(cmd: list[str]) -> dict:
    """Run a command, never raise; report what happened."""
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    except FileNotFoundError:
        return {"cmd": " ".join(cmd), "returncode": None, "error": "not found"}
    except Exception as exc:  # noqa: BLE001 - a probe must not die on its own probing
        return {"cmd": " ".join(cmd), "returncode": None, "error": repr(exc)}
    return {
        "cmd": " ".join(cmd),
        "returncode": p.returncode,
        "stdout": p.stdout.strip()[:2000],
        "stderr": p.stderr.strip()[:2000],
    }


def _isysroot_paths(*flag_strings: str) -> list[dict]:
    """Every ``-isysroot`` named by these flag strings, and whether it exists.

    This is the second failure mode: a frozen interpreter's baked-in CFLAGS can
    name an SDK that is absent on the user's machine, and CPython only warns.
    """
    found: list[dict] = []
    for s in flag_strings:
        if not s:
            continue
        parts = s.split()
        for i, part in enumerate(parts):
            if part == "-isysroot" and i + 1 < len(parts):
                sdk = parts[i + 1]
            elif part.startswith("-isysroot") and len(part) > len("-isysroot"):
                sdk = part[len("-isysroot"):]
            else:
                continue
            found.append({"sdk": sdk, "exists": os.path.isdir(sdk)})
    return found


def environment() -> dict:
    """Everything about this machine that decides whether a compile can work."""
    cc = sysconfig.get_config_var("CC") or ""
    cflags = sysconfig.get_config_var("CFLAGS") or ""
    ldshared = sysconfig.get_config_var("LDSHARED") or ""
    py_include = sysconfig.get_paths()["include"]
    env: dict = {
        "python": sys.version.split()[0],
        "executable": sys.executable,
        "frozen": hasattr(sys, "_MEIPASS"),
        "platform": platform.platform(),
        "machine": platform.machine(),
        "macos": platform.mac_ver()[0] or None,
        "sysconfig": {
            "CC": cc,
            "CFLAGS": cflags,
            "LDSHARED": ldshared,
            "MACOSX_DEPLOYMENT_TARGET": sysconfig.get_config_var(
                "MACOSX_DEPLOYMENT_TARGET"
            ),
            "include": py_include,
            "Python.h": os.path.isfile(os.path.join(py_include, "Python.h")),
        },
        # The flag that fails on a machine whose SDK sits somewhere else.
        "isysroot": _isysroot_paths(cflags, ldshared, os.environ.get("CFLAGS", "")),
        "env": {
            k: os.environ.get(k)
            for k in ("DEVELOPER_DIR", "SDKROOT", "CC", "CFLAGS", "ARCHFLAGS",
                      "MACOSX_DEPLOYMENT_TARGET")
            if os.environ.get(k) is not None
        },
        # `which` is exactly what has_cpp_compiler() asks, and on macOS these are
        # OS-shipped xcrun shims that exist with no toolchain behind them.
        "which": {name: shutil.which(name) for name in ("cc", "gcc", "clang", "cl")},
    }
    if sys.platform == "darwin":
        env["xcode_select_p"] = _run(["xcode-select", "-p"])
        env["xcrun_sdk_path"] = _run(["xcrun", "--show-sdk-path"])
        env["clang_version"] = _run(["/usr/bin/clang", "--version"])
    return env


def detection() -> dict:
    """What CUFLynx believes, via the module the app and installer share."""
    try:
        from compiler_check import compiler_status, has_cpp_compiler
    except Exception as exc:  # noqa: BLE001
        return {"importable": False, "error": repr(exc)}
    return {
        "importable": True,
        "has_cpp_compiler": bool(has_cpp_compiler()),
        "status": compiler_status(),
    }


def real_compile() -> dict:
    """Build a real CPython extension through setuptools' build_ext.

    Deliberately the same path Myokit takes (``setuptools`` ->
    ``distutils.ccompiler`` -> ``spawn``), so a failure here raises the same
    ``DistutilsExecError`` from the same ``spawn.py`` line the bug report shows.
    """
    work = Path(tempfile.mkdtemp(prefix="cuflynx_probe_"))
    src = work / "cuflynx_probe.c"
    src.write_text(_EXT_SRC)

    # Run it out-of-process: build_ext mutates global distutils state and writes
    # at C-fd level, and we want the compiler's own stderr — the line the
    # packaged app throws away.
    driver = work / "build_it.py"
    driver.write_text(
        textwrap.dedent(
            f"""
            from setuptools import Distribution, Extension

            ext = Extension("cuflynx_probe", sources=[{str(src)!r}])
            dist = Distribution({{"name": "cuflynx_probe", "ext_modules": [ext]}})
            dist.script_args = ["build_ext", "--build-lib", {str(work)!r},
                                "--build-temp", {str(work / 'tmp')!r}]
            dist.parse_command_line()
            dist.run_commands()
            """
        )
    )
    p = subprocess.run(
        [sys.executable, str(driver)],
        capture_output=True, text=True, cwd=str(work), timeout=600,
    )
    built = sorted(f.name for f in work.glob("cuflynx_probe*.so"))
    out = (p.stdout or "") + (p.stderr or "")
    return {
        "ok": p.returncode == 0 and bool(built),
        "returncode": p.returncode,
        "artifacts": built,
        # The whole point: keep the compiler's own words.
        "output": out.strip()[-6000:],
        "distutils_exec_error": "DistutilsExecError" in out,
        "spawn_traceback": "distutils/spawn.py" in out,
    }


def myokit_compile() -> dict:
    """The real thing, if Myokit is importable: compile and run one CVODE model.

    ``real_compile`` proves the toolchain; this proves the toolchain *plus* the
    bundled Sundials headers and libraries, which is the other half of what the
    packaged app needs and what a bare ``clang`` check cannot see.
    """
    try:
        import myokit  # noqa: PLC0415
    except Exception as exc:  # noqa: BLE001
        return {"attempted": False, "reason": repr(exc)}

    info = {
        "attempted": True,
        "myokit": getattr(myokit, "__version__", "?"),
        "DIR_CFUNC": getattr(myokit, "DIR_CFUNC", None),
        "SUNDIALS_INC": list(getattr(myokit, "SUNDIALS_INC", []) or []),
        "SUNDIALS_LIB": list(getattr(myokit, "SUNDIALS_LIB", []) or []),
    }
    info["sundials_headers_present"] = any(
        os.path.isfile(os.path.join(d, "cvodes", "cvodes.h"))
        or os.path.isfile(os.path.join(d, "sundials", "sundials_config.h"))
        for d in info["SUNDIALS_INC"]
    )
    # A one-state model is enough: the compile is what is under test.
    mmt = textwrap.dedent(
        """
        [[model]]
        c.x = 1
        [c]
        t = 0 bind time
        dot(x) = -0.5 * x
        [[protocol]]
        """
    )
    try:
        model, protocol, _ = myokit.parse(mmt)
        sim = myokit.Simulation(model, protocol)
        sim.run(1.0)
        info["ok"] = True
    except Exception:  # noqa: BLE001
        info["ok"] = False
        info["error"] = traceback.format_exc()[-6000:]
    return info


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--expect", choices=("present", "absent"),
                    help="assert what a real compile does on this machine")
    ap.add_argument("--label", default=os.environ.get("PROBE_LABEL", "probe"),
                    help="name for this toolchain state, echoed into the report")
    ap.add_argument("--json", type=Path, help="also write the full report here")
    ap.add_argument("--with-myokit", action="store_true",
                    help="additionally compile and run a Myokit CVODE model")
    ap.add_argument("--report-only", action="store_true",
                    help="never fail; just print the report (for diagnostics steps)")
    args = ap.parse_args()

    report = {
        "label": args.label,
        "environment": environment(),
        "detection": detection(),
        "compile": real_compile(),
    }
    if args.with_myokit:
        report["myokit"] = myokit_compile()

    text = json.dumps(report, indent=2, default=str)
    print(text)
    if args.json:
        args.json.write_text(text)

    can_compile = report["compile"]["ok"]
    detected = report["detection"].get("has_cpp_compiler")

    print(f"\n=== {args.label} ===", file=sys.stderr)
    print(f"real compile:            {'OK' if can_compile else 'FAILED'}", file=sys.stderr)
    print(f"has_cpp_compiler() says: {detected}", file=sys.stderr)
    if not can_compile and report["compile"]["distutils_exec_error"]:
        print("reproduced the reported DistutilsExecError", file=sys.stderr)
    for entry in report["environment"]["isysroot"]:
        if not entry["exists"]:
            print(f"MISSING SDK named by sysconfig flags: {entry['sdk']}", file=sys.stderr)

    if args.report_only:
        return 0

    failures = []
    # The bug, stated as an assertion: detection must match reality. A false
    # positive is the one that hurts — the app promises CVODE_myokit and then
    # dies inside distutils with no console to print the reason to.
    if report["detection"].get("importable") and detected is not None and detected != can_compile:
        failures.append(
            f"compiler detection says {detected} but a real compile "
            f"{'succeeded' if can_compile else 'failed'} — "
            f"has_cpp_compiler() is wrong on this machine"
        )
    if args.expect == "present" and not can_compile:
        failures.append("expected a working compiler, but the build failed")
    if args.expect == "absent" and can_compile:
        failures.append("expected no working compiler, but the build succeeded")

    for f in failures:
        print(f"::error::[{args.label}] {f}", file=sys.stderr)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
