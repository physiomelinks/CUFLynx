# Changelog

## 0.8.0 (unreleased)

Requires libcuflynx 0.8.0 (libCellML 0.7.2 or later).

### Breaking changes
- Only libcuflynx's current forms are read: `ParamID`/`MCMC`, `run_UQ` with `uq_options`, `do_uq`, and JSON `params_for_id`. The old names, the `mcmc_options` and `cellml_only` keys and the `bdf_tape`/`bdf_kernel` entries are gone. (#387)
- An obs_data without `protocol_info`, or a bare list, is refused with libcuflynx's error; `cuflynx-upgrade obs-data` converts an old file. (#387)
- An uploaded params_for_id CSV is converted to JSON on the way in. (#387)
- The Outputs directory is now the Working dir, and every run needs one. (#397)
- The Emulator tab is renamed Surrogate. (#415)
- libCellML 0.7.2 or later is required. (#387, #410)

### Calibration workflows and the Workflow view
- The Workflow tab opens, runs and steps through a calibration workflow, from submodules to the supermodule. (#369)
- Every calibration, sensitivity analysis and UQ run is a step of one growing workflow per working dir, with CA's provenance diagram shown as the workflow grows. (#414)
- The Workflow view is an editable diagram of the study workflow; a run from a panel changes its step, and New step or New workflow adds one. (#417)
- Workflow steps and targets can be external Python models, with step results and provenance shown. (#413)
- Calibration workflows can use CellML models, `start_from`, UQ steps and per-step error vectors. (#414)
- The LaTeX symbols dialog in the Variables panel edits a model's variable mapping for the generated methods text. (#370)
- Calibrate a selection of PhLynx instances: unset boundaries, step inputs and send-back are supported, and archives with selection steps open straight away. (#388)
- An OMEX carries a whole calibration workflow, packed on export and unpacked on import. (#386)

### PhLynx exchange
- A modular PhLynx OMEX opens: the SED-ML master is followed and archive paths are kept. (#428)
- A modular study is sent back as PhLynx's own files, with the new values written into the model that holds the parameter components. (#429)
- PhLynx's generated components are found by their ids, so a modeller's component with the same name is not mistaken for PhLynx's. (#426)
- OMEX import picks obs_data and params_for_id by name and content, and loads none instead of the nearest-looking file. (#364)
- An OMEX's operation, cost and modifier functions are installed on import, so its calibrations run straight away. (#367)
- Opening an OMEX applies a module-library calibration's values. (#389)
- `cuflynx://open?url=` opens a COMBINE archive from a link, after a confirmation dialog. (#389)
- `cuflynx://open?study=` opens a study directory from a link. (#393)
- A nightly and on-demand round trip against PhLynx checks the exchange in both directions. (#427)

### Studies and working directories
- The Working dir holds every input and output of a study; each input version is stored under a dated folder and `inputs/index.json` lists them. (#397)
- A study opens from libcuflynx's study manifest, which the OMEX also carries. (#385)
- A dropped `.zip` of a working dir is extracted where the user says and loaded. (#401, #402)
- A study's `user_inputs.yaml` solver, solver settings and dt apply to the live plot and to every run. (#368)
- Choose which emulator bundle a study is read with, listed with its worst-feature R2, instead of the newest. (#400)
- The pipeline export uses libcuflynx's bundle writer, and MCMC results are read from the new run layout, falling back to the old one. (#409, #423)
- On Linux, the process and its temporary files are removed when the last browser tab closes. (#379)

### Data and obs_data
- Add from dataset in the obs_data editor reads patch-clamp recordings, groups them, applies data modifiers and builds an obs_data. (#324, #325, #326, #327)
- A data modifier can read the recording's other channels, and validation recordings become held-out prediction items. (#380)
- The Analysis tab validates the best fit against held-out data and shows the result. (#365)
- Prediction items with an operation can be included as features in sensitivity analysis and emulator training. (#366)

### Analyses
- The Identifiability tab runs parameter reduction and identifiability analysis. (#412)
- The Surrogate tab has a Reduced-order model section, built and fitted through libcuflynx. (#415, #416)
- The Analysis tab's error bars come from the error vectors libcuflynx writes, including for a stopped calibration. (#424)
- Left-hand tabs show standard settings first and the rest under a collapsed Advanced settings section. (#419)

### Solvers and settings
- `BE_batched_c` is offered, runs live, in protocols and in all analyses, and has its own step defaults. (#384)
- `CVODE_libopencor` is listed only when libOpenCOR is importable. (#408)
- The top bar shows the selected solver; hovering shows its settings and clicking opens Settings. (#420)
- A newer live run stops a long one in flight, and the status bar has a Cancel button. (#421)

### Desktop app and packaging
- The desktop bundle excludes the proprietary AADC and aadc-ode packages. (#392)

### CI and tests
- CI pins ubuntu-24.04, runs on push for `main` only, and checks the libcuflynx pin on pull requests. (#357, #381, #403, #425)
- A runner's config is cleared before the job reports it finished, which removes a test race. (#358)
- Tests check that CUFLynx and the notebook route give the same results. (#411)
