# CUFLynx — Agent summary

## Purpose

interactive calibration for computational models: sliders change constants, simulations update plots. The user can define their observable data and parameters to calibrate, then create an emulator, do sensitvity analysis, calibration, and UQ. 

## Test fixtures (`resources/`)

| Artifact | Role |
|----------|------|
| `resources/BG_MWC_Huang-Peskin_SS.cellml` | Primary test model (CellML 1.1, component `main`; `main/p_o2` is state, `main/alpha_o2` is parameter) |
| `resources/Lotka_Volterra_forced.cellml` | Integration test model (CellML 2.0, component `Lotka_Volterra_module`; params `alpha`,`beta`,`delta`,`gamma`) |
| `resources/Lotka_Volterra_obs_data.json` | Test obs_data fixture (1 experiment, 2 constant data_items for `x_max` / `y_max`) |
| `resources/Lotka_Volterra_params_for_id.csv` | Test params fixture (4 rows, vessel `Lotka_Volterra_module`): a CSV, so the tests cover the upload's conversion |
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
- `*_params_for_id.json` — `{"version": 1, "params": [{"targets": ["heart/lv/E_A", "heart/rv/E_A"], "min", "max", ...}, {"name", "modifies": [...], "modifier": "scale", ...}]}` (`libcuflynx.utilities.params_for_id_file`). CA refuses a params_for_id CSV; CUFLynx converts an uploaded one with CA's `params_for_id_csv_to_json` (what `cuflynx-upgrade params-for-id` writes: `vessel_name` "a b" + `param_name` "C" → `targets` ["a/C", "b/C"], a nested `heart__lv` as `heart/lv/C`). **One entry is one parameter**, even when `targets` names several: one quantity written into several components at once, and CA carries one value for the whole entry. CUFLynx mirrors that — one `ParamEntry` per entry with every member in `qnames`, one slider, expanded to all members only where the values are handed to the solver (`useSliders.paramDict`). Splitting an entry per target gives each component its own handle and lets the user build a state the model never has (#193).
- `<prefix>_parameters.json` — a module-array model's parameter values, nested instance → sub-instance → variable (`libcuflynx.utilities.parameters_file`). CA refuses a parameters CSV. PhLynx reads and writes a parameters CSV (`variable_name,units,value,data_reference`), so CUFLynx converts at that boundary: `rows_to_document` on the way in, `parameter_rows` (`<instance>/<var>` names) on the way out.

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
import — and carries a deliberate duplicate (`_ca_import` / `_ca_from`); keep the
two in step. The exported `run_pipeline.py` is CA's own
(`scripts.generate_pipeline_script`), imported as `libcuflynx.*` only.

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

  **Every gradient source is computed by CA**, through the one backend-agnostic accessor `ParamID.get_observable_sensitivities(param_vals, gradient_method, fd_rel_step)` — FD (`fd_backend`), AD (`casadi_backend`) and FSA (`fsa_backend`) alike, so the runner builds the param-id engine for all three. CUFLynx reimplemented the FD loop and the CasADi jacobian until #210's follow-up; that is why it had to mirror CA's flatten/fold contract for grouped and modifier rows, and why CA #390 tightening that contract broke the AD path. **Do not reimplement a gradient here.** What `local_sensitivity.py` legitimately owns is only what CA does not answer: the nominal point, the *signed* relative coefficient (CA's is unsigned and 0.0 on a degenerate denominator), the `var^{e,s} [op]` labels shared with the Sobol heatmap, and the `CVODES`/`AUTO` alias resolution. Two traps: read the nominal features **before** the sensitivities (CA's CasADi arm leaves the helper in AD mode, so a later numeric evaluation returns an `SX`), and pass `rel_step` explicitly as `fd_rel_step` (CUFLynx defaults to 1e-2, CA to 1e-3 — up to 48% apart).
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
  memory: `ca_run_history.emulator_dir()` derives the location from the working
  dir by CA's own rule (`<working dir>/emulators/<prefix>_<obs_prefix>`) on
  *both* sides, and `emulator_metadata()` reads CA's `emulator_metadata.json`.
  One consequence worth keeping: an emulator trained by CA's own script into the
  same working dir is usable from the GUI, and nothing has to be re-pointed.

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

