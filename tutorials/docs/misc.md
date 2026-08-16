# Using CUFLynx

Everything here is optional reading — the app runs without any of it. See the
[README](../../README.md) to download and start it.

## The guided tour

The **Tutorial** button at the top right walks through a whole study on the
bundled 3-compartment circulation model: getting a model, the settings, the
sliders and plots, `obs_data` and `params_for_id`, then the emulator,
sensitivity, calibration and UQ. It is the fastest way to find out what the app
is for.

Three things about how it behaves, because they are deliberate:

- **It waits for you; it never clicks for you.** A step that asks you to open a
  dialog sits there until you open it. Every bubble also has **Next** and
  **Back**, so a wrong click cannot strand the tour — you can always walk past a
  step you have already done.
- **It skips what is not on screen.** A step whose control does not exist in the
  state you are in — a dialog you chose not to open, an emulator your Python
  environment cannot train, a tab that is not the active one — is silently
  passed over rather than blocking. Start it with a model already loaded and the
  first few steps about *getting* one evaporate.
- **Skip ends it.** There is no Escape handler, because Escape belongs to
  whichever dialog you are in.

The button asks once: until the tour has been started or skipped it pulses
gently. After that it is an ordinary button, and pressing it starts the tour
again from the beginning.

## Solver backends, and the one that needs a C compiler

CUFLynx works out of the box with no compiler. Of the three solver backends
(**Settings → Generated model format**), only one needs a C toolchain:

| Backend | Solver | Needs a C compiler? |
|---|---|---|
| `python` | scipy `solve_ivp` | no |
| `casadi_python` | `casadi_integrator` | no |
| `cellml` | `CVODE_myokit` | **yes** |

Myokit compiles each CellML model to a native extension when it runs, and that
toolchain can't be shipped inside the app. If it's missing CUFLynx warns and you
pick one of the other two. To enable `CVODE_myokit`:

- **Linux** — `sudo apt install build-essential`
- **macOS** — `xcode-select --install`
- **Windows** — [Microsoft C++ Build Tools](https://visualstudio.microsoft.com/visual-cpp-build-tools/) ("Desktop development with C++")

Sundials/CVODE itself is bundled — you do *not* need to install it separately.

## AADC — a fourth backend, licensed separately

`aadc_python` records the forward integration on a tape and replays it, giving an
exact gradient from a single evaluation, which suits gradient-based calibration.
It needs [AADC](https://matlogica.com/) from Matlogica: proprietary, **free for
academic use**.

1. Request an academic licence at <https://matlogica.com/>.
2. Install the wheel **into the Python you use for analysis runs**
   (**Settings → Python**) — that is where calibration, sensitivity and UQ
   execute. Installing it only into the app's own environment leaves those runs
   failing.
3. Restart CUFLynx.

`aadc_python` is only listed once the library actually imports; offering a
backend that can't run would just move the failure to your first calibration.
Settings says whether it was found.

## Myokit models

Drop a Myokit `.mmt` on the model box and it is converted to CellML on the way
in. **Only the `[[model]]` section is imported**: in CUFLynx the protocol lives in
`obs_data.json`, so baking Myokit's stimulus into the CellML would give the model
two sources of pacing that disagree.

The `[[protocol]]` section is carried across separately. If you have no obs_data
loaded, dropping the `.mmt` creates `<model>_obs_data.json` from it, saves it to
the outputs directory and loads it — so the model is paced as Myokit paced it. An
obs_data you dropped yourself is never replaced by a derived one. It arrives with
no `data_items` (what the model should be measured against isn't in the `.mmt`),
so add those via **Edit**.

Events cross over unchanged, under Myokit's own names, as a `protocol_shapes`
entry:

```json
"sim_times": [[2000.0]],
"params_to_change": {"engine/pace": [["engine_pace"]]},
"protocol_shapes": {
  "engine_pace": {"events": [{"level": 1.0, "start": 100.0, "length": 2.0,
                              "period": 1000.0, "multiplier": 0}]}
}
```

so the file still says "1 Hz" after it is written, and the period can be edited
rather than recomputed. The obs_data editor writes every time-varying input this
way — constant, ramp, step, pulse and paced — and reads them back as the fields
you typed. **Needs a circulatory_autogen with `protocol_shapes` support**
([CA #339](https://github.com/physiomelinks/circulatory_autogen/issues/339));
hand-written `protocol_traces` point tables are still accepted and preserved
untouched.

An archive can be built around a `.mmt` instead of a CellML — see
`resources/br-1977.omex`, which holds the Myokit model and a `params_for_id` and
no obs_data at all. Dropping it converts the model, loads the parameters and takes
the protocol from the `.mmt`, so the study runs from one drop. An obs_data *in*
the archive always wins over the model's own protocol.

The same conversion is available from the command line, which is how to re-derive
a protocol into an obs_data you have already written:

```bash
python scripts/mmt_to_obs_data.py resources/br-1977.mmt
```

That writes (or updates) `br-1977_obs_data.json`, filling in `protocol_info` from
the `[[protocol]]` section. Updating keeps everything else, so hand-written
`data_items` survive. A Myokit protocol usually repeats forever while a CUFLynx
experiment is a finite list of durations, so it takes `--beats` (default 2) or an
explicit `--duration`.

## Replotting a run outside the app

**Export plotting script** writes `plot_outputs.py`, which regenerates the app's
plots from a run's data into a **`pyscript_plots/`** folder — so a directory of
results doesn't gradually become a directory of results *and pictures of results*.

```bash
python plot_outputs.py                       # find the data automatically
python plot_outputs.py --output-dir <dir>    # a specific run directory
```

It finds the data on its own: `output/` beside the script when an exported
pipeline made one, otherwise the script's own folder — which is where CUFLynx puts
it, alongside circulatory_autogen's run directories. So after a calibration or
sensitivity run, **Export plotting script** then `python plot_outputs.py` just
works.

Two files are written. **`plot_outputs.py`** is yours to edit and the one you run:
a `STYLE` block, one named function per fitted observable (generated from your
obs_data, with the variables written in), and one function per figure. Change a
plot by editing its function; drop it by removing it from `FIGURES`.
**`plot_utilities.py`** finds the run and reads its files, and you shouldn't need
to open it.

Sensitivity plots drawn by the app itself land in **`SA_plots/`** inside the run
directory, for the same reason.
