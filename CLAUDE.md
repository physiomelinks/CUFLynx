# CUFLynx — Agent summary

## Purpose

interactive calibration for computational models: sliders change constants, simulations update plots. The user can define their observable data and parameters to calibrate, then create an emulator, do sensitvity analysis, calibration, and UQ. 

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

**Backend engine (target):** [circulatory_autogen](https://github.com/...) `protocol_runners.ProtocolRunner` + `solver_wrappers.get_simulation_helper` (**Myokit** CVODE). Not the in-browser RK4.

> **No OpenCOR — ever.** CUFLynx must **not** bundle OpenCOR or any OpenCOR dependency, and must not depend on an OpenCOR runtime. CellML is simulated through **Myokit** (`CVODE_myokit`), not OpenCOR. Consequently CA's `CVODE_opencor` solver must never be surfaced in CUFLynx: it's filtered out of the solver options in `apps/api/solver_options.py` (`UNSUPPORTED_SOLVERS`), so `cellml` offers/defaults to `CVODE_myokit`. When consuming CA's discoverable schemas, always drop OpenCOR-only choices rather than passing them through. (CA is moving off OpenCOR's Python shell too — it now documents a plain venv — so there is no OpenCOR requirement to leak in either direction. If you point the CA dir at a checkout, no `pythonshell` is needed.)

**Reference implementation:** sibling repo `ICUHealthy` — FastAPI + cached `get_simulation_helper`, `helper.set_param_vals(param_names, param_vals)`, `helper.run()`, `helper.get_results()`.

**Config formats (circulatory_autogen):**

- `obs_data.json` — `protocol_info` (experiments/subexperiments) + `data_items` (ground truth / comparison targets) + optional `prediction_items`.
- `*_params_for_id.csv` — columns: `vessel_name`, `param_name`, `param_type`, `min`, `max`, `name_for_plotting`; full Myokit names are `vessel_name/param_name`. **One row is one parameter**, even when `vessel_name` names several vessels (whitespace-separated): that is CA's notation for one quantity written into several components at once, and CA carries one value for the whole row (`param_id_info["param_names"]` is a list *per row*). CUFLynx mirrors that — one `ParamEntry` per row with every member in `qnames`, one slider, expanded to all members only where the values are handed to the solver (`useSliders.paramDict`). Splitting a row per vessel gives each component its own handle and lets the user build a state the model never has (#193).

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