**Calibration workflows** (`apps/api/workflow_manager.py` + `workflow_runner.py`,
`WorkflowPanel.vue` / `WorkflowBar.vue` / `stores/useWorkflow.js`). A workflow
file orders calibrations -- each step a model (a module array or a library module), an
obs_data and a params_for_id -- merged into a target model, and **all of it is CA's**
(`libcuflynx.calibration_workflow`, CA #555): the format and its checks, finding library
modules (also in the **module libraries** set in Settings, `module_library_dirs`,
persisted), how a step's names map into the target, the run, and the run directory --
which holds the workflow it ran, so reopening that directory reopens the workflow.
CUFLynx keeps which workflow is open, runs `workflow_runner.py` like the other
runners (one job; refused while a calibration runs, and vice versa), and loads each
tab through CA's `workflow_model` -> `import_omex_bytes`, so a step tab is an
ordinary study. A step's `kind` (calibration, sensitivity, reduction, emulation,
identifiability; CA #585) is shown in the step list with what it takes from where; a
reduction has no model, so no tab. **Export .omex** (`/api/workflow/export`) puts the
target model and the workflow as CA's `pack_workflow` packs it under `workflow/` in an
archive (no results), and a study sent from a workflow tab carries the pack too; an
imported archive's `workflow/` is unpacked whole into the uploads dir and offered, so
its relative paths resolve and it reruns from the archive alone. Two traps found
building it:

- **No app module may share a flat name with a CA module.** `ca_from` resolves
  `libcuflynx.<name>` *and* `<name>`, and an app module already in `sys.modules`
  wins -- the manager was first called `calibration_workflow.py` and was handed back
  as CA's. A new CA top-level package also has to join `CA_PACKAGES` (and its two
  mirrors, pinned by `test_ca_import_parity.py`) or its namespaced spelling is never tried.
