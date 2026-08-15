# Tutorial screenshots

Screenshots referenced by the tutorials in `tutorials/docs/`. Save each one here,
under **exactly** the filename below — `external_python.md` links to them as
`images/<name>.png`, so a different name is a broken image.

PNG, taken at a window width where the labels are readable (roughly 1400 px wide is
plenty). Crop to the panel described rather than shipping the whole desktop.

## Setup common to all three

1. Build the `fenicsx` environment as in
   [Installing the model's dependencies](../external_python.md#installing-the-models-dependencies).
2. Start CUFLynx, open **Settings**, set **Python** to that environment's
   interpreter and **CA dir** to your circulatory_autogen checkout.
3. Drop `circulatory_autogen/funcs_user/heat_fenics/heat_fenics_model.py` on the
   model box.
4. Set **t₁** to `2.0` and **Time step (dt)** to `0.02`.
5. Drop (or build in the editor) `heat_fenics_obs_data.json` from the same folder,
   and `heat_fenics_params_for_id.csv`.

## `edit-obs-data.png`

**Shows:** the obs_data editor, and specifically where the ground truth is typed in.

- Click **Edit** beside the obs_data box.
- Scroll to the **data_items** list and expand one row with its chevron — the
  `near probe mean` row is the one the tutorial quotes.
- Must be visible in frame: the `Variable / value / std / operation / exp / sub`
  column headers, at least one row with a non-empty **value** and **std** (0.47 and
  0.02 for that row), the **operation** dropdown showing `mean`, and in the expanded
  details panel the **operands** picker showing `heat/T_p1`. `cost_type`, `weight`,
  `unit` and `plot_type` in the same details panel if they fit.

## `output-plots.png`

**Shows:** where the automatic per-observable plots come from *and* where the
`extra_plots()` figures appear.

- With the model and obs_data loaded, add sliders for `heat/k` and `heat/u_D` and
  run once so the plots are populated.
- Select the **Output plots** tab and scroll so both kinds of cell are in frame.
- Must be visible in frame: at least one probe trace (`heat/T_p1` …) with its dashed
  obs_data reference line and the solid model feature line, **and** at least one of
  the two `extra_plots()` field heatmaps at the end of the grid (the square colour
  map of `u` with p1/p2/p3 marked). The **cost** line above the plots should be in
  frame if possible.

## `emulator-tab.png`

**Shows:** that an emulator can be trained for an external python model, so
calibration, SA and UQ run against the surrogate.

- Needs the `[emulation]` extra installed in the chosen interpreter, or the tab
  shows the "unavailable" explanation instead.
- Open the **Emulator** tab, train one (the defaults are fine — a few dozen samples
  on this model is seconds), and wait for it to finish.
- Must be visible in frame: the tab itself selected, the training settings
  (`num_train_samples`, `sample_type`, `models`), the **Train** button in its
  finished state, and the **Use the emulator for sensitivity / calibration / UQ**
  tick box — ticked, so the shot shows the surrogate being handed to the analysis
  tabs. Any per-feature validation
  numbers the panel reports afterwards are a bonus.
