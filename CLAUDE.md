# cellml_file_slider_visualization — Agent summary

## Purpose

Interactive **manual parameter exploration** for CellML models: sliders change constants, simulations update plots, and experimental CSV data can be overlaid for rough calibration before formal parameter identification.

## Test fixtures (`resources/`)

| Artifact | Role |
|----------|------|
| `resources/BG_MWC_Huang-Peskin_SS.cellml` | Primary test model (CellML 1.1, component `main`; `main/p_o2` is state, `main/alpha_o2` is parameter) |
| `resources/Lotka_Volterra_forced.cellml` | Integration test model (CellML 2.0, component `Lotka_Volterra_module`; params `alpha`,`beta`,`delta`,`gamma`) |
| `resources/Lotka_Volterra_obs_data.json` | Test obs_data fixture (1 experiment, 2 constant data_items for `x_max` / `y_max`) |
| `resources/Lotka_Volterra_params_for_id.csv` | Test params fixture (4 rows, vessel `Lotka_Volterra_module`) |
| `resources/*.csv` (e.g. Dash2016, winslow_rw2) | Experimental traces for overlay |

## Architecture

```
apps/web/          Vue 3 + Vite + PrimeVue
apps/api/          FastAPI, depends on sibling circulatory_autogen
apps/desktop/      pywebview shell (native window around the same server)
packaging/         PyInstaller spec + runtime hooks (single-file executable)
```