- **Give a runner an explicit `cwd`.** Myokit `chdir`s the whole server into a
  temporary build directory while it compiles a live simulation, then deletes it; a
  runner spawned in that window starts in a directory that no longer exists and dies
  at its first compile with `[Errno 2] No such file or directory`. The workflow
  runner runs in its run directory. (The other managers still inherit the server's.)

**LaTeX symbols** (`apps/api/symbol_mapping.py`, `EditVariableMappingDialog.vue`, opened
from the Variables panel). A study's `<prefix>_variable_mapping.csv` -- the symbol of every
variable, which CA's `cuflynx-methods-latex` typesets the methods with -- is entirely CA's
(`libcuflynx.reporting.variable_mapping`, CA #547): rows, default rule, keeping edits.
CUFLynx only picks where the file lives: beside the target's parameters file for a
workflow's target tab (CA's `workflow_mapping_path`, where the methods writer looks), else
the version stored last in `<working dir>/inputs/variable_mapping/<stamp>/` (a save stores a new
one; top level in an older folder), else the uploads directory. `reporting` is a CA
package in `CA_PACKAGES` (and its two mirrors).

**The working dir** (`apps/api/working_dir.py`, user docs `tutorials/docs/working_dir.md`)
is the one folder a study lives in, inputs and outputs, so it can be zipped and opened
elsewhere. The old name, "Outputs directory", survives only in the aliases below. Rules:

- **Every input version lives in `<W>/inputs/<kind>/<yyyy-mm-dd_HHMMSS>/<file>`**, the
  layout libcuflynx writes (Finbar, 2026-10-31). **Write only through `working_dir.store`**
  (libcuflynx's `cuflynx_study.store_input`, reached with `ca_from`; a CA without it raises
  `WorkingDirUnavailable`, a 422 naming CA #619): a new content gets a new dated folder, the
  same content reuses its folder (content hash), `inputs/index.json` lists every version, and
  nothing is ever rewritten in place. So no file under a plain name can look current by
  mistake. Kinds: `model`, `obs_data` (also `uq_obs_data.json`), `params_for_id`,
  `user_inputs`, `funcs`, `variable_mapping`, `obs_extraction`, `workflows/<wf>`,
  `generated_models/<prefix>`. Something written by a converter or CA first (a conversion,
  PhLynx state, an unpacked workflow, the variable mapping) is written into a scratch folder
  and that folder or file stored.
- **Which version is current** is the record's (`working_model`, `obs_path`, `params_path`,
  the paths a run is given and the manifest records), or for a file kept per working dir
  (funcs, `user_model.py`, the variable mapping) `working_dir.latest` (libcuflynx's
  `latest_input`: the version stored last; storing an earlier content again makes it the
  latest). `main._bind_working_dir(record, W)` stores a model record's inputs: every upload
  naming a working dir and every run calls it. `_write_study_file` writes the session copy
  and stores it once `record.working_dir` is set. The uploads dir (`UPLOAD_DIR`, pruned after
  7 days) stays the session's scratch copy, never what a study depends on. Readers never
  glob `inputs/<kind>/`: `load_outputs` and `study_manifest._resolve` ask `latest`.
- **A run needs a working dir.** `_require_working_dir` 422s with
  `working_dir.REQUIRED_MESSAGE`; there is no temp `calib_*`/`sa_*`/`uq_*`/`emu_*`
  fallback any more, and the frontend refuses before sending (`lib/workingDir.js`).
- **Outputs:** case dirs at the top (CA), sensitivity in `<W>/sensitivity/`
  (`sa_output_dir` in the runner config; the param-id side stays at the top so the
  manifest does), saved parameter sets in `<W>/saved_params/`.
- **Older folders stay readable where their files are**: `working_dir.readable()` is the
  latest stored version, else `user_funcs/`, `generated_models/` or the top level, and
  readers (`load_outputs`, `/api/runs`) look in both places. A save stores a version in
  `inputs/` and leaves the old file as it was.
- **Wire names:** `working_dir` and `/api/working-dir/*`. `wire_aliases.WorkingDirAliases`
  (ASGI middleware) copies `working_dir` onto `output_dir` / `outputs_dir` /
  `config_outputs_dir` in queries and JSON bodies at any depth, and rewrites
  `/api/outputs/*` to `/api/working-dir/*`, so the routes still declare the old field
  names. The old names are accepted for one release; when they go, rename the fields and
  drop the middleware. CA's own `param_id_output_dir` keeps its name. The browser
  setting is `cuflynx-working-dir`, migrated once from `cuflynx-outputs-dir`.

**Only circulatory_autogen writes results to the user's working dir** (#210). A run
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
- **Held-out data** (CA #535): a `prediction_item` carrying `value` (+ `data_type`,
  `std`, `obs_dt`) is validation data, never scored. **libcuflynx does all of the
  validation**: after a calibration the runner calls its `save_prediction_data`,
  which scores the items (`param_id.validation`) and writes
  `validation_results.json`; the managers only read that file. CUFLynx keeps no
  copy of the scoring or of its rules -- a libcuflynx without `param_id.validation`
  gives no validation, and the Analysis tab shows a one-line note instead (from
  `/api/config` `held_out_validation_supported`). The status/load-outputs
  `validation` is None -- never empty -- without it.
- **Prediction items as features** ("Include prediction items" in the Sensitivity
  (Sobol and local) and Emulator forms): passed through as
  `sa_options.include_prediction_items` / `emulator_settings.include_prediction_items`;
  libcuflynx then also uses the prediction_items that have an `operation` (labelled
  by `data_item_name`) and skips the rest with a warning. CUFLynx does no feature
  logic. Which outputs are prediction features is read from libcuflynx, never from
  labels: Sobol's `sobol_output_features.json` (`ca_run_history.sobol_prediction_outputs`,
  filtered to the CSV's own columns so an earlier run's file tags nothing), the
  emulator bundle's `prediction_feature_labels` (last in `feature_labels`), and for
  local SA the rows libcuflynx's `SensitivityAnalysis._prediction_feature_sensitivities`
  returns -- the call its own `run_local_sensitivity` makes, evaluated here at
  CUFLynx's nominal point (libcuflynx's local SA only linearises about the model
  defaults) and normalised like the data_item rows; the runner's meta line carries
  their names, since CUFLynx writes that CSV. The UI tags all of these "prediction".
  The option is sent (runners, exported yaml) only when on **and** supported; the
  feature detect is `obs_data.PREDICTION_FEATURES_FLAG` (`(module, constant)`),
  **the one place its name is spelled**, read by
  `obs_data.ca_supports_prediction_features()` and surfaced as
  `prediction_features_supported` on `/api/sensitivity/defaults` and
  `/api/emulator/defaults`. SA on an emulator forwards it too; libcuflynx refuses a
  bundle trained without the features, and the SA status's `error` is the runner's
  own reason (its `__SENSITIVITY_FAILED__` line), so that message reaches the panel.
  Validation items with an `operation` are scalar features (`t` = end of run,
  `model` = the feature): the table names them "x (max)" and all scalar items share
  one parity chart (model vs data ± std, y = x).
