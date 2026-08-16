# Tutorial screenshots

Images referenced by the tutorials in `tutorials/docs/`. They are linked as
`images/<name>.png`, so **renaming one breaks the page that uses it** — grep before
you move anything.

House style: PNG, taken at a window width where the labels are readable (~1400 px
is plenty), cropped to the panel being discussed rather than the whole desktop.

## What is here

All five are the FEniCSx heat model from
`circulatory_autogen/funcs_user/heat_fenics/`, and all are used by
[`external_python.md`](../external_python.md).

| File | Shows | Used in |
|---|---|---|
| `drag_inputs-fenics-heat.png` | the `heat/k` / `heat/u_D` sliders being dragged, probe traces redrawing | *Running it* |
| `extra-plots-fenics-heat.png` | the field heatmaps `extra_plots()` returns, at the end of the Output plots tab | *Running it* |
| `edit-obs-data-fenics-heat.png` | the obs_data editor: a data_item's `value` / `std` and the operand picker | *Telling CUFLynx what to fit* |
| `edit-params-for-id-fenics-heat.png` | the params_for_id editor: which parameters are calibrated and their bounds | *Calibrating it* |
| `emulator-settings.png` | the Emulator tab: training settings and the tick box that makes calibration / SA / UQ use the surrogate | *Calibrating it* |

## Reproducing them

The state every shot was taken from:

1. Build the `fenicsx` environment as in
   [Installing the model's dependencies](../external_python.md#installing-the-models-dependencies).
2. Start CUFLynx, open **Settings**, set **Python** to that environment's
   interpreter and **CA dir** to your circulatory_autogen checkout.
3. Drop `funcs_user/heat_fenics/heat_fenics_model.py` on the model box, and its
   `heat_fenics_obs_data.json` / `heat_fenics_params_for_id.csv` on the other two.
4. Set **t₁** to `2.0` and **dt** to `0.02` — the grid the model is sized for.
5. Run once, so the traces, the reference lines and the `extra_plots()` figures
   are all on screen.

The emulator shot additionally needs a trained emulator (Emulator tab → **Train**),
which needs `autoemulate` in the interpreter chosen in Settings — CA's optional
`emulation` extra, not part of `dev`.
