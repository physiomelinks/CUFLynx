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
# needs its *headers* to compile and its *libraries* to link/load. Bundling both
# means the user doesn't have to install Sundials; the runtime hook repoints
# myokit.SUNDIALS_INC / SUNDIALS_LIB at these copies.
#
# We *search* rather than trust myokit.SUNDIALS_INC/LIB, because those are only
# hints and are wrong on two of the three platforms we ship:
#   - Linux: myokit hard-codes /usr/local/*, but apt's libsundials-dev installs to
#     /usr/include + /usr/lib/<triplet>. (Myokit still works there because the
#     compiler searches those by default — but we need the real location to copy.)
#   - Windows: myokit ships its own Sundials under myokit/_bin/sundials-win-vs,
#     and names the libs `sundials_cvodes.lib` — no "lib" prefix.
import myokit  # noqa: E402 - the build env has it; the spec fails loudly if not

_HEADER_SUBDIRS = ("sundials", "cvode", "cvodes", "nvector", "sunmatrix",
                   "sunlinsol", "sunnonlinsol")
# Loadable at run time (must reach the bundle root, for the dynamic loader) vs
# link-time-only import libs / static archives (only need to sit under -L).
_SHARED_SUFFIXES = (".so", ".dylib", ".dll")


def _looks_like_sundials_include(d: Path) -> bool:
    return (d / "cvodes" / "cvodes.h").is_file() or (
        d / "sundials" / "sundials_config.h"
    ).is_file()


def _sundials_libs_in(d: Path) -> list:
    # libsundials_cvodes.so (unix) and sundials_cvodes.lib/.dll (windows).
    return sorted(
        f for pat in ("libsundials_*", "sundials_*") for f in d.glob(pat)
        if f.is_file() or f.is_symlink()
    )


_inc_candidates = [Path(p) for p in myokit.SUNDIALS_INC] + [
    Path("/usr/include"), Path("/usr/local/include"),
    Path("/opt/homebrew/include"), Path("/opt/local/include"),
]
_lib_candidates = [Path(p) for p in myokit.SUNDIALS_LIB] + [
    Path("/usr/lib"), Path("/usr/lib64"), Path("/usr/local/lib"), Path("/usr/local/lib64"),
    Path("/opt/homebrew/lib"), Path("/opt/local/lib"),
] + sorted(Path("/usr/lib").glob("*-linux-gnu"))  # Debian/Ubuntu multiarch triplet

_sundials_inc = next((d for d in _inc_candidates if d.is_dir() and _looks_like_sundials_include(d)), None)
_sundials_lib, _sundials_lib_files = next(
    ((d, libs) for d in _lib_candidates if d.is_dir() and (libs := _sundials_libs_in(d))),
    (None, []),
)

if _sundials_inc is None or not _sundials_lib_files:
    raise SystemExit(
        "Sundials (CVODE) not found. Myokit needs its headers to compile models "
        "and its libraries to link them.\n"
        f"  headers: {'ok: ' + str(_sundials_inc) if _sundials_inc else 'NOT FOUND'}\n"
        f"  libs:    {'ok: ' + str(_sundials_lib) if _sundials_lib_files else 'NOT FOUND'}\n"
        f"  searched (inc): {[str(p) for p in _inc_candidates]}\n"
        f"  searched (lib): {[str(p) for p in _lib_candidates]}\n"
        "Install Sundials in the build environment (apt install libsundials-dev, "
        "brew install sundials, conda install sundials) and rebuild."
    )

for _sub in _HEADER_SUBDIRS:
    _d = _sundials_inc / _sub
    if _d.is_dir():
        datas.append((str(_d), f"sundials/include/{_sub}"))

for _lib in _sundials_lib_files:
    # Under sundials/lib for the linker's -L (import libs and static archives
    # only ever need to be here)...
    datas.append((str(_lib), "sundials/lib"))
    # ...and shared libraries also at the bundle root, where the dynamic loader
    # looks (PyInstaller puts its search path there).
    if any(s in _lib.name for s in _SHARED_SUFFIXES):
        binaries.append((str(_lib), "."))

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
