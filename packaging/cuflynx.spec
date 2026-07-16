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

import importlib
import os
import sys
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

# The runner scripts must ship as *data*, not as frozen modules: they're executed
# by an external Python (`python runner.py config.json`), so they have to exist as
# real .py files on disk. runtime_paths.resource_path() finds them at the bundle
# root, and Python puts that dir on the runner's sys.path — so their apps/api
# sibling imports must sit beside them as data files too. local_sensitivity is the
# one such sibling (imported by sensitivity_runner); without it a local
# sensitivity run dies with "No module named 'local_sensitivity'" on any machine
# whose runner interpreter doesn't happen to have cuflynx-api installed.
# ...into a "runners/" SUBDIR, not the bundle root. The external interpreter puts
# the runner's own directory on sys.path[0]; if that were the bundle root (which
# holds the app's numpy/scipy/...), the runner would import the *bundle's* numpy
# instead of its own and crash with "numpy.core.multiarray failed to import". A
# dedicated subdir keeps the bundle's Python packages off the runner's path.
# runtime_paths.runner_path() resolves them here.
for runner in (
    "calibration_runner.py",
    "sensitivity_runner.py",
    "uq_runner.py",
    "local_sensitivity.py",
):
    datas.append((str(API_DIR / runner), "runners"))

# CPython's development headers. Myokit compiles a *CPython extension module* at
# run time, inside this frozen process — so the bundle has to carry Python.h and
# friends. distutils finds them via sysconfig's include path, and that path differs
# by platform, so the headers must land where the *frozen* interpreter will look:
#   - posix (Linux/macOS): <bundle>/include/python<X.Y>
#   - nt (Windows):        <bundle>/Include   (capital I, no version dir)
# Shipping to the posix location on Windows is why CVODE_myokit there died with
#   fatal error C1083: Cannot open include file: 'Python.h'
# even though MSVC ran fine. (Build machine needs python3-dev / the Xcode CLT / the
# Windows Python headers, which ship with the standard installer.)
_PY_INCLUDE = sysconfig.get_paths()["include"]
if not Path(_PY_INCLUDE, "Python.h").is_file():
    raise SystemExit(
        f"Python.h not found in {_PY_INCLUDE}. The bundle must ship CPython's "
        "headers so Myokit can compile models at run time. Install the Python "
        "development headers (e.g. `sudo apt install python3-dev`) and rebuild."
    )
if sys.platform == "win32":
    datas.append((_PY_INCLUDE, "Include"))
    # Linking the extension on Windows needs pythonXX.lib, which MSVC looks for in
    # <prefix>/libs. Without it the compile finds Python.h but fails at link.
    _py_libs = Path(sys.base_exec_prefix) / "libs"
    _lib_files = list(_py_libs.glob("python*.lib")) if _py_libs.is_dir() else []
    if not _lib_files:
        raise SystemExit(
            f"No python*.lib found in {_py_libs}. Windows needs the import library "
            "to link Myokit's compiled models. Use a standard python.org / "
            "actions-setup-python interpreter (it ships libs/pythonXX.lib)."
        )
    for _lib in _lib_files:
        datas.append((str(_lib), "libs"))
else:
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


# CUFLYNX_SUNDIALS_ROOT is searched FIRST when set. The macOS build uses it to
# point at a serial (non-MPI) Sundials built from source: Homebrew's Sundials is
# MPI-built and its libraries abort at run time with "MPI_Comm_dup() called
# before MPI_INIT" even for a serial model, so it can't be shipped. See the macOS
# "Build serial Sundials" step in release.yml.
_env_root = os.environ.get("CUFLYNX_SUNDIALS_ROOT")
_env_inc = [Path(_env_root) / "include"] if _env_root else []
_env_lib = [Path(_env_root) / "lib", Path(_env_root) / "lib64"] if _env_root else []