**Two module layouts, one resolver** (`apps/api/ca_imports.py`, CA #428/#437).
CA moved every module under a `libcuflynx.` namespace — `parsers` →
`libcuflynx.parsers`, and likewise `param_id`, `utilities`, `scripts`,
`solver_wrappers`, `generators`, `emulators`, `protocol_runners`,
`sensitivity_analysis`, `models`, `checks`, `coupler`, `solver1d`,
`identifiabilty_analysis`. CUFLynx **cannot pick a side**: the CA directory is
chosen at runtime, so one build is pointed at old flat checkouts and new
namespaced ones on the same machine, and upstream's deprecation shims last
exactly one release. So **never spell a CA module in an import statement** —
call `ca_from("parsers.PrimitiveParsers", "SOLVER_SCHEMA")` / `ca_import(...)`
with the *flat* name and let the resolver try `libcuflynx.<name>` first and
`<name>` second. Namespaced wins when both work (the flat one is the shim, and
its `DeprecationWarning`s are not the user's business). `CaImportError`
subclasses `ImportError`, so the "older CA → built-in fallback" arms all over
`solver_options` / `obs_options` keep working. Three rules that are easy to get
wrong: a `ModuleNotFoundError` naming something *other* than the candidate (CA's
`sensitivity_analysis` needing SALib) is re-raised, never turned into "this CA
does not provide it"; `engine.reset()` clears the cached layout because a new CA
dir can be the other one; and string targets — `mock.patch`, a probe run in
another interpreter — must go through `ca_imports.resolved_name()` or spell both
spellings, since neither is a literal any more. Tests inject fakes with
`conftest.set_ca_module`, which registers **both** names.

`ca_imports.py` **ships into `runners/`** alongside the other modules both tiers
share, so the analysis runners use the one resolver. `sim_worker_runner.py` is
the exception CLAUDE.md already carves out — it must stay free of every app
import — and carries a deliberate duplicate (`_ca_import` / `_ca_from`); so does
the exported `run_pipeline.py` (a string in `export_pipeline.py`), which runs in
the user's own environment. Keep the three in step.

## Key files

- `apps/web/src/App.vue` — main UI (tabs: Parameters · Emulator · Sensitivity · Calibration · UQ; center: Output plots · Progress · Analysis)
- `apps/web/src/components/TourOverlay.vue` + `lib/tourSteps.js` — the **Tutorial**
  button's guided tour. **Every anchor is a `data-testid`** and nothing else:
  renaming one breaks a tour step silently in the app, which is why
  `tourSteps.test.js` reads every `.vue` under `src/` and fails when a referenced
  testid no longer appears anywhere. The overlay *observes* — it has no way to
  mutate app state, so "wait for the user to act" is structural rather than a
  convention. Steps whose anchor is absent or zero-sized are skipped, which is
  also how inactive `v-show` panes are handled with no tab bookkeeping.
- **The run window comes from the obs_data's `protocol_info` and from nowhere
  else.** There are no `t₁`/`pre` controls and no Run button: `simTime`/`preTime`
  in `App.vue` are `computed` over `useObsData`'s `protocolSimTime`/`protocolPreTime`,
  runs fire on the debounced parameter watcher, and `canRun` blocks everything
  when no protocol is loaded. Two owners for one window is what let a calibration
  and the live cost score the same parameters differently; don't reintroduce a
  second source.
- `apps/api/main.py` — FastAPI app: `/api/*` routes + serves the built frontend
- `apps/api/engine.py` — live simulation; delegates to `sim_worker.py` /
  `sim_worker_runner.py` when an interpreter is chosen (#167), else runs in-process
- `scripts/install.py`, `scripts/run.py` — cross-platform setup + single-server launcher
- `apps/api/myokit_import.py` — `.mmt` → CellML at upload (model section only);
  `apps/api/mmt_protocol.py` + `scripts/mmt_to_obs_data.py` — the other half: the
  `[[protocol]]` section → obs_data `protocol_info`, since the protocol belongs in
  obs_data rather than in the CellML. `/api/models/upload` returns it as
  `protocol_obs_data`; `FileImport.vue` adopts it only when the user has no
  obs_data of their own, so a hand-written one is never replaced by a derived one
- `scripts/package.py` — build the single-file desktop executable (see below)
- `apps/desktop/app.py` — pywebview shell; `packaging/cuflynx.spec` — PyInstaller spec
- `README.md` — user-facing quick start

**Analysis backends** (one API module + runner each, plus a Vue panel):

- `apps/api/sensitivity.py` / `sensitivity_runner.py` — global **Sobol** sensitivity; `local_sensitivity.py` — local sensitivity (`d ln Y/d ln P` about a nominal point: current values / reused calibration best fit / bounds centre; optional "run calibration first"). UI: `SensitivityPanel.vue`; results render in `AnalysisPanel.vue` (S1/ST/local heatmaps).

  **Every gradient source is computed by CA**, through the one backend-agnostic accessor `OpencorParamID.get_observable_sensitivities(param_vals, gradient_method, fd_rel_step)` — FD (`fd_backend`), AD (`casadi_backend`) and FSA (`fsa_backend`) alike, so the runner builds the param-id engine for all three. CUFLynx reimplemented the FD loop and the CasADi jacobian until #210's follow-up; that is why it had to mirror CA's flatten/fold contract for grouped and modifier rows, and why CA #390 tightening that contract broke the AD path. **Do not reimplement a gradient here.** What `local_sensitivity.py` legitimately owns is only what CA does not answer: the nominal point, the *signed* relative coefficient (CA's is unsigned and 0.0 on a degenerate denominator), the `var^{e,s} [op]` labels shared with the Sobol heatmap, and the `CVODES`/`AUTO` alias resolution. Two traps: read the nominal features **before** the sensitivities (CA's CasADi arm leaves the helper in AD mode, so a later numeric evaluation returns an `SX`), and pass `rel_step` explicitly as `fd_rel_step` (CUFLynx defaults to 1e-2, CA to 1e-3 — up to 48% apart).
- `apps/api/calibration.py` / `calibration_runner.py` — GA parameter identification; `CalibrationPanel.vue` (also emits live settings reused by local-sensitivity "run calibration first").
- `apps/api/uq.py` / `uq_runner.py` — uncertainty quantification; `UQPanel.vue`.
- `apps/api/emulator.py` / `emulator_runner.py` — trains a **surrogate** of the
  model's scalar observable features with CA's `EmulatorTrainer` (CA #333);
  `EmulatorPanel.vue`, tab between Parameters and Sensitivity. Two independent
  controls, because emulation has two steps: **Train** fits one against the
  solver, and the **use** tick box (`useEmulator` store) makes sensitivity,
  calibration and UQ evaluate it instead. `solver:` keeps meaning the truth
  solver throughout, so an emulator run stays comparable with what it approximates.

  **The bundle outlives the session**, so nothing about it is remembered in
  memory: `ca_run_history.emulator_dir()` derives the location from the outputs
  directory by CA's own rule (`<outputs>/emulators/<prefix>_<obs_prefix>`) on
  *both* sides, and `emulator_metadata()` reads CA's `emulator_metadata.json`.
  One consequence worth keeping: an emulator trained by CA's own script into the
  same outputs directory is usable from the GUI, and nothing has to be re-pointed.

  `emulator_config.engine_kwargs()` is the one place "use the emulator" becomes
  CA engine kwargs; **all three** analysis runners call it (sensitivity twice
  more, for its local arm and its calibrate-first engine) so a study cannot be
  calibrated on the surrogate and analysed on the solver without saying so. It
  returns `{}` rather than `use_emulator=False` when off, because a CA that
  predates emulators does not accept the keyword at all.

  The panel form is built from CA's `ANALYSIS_OPTIONS['emulation']` — the only
  mode carrying a `use_flag` as well as an `enable_flag`, now passed through
  `solver_options._introspect_analysis_options`. `models` is a *runtime* registry
  (`solver_options.emulator_models()` → CA's `emulator_model_names()`), so it is
  a menu when the backend could read it and free text when it could not; an empty
  menu would read as "there are none".

  **Live comparison on the Parameters tab.** The sliders keep running the real
  solver; with the tick box on, the emulator's prediction of each scalar feature
  is drawn as a *third* reference line (dotted) beside the measurement (dashed)
  and the model's own feature (solid), in that feature's colour — so dragging a
  parameter shows where the surrogate stops agreeing with the model. That
  prediction is a **fifth sim-worker verb** (`emulator_predict`), not a fifth
  process: it answers during a drag, which is the live tier, and loading the
  bundle needs the autoemulate/torch CUFLynx does not bundle — so it has to run
  in the interpreter chosen in Settings. With no worker configured it says so
  rather than failing inside a joblib unpickle. `plot.js` matches predictions to
  data_items **by CA's feature label**, including the `[exp e, sub s]` form CA
  uses when a label repeats, never by position.

**Only circulatory_autogen writes to the user's outputs directory** (#210). A run
leaves CA's own files there and nothing else — no CUFLynx-authored results
format, and no plumbing:

- `apps/api/ca_run_history.py` is **the one place that knows CA's output
  formats**. Managers and the exported plotting script both read through it:
  `best_param_vals.npy` / `best_cost.npy` / `param_names.csv` /
  `param_modifiers.json` (resolved baselines + affine chain-rule weights) /
  `percent_error_vec.npy` + `error_vec_names.npy` (CA#341 — the vectors
  self-identify, so labels are never guessed from obs_data) /
  `all_outputs_n<N>_Sobol_indices.csv` / `local_sensitivity_{relative,absolute}.csv`,
  plus CA's `param_id.run_history` reader (`read_run_history` / `clear_run_history`
  / `find_run_dir`, CA#392). A run directory produced by **CA's own scripts** is
  therefore just as readable as one produced through the GUI.
  The **live Progress payload** comes from `progress_history()` here, not from
  hand-written parsing in `calibration.py` (which used to hold ~260 lines of it).
  Three things it owns and CA does not: it takes CA's **`param_history_norm`**,
  never its denormalised `param_history` (the plot pins its y-axis to [0,1],
  titles it "normalised value" and denormalises in the tooltip — and CA writes
  `param_bounds.json` on every real run, so the wrong key is populated in
  production and `None` in most fixtures: wrong in the app, green in CI); it
  filters torn trailing rows (CA guards against *unparseable* rows, not short
  ones, and a run is polled while it is being written); and it clears **every**
  case subdirectory, because CA's clearer locates one run dir and an outputs
  directory reused across methods accumulates several.
- The runners' `<stage>_config.json` payloads go to a **temp dir** — they are
  `argv[1]` and nothing reads them back. `write_run_config` / `clear_run_config`
  in `calibration.py`, shared by all three managers.
- What no file holds — which method ran, the point a local SA was linearised
  about — travels on the stdout the manager already reads, via a `__*_META__`
  marker line. Keep those lines **small**: under `mpiexec` every rank shares that
  pipe and a line over `PIPE_BUF` can interleave.
- Where CA persists nothing usable, the run persists the **result**, in CA's own
  idiom, never a summary of it: forward-simulation traces as
  `all_outputs_exp_<i>.npz` (the shape CA writes for a best fit, so one reader
  covers both), UQ posteriors as `uq_posterior_samples.npy` (binned on read —
  CA's burn-in rule needs a live param-id object, so reading its raw
  `mcmc_chain.npy` would report a different posterior from the one the run did).
- Reading via `find_run_dir` can reach an **earlier** run's `<case_type>` subdir,
  which the old direct read could not. `has_results(output_dir, newer_than=…)`
  takes the job's start time so a run whose own results are missing fails
  instead of reporting someone else's numbers.

One analysis is deliberately **not** in that tier: `apps/api/cost_sensitivity.py`
(`POST /api/cost_sensitivity`, `CostSensitivityBar.vue`, #188) — `d ln(cost)/d ln(p)`
per parameter, shown beside the cost while the sliders are being dragged. It runs
in the **live** tier (`engine`, hence the worker interpreter) because it has to
answer in the time a drag allows, and it differences the very cost the panel shows
(`main._single_run_cost` / `_protocol_run_cost` → `obs_cost.evaluate` → CA). Central
FD, `rel_step` defaulting to CA's `1e-3` **not** local SA's `1e-2` — the two differ
by up to 48% on a rough functional, and these numbers sit next to CA's. CA's
analytic `get_gradient` is better and unused: it needs a solver-backed
`OpencorParamID` (compile per call, casadi/aadc/FSA only), which is the subprocess
tier this is avoiding. Opt-in and off by default: 2M+1 simulations per settle.

**Exchange with PhLynx** (`apps/api/omex_import.py` + `omex_export.py`, #287/#290).
A study travels between the two apps as a **COMBINE archive**, and the contract is
asymmetric: CUFLynx owns the model's constants and **nothing else in the archive**.
So `unpack` returns every member and the parsed manifest — not just the four roles
it understands — and `build_archive` copies them all back byte-for-byte with exactly
two exceptions: the model CellML (flattened, with the chosen values substituted) and
params_for_id (refreshed from the study, so range/selection edits travel). **obs_data
is never replaced** — verbatim when the archive has one, *added* only when it does
not. PhLynx's `flow.json` / `changes.json` / `module_config.json` / SED-ML /
`simulation.json` are never parsed, authored or mutated; whichever arrived is exactly
what leaves, and none is invented when absent (#287 has not settled which editor-state
file is authoritative, and this is what keeps CUFLynx from having to care).

Three traps:

- **Classify JSON members by manifest format and content, never by "leftover
  `.json`".** A PhLynx archive ships `simulation.json` declared as plain
  `application/json` — the same format a real obs_data carries — so the manifest
  alone cannot separate them; `_looks_like_obs_data` sniffs for the two shapes
  `obs_data.parse_obs_data` accepts. Upstream is explicit that `flow.json` may be
  **renamed**, so its format specifier (`application/x.vnd.phlynx-flow+json`) is the
  contract and its name is not. A params_for_id must be matched against `.json` too:
  CUFLynx stores the canonical JSON form, so without that an archive CUFLynx writes
  is not one CUFLynx can read.
- **The send URL is `${PHLYNX_URL}/?open=omex#<bare base64>`** (`phlynxOpenUrl` in
  `lib/examples.js`). PhLynx has no `phlynx://openFile/` scheme — `useLoadFromUrl`
  reads `?open=<keyword>` and `urlLoaders` registers `omex` — and its `base64ToBlob`
  builds the `data:` URI itself, so a data URI in the fragment arrives
  double-prefixed. One function owns the URL for when that changes.
- **The source archive has to be kept to be returned.** `UPLOAD_DIR/<model_id>.omex`,
  `_ModelRecord.archive_path`, restored by `_get_model`'s disk-recovery path.
  Deliberately **not** in `MODEL_SOURCE_SUFFIXES`: that tuple drives "Edit source",
  which would try to open a zip in the user's text editor.

Value substitution goes through `calibrated_model.calibrated_cellml` — the same
writer the calibrated-model save uses, so a send and a saved best fit produce the
same file for the same values. Its report now names the owning `component/var` per
parameter, which is how the send warns about a value written outside
`parameters` / `parameters_global`: per #287 PhLynx will not read those back.

**Receiving a study from PhLynx** (`apps/api/inbox.py`, #287). PhLynx hands a study
to a *running* CUFLynx by posting the archive to `/api/inbox` on localhost. Four
things about it are load-bearing:

- **CORS stops a page reading our responses; it does not stop it sending
  requests.** So anything running in the user's browser can reach the inbox, and
  the endpoint therefore **stages** rather than imports. The confirmation dialog in
  `FileImport.vue` is the security control — it names the *sending origin*, which
  is the only basis a user has for judging a payload they never asked for — and a
  route that imported on arrival would leave nothing to confirm.
- **The port range is a contract.** `inbox.RECEIVE_PORTS` (8787–8791) is written
  down once and imported by both the API and `apps/desktop/app.py`, which tries
  those ports before falling back to a random one. A browser cannot discover a
  random port, so a range that drifted between the two tiers would leave the app
  listening where PhLynx never looks. Falling back past the range is **not** an
  error: the app works, deliveries just do not, and it says so.
- **`/api/health` names the app.** PhLynx probes the range and checks
  `app == "CUFLynx"` before sending, so it cannot post a study at some unrelated
  service that also answers `/api/health` on 8787.
- **One importer.** `/api/inbox/accept` runs `main.import_omex_bytes`, the same
  body `/api/omex/upload` runs, and returns the same shape — which the frontend
  feeds into the same `applyImportedStudy` the drop path uses. A delivered study
  has to behave exactly like a dropped one, and the way to guarantee that is for
  there to be one function on each side, not two that agree today.

`ALLOWED_ORIGINS` is an explicit list and never `*`: it is the only door into an
API that is otherwise trusted because nothing off-machine can reach it. Chrome
additionally treats public→private as Private Network Access, so a small
middleware answers its preflight with `Access-Control-Allow-Private-Network` —
`CORSMiddleware` knows nothing about that header. That corner of the platform is
moving (Local Network Access will likely add a browser permission prompt), so the
flow's UX is not entirely ours.

**Not** a `cuflynx://` URL scheme: a scheme handler delivers the URI through the OS
process-launch path, where truncation bites in the low thousands of characters, and
`3compartment.omex` is already ~14 KB base64. Registering CUFLynx as the `.omex`
file handler is the planned next step — it needs a macOS `.app` bundle, which the
single-file spec does not produce.

**GUI config editing** (edit CA config files in the browser → download dated copy → apply immediately):

- **obs_data.json** — `EditObsDataDialog.vue` + `apps/web/src/lib/obsDataJson.js`; its operand and operation pickers are `SearchableSelect.vue` (type-to-filter, #160) because those lists are a model's whole variable set and every registered operation; edits `data_items`/`prediction_items` (incl. `source`/`comment` notes) and embeds `ProtocolInfoEditor.vue` (+ `lib/protocolInfo.js`) for `protocol_info` (experiments, params_to_change, constant/ramp/step/pulse/paced inputs, time-view plots). Time-varying inputs are written as **`protocol_shapes`** — declarations in Myokit's `[[protocol]]` vocabulary (`level`/`start`/`length`/`period`/`multiplier`), which CA expands into `protocol_traces` on read (CA#339). Declarations, not expansions, because a point table cannot be read back into the fields that produced it. `expandShape()` in `lib/protocolInfo.js` mirrors CA's expansion for the plots; **the two must agree** — change both. Hand-written `protocol_traces` are still accepted and preserved verbatim, as is any shape the editor has no form for. Dropdown vocabularies come from `apps/api/obs_options.py` (`GET /api/obs_data/options`), which introspects CA registries — **never hardcode** operations/cost_types/data_types/plot_types. A data_item's `operation_kwargs` and `cost_kwargs` (CA#304 / CA#370) are edited the same way: an input per keyword argument, from a schema introspected off the chosen func's signature (`operation_kwargs_schema` / `cost_kwargs_schema`). A stored kwarg is only ever deleted when CA has said the newly chosen func cannot accept it (`cost_kwargs_accepts_any` is a *full* map so "accepts nothing else" is distinguishable from "CA never answered") — otherwise it round-trips untouched, because dropping a key is data loss. `apps/api/obs_cost.py` calls cost funcs through CA's `call_cost_func`, so the panel's cost is the call the calibration makes: `std`/`weight` only when the signature declares them, plus the data_item's `cost_kwargs`.
- **params_for_id.json** — `EditParamsDialog.vue` + `apps/web/src/lib/paramsCsv.js`; edits ranges/selection, writes a dated JSON, can apply best-fit to sliders. **An uploaded CSV is converted to JSON on the way in** (by CA, which owns the conversion): JSON is the only form that can express a modifier, its `inputs` or a prior's parameters, so a stored CSV would make those unrepresentable in the study the user ends up with. Without CA the CSV is kept as-is rather than the upload being refused — the packaged app starts with no CA directory set.

**User-authored funcs** (`apps/api/user_funcs.py`) come in **three** kinds, one
file and one CA config key each, all under `<outputs>/user_funcs/`:
`operation_funcs_user.py` / `cost_funcs_user.py` (CA#303, named by an obs_data
data_item) and `modifier_funcs_user.py` (CA#383, named by a params_for_id entry).
Everything is driven off the `_KINDS` map — the run config, the export copy into
`resources/`, and the yaml keys all fall out of it, and the exported script
matches the external-path keys **by suffix** so a fourth kind needs no edit
there. A study is not reproducible without its funcs: CA dies on an operation or
modifier it has never heard of, which is why they travel with the bundle. A
modifier's `inputs` (`{name: qname}` / `{name: [qnames]}`) round-trips verbatim
through both the backend and the editor — dropping it silently breaks the entry
on the next run. *Not yet built:* a form for entering those input qnames, so a
modifier that takes any is still only configurable in a hand-written file.

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

**The split is the app's biggest structural flaw, and we want it gone.** The two
tiers do not merely have different *dependencies* — they run in **different Python
interpreters**, and only the subprocess tier honours the one the user picks in
Settings. So "switch Python" only ever switches half the app:

- The interpreter picker is a **half-truth**. Choosing a venv that has `aadc` (or a
  patched CA, or a different numpy) changes calibration/SA/UQ and leaves live
  simulation on the app's own interpreter, which does not have it. That is the
  whole of issue #122's failure mode: the user selected the right environment and
  the live engine kept saying "aadc is not installed".
- The CA dir has the **same asymmetry**. Subprocess runs pick it up on their next
  launch; the in-process engine caches CA's modules after its first simulation, so
  a mid-session switch only fully re-points it after a restart.
- A user editing CA cannot see their edits in the sliders without restarting, only
  in analysis runs.

**Fixed by the simulation worker** — `sim_worker.py` (parent) +
`sim_worker_runner.py` (child), issue #167. Live simulation now runs in the
interpreter chosen in Settings, so one choice governs both tiers.
`_set_analysis_python()` sets `engine.worker_python` alongside the three analysis
managers; there is deliberately no separate setting for it.

Persistent, not per-request: the expensive thing is the model compile, and the
worker holds the same helper/runner caches the engine used to. Changing
interpreter, CA dir, solver, dt or solver_info **restarts** it rather than
reconfiguring it — CA caches its modules on first import, so a mid-life change
could not fully take effect, which is the bug being removed.

Rules it follows, and must keep following:

- **In-process stays the fallback** when no interpreter is chosen — the frozen
  app's default, so a user who never opens Settings sees no change.
- **The worker is skipped when the chosen interpreter is the environment already
  running**, because it would cost a process and a compile and change nothing
  importable. "Same environment" is judged by **`sys.prefix`, never by resolved
  executable path**: a venv's `bin/python` is usually a symlink to its base
  interpreter, so realpath makes a venv look identical to the interpreter it was
  created from — the same mistake that once hid venvs from the picker.
- **A worker that will not start is an error, not a silent fallback.** Running
  somewhere other than where the user asked is precisely the confusion this
  removes.
- Launch through `runtime_paths.runner_command()` + `subprocess_env()`. Never a
  bare `sys.executable` (in a bundle that relaunches the GUI), and never inherit
  the bundle's loader vars (an external Python then imports the bundle's numpy).
- `sim_worker_runner.py` ships as **data** in the `runners/` subdir, because an
  external interpreter executes it as a file — and it must stay free of imports
  from the app, whose modules are frozen into the bundle and unreachable from
  outside it. `_resolve_output_key`, `bind_protocol`, the sub-experiment
  helpers (`_sub_counts`, `_run_protocol_by_sub`, `_subexperiment_outputs`, the
  join) and the CA import resolver (`_ca_import` / `_ca_from`, mirroring
  `ca_imports.py`) are duplicated there for that reason; keep each pair in step,
  or the cost silently depends on whether an interpreter is configured in
  Settings.
- The worker returns the **captured solver output plus a fallback reason**, and
  the parent composes the message through `failure_message` — so the issue #138
  error quality lives in one place rather than on both sides of a pipe.
- **Four verbs** (`configure` / `simulate` / `run_protocol` / `ping`). The failure
  mode for this design is a second API growing beside the first.

Its stderr is mirrored to the server log with a `[sim-worker]` prefix and the tail
is kept, so a worker that dies on `import myokit` says so instead of presenting as
an empty pipe.

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
- **`resources/` is not in the bundle — only what is listed is.** The "Start"
  dialog's examples are served from `resources/`, and nothing collected that dir,
  so the example 404'd with "example model file missing" in the packaged app only
  (issue #180). `apps/api/examples.py` now holds the manifest and the spec imports
  it (`datas += examples.example_datas()`), so route and bundle cannot drift; a
  listed file that is missing fails the *build*. Examples ship as **`.omex`**, not
  loose CellML, because an example is a study — model + obs_data + params_for_id —
  and the frontend loads it through the existing archive path.
- **A C compiler cannot be bundled away — but it is only needed for one backend.**
  Of CA's `src/solver_wrappers/*`, **only `myokit_helper.py` compiles anything**;
  `python` (scipy `solve_ivp`) and `casadi_python` (`casadi_integrator`) are
  compiler-free and both are verified working in the frozen app. So a missing
  compiler is a **warning, not an error**: `compiler_check.py` detects it,
  `GET /api/config` returns `cpp_compiler: {present, hint, affects, alternatives}`,
  and `App.vue` shows a `warn` banner naming the backends that still work. Don't
  regress this to an error. `scripts/install.py` shares the detection — keep it in
  one place.

**`POST /api/config` semantics — `ca_dir` omitted means "leave unchanged".** It is
`str | None = None`; only an explicit `""` resets to the default. This is load-
bearing: the Settings popup saves solver choices with a payload that carries no
`ca_dir`, and when omission meant "reset", **every solver change silently wiped the
CA directory**. From source that was invisible (the default is the sibling clone,
which is correct on a dev box); in the packaged app there is no sibling, so CA was
lost and the non-Myokit backends died with `No module named 'generators'`. Relatedly,
`engine._circulatory_autogen_src()` returns `""` when frozen and unconfigured rather
than guessing a sibling (which produced `/circulatory_autogen`).

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
