### Summary

Allow users to upload a **`obs_data.json`** file (circulatory_autogen format) so the app:
1. Runs the protocol defined in `protocol_info` instead of the manual t₀/t₁/N controls.
2. Overlays ground-truth `data_items` on the chart for visual comparison.

### Plan

**1. Backend — upload and parse**

New endpoint `POST /api/obs_data/upload`:
- Accept JSON body or multipart file.
- Validate via `PrimitiveParsers().parse_obs_data_json(obs_data_dict=...)`.
- Store parsed result in session keyed by `model_id`.
- Return summary: `{ n_experiments, n_data_items, n_prediction_items, experiment_labels }`.

Required fields: `protocol_info` → `pre_times`, `sim_times`. Optional: `params_to_change`, `protocol_traces`, `offline_pre_time`, `prediction_items`.

**2. Protocol driving**

When `obs_data.json` is loaded, `POST /api/protocol/run` (Issue 3) automatically uses `obs_data["protocol_info"]` — the frontend no longer needs to send `protocol_info` in the body; it just sends `{ model_id, params }`.

`params_to_change` values that are strings are trace keys in `protocol_traces` (time-varying forcing). Pass through to `ProtocolRunner` unmodified — the `ProtocolExecutor` and `myokit_helper` handle the trace-to-pace binding.

`offline_pre_time` (scalar, optional): passed as `inp_data_dict["pre_time"]` to avoid re-compiling; the helper runs it once and caches the warmup state.

**3. Frontend — protocol controls**

When `obs_data.json` is loaded:
- Replace t₀/t₁/N inputs with a read-only protocol summary panel showing experiment count, subexperiment durations, and `experiment_labels`.
- Show a clear "Clear obs data" button to restore manual time controls.

**4. Frontend — data overlay on chart**

For each `data_item` in `data_items`:

| `data_type` | Rendering |
|-------------|-----------|
| `constant` | Horizontal line at `value` ± shading for `std` |
| `series` | Scatter/line overlay; use `obs_dt` for x-axis; aligned to `experiment_idx` / `subexperiment_idx` |
| `frequency` | Text annotation or horizontal band |

Use `name_for_plotting` as legend label (supports LaTeX via chart annotation plugin or plain text fallback). Colour per `experiment_idx` matching simulation traces.

`prediction_items` rendered as dashed simulation-only lines (not in cost function / not an observable).

**5. Error handling**

- Missing `obs_dt` for series entry → 422 with `"obs_dt is required for series entries"`.
- `experiment_idx` / `subexperiment_idx` out of range → 422.
- Trace key in `params_to_change` not found in `protocol_traces` → 422.

### Tests

**`apps/api/tests/test_obs_data_upload.py`**

- `test_upload_valid_obs_data_returns_summary` — POST `Lotka_Volterra_obs_data.json`; assert `n_experiments == 1`, `n_data_items == 2`.
- `test_upload_missing_protocol_info_returns_422` — POST `{}`.
- `test_upload_series_without_obs_dt_returns_422` — POST a `data_item` with `data_type: "series"` missing `obs_dt`.
- `test_upload_experiment_idx_out_of_range_returns_422`.

*Integration (`@pytest.mark.integration`):*

- `test_protocol_run_uses_uploaded_obs_data` — upload `resources/Lotka_Volterra_forced.cellml` + `resources/Lotka_Volterra_obs_data.json`, then `POST /api/protocol/run` with only `{ model_id, params }`; assert single-experiment structure, `Lotka_Volterra_module/x` output present, and time is monotonically increasing.
- `test_protocol_run_bg_model_with_minimal_obs_data` — upload `resources/BG_MWC_Huang-Peskin_SS.cellml` + a minimal inline obs_data (`pre_times=[0]`, `sim_times=[[20]]`, no data_items); assert run completes and `main/p_o2` is in outputs.

**`apps/web/src/stores/useObsData.test.js`**

- `test_set_obs_data_updates_experiment_count`.
- `test_clear_obs_data_restores_manual_time_controls`.

**`apps/web/src/components/PlotPanel.test.js`**

- `test_renders_horizontal_line_for_constant_data_item` — mount with one `constant` data_item; assert a dataset with `borderDash` style or annotation is present.
- `test_renders_series_overlay_for_series_data_item`.

### Acceptance criteria

- [ ] Import `resources/Lotka_Volterra_obs_data.json` and run protocol without manually setting time.
- [ ] `x_max` and `y_max` constant observables render as horizontal reference lines on the plot.
- [ ] Manual t₀/t₁/N controls hidden while obs data loaded; restored on clear.
- [ ] Missing `obs_dt` for a series item produces a clear error message in UI and API.

### References

- `resources/Lotka_Volterra_obs_data.json` — primary test fixture (2 constant data_items, single experiment, operands use `Lotka_Volterra_module/x`)
- `resources/BG_MWC_Huang-Peskin_SS.cellml` — secondary fixture for minimal inline obs_data backend test
- `circulatory_autogen/tutorial/docs/parameter-identification.md` — protocol_info and data_items sections
- `circulatory_autogen/src/utilities/obs_data_helpers.py`
- `circulatory_autogen/src/parsers/PrimitiveParsers.py` — `parse_obs_data_json`
- `circulatory_autogen/resources/test_fft_obs_data.json` — frequency data_type example (for future extension)
