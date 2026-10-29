# The working dir

The **Working dir** is the folder that holds one study: every input it was run
from and every output it produced. It replaces the field the app used to call the
"Outputs directory", because the folder now holds the inputs too.

Set it under **Working dir** in the import panel (type a path, or browse for a
folder) before you run anything. Calibration, sensitivity, UQ, emulator training
and calibration workflows all refuse to start without one, and say so: a run has
to have somewhere to keep what it was run from.

## What is in it

`<W>` is the working dir. `<prefix>` is the name of the model file you loaded
(`3compartment` for `3compartment.cellml`), and `<obs>` the name of the obs_data file.

```
<W>/
├─ <study>_cuflynx_study.json        the study manifest: every part of the study, paths relative to <W>
├─ inputs/                           every input of the study, copied in when it is loaded or first used
│   ├─ model/
│   │   ├─ <prefix>.cellml           the model as it runs (a multi-file CellML is flattened into one)
│   │   ├─ source/                   a multi-file CellML as you dropped it, with the files it imports
│   │   ├─ <prefix>.mmt / .model     the Myokit or EasyML file a model was converted from
│   │   ├─ <prefix>.omex             the COMBINE archive a study was loaded from, kept whole
│   │   └─ user_model.mmt / .model   the copy "Edit source" opens for a converted model
│   ├─ obs_data/
│   │   ├─ <prefix>_obs_data.json    the obs_data the study runs with
│   │   └─ <stem>_<yymmdd>.json      copies saved from the obs_data editor
│   ├─ params_for_id/
│   │   ├─ <prefix>_params_for_id.json   the params_for_id the study runs with (a CSV is converted)
│   │   └─ <stem>_<yymmdd>.json      copies saved from the params_for_id editor
│   ├─ user_inputs/
│   │   ├─ user_inputs.yaml          a user_inputs.yaml loaded in Settings, as given
│   │   └─ user_inputs_<yymmdd>.yaml the dated copy a libcuflynx script run archives
│   ├─ funcs/
│   │   ├─ operation_funcs_user.py   your observable operations
│   │   ├─ cost_funcs_user.py        your cost functions
│   │   ├─ modifier_funcs_user.py    your parameter modifiers
│   │   └─ user_model.py             an external_python model (this copy is the one that runs)
│   ├─ system/                       a generated model's module array and parameters file (libcuflynx)
│   ├─ emulator/                     an emulator a script run used from outside <W> (libcuflynx)
│   ├─ workflows/<wf>/               a calibration workflow and the inputs packed with it
│   ├─ generated_models/<prefix>/    a Myokit / EasyML model converted to CellML, PhLynx's module_config
│   ├─ <prefix>_variable_mapping.csv the LaTeX symbol of each variable
│   ├─ obs_extraction_config.json    the choices made in "Add from dataset"
│   └─ uq_obs_data.json              the obs_data a UQ run scored with (its MLE cost type set)
├─ <prefix>_calibrated.cellml        the model with the best fit written in
├─ <method>_<prefix>_<obs>/          one per calibration or UQ run (circulatory_autogen's case dir):
│                                    best fit, progress history, error vectors, MCMC chain, plots,
│                                    and the obs_data and params_for_id the run was made from
├─ sensitivity/                      Sobol indices and local sensitivity, with their plots (SA_plots/)
├─ emulators/<prefix>_<obs>/         trained emulator bundles
├─ workflows/<wf>/                   a calibration workflow's run: one folder per step and its result
├─ saved_params/                     parameter sets saved from the sliders (.npy / .csv + _outputs.json)
└─ export_<yymmdd>/                  an exported, runnable script bundle
```

Nothing the study needs is outside `<W>`. The app still keeps a working copy of
each upload in a temporary folder, which it clears after 7 days; the copies in
`inputs/` are the ones that last.

## Sending a study to someone

Every input is copied into `inputs/` and every path in the study manifest is
relative to the folder it is in. So the folder is the whole study:

```bash
zip -r BVC_RAAS6.zip BVC_RAAS6/
```

Whoever receives it unzips it anywhere and opens it in CUFLynx in either of two ways:

- **drop the study manifest** (`<study>_cuflynx_study.json`) onto the import
  panel, or pick it with the file button beside the Working dir field. The app sets
  the working dir to the folder holding it and loads it;
- **set the Working dir** to the unzipped folder and press the reload button
  (the circular arrow) beside it.

Either way the model, obs_data, params_for_id, user funcs and solver settings come
back, with the best fit, the progress history, the sensitivity results, the UQ
posterior and the emulator that were in the folder.

In the browser (rather than the desktop app) a dropped file does not say which
folder it came from, so there set the Working dir to the folder instead.

## A libcuflynx script run writes the same folder

A study run from a script with libcuflynx (circulatory_autogen) produces the same
layout. Its `param_id_output_dir` is the working dir:

- the study manifest is written there, and each input from outside it (the model
  with the CellML files it imports, obs_data, params_for_id, the user_inputs yaml,
  the user funcs, a generated model's module array and parameters file, an emulator)
  is copied into `inputs/<kind>/` once per content. Two different files of the
  same name go into `inputs/<kind>/<hash>/`;
- the dated `user_inputs_<yymmdd>.yaml` goes into `inputs/user_inputs/`;
- sensitivity analyses write to `sensitivity/` unless `sa_options: output_dir`
  names another folder;
- a calibration workflow packs its workflow into `inputs/workflows/<name>/`.

So the folder a script run leaves opens in CUFLynx like one the app made. To keep
the old behaviour (inputs left where they are, recorded by absolute path) set
`cuflynx_study_copy_inputs: false` in `user_inputs.yaml`. See "The study working
dir" in circulatory_autogen's parameter-identification tutorial.

## Opening an older folder

A folder written before the working dir still opens, and nothing in it is moved:

- **Its manifest**, which recorded inputs by absolute path and the folder's own
  path as `study_dir`, is read relative to where the folder is now. A path inside
  the old `study_dir` is found in the moved folder; an input that is no longer where
  it was recorded (an upload the app has since cleared) is found under
  `inputs/<kind>/` by its name, if it has been copied there.
- **Its funcs** in `user_funcs/` are read, run and edited where they are. A folder
  with no funcs yet gets them in `inputs/funcs/`.
- **Its sensitivity results** at the top level, **its saved parameter sets** at the
  top level, a variable mapping at the top level and models in `generated_models/`
  are all read from where they are.

New inputs and outputs go to the new places, so an older folder you keep working
in gains an `inputs/` folder beside what it had.

The app's request names changed with the label: `working_dir` replaces
`output_dir`, `outputs_dir` and `config_outputs_dir`, and `/api/working-dir/*`
replaces `/api/outputs/*`. The old names and routes are still accepted for one
release. A working dir chosen in an earlier version is remembered: it is moved
from the `cuflynx-outputs-dir` browser setting to `cuflynx-working-dir` the first
time the app starts.