- **An older libcuflynx rejects newer prediction_item keys** -- the held-out keys
  and `operation` / `operation_kwargs` -- so every hand-over goes through
  `obs_data.for_ca` (in-process) or `obs_data.with_ca_obs_path` (runners), which
  strip each group only when this libcuflynx cannot read it. The obs_data editor
  keeps them (an operation picker per prediction row; the rest ride on `_orig`).
- Reading via `find_run_dir` can reach an **earlier** run's `<case_type>` subdir,
  which the old direct read could not. `has_results(output_dir, newer_than=…)`
  takes the job's start time so a run whose own results are missing fails
  instead of reporting someone else's numbers.
- **The study manifest is CA's** (`apps/api/study_manifest.py`). libcuflynx
  (`utilities/cuflynx_study.py`) records every calibration, UQ, sensitivity and emulator
  run from one config in `<param_id_output_dir>/<study_name>_cuflynx_study.json`: model,
  base_model, obs_data, params_for_id, `user_funcs` ({kind: path}), `solver_info` (+dt),
  `runs` (newest first, `analyses` per dir), `sensitivity` and `emulator`, every path
  relative to the manifest's folder (libcuflynx copies inputs into `inputs/`). CUFLynx only
  reads it; bare `cuflynx_study.json` (what sympathetic_neuron's pipeline wrote) is still
  read. Relative paths resolve against the manifest's own folder; an older manifest's
  absolute paths are rebased from its recorded `study_dir` onto that folder, or found in
  `inputs/<kind>/` by name (`study_manifest._resolve`). A copy away from its study
  resolves against `study_dir` when it records one (`base_dir`). Dropping one on the app
  (`POST /api/working-dir/manifest`, `handleStudyManifest` in `FileImport.vue`) only sets
  the working dir and reloads it — one path for a dropped manifest and a picked folder.
  A dropped (or picked) **.zip** goes to `POST /api/working-dir/zip` first
  (`study_manifest.zip_study`): a `manifest.xml` at its root makes it an OMEX (an OMEX
  carries a study manifest too), else a `*_cuflynx_study.json` at its root or one folder
  down makes it a working dir, staged in `UPLOAD_DIR/working_dir_zips/` until
  `/api/working-dir/zip/extract` is told where (a 409 for a folder holding files unless
  `overwrite`). Any member with `..`, an absolute path or a symlink refuses the whole zip.
  The answer is `study_location`'s for the extracted manifest, so `handleWorkingDirZip`
  then takes the dropped-manifest path (`openStudyDirectory`); outputs the manifest flags
  outside the working dir are named by the reload's `warnings` banner.
  `pre_time`/`sim_time` in it are informational: timing comes from the obs_data protocol.
- **Declared funcs are the user's file, not ours.** `user_funcs._user_file` returns the
  manifest's `user_funcs[kind]` when declared, so runs, option lists and the editor all
  act on it; saving/deleting then goes through `_splice` (replace only that `def`'s
  lines), never `_render`, which would replace the user's docstring, imports and module
  constants with this app's header.

One analysis is deliberately **not** in that tier: `apps/api/cost_sensitivity.py`
(`POST /api/cost_sensitivity`, `CostSensitivityBar.vue`, #188) — `d ln(cost)/d ln(p)`
per parameter, shown beside the cost while the sliders are being dragged. It runs
in the **live** tier (`engine`, hence the worker interpreter) because it has to
answer in the time a drag allows, and it differences the very cost the panel shows
(`main._single_run_cost` / `_protocol_run_cost` → `obs_cost.evaluate` → CA). Central
FD, `rel_step` defaulting to CA's `1e-3` **not** local SA's `1e-2` — the two differ
by up to 48% on a rough functional, and these numbers sit next to CA's. CA's
analytic `get_gradient` is better and unused: it needs a solver-backed
`ParamID` (compile per call, casadi/aadc/FSA only), which is the subprocess
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

The third CUFLynx-owned part is the **study**: `build_archive(study=, funcs=)` writes
the same study manifest CA writes beside results — `<prefix>_cuflynx_study.json`,
format `application/x.vnd.cuflynx-study+json` (`study_manifest.OMEX_FORMAT`) — with
its paths pointing at archive members, `solver_info`, and the user funcs under
`user_funcs/`; runs/sensitivity/emulator/study_dir and a manifest's `workflow` block
are dropped (they are one machine's directories), and `workflow` names the packed
workflow (`workflow/calibration_workflow.json`) when the archive has one. Being ours, it and the funcs are *replaced* on each send. On import,
`_apply_archive_study` adopts `solver_info` and writes the funcs to
`<working dir>/inputs/funcs/`, keeping a differing existing file as
`*.before_import_<stamp>.py`. One vocabulary for both: `study_manifest.KEYS`; the OMEX
role names (`cellml`/`obs`/`params`) stay internal to `omex_import`.

Three traps:

- **Classify JSON members by manifest format and content, never by "leftover
  `.json`".** A PhLynx archive ships `simulation.json` declared as plain
  `application/json` — the same format a real obs_data carries — so the manifest
  alone cannot separate them; `_looks_like_obs_data` sniffs for an obs_data (a bare
  list too, so `parse_obs_data` can refuse it naming `cuflynx-upgrade`). Upstream is explicit that `flow.json` may be
  **renamed**, so its format specifier (`application/x.vnd.phlynx-flow+json`) is the
  contract and its name is not. The embedded study manifest is classified the same way
  (`_declared_study`) and taken out of every other pool first, or a prefix containing
  "param" would make it the params_for_id. A params_for_id must be matched against `.json` too:
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

**A module-library archive opens at its calibrated values**
(`apps/api/library_calibration.py`). circulatory-autogen-modules ships
`<instance>_calibration.json` (`calibrated_parameters`, `cost`, `method`, `date`)
and `<instance>_calibrated_parameters.csv`. `omex_import` claims both as the
`calibration` role -- by `calibrat` in the name, and a JSON by a
`calibrated_parameters` object -- *before* the params_for_id and obs_data pools
are filled (the CSV's name contains "param"). `import_omex_bytes` writes the values
into the model with `calibrated_cellml` before parsing it, resolving the library's
bare names through the params_for_id rows, so the sliders, the entries'
`initial_value` and a later send all start from them; the response's `calibration`
says so and the UI's notice names the cost. Unmatched names are a warning.

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

**`cuflynx://open?url=<archive URL>` and `?study=<file: URL>` links** (`apps/api/open_link.py`,
`apps/desktop/url_scheme.py`). A page links to an archive by **URL**, never by
content: a scheme handler delivers the link through the OS process-launch path,
where truncation bites in the low thousands of characters. What is load-bearing:

- **A link stages, like a PhLynx delivery.** Any page can launch a `cuflynx://`
  link, so the app fetches the archive (https/http/file only, capped at
  `inbox.MAX_DELIVERY_BYTES`) and puts it in the inbox; the same dialog asks, and
  accept runs `import_omex_bytes`. A `study=` link (a `file:` URL of a study
  manifest or its directory; http(s) refused) stages the manifest as inbox kind
  `study`; accept returns `kind: "study"` and what `POST /api/working-dir/manifest`
  returns, and the UI opens that working dir as a dropped manifest does.
  Exactly one of `url` / `study`. A link that fails is recorded with
  `inbox.report_error` and returned once by `GET /api/inbox` as `error`, which the
  UI shows (`peekInbox` throws with `deliveryFailed`).
- **Single instance by an instance file, not a port.** The shell writes
  `<config dir>/instance.json` (port + random token, 0600) once its server
  answers; a second launch with a link posts it to `/api/open-link` with the token
  in `X-CUFLynx-Token` and exits. The route is off unless the shell set a token,
  and a page cannot read the token, so the app cannot be used to fetch URLs for a
  page. A stale file (nothing answering, or a different token) means start anew.
- **Every platform runs `<exe> --open-url <link>`.** The release is a bare
  executable per OS with no installer, so the app registers itself per user on a
  frozen launch (`register_if_needed`, redone when the executable moves): a
  `.desktop` + `xdg-mime` on Linux, `HKCU\Software\Classes\cuflynx` on Windows,
  and on macOS an AppleScript applet in `~/Applications` whose Info.plist declares
  `CFBundleURLTypes` -- LaunchServices only routes schemes to bundles, and sends the
  URL as an Apple event (`on open location`), which the applet turns into argv.

**GUI config editing** (edit CA config files in the browser → download dated copy → apply immediately):

- **obs_data.json** — `EditObsDataDialog.vue` mounts **`@physiomelinks/protocol-kit`'s `ObsDataEditor`** (the editor PhLynx mounts too) on the study's whole obs_data document (`GET /api/obs_data/document`): `protocol_info` on a timeline, `data_items`, `prediction_items`, `prediction_plots`, and every key it does not know, which it keeps. Reading, editing, checking and expanding protocol shapes (`expandShape`, which `lib/plot.js` also uses) are protocol-kit's, checked against CA by its golden vectors — fix them there, not here. Time-varying inputs are written as **`protocol_shapes`** (Myokit's `[[protocol]]` vocabulary), which CA expands into `protocol_traces` on read (CA#339). Save writes the dated copy (`versionedJsonName` in `lib/obsDataJson.js`) and waits while protocol-kit reports what CA would refuse. The vocabulary comes from `apps/api/obs_options.py` (`GET /api/obs_data/options`, read with protocol-kit's `readObsDataOptions`), which introspects CA registries — **never hardcode** operations/cost_types/data_types/plot_types. A host can give the dialog a `document`, a `save` and `preselect` parameters (lanes with no value, `withParameters`): the Workflow tab edits a step's own obs_data that way. `apps/api/obs_cost.py` calls cost funcs through CA's `call_cost_func`, so the panel's cost is the call the calibration makes: `std`/`weight` only when the signature declares them, plus the data_item's `cost_kwargs`.
- **Calibrating PhLynx selections (PhLynx #492, CA #594)** — a workflow step whose model is `{"select": [...]}` calibrates instances of its module-array target. An imported archive whose workflow has any (`calibration_workflow.select_steps`) opens it straight away. CA's `UnsetParametersError` (a boundary the selection cuts, with no protocol) reaches the GUI as a 422 from `/api/workflow/view` with `detail.unset_parameters {step_id, names}`, and from a run as the runner's `unset_parameters` event; either opens the step's own obs_data (`/api/workflow/step_input`, target names, not the tab's `mod` copy) with those names preselected. A study sent to PhLynx from a workflow tab also carries the target's module array and its parameters with the merged values, top level, once every step has a current result: CA writes them as nested JSON (`write_calibrated_parameters_to`) and `workflow_manager.phlynx_parameters_csv` converts that to the `<prefix>_parameters.csv` PhLynx reads (`parameter_rows`: `<instance>/<variable>`, `heart/lv/E_A`, a global by its name). `tests/fixtures/phlynx_send_back_parameters.csv` is that CSV, and PhLynx's tests read it. A step's params_for_id is written as JSON; a picked CSV is converted.
- **params_for_id.json** — `EditParamsDialog.vue` + `apps/web/src/lib/paramsCsv.js`; edits ranges/selection, writes a dated JSON, can apply best-fit to sliders. **An uploaded CSV is converted to JSON on the way in** (by CA, which owns the conversion; `params_json.csv_to_file_text`) and stored as `<study>_params_for_id.json`: CA reads no other form. Without CA, `params_json._csv_to_json_without_ca` does the same mapping (the packaged app starts with no CA directory set); `test_params_csv_fallback_matches_ca` pins it to CA's.

**User-authored funcs** (`apps/api/user_funcs.py`) come in **three** kinds, one
file and one CA config key each, all under `<working dir>/inputs/funcs/` (an older
folder's `user_funcs/` is used where it already holds them):
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

**An archive's funcs are installed on import.** A `.py` member named
`*operation_funcs*` / `*cost_funcs*` / `*modifier_funcs*` (`omex_import.user_func_kind`
— by name, never by manifest format, since an external_python model is
`text/x-python` too) is split into its top-level `def`s and each goes through
`save_user_func` into the store under the upload's `output_dir`, exactly as if typed
into Custom funcs. Never clobbers: an identical func is `unchanged`, a *different*
one of the same name is a `conflict` (kept, warned); a def the dialog would reject is
`invalid` (warned); the study loads regardless. Only defs travel, so module-level
names the stored header does not provide are named in a warning. The response's
`user_funcs` lists each with its status, and the inbox summary lists funcs members
so the confirmation dialog can say a delivery carries code before it is accepted.
**The study manifest wins:** an archive carrying a readable study manifest
(`application/x.vnd.cuflynx-study+json`) installs only the funcs files it declares, whole
(`main._apply_archive_study`); its funcs-named members are then listed with status
`manifest` and not installed a def at a time, so nothing is installed twice.

**A study's user_inputs.yaml sets the solver** (`main._adopt_solver_settings`). A
`*user_inputs*.yaml` in an `.omex` without a study manifest (with one, the manifest's
`solver_info` is adopted instead and the yaml is not read), in a reopened run directory (when its
`cuflynx_study.json` records no `solver_info`), or picked in Settings
(`POST /api/user_inputs/upload`) has its `solver`, `solver_info` and `dt` adopted
exactly as a Settings change: env, saved settings, `engine.reset()`. Keys the solver
cannot honour (`MaximumNumberOfSteps` under `CVODE_myokit`) and solvers the format does
not offer (OpenCOR) are **dropped with a warning, never a 422** on import. Responses carry
`solver_settings: {solver, solver_info, dt, source, ignored}` (null when there was none),
and the frontend re-reads `/api/config` on every model load so Settings is never stale.
Runs get `engine.dt` server-side (`_with_engine_dt`) unless the request names a `dt` —
the analysis panels therefore send none; a literal there overrode the adopted step.

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

**The webview is not a browser, and its backend differs per OS.** pywebview picks
Cocoa/**WKWebView** on macOS, GTK/**WebKitGTK** on Linux and **WebView2** on
Windows, and they do not agree on what the page is allowed to do. Two rules follow,
both learned from shipped bugs (#340, #114):

- **Never open an external URL from script.** Render a real
  `<a target="_blank" rel="noopener">` and let the user click it. pywebview's macOS
  backend forwards a new-window request only when
  `action.navigationType() == WKNavigationTypeLinkActivated` — a genuine link
  click — so a `window.open()` is `WKNavigationTypeOther` and is dropped silently.
  Linux happened to work because the GTK backend keys off the `_blank` frame name
  instead, which is why "Send to PhLynx" worked everywhere except the packaged Mac
  app. `apps/web/src/lib/externalLinks.test.js` pins this as a *source* contract,
  because no DOM-level runner — jsdom, happy-dom, even Playwright's WebKit — models
  the embedder that breaks it.
- **Never download from a `blob:` URL.** Serve the bytes from the API and link to
  that URL (`/api/phlynx/archive/...`, `CalibrationPanel.vue`'s
  `calibratedModelUrl`). The macOS download path cancels the navigation and
  re-fetches with `NSURLSession`, which cannot read `blob:`. Downloads also need
  `webview.settings['ALLOW_DOWNLOADS'] = True`, set in the shell before `start()`;
  pywebview defaults it off, which had silently disabled every download in the
  packaged app on all three platforms.

Setting pywebview *settings* in `apps/desktop/app.py` is shell configuration and
does not breach the no-JS-bridge rule above — the frontend stays untouched.

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
- **A C compiler cannot be bundled away — but only the compiling cellml solvers need one.**
  Of CA's solver wrappers, `myokit_helper.py` (CVODE_myokit) and `batched_c_helper.py`
  (BE_batched_c, which also needs **CMake**) compile each model at run time;
  `python` (scipy `solve_ivp`) and `casadi_python` (`casadi_integrator`) are
  compiler-free and both are verified working in the frozen app. So a missing
  compiler is a **warning, not an error**: `compiler_check.py` detects it,
  `GET /api/config` returns `cpp_compiler: {present, hint, affects, alternatives,
  unavailable_solvers}`, and `App.vue` shows a `warn` banner naming the backends that
  still work. Don't regress this to an error. BE_batched_c is instead **withheld from
  the solver menu** when CMake or the compiler is missing (`unavailable_solvers`, read
  by `solver_options`), and Settings says why. `scripts/install.py` shares the
  detection — keep it in one place.
- **BE_batched_c reads `MaximumStep` and `MaximumNumberOfSteps` differently from CVODE**
  (no step cap by default; a step budget per block over the whole run), so CVODE's
  defaults — CA's ProtocolRunner's 0.001/5000, its parser's 5000, the runners' 1e-4 —
  must never reach it: `solver_info_defaults.py` puts the solver's own declared
  defaults under the settings first, in the engine (`run_solver_info`, which also adds
  its `build_dir` under the user cache) and in every runner. Its build-cache key is a
  digest of two of libcuflynx's own source files, which is why the spec collects
  those as data (`_SOURCES_READ_AT_RUN_TIME`).

- **The browser fallback must still end the process.** A Linux build has no
  GTK/Qt bindings, so pywebview cannot open a window and the shell opens the
  system browser instead. Closing a tab ends no process and a file-manager launch
  has no terminal to Ctrl+C, so the shell must stop itself, or the server and its
  `/tmp/_MEI*` dir live until logout. Tabs report through
  `/api/presence/{beat,leave}` (`lib/presence.js` → `apps/api/presence.py`) and
  `wait_until_abandoned` stops the server once the last one has gone. Never wait
  on an Event nothing sets (`test_no_browser_mode_waits_forever` checks). Keep
  `stale_after` above Chrome's one-run-a-minute throttle for hidden tabs, or a
  backgrounded tab reads as closed.

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

### PR review guides

When asked for a review page for a PR, publish an HTML artifact with:

- **What it does**, in the PR's main parts, from the current diff rather than the PR description; say where the description is out of date.
- **Multiple-choice questions**: four options, one correct; picking one marks it right or wrong at once and shows the reason with a `file:line` reference, with a running score and a reset.
- **No give-aways**: options of similar length (the correct one is not the longest or shortest more than chance would make it), correct answers spread evenly over A-D, and wrong options that are plausible misreadings of the code.
- **Before you merge**: merge-order holds, conflicts with other open PRs, stale descriptions, and what a local test run needs.

Check every answer against the code at the PR's head before publishing.

## Security caveats (localhost-only assumptions)

The backend assumes a single-user, localhost deployment and exposes the host filesystem to any client that can reach the API:

- **`GET /api/fs/list`** (`apps/api/main.py`) — the in-app file/folder browser (Python interpreter + working dir pickers) lists arbitrary server directories, defaulting to `$HOME`. No path confinement.
- **`working_dir`** (every run, and uploads) — writes inputs and outputs to any absolute path the client supplies.

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
