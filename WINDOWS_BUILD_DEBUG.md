# Debugging the Windows release build — what is already known

**Symptom.** `python scripts/package.py` fails on Windows only, during PyInstaller's
`find_binary_dependencies`:

```
PyInstaller.isolated._parent.SubprocessDiedError: Isolated subprocess crashed while
importing package 'libcuflynx.solver_wrappers'!
Package import list: [~700 packages]
```

Linux and both macOS runners build and pass their smoke tests. Only Windows fails.

**When it started.** CUFLynx #269 bundled circulatory_autogen as `libcuflynx` (issue #18).
Before that the package was not in the bundle at all and Windows built fine. So the trigger is
libcuflynx being collected — but see "ruled out" below, because the obvious cause is not it.

## Reproduce

```powershell
cd CUFLynx
python -m venv venv
venv\Scripts\pip install -e "apps/api[desktop,analysis]"
cd apps\web; yarn install; yarn build; cd ..\..
venv\Scripts\python scripts\package.py --clean --no-build
```

Failure is in the PyInstaller step, a few minutes in. `--no-build` skips the frontend rebuild.

## Already ruled out — please do not re-derive

- **It is not `collect_all` putting the package into `collected_packages`.** That was my first
  fix (now merged) and it did not help: `collected_packages` is derived from everything
  *bundled*, so the `collect_submodules` hidden imports still put it there. The change was
  still worth keeping — libcuflynx is pure Python, so the binary scan collects nothing, and
  the bundle got 1 MB smaller — but it is not the cause.
- **It is not what `solver_wrappers/__init__.py` imports.** Module scope is only
  `libcuflynx.solver_wrappers.python_solver_helper` (numpy, scipy.integrate) and
  `libcuflynx.utilities.mpi_utils` (`os` only). Nothing exotic.
- **It is not an unguarded risky import.** An AST sweep of every shipped module found *no*
  module-scope import of `aadc`, `opencor`, `mpi4py`, `torch`, `autoemulate` or `pymc` — all
  are lazy or behind try/except.
- **It is not a missing dependency.** That was a separate, earlier failure (`libcuflynx` was
  collected but never declared) and is fixed; that one failed on all four runners with a clear
  "Cannot build: ... not installed" message, which this is not.

## Most likely remaining explanations

The crash names ~700 packages imported into one subprocess. `libcuflynx.solver_wrappers` may be
where it *died* rather than what *killed* it. Worth testing in this order:

1. **Reproduce the subprocess by hand.** The isolated child does roughly:
   `python -c "import <each collected package>"`. Try importing the whole list, then bisect.
   If it dies without a Python traceback, it is a native crash — a DLL conflict or an access
   violation, not an exception.
2. **`libcuflynx.scripts.*` and `libcuflynx.coupler`/`solver1d`.** `collect_submodules` adds
   ~100 submodules the app never imports in-process. Narrowing the filter to what is actually
   used would shrink the surface and is a plausible fix, but it is a hypothesis, not a
   diagnosis.
3. **Windows handle/memory limits in the isolated child.** ~700 imports including scipy,
   sklearn, statsmodels, nevergrad, casadi, myokit and matplotlib is a lot for one process.
4. **mpi4py + MS-MPI.** `MPI4PY_RC_INITIALIZE: "0"` is already set in `release.yml` for a
   *previous* SubprocessDiedError (a UCX crash on Linux). Confirm it is actually in the
   environment for this phase too.

## Useful context

- `packaging/cuflynx.spec` — `_REQUIRED` fails the build when a bundled package is missing;
  the libcuflynx collection is near the `collect_all` block for myokit/libcellml/casadi.
- `.github/workflows/release.yml` — the build step and its env, including the existing
  mpi4py workaround and why it exists.
- The Linux binary from this same commit builds and passes both smoke tests: config with no CA
  directory (39 operations, 8 cost types) and an end-to-end simulation of the bundled example
  (1001 samples, 27 outputs). So the spec is not wrong in general.

## What "fixed" looks like

`python scripts\package.py --clean` produces `dist\CUFLynx.exe`, and running it with no CA
directory configured reports `ca_exists: true` with ~39 operations rather than the 9-entry
hardcoded fallback.
