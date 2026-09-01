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

circulatory_autogen itself **is** bundled now, as the pip-installable
``libcuflynx`` (#18) — see the ``collect_all("libcuflynx")`` below. So the app
runs with no CA directory set at all, and the Settings "CA dir" picker becomes an
override for developers pointing at a checkout rather than a precondition.

One consequence worth stating here, because it is invisible from the runtime
code: the pre-0.4.0 flat shim packages are deliberately not collected, so inside
the bundle the *only* spelling that resolves is the namespaced one, and there is
no ``<src>/param_id`` directory for a bare-name import to come off. Everything
therefore has to go through ``ca_imports`` (which ships into ``runners/`` for the
same reason) — a literal ``import operation_funcs`` works from a checkout and
silently fails here.
"""

import importlib
import os
import sys
import sysconfig
from pathlib import Path

from PyInstaller.utils.hooks import (collect_all, collect_data_files, collect_submodules,
                                     copy_metadata)

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
    # Live simulation runs here too when the user picks an interpreter (#167).
    "sim_worker_runner.py",
    # circulatory_autogen moved its modules under a ``libcuflynx.`` namespace and
    # CUFLynx has to import either layout (CA #437). One resolver, used by both
    # tiers -- so it ships here with the modules that use it.
    "ca_imports.py",
    # The fields the runners read out of CA's parsed dicts. A leaf like ca_imports --
    # stdlib and ca_imports only -- so it ships with them for the same reason.
    "ca_obs.py",
    "local_sensitivity.py",
    # Every runner writes its results in circulatory_autogen's own formats and the
    # managers read them back from there (#210); this module is the one place that
    # knows those formats, so both sides need it.
    "ca_run_history.py",
    # calibration_runner -> calibrated_model -> cellml_meta / params_for_id, to
    # save a calibrated CellML when a run finishes (issue #114).
    "calibrated_model.py",
    "cellml_meta.py",
    "params_for_id.py",
    # An obs_data.json is an object *or* a bare array of data_items; uq_runner
    # rewrites one (MLE cost_type) and reads its items through the single helper
    # that knows both shapes, rather than assuming the object form.
    "obs_data.py",
    # "use the emulator" -> CA engine kwargs, shared by all three analysis
    # runners so a study cannot be calibrated on a surrogate and analysed on the
    # solver without saying so (CA #333).
    "emulator_config.py",
    # Training the emulator itself.
    "emulator_runner.py",
):
    datas.append((str(API_DIR / runner), "runners"))

# The bundled example studies the "Start" dialog offers. Their filenames come
# from apps/api/examples.py -- the same manifest the route serves from -- because
# a hand-maintained copy here is exactly how issue #180 happened: the route read
# resources/, nothing collected it, and the packaged app 404'd with "example
# model file missing" while source ran fine. example_datas() raises if a listed
# example is absent, so the mismatch fails the build instead of the user.
sys.path.insert(0, str(API_DIR))
import examples  # noqa: E402 - needs API_DIR on sys.path

datas += examples.example_datas()

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
# itself (no external Python needed by default).
#
# mpi4py and schwimmbad come from libcuflynx's `[mpi]` extra, so the build
# environment must install `libcuflynx[mpi]` rather than the bare distribution --
# CA 0.4.0 made them optional (CA #435) and nothing under libcuflynx imports
# mpi4py at module scope any more, so a bare install leaves them out and this
# guard fires. They are still wanted *here*: the in-bundle analysis tier is the
# whole point of the list above, a multi-rank run needs the real MPI rather than
# CA's one-rank stub, and CA's pymc UQ backend imports mpi4py at module scope
# regardless of that stub.
_ANALYSIS_PKGS = ("matplotlib", "emcee", "corner", "SALib", "seaborn", "statsmodels",
                  "schwimmbad", "nevergrad", "numdifftools", "sklearn", "tqdm", "mpi4py")

# The two features that are still "bring your own Python": surrogate emulators
# (CA's do_emulation/use_emulator, and the Emulator tab's live prediction line) and the
# pyMC UQ backend. Off by default and built only for the extra Linux asset, because
# autoemulate pulls torch/gpytorch/pyro-ppl/lightgbm -- roughly 350 MB, taking the binary
# from 294 MB to 645 MB (measured, v0.4.1) -- and requires Python <3.13, which would pin
# the whole matrix's ceiling to one optional dependency.
#
# An env var rather than a second spec file: the two Linux bundles must differ in exactly
# this list and nothing else, and two spec files drift. `CUFLYNX_BUNDLE_FULL=1` is set by
# the `full: "1"` matrix entry in .github/workflows/release.yml.
#
# pytensor is named alongside pymc because it is not merely transitive here: it ships C
# templates that it compiles at run time, so its *data* files have to be collected, the
# same reason myokit needs its headers bundled.
_FULL = os.environ.get("CUFLYNX_BUNDLE_FULL") == "1"
_FULL_PKGS = ("autoemulate", "pymc", "arviz", "pytensor")

_REQUIRED = ("libcuflynx", "myokit", "libcellml", "casadi", "webview", "setuptools",
             "numpy", "scipy", "pandas", "yaml", "rdflib", "pint",
             *_ANALYSIS_PKGS,
             *(_FULL_PKGS if _FULL else ()))
# sympy and ruamel.yaml were dropped from this list when circulatory_autogen stopped
# declaring them (libcuflynx 0.4.0, CA #435): nothing under libcuflynx imports either
# one unguarded -- sympy is used only by the RICRI frequency operations, behind a
# try/except, and ruamel is only reached from a caller that already has it. Keeping
# them required would fail this build on any environment that installs libcuflynx and
# believes its metadata, which is every clean build environment.
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
        "The frozen app imports circulatory_autogen in-process, so CA's simulation "
        "and analysis dependencies must be present here. Run "
        f"`pip install -e \".[desktop,analysis{',full' if _FULL else ''}]\"` in apps/api "
        "and rebuild."
    )

for pkg in ("myokit", "libcellml", "casadi", "webview", "setuptools", "numpy"):
    pkg_datas, pkg_binaries, pkg_hidden = collect_all(pkg)
    datas += pkg_datas
    binaries += pkg_binaries
    hiddenimports += pkg_hidden

# circulatory_autogen itself, as the pip-installable `libcuflynx` (#18). Bundling it
# is what lets the app run with **no CA directory set**: `ca_imports` resolves CA
# through plain importlib, so an installed package is found with no sys.path entry,
# and a directory chosen in Settings still wins for a developer pointing at a
# checkout.
#
# collect_all rather than collect_submodules, because the package ships data that is
# not code: the CellML module library (`libcuflynx/generators/resources`, ~3 MB) that
# every generation reads, the 1D solver's Make_files, and the C++ templates. Without
# those the app imports CA fine and then fails at the first generate call -- the same
# failure mode `_REQUIRED` above exists to prevent.
#
# Its submodules are resolved dynamically in several places (the solver factory picks
# a backend by name, the cost/operation registries import by name), so the explicit
# hidden imports matter as much as the data.
# Deliberately NOT collect_all: libcuflynx is pure Python (253 files, zero .pyd/.so),
# so its "binaries" are empty -- but collect_all puts the package into PyInstaller's
# collected_packages, and find_binary_dependencies then *imports* each of those in an
# isolated subprocess to scan for DLL dependencies. Importing libcuflynx.solver_wrappers
# pulls in every backend, and on the Windows runner that crashed the child:
#     SubprocessDiedError: Isolated subprocess crashed while importing package
#     'libcuflynx.solver_wrappers'
# (Linux and both macOS runners survived it, which is what makes this easy to miss.)
#
# The two halves that actually matter are collected directly, and neither imports the
# package: collect_data_files walks the distribution's file list, and collect_submodules
# imports only `libcuflynx` itself and then walks __path__ with pkgutil. Same data, same
# hidden imports, no import of a backend at build time.
datas += collect_data_files("libcuflynx")
hiddenimports += collect_submodules("libcuflynx")
# The .dist-info too, which collect_all would have brought and collect_data_files does
# not: without it importlib.metadata.version("libcuflynx") raises inside the bundle, and
# "which engine is this app carrying" is a question worth being able to answer.
datas += copy_metadata("libcuflynx")
# The 11 deprecation shims are deliberately NOT collected. They exist for user code
# written against the pre-0.4.0 flat names and warn on import; nothing in CUFLynx
# imports them (ca_imports prefers the namespaced spelling precisely so the app never
# sees their DeprecationWarnings), and they are removed in libcuflynx 0.5.0.

# CA's analysis stack. collect_all grabs each package's data + compiled libs +
# submodules (matplotlib's mpl-data/fonts, sklearn/statsmodels/scipy .so's,
# mpi4py's MPI extension). Several resolve submodules dynamically, so collecting
# submodules explicitly avoids "module not found" at runtime.
for pkg in _ANALYSIS_PKGS:
    pkg_datas, pkg_binaries, pkg_hidden = collect_all(pkg)
    datas += pkg_datas
    binaries += pkg_binaries
    hiddenimports += pkg_hidden

# The emulation/UQ stack, for the full Linux bundle only. collect_all for the same reason
# as above -- autoemulate and pymc both resolve submodules dynamically, and pytensor ships
# the C templates it compiles against. torch, gpytorch, pyro and lightgbm are deliberately
# NOT listed: PyInstaller ships hooks for them and collect_all on torch in particular
# drags in test fixtures and unused CUDA payloads. They arrive as dependencies of the
# packages named here.
if _FULL:
    for pkg in _FULL_PKGS:
        pkg_datas, pkg_binaries, pkg_hidden = collect_all(pkg)
        datas += pkg_datas
        binaries += pkg_binaries
        hiddenimports += pkg_hidden

# scipy's *data files* are not collected by the loops above (scipy is neither in
# _ANALYSIS_PKGS nor the numpy/etc. collect_all list -- its modules and compiled
# libs come in transitively via PyInstaller's built-in scipy hook, but its data
# files do not). One of them is load-bearing at run time: scipy.stats.qmc.Sobol
# reads scipy/stats/_sobol_direction_numbers.npz to seed the sequence, so without
# it a Sobol run (e.g. multi-start gradient descent with start_sampling='sobol')
# fails with FileNotFoundError on that .npz. scipy swallows the error
# ("Exception ignored in: scipy.stats._sobol._initialize_v"), so the run limps on
# with un-seeded direction numbers rather than aborting -- a silent-wrong, not a
# crash. Collect scipy's runtime data files (excluding the bulky tests/data
# fixtures) so the .npz -- and any sibling runtime data -- is present.
datas += collect_data_files("scipy", excludes=["**/tests/**"])

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


# mpi4py 4.x ships one extension per MPI ABI (MPI.mpich.*.so, MPI.openmpi.*.so)
# and picks one at import via a custom finder that looks for the file in the
# mpi4py package dir. PyInstaller doesn't reliably place these ABI-suffixed
# extensions there, so the finder fails with "unsupported MPI ABI 'mpich'". Force
# every MPI.<abi> extension into the mpi4py/ dir so the finder resolves it.
import mpi4py  # noqa: E402
_m4p_dir = Path(mpi4py.__file__).parent
for _pat in ("MPI.*.so", "MPI.*.pyd", "MPI.*.dylib"):
    for _so in _m4p_dir.glob(_pat):
        binaries.append((str(_so), "mpi4py"))

# On Windows, mpi4py's __init__ does `__import__('_mpi_dll_path').install()` to put
# the MS-MPI DLL dir on the search path. That top-level module is generated at
# install time and isn't part of the mpi4py package, so PyInstaller misses it and
# `from mpi4py import MPI` dies with "No module named '_mpi_dll_path'". Collect it.
# (The bundled msmpi.dll is found via PyInstaller's own bundle-root DLL path, so
# the wrong build-time path _mpi_dll_path adds is harmless.)
if sys.platform == "win32":
    hiddenimports += ["_mpi_dll_path"]

_prefix_lib = Path(sys.prefix) / "lib"
_found_mpi = False
# pip-MPICH: the library + its bundled deps (in the mpich/ subdir). This is the
# canonical, relocatable runtime; take it and STOP, so a system OpenMPI on a dev
# machine (e.g. `brew install open-mpi`) isn't also pulled in — two different
# libmpis in one bundle would let mpi4py (built for the MPICH ABI) bind the wrong
# one at run time.
_found_mpi |= _add_mpi_libs(_prefix_lib, ("libmpi*.so*", "libmpi*.dylib"))
_found_mpi |= _add_mpi_libs(_prefix_lib / "mpich", ("*.so*", "*.dylib"))
if not _found_mpi and sys.platform != "win32":
    # No pip-MPICH: fall back to a system MPI (only reached on a dev build without
    # the analysis extra installed).
    for _d in (Path("/usr/lib/x86_64-linux-gnu"), Path("/usr/lib"), Path("/usr/local/lib"),
               Path("/opt/homebrew/lib"), Path("/opt/local/lib")):
        _found_mpi |= _add_mpi_libs(_d, ("libmpi*.so*", "libmpi*.dylib"))
if not _found_mpi and sys.platform == "win32":
    # Windows has no MPICH wheel; use Microsoft MPI's msmpi.dll.
    _found_mpi |= _add_mpi_libs(Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32",
                                ("msmpi*.dll",))
    _found_mpi |= _add_mpi_libs(os.environ.get("MSMPI_BIN", ""), ("msmpi*.dll",))
if not _found_mpi:
    raise SystemExit(
        f"MPI runtime not found (looked in {_prefix_lib} and system dirs). mpi4py "
        "needs it at run time. Install the pip `mpich` package (Linux/macOS) or "
        "Microsoft MPI (Windows) in the build environment."
    )

# --- PROTOTYPE: bundle MPICH's own launcher (Hydra) so multi-core analysis in the
# packaged app uses a launcher that matches the bundled MPICH runtime.
#
# Without this the app falls back to whatever `mpiexec` is on the user's PATH.
# When that is a different MPI (e.g. system Open MPI, whose launcher speaks PMIx)
# driving the bundle's MPICH ranks, every rank aborts at MPI_Init with
# "unsupported PMI version PMIx" -- or, on a machine with no MPI at all, there is
# no launcher and the run silently drops to a single core.
#
# Hydra is MPICH-only. It is NOT how Windows MPI works (Microsoft MPI uses its own
# mpiexec.exe + smpd, no Hydra) and there is no MPICH wheel for Windows or macOS
# Intel (pip installs a do-nothing 0.0.0 stub there). So bundle the launcher only
# where a real MPICH wheel provided one; other platforms keep the PATH fallback.
#
# mpiexec.hydra and hydra_pmi_proxy are standalone process-manager binaries that
# do NOT link libmpi, and mpiexec.hydra locates its proxy relative to its own
# path -- so bundling the two together in one dir is sufficient; no env or rpath
# work is needed (verified on Linux with a stripped environment).
_prefix_bin = Path(sys.prefix) / "bin"
_hydra = _prefix_bin / "mpiexec.hydra"
_hydra_proxy = _prefix_bin / "hydra_pmi_proxy"
if _hydra.is_file() and _hydra_proxy.is_file():
    # Land under mpi/bin/ (a dedicated subdir, not the flattened root) so the
    # launcher and its proxy sit beside each other and runtime_paths.bundled_mpiexec
    # can find them deterministically.
    binaries.append((str(_hydra), "mpi/bin"))
    binaries.append((str(_hydra_proxy), "mpi/bin"))
    # A plain `mpiexec` shim if present (hydra symlink/copy); harmless, keeps the
    # dir self-consistent.
    _mpiexec_shim = _prefix_bin / "mpiexec"
    if _mpiexec_shim.is_file():
        binaries.append((str(_mpiexec_shim), "mpi/bin"))
elif sys.platform != "win32":
    # Linux / macOS-arm64 are expected to have it; macOS-Intel legitimately won't
    # (0.0.0 stub wheel). Warn rather than fail so the build still produces a
    # working single-core-capable app.
    print(f"WARNING: no bundled Hydra launcher ({_hydra}); multi-core analysis in "
          "the packaged app will fall back to the PATH mpiexec.")

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
    # external_python: the AST-only model reader and the extra-figure store. The
    # store reaches matplotlib lazily, which is already collected in full for the
    # analysis stack (and MPLBACKEND=Agg is set before any pyplot import).
    "py_model_meta",
    "solver_plots",
    "compiler_check",
    "runtime_paths",
]

# CA's analysis dependencies (emcee/SALib/nevergrad/matplotlib/mpi4py/...) are now
# BUNDLED, not excluded, so sensitivity / calibration / UQ run in the app's own
# interpreter (the exe re-invokes itself as the runner). Only genuine dead weight
# is excluded. tkinter is dropped because matplotlib defaults to the headless Agg
# backend here (MPLBACKEND=Agg is set before any pyplot import).
_BASE_EXCLUDES = (
    "tkinter",
    "pytest",
    "IPython",
    "notebook",
    # Cython is a BUILD-time tool that nothing here needs at run time -- but leaving it in
    # the bundle breaks Myokit's CVODE backend, and only in the full bundle, because that
    # is the only tier whose dependencies pull Cython in.
    #
    # Myokit compiles each model to a C extension at run time by calling setuptools'
    # setup(), which resolves the build_ext command class. setuptools/command/build_ext.py
    # opens with
    #     try:
    #         from Cython.Distutils.build_ext import build_ext as _build_ext
    #         __import__('Cython.Compiler.Main')
    #     except ImportError:
    #         _build_ext = _du_build_ext
    # -- it catches ImportError only. Importing Cython.Compiler reads its utility templates
    # (Cython/Utility/*.c, *.cpp) from disk, and those are data files PyInstaller does not
    # collect, so frozen it raises FileNotFoundError instead:
    #     FileNotFoundError: /tmp/_MEIxxxxxx/Cython/Utility/CppSupport.cpp
    # which sails straight through the except and kills every CVODE_myokit simulation with
    # "CompilationError: Unable to compile".
    #
    # Excluding it restores the ImportError that the fallback is written for, so the full
    # bundle compiles models by exactly the same path as the other four assets. The
    # alternative -- collecting Cython/Utility so the import succeeds -- is worse: it would
    # switch this one asset over to Cython's build_ext, making the most fragile runtime path
    # in the app behave differently in the bundle nobody builds locally.
    "Cython",
)

# ...except that one of them is dead weight only in the ordinary bundles. autoemulate's
# core/plotting.py does `from IPython.display import ...` at module scope, unguarded, so
# excluding IPython does not trim a notebook helper out of the full bundle -- it makes
# `import autoemulate` raise ModuleNotFoundError, i.e. the entire reason that asset exists
# fails to load. Found by the runner-mode probe in scripts/analysis_smoke.py; every other
# check in the pipeline passed on that bundle, because nothing else imports autoemulate.
# `notebook` stays excluded: only IPython.display is reached.
_FULL_KEEPS = ("IPython",)

excludes = [m for m in _BASE_EXCLUDES if not (_FULL and m in _FULL_KEEPS)]

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
    # Left as None deliberately (issue #67). A per-user cache dir cannot be baked
    # here: PyInstaller 6.x does NOT expand ~/$VAR in runtime_tmpdir on POSIX, and
    # this spec runs on the *build* machine, so any absolute path would point at
    # the CI runner's home, not the user's. The onefile extraction is therefore
    # *relocated* at launch time (TMPDIR/TMP/TEMP) onto a per-build user-cache dir
    # in runtime_paths.runner_launch_env(), where a real per-user path is available,
    # so N MPI ranks don't exhaust the system temp. (There is no cross-rank
    # extraction sharing to bake either: 6.x has no env-triggerable reuse for
    # independent mpiexec processes -- see runtime_paths / packaging/README.md.)
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
    # Windows version resource (CompanyName / ProductName / version). A bare exe
    # with no metadata is a heuristic red flag for AV engines; this makes the
    # binary look like the real software it is. Ignored on Linux/macOS.
    version=(str(Path(SPECPATH) / "version_info.txt") if sys.platform == "win32" else None),
    # The same icon the browser tab uses, so the downloaded executable is
    # recognisable in Explorer. Windows only, like `version` above: PyInstaller
    # wants a .icns for a macOS bundle and converts .ico only when Pillow happens
    # to be in the build env, which is not something to rely on in CI. A macOS
    # .icns is a follow-up, not a silent maybe.
    icon=(str(ROOT / "apps" / "web" / "public" / "favicon.ico") if sys.platform == "win32" else None),
)
