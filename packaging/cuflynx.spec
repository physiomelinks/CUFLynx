# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for the CUFLynx desktop app. Build via ``python scripts/package.py``.

What goes in the bundle, and why
--------------------------------
CUFLynx has two execution tiers, and they have different dependency needs:

* **Live simulation** (sliders / plots) runs *in-process*: ``engine.py`` puts
  circulatory_autogen on ``sys.path`` at runtime and imports ``solver_wrappers``
  inside this very interpreter. So every module CA touches on the simulation path
  — myokit, libcellml, casadi, numpy — must be **inside the bundle**. PyInstaller
  cannot discover them by static analysis, because the import happens through a
  path chosen at runtime; hence the explicit ``collect_all`` / ``hiddenimports``.

* **Analysis** (calibration / sensitivity / UQ) runs *out-of-process*: the API
  spawns ``*_runner.py`` with a **user-chosen external Python** (Settings ->
  Python interpreter). That interpreter supplies emcee / SALib / nevergrad /
  mpi4py / matplotlib, so those are deliberately **excluded** here — bundling
  them would inflate the executable for code that never runs inside it.

circulatory_autogen itself is *not* bundled: it's selected at runtime via the
Settings "CA dir" picker. When CA becomes pip-installable it can simply be added
to the build environment and it will be collected like any other package — no
change to this split is needed.
"""

import sysconfig
from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_submodules

ROOT = Path(SPECPATH).parent  # noqa: F821 - SPECPATH is injected by PyInstaller
API_DIR = ROOT / "apps" / "api"
WEB_DIST = ROOT / "apps" / "web" / "dist"
ENTRY = ROOT / "apps" / "desktop" / "app.py"

if not WEB_DIST.is_dir():
    raise SystemExit(
        f"frontend not built: {WEB_DIST} is missing. Run `yarn build` in apps/web "
        "(scripts/package.py does this for you)."
    )

datas = [
    # The built Vue SPA. runtime_paths.frontend_dist() looks for it at "web/dist".
    (str(WEB_DIST), "web/dist"),
]
binaries = []
hiddenimports = []

# The runner scripts must ship as *data*, not as frozen modules: they are executed
# by an external Python (`python runner.py config.json`), so they have to exist as
# real .py files on disk. runtime_paths.resource_path() finds them at the bundle root.
for runner in ("calibration_runner.py", "sensitivity_runner.py", "uq_runner.py"):
    datas.append((str(API_DIR / runner), "."))

# CPython's development headers. Myokit compiles a *CPython extension module* at
# run time, inside this frozen process — so the bundle has to carry Python.h and
# friends. distutils looks for them at sysconfig's include path, which inside the
# bundle resolves to <bundle>/include/python<X.Y>, so that's where they must land.
# (Build machine therefore needs python3-dev / the Xcode CLT / the Windows Python
# headers, which ship with the standard installer.)
_PY_INCLUDE = sysconfig.get_paths()["include"]
if not Path(_PY_INCLUDE, "Python.h").is_file():
    raise SystemExit(
        f"Python.h not found in {_PY_INCLUDE}. The bundle must ship CPython's "
        "headers so Myokit can compile models at run time. Install the Python "
        "development headers (e.g. `sudo apt install python3-dev`) and rebuild."
    )
datas.append((_PY_INCLUDE, f"include/python{sysconfig.get_python_version()}"))

# Sundials (CVODE) — the ODE solver Myokit's generated C links against. Myokit
# needs its *headers* to compile and its *shared libraries* to link/load, and by
# default finds them on the host. Bundling both means a user doesn't have to
# install Sundials separately; the runtime hook repoints myokit.SUNDIALS_INC /
# SUNDIALS_LIB at these copies. Source dirs come from the build interpreter's own
# myokit config, so this follows however Sundials was installed here (apt, conda,
# brew, ...) rather than hardcoding a path.
import myokit  # noqa: E402 - the build env has it; the spec fails loudly if not

_SUNDIALS_HEADERS = ("sundials", "cvode", "cvodes", "nvector", "sunmatrix",
                     "sunlinsol", "sunnonlinsol")
_found_sundials_inc = False
for _inc in myokit.SUNDIALS_INC:
    for _sub in _SUNDIALS_HEADERS:
        _d = Path(_inc) / _sub
        if _d.is_dir():
            datas.append((str(_d), f"sundials/include/{_sub}"))
            _found_sundials_inc = True

_found_sundials_lib = False
for _libdir in myokit.SUNDIALS_LIB:
    for _lib in Path(_libdir).glob("libsundials_*"):
        if _lib.is_file() or _lib.is_symlink():
            # Into sundials/lib for the linker's -L, and to the bundle root (via
            # `binaries`) so the loader finds them through PyInstaller's search path.
            datas.append((str(_lib), "sundials/lib"))
            binaries.append((str(_lib), "."))
            _found_sundials_lib = True

if not (_found_sundials_inc and _found_sundials_lib):
    raise SystemExit(
        "Sundials (CVODE) headers/libraries not found via myokit.SUNDIALS_INC="
        f"{myokit.SUNDIALS_INC} / SUNDIALS_LIB={myokit.SUNDIALS_LIB}. Myokit needs "
        "them to compile models. Install Sundials in the build environment (e.g. "
        "`conda install sundials`, `brew install sundials`, or apt) and rebuild."
    )

# uvicorn resolves its loop/protocol implementations by string name at runtime.
hiddenimports += collect_submodules("uvicorn")

# Packages with data files / shared libraries that CA imports on the simulation
# path. myokit in particular ships the C templates it JIT-compiles models from —
# without its data files, every simulation fails.
#
# setuptools and numpy are here for a less obvious reason: Myokit *compiles a C
# extension at run time*, and that compile happens inside this frozen process. It
# needs setuptools' build_ext command (resolved dynamically via pkg_resources, so
# invisible to static analysis) and numpy's C headers (package data, not code).
for pkg in ("myokit", "libcellml", "casadi", "webview", "setuptools", "numpy"):
    pkg_datas, pkg_binaries, pkg_hidden = collect_all(pkg)
    datas += pkg_datas
    binaries += pkg_binaries
    hiddenimports += pkg_hidden

# The build_ext machinery Myokit reaches for when compiling a model. distutils
# and setuptools look their commands up *by name* (`get_command_class('build')`),
# so nothing imports them statically and PyInstaller can't infer them — every one
# has to be named. setuptools>=60 vendors distutils as setuptools._distutils and
# shims it into place, so collect both spellings.
for pkg in ("setuptools.command", "distutils", "setuptools._distutils"):
    hiddenimports += collect_submodules(pkg)
hiddenimports += ["pkg_resources", "_distutils_hack"]

# Imported by CA at runtime; invisible to static analysis for the same reason.
hiddenimports += [
    "numpy",
    "scipy",
    "scipy.integrate",
    "scipy.optimize",
    "pandas",
    "sympy",
    "yaml",
    "ruamel.yaml",
    "rdflib",
    "pint",
    # The API's own modules, imported via `from main import app` in the shell.
    "main",
    "engine",
    "calibration",
    "sensitivity",
    "uq",
    "export_pipeline",
    "model_codegen",
    "obs_options",
    "solver_options",
    "compiler_check",
    "runtime_paths",
]

# Analysis-only (they run in the user's external Python, never in here) plus the
# usual PyInstaller dead weight. Keeps the executable substantially smaller.
excludes = [
    "emcee",
    "corner",
    "SALib",
    "seaborn",
    "statsmodels",
    "nevergrad",
    "numdifftools",
    "schwimmbad",
    "sklearn",
    "mpi4py",
    "tkinter",
    "pytest",
    "IPython",
    "notebook",
]

a = Analysis(  # noqa: F821
    [str(ENTRY)],
    pathex=[str(API_DIR)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    # Repoints myokit's DIR_CFUNC at the bundled C templates; without it every
    # simulation fails with a FileNotFoundError on cmodel.h. See the hook.
    runtime_hooks=[str(Path(SPECPATH) / "rthook_myokit.py")],  # noqa: F821
    excludes=excludes,
    noarchive=False,
)
pyz = PYZ(a.pure)  # noqa: F821

exe = EXE(  # noqa: F821
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="CUFLynx",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    runtime_tmpdir=None,
    # One file, double-click. console=False would hide the terminal, but it also
    # hides startup errors (e.g. the missing-C-compiler warning) and breaks the
    # `--browser` mode's output, so keep a console on Windows for now.
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