_inc_candidates = _env_inc + [Path(p) for p in myokit.SUNDIALS_INC] + [
    Path("/usr/include"), Path("/usr/local/include"),
    Path("/opt/homebrew/include"), Path("/opt/local/include"),
]
_lib_candidates = _env_lib + [Path(p) for p in myokit.SUNDIALS_LIB] + [
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

# Bundle the Sundials headers via a staging copy so we can patch one line without
# touching the build machine's system headers.
#
# Homebrew's macOS Sundials is built with MPI, so its sundials_config.h has
#   #define SUNDIALS_MPI_ENABLED 1
# which makes sundials_types.h `#include <mpi.h>`. Myokit compiles each model
# against these headers at the *user's* run time, where mpi.h isn't present, and
# CVODE_myokit then dies with "fatal error: 'mpi.h' file not found". Myokit only
# ever uses the SERIAL N_Vector, so MPI is genuinely unused — force the flag off.
# (Linux/Windows Sundials already ship it as 0, so the rewrite is a no-op there.)
import re  # noqa: E402
import shutil  # noqa: E402
import tempfile  # noqa: E402

_sundials_stage = Path(tempfile.mkdtemp(prefix="cuflynx_sundials_"))
for _sub in _HEADER_SUBDIRS:
    _d = _sundials_inc / _sub
    if _d.is_dir():
        shutil.copytree(_d, _sundials_stage / _sub)

_cfg = _sundials_stage / "sundials" / "sundials_config.h"
if _cfg.is_file():
    _cfg.write_text(
        re.sub(
            r"(#define\s+SUNDIALS_MPI_ENABLED\s+)1",
            r"\g<1>0",
            _cfg.read_text(),
        )
    )

for _sub in _HEADER_SUBDIRS:
    _sd = _sundials_stage / _sub
    if _sd.is_dir():
        datas.append((str(_sd), f"sundials/include/{_sub}"))

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
# collect_all() on a package that isn't installed returns EMPTY LISTS rather than
# raising — so a missing dependency silently produces a bundle without it, and the
# failure only shows up as a runtime error in the user's hands. That is exactly how
# v0.1.0 shipped with no casadi ("CasADi solver requested but CasADi is not
# available"): a dev machine had it installed for CA, the CI build machine did not.
# Fail the build instead.
# CA's analysis-path packages are bundled too, so the app runs SA/calibration/UQ
# itself (no external Python needed by default). mpi4py is imported unconditionally
# by CA's param_id modules, so it's required, not optional.
_ANALYSIS_PKGS = ("matplotlib", "emcee", "corner", "SALib", "seaborn", "statsmodels",
                  "schwimmbad", "nevergrad", "numdifftools", "sklearn", "tqdm", "mpi4py")
_REQUIRED = ("myokit", "libcellml", "casadi", "webview", "setuptools", "numpy",
             "scipy", "pandas", "sympy", "yaml", "ruamel.yaml", "rdflib", "pint",
             *_ANALYSIS_PKGS)
_missing = []
for pkg in _REQUIRED:
    try:
        importlib.import_module(pkg)
    except ImportError:
        _missing.append(pkg)
if _missing:
    raise SystemExit(
        f"Cannot build: these packages are required in the bundle but are not "
        f"installed in the build environment: {', '.join(_missing)}.\n"
        "The frozen app imports circulatory_autogen in-process, so CA's simulation-"
        "path dependencies must be present here. Run `pip install -e \".[desktop]\"` "
        "in apps/api and rebuild."
    )

for pkg in ("myokit", "libcellml", "casadi", "webview", "setuptools", "numpy"):
    pkg_datas, pkg_binaries, pkg_hidden = collect_all(pkg)
    datas += pkg_datas
    binaries += pkg_binaries
    hiddenimports += pkg_hidden

# CA's analysis stack. collect_all grabs each package's data + compiled libs +
# submodules (matplotlib's mpl-data/fonts, sklearn/statsmodels/scipy .so's,
# mpi4py's MPI extension). Several resolve submodules dynamically, so collecting
# submodules explicitly avoids "module not found" at runtime.
for pkg in _ANALYSIS_PKGS:
    pkg_datas, pkg_binaries, pkg_hidden = collect_all(pkg)
    datas += pkg_datas
    binaries += pkg_binaries
    hiddenimports += pkg_hidden

# CA imports mpi4py unconditionally, so the MPI runtime must be in the bundle for
# the app to run analysis with no MPI on the user's machine. On Linux/macOS the
# build pip-installs a self-contained MPICH (the `mpich` wheel) into <prefix>/lib:
#   <prefix>/lib/libmpi.so.12         the MPICH library mpi4py's MPI.mpich.so loads
#   <prefix>/lib/mpich/lib{fabric,uc*} its dependencies
# Flatten all of them to the bundle root; PyInstaller puts the root on the runtime
# loader path (LD_LIBRARY_PATH / DYLD), so libmpi and its deps resolve. On Windows
# there is no MPICH wheel, so Microsoft MPI's msmpi.dll (System32) is bundled.
_seen_mpi = set()


def _add_mpi_libs(directory, patterns):
    found = False
    if not directory or not Path(directory).is_dir():
        return found
    for pat in patterns:
        for lib in Path(directory).glob(pat):
            if lib.name.lower() not in _seen_mpi and (lib.is_file() or lib.is_symlink()):
                _seen_mpi.add(lib.name.lower())
                binaries.append((str(lib), "."))
                found = True
    return found


_prefix_lib = Path(sys.prefix) / "lib"
_found_mpi = False
# pip-MPICH: the library + its bundled deps (in the mpich/ subdir).
_found_mpi |= _add_mpi_libs(_prefix_lib, ("libmpi*.so*", "libmpi*.dylib"))
_found_mpi |= _add_mpi_libs(_prefix_lib / "mpich", ("*.so*", "*.dylib"))
# Fall back to a system MPI (e.g. a dev machine with OpenMPI) if no pip-MPICH.
for _d in (Path("/usr/lib/x86_64-linux-gnu"), Path("/usr/lib"), Path("/usr/local/lib"),
           Path("/opt/homebrew/lib"), Path("/opt/local/lib")):
    _found_mpi |= _add_mpi_libs(_d, ("libmpi*.so*", "libmpi*.dylib"))
if sys.platform == "win32":
    _found_mpi |= _add_mpi_libs(Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32",
                                ("msmpi*.dll",))
    _found_mpi |= _add_mpi_libs(os.environ.get("MSMPI_BIN", ""), ("msmpi*.dll",))
if not _found_mpi:
    raise SystemExit(
        f"MPI runtime not found (looked in {_prefix_lib} and system dirs). mpi4py "
        "needs it at run time. Install the pip `mpich` package (Linux/macOS) or "
        "Microsoft MPI (Windows) in the build environment."
    )

# casadi needs its native libraries to sit NEXT TO the _casadi extension module,
# not at the bundle root where PyInstaller normally flattens binaries. Without the
# original layout, `import casadi` fails inside the frozen app on Windows and CA
# reports "CasADi solver requested but CasADi is not available" — while the build
# itself looks perfectly healthy. Re-add the whole package tree preserving its
# structure; PyInstaller de-duplicates identical entries.
import casadi  # noqa: E402 - guaranteed importable by the _REQUIRED check above

_CASADI_DIR = Path(casadi.__file__).parent
_NATIVE_SUFFIXES = {".dll", ".so", ".dylib", ".pyd"}
for _f in _CASADI_DIR.rglob("*"):
    if not _f.is_file():
        continue
    _dest = str(Path("casadi") / _f.relative_to(_CASADI_DIR).parent)
    # `.so.3`-style versioned names have a numeric suffix, so match on the stem too.
    if any(s in _f.name for s in _NATIVE_SUFFIXES):
        binaries.append((str(_f), _dest))
    else:
        datas.append((str(_f), _dest))

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

# CA's analysis dependencies (emcee/SALib/nevergrad/matplotlib/mpi4py/...) are now
# BUNDLED, not excluded, so sensitivity / calibration / UQ run in the app's own
# interpreter (the exe re-invokes itself as the runner). Only genuine dead weight
# is excluded. tkinter is dropped because matplotlib defaults to the headless Agg
# backend here (MPLBACKEND=Agg is set before any pyplot import).
excludes = [
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