**Backend engine (target):** [circulatory_autogen](https://github.com/...) `protocol_runners.ProtocolRunner` + `solver_wrappers.get_simulation_helper` (Myokit CVODE). Not the in-browser RK4.

**Reference implementation:** sibling repo `ICUHealthy` — FastAPI + cached `get_simulation_helper`, `helper.set_param_vals(param_names, param_vals)`, `helper.run()`, `helper.get_results()`.

**Config formats (circulatory_autogen):**

- `obs_data.json` — `protocol_info` (experiments/subexperiments) + `data_items` (ground truth / comparison targets) + optional `prediction_items`.
- `*_params_for_id.csv` — columns: `vessel_name`, `param_name`, `param_type`, `min`, `max`, `name_for_plotting`; full Myokit names are `vessel_name/param_name`.

Docs: `circulatory_autogen/tutorial/docs/parameter-identification.md`, `circulatory_autogen/claude.md`, `src/utilities/obs_data_helpers.py`.

**Locating circulatory_autogen.** The source dir is resolved via the
`CIRCULATORY_AUTOGEN_SRC` env var, defaulting to the sibling clone. It is
selectable at runtime from the **Settings popup** (gear icon) "CA dir" picker (`POST /api/config`
sets the env var + `engine.reset()`); subprocess runs inherit it, the in-process
engine picks it up before its first sim (module caching means a mid-session
switch fully re-points the live-plot engine only after a restart).
**Planned:** once `circulatory_autogen` is pip-installable, default to the
**installed package** instead of the sibling dir — but keep the CA-dir override
so developers can point at a local checkout. (See issue #18.)

## Key files

- `apps/web/src/App.vue` — main UI (tabs: Parameters · Sensitivity · Calibration · UQ; center: Output plots · Progress · Analysis)
- `apps/api/main.py` — FastAPI app: `/api/*` routes + serves the built frontend
- `scripts/install.py`, `scripts/run.py` — cross-platform setup + single-server launcher
- `scripts/package.py` — build the single-file desktop executable (see below)
- `apps/desktop/app.py` — pywebview shell; `packaging/cuflynx.spec` — PyInstaller spec
- `README.md` — user-facing quick start

**Analysis backends** (one API module + runner each, plus a Vue panel):

- `apps/api/sensitivity.py` / `sensitivity_runner.py` — global **Sobol** sensitivity; `local_sensitivity.py` — local **finite-difference** sensitivity (`d ln Y/d ln P` about a nominal point: current values / reused calibration best fit / bounds centre; optional "run calibration first"). UI: `SensitivityPanel.vue`; results render in `AnalysisPanel.vue` (S1/ST/local heatmaps).
- `apps/api/calibration.py` / `calibration_runner.py` — GA parameter identification; `CalibrationPanel.vue` (also emits live settings reused by local-sensitivity "run calibration first").
- `apps/api/uq.py` / `uq_runner.py` — uncertainty quantification; `UQPanel.vue`.

**GUI config editing** (edit CA config files in the browser → download dated copy → apply immediately):

- **obs_data.json** — `EditObsDataDialog.vue` + `apps/web/src/lib/obsDataJson.js`; edits `data_items`/`prediction_items` (incl. `source`/`comment` notes) and embeds `ProtocolInfoEditor.vue` (+ `lib/protocolInfo.js`) for `protocol_info` (experiments, params_to_change, ramp/pulse/step traces, time-view plots). Dropdown vocabularies come from `apps/api/obs_options.py` (`GET /api/obs_data/options`), which introspects CA registries — **never hardcode** operations/cost_types/data_types/plot_types.
- **params_for_id.csv** — `EditParamsDialog.vue` + `apps/web/src/lib/paramsCsv.js`; edits ranges/selection, writes a dated CSV, can apply best-fit to sliders.

## Desktop packaging (pywebview + PyInstaller)

**Current shipping model:** one double-clickable executable per OS, built by
`python scripts/package.py` → `dist/CUFLynx[.exe]` (~420 MB), published by
`.github/workflows/release.yml` on a `v*` tag.

**This is deliberately a thin shell, not a second frontend.** `apps/desktop/app.py`
starts the *same* uvicorn + `main:app` and points a pywebview window at
`http://127.0.0.1:<free port>`. It uses **no** pywebview JS bridge and no
`window.pywebview` APIs, so **the web-app path stays first-class** — the intended
future is to drop the shell and serve the same app remotely. Keep it that way:
**never** reach for pywebview APIs from Vue, and never let the frontend assume a
local filesystem/backend. `--browser` runs server-only and opens a normal browser.

**What is and isn't bundled.** The app has two execution tiers with different needs:

| Tier | Runs | Deps come from |
|------|------|----------------|
| Live simulation (sliders/plots) | **in-process** (`engine.py` imports CA's `solver_wrappers`) | **bundled**: myokit, libcellml, casadi, numpy, scipy, pandas |
| Calibration / sensitivity / UQ | **subprocess** (`*_runner.py`) | the **user's own Python**, picked in Settings (emcee, SALib, nevergrad, mpi4py…) |

`circulatory_autogen` itself is **not** bundled — it's chosen at runtime (Settings
→ CA dir). When CA becomes pip-installable, add it to the build env and it gets
collected like any other package; no change to this split. (Issue #18.)

**Frozen-app hazards — all fixed; do not regress:**

- **`sys.executable` is the bundle, not a Python.** A naive
  `Popen([sys.executable, runner.py, …])` *relaunches the GUI*. Always go through
  `runtime_paths.default_python()`, which returns `None` when frozen so the caller
  falls back to a user-chosen interpreter. Never reintroduce bare `sys.executable`
  in `calibration.py` / `sensitivity.py` / `uq.py`.
- **`__file__` doesn't point at real files.** Use `runtime_paths.resource_path()` /
  `frontend_dist()`. The `*_runner.py` scripts ship as **data**, not frozen modules,
  because an *external* Python has to execute them as files.
- **Myokit JIT-compiles a CPython extension at run time, inside the frozen
  process.** That drags in things static analysis can't see, all handled in
  `packaging/cuflynx.spec` + `packaging/rthook_myokit.py`:
  - Myokit derives `DIR_CFUNC` from `abspath(dirname(inspect.getfile(...)))`, which
    in a bundle resolves against the **CWD** → `FileNotFoundError: cmodel.h`. The
    runtime hook repoints `DIR_CFUNC` / `DIR_DATA` (they're read at call time).
  - The compile needs **setuptools/distutils command modules** (looked up *by name*,
    so every one must be a hidden import) and **CPython's headers** (`Python.h`),
    which the spec bundles at `include/python<X.Y>`.
  - **Sundials/CVODE headers + libs are bundled** and the hook repoints
    `myokit.SUNDIALS_INC` / `SUNDIALS_LIB`, so users needn't install Sundials.
- **A C compiler is still required on the user's machine** and cannot be bundled
  away. `compiler_check.py` detects it; `GET /api/config` returns `cpp_compiler:
  {present, hint}` and `App.vue` shows a banner. `scripts/install.py` shares the
  same detection — keep them in one place.

**Settings persist** (`settings_store.py` → user config dir; `CUFLYNX_CONFIG_DIR`
overrides, and the test suite points it at a tmp dir). `ca_dir` and `python_path`
*must* persist: the frozen app has no sibling CA checkout and no default
interpreter, so without this the user reconfigures on every launch.

## Conventions for agents

- Prefer **minimal diffs**; match ICUHealthy / circulatory_autogen patterns when adding API or UI.
- Do not extend the custom in-browser CellML parser for new features — delegate simulation to **protocol_runner**.
- Parameter names for Myokit must use **`component/param`** form from `params_for_id` (`PrimitiveParsers.get_param_id_info`).
- Slider debouncing: interactive exploration needs low-latency sim; protocol runs may take seconds on first compile (cache helper like ICUHealthy `acquire_helper`).

## Security caveats (localhost-only assumptions)

The backend assumes a single-user, localhost deployment and exposes the host filesystem to any client that can reach the API:

- **`GET /api/fs/list`** (`apps/api/main.py`) — the in-app file/folder browser (Python interpreter + outputs dir pickers) lists arbitrary server directories, defaulting to `$HOME`. No path confinement.
- **`config_outputs_dir`** (calibration) — writes calibration outputs to any absolute path the client supplies.

These are acceptable for the current local use. **If the API is ever served beyond localhost, gate/confine both** under a configured root (and authenticate).

## Related repos (local paths may vary)

| Repo | Use |
|------|-----|
| `circulatory_autogen` | `ProtocolRunner`, `ProtocolExecutor`, `get_simulation_helper`, `obs_data.json` / `params_for_id` parsers |
| `ICUHealthy` | Example FastAPI + PrimeVue + `set_param_vals` integration |

## Tests

There are two suites — **keep both green** and run them before declaring work done:

- **Frontend (vitest):** `cd apps/web && npm test` (`vitest run`). Component/lib tests live beside their source as `*.test.js` (e.g. `EditObsDataDialog.test.js`, `lib/obsDataJson.test.js`).
- **Backend (pytest):** `cd apps/api && pytest -m "not integration"` (unit only, no Myokit required). Tests live in `apps/api/tests/`. Integration tests need Myokit + `circulatory_autogen` on `sys.path`; they skip automatically via the `requires_simulation` fixture (run them with plain `pytest`).

> **Add tests with every change.** Each new feature should ship with frontend and/or backend tests covering it, and each bug fix should add a regression test that fails before the fix and passes after. Match the existing patterns (co-located `*.test.js`, `apps/api/tests/test_*.py`). Recent PRs report **141 frontend + ~79 backend** passing — don't let that regress, and confirm `npm run build` is clean.

Test fixtures live in `resources/`:
- **BG model** (`resources/BG_MWC_Huang-Peskin_SS.cellml`) — used in upload, simulate, and variable-list integration tests.
- **Lotka-Volterra** (`resources/Lotka_Volterra_forced.cellml` + `Lotka_Volterra_obs_data.json` + `Lotka_Volterra_params_for_id.csv`) — primary integration fixture for protocol runs and param slider tests.
