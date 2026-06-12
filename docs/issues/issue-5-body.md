### Summary

Allow upload of a **`*_params_for_id.csv`** file so sliders are created automatically — including Myokit-qualified names and min/max ranges — without requiring the user to manually click `+` on every parameter.

### Plan

**1. Backend — upload and parse**

New endpoint `POST /api/params_for_id/upload`:
- Accept multipart CSV.
- Parse with `PrimitiveParsers().get_param_id_info(path)` or `get_param_id_info_from_entries(entries)`.
- Required columns: `vessel_name`, `param_name`, `min`, `max`. Optional: `param_type`, `name_for_plotting`.
- `vessel_name` may contain multiple space-separated tokens (e.g. `"aortic_root venous_root"`) — the parser splits by whitespace and builds a list of qualified names (`aortic_root/param_name`, `venous_root/param_name`). The API response should expose all resolved Myokit qnames.
- Return: `{ params: [{ qname, min, max, name_for_plotting, initial_value }] }` where `initial_value` is looked up from the loaded model's defaults (or `null` if not found).

**2. Frontend — slider seeding**

On receipt of `params_for_id` response:
- Create one slider per entry keyed by `qname` (e.g. `"aortic_root/C"`).
- **Initial value**: use `initial_value` from API if not null, else midpoint `(min + max) / 2`.
- **Log scale heuristic**: auto-enable if `max / min > 1e4` or `min < 1e-3`, matching the current `cellml_explorer.html` logic.
- **Label**: use `name_for_plotting` (display raw string; LaTeX rendering is best-effort).
- Replace any manually added sliders for overlapping params.

**3. Sync with simulation**

Slider values feed `id_param_names` / `id_param_vals` in `POST /api/protocol/run` (Issue 3) or `POST /api/simulate`. The arrays must keep the same order as `param_id_info["param_names"]` from the parser.

**4. UI**

- Import button in slider panel header (alongside the existing `+` on variable rows).
- After import: show chip with filename and row count; allow "Clear" to reset to empty slider set.
- If `obs_data.json` is also loaded (Issue 4), the slider panel and protocol run coexist — slider changes re-trigger `run_protocols` with updated `id_param_vals`.

### Tests

**`apps/api/tests/test_params_for_id.py`**

Use `resources/Lotka_Volterra_params_for_id.csv` (vessel_name `Lotka_Volterra_module`, 4 params: alpha, beta, delta, gamma) as the primary fixture:

- `test_upload_lv_csv_returns_four_params` — POST `resources/Lotka_Volterra_params_for_id.csv`; assert response `params` list has length 4.
- `test_lv_qnames_correctly_formed` — assert qnames are `Lotka_Volterra_module/alpha`, `.../beta`, `.../delta`, `.../gamma`.
- `test_multi_vessel_name_expands_to_multiple_qnames` — POST a CSV row with two space-separated vessel names; assert two entries in response.
- `test_missing_required_column_returns_422` — POST CSV missing `min` column.
- `test_min_greater_than_max_returns_422` — POST row where `min > max`.

*Integration (`@pytest.mark.integration`):*

- `test_simulate_lv_at_alpha_min_and_max` — upload `resources/Lotka_Volterra_forced.cellml` + `resources/Lotka_Volterra_params_for_id.csv`; simulate at `alpha=0.1` (min) and `alpha=7.0` (max); assert `max(x)` differs by > 10 %.
- `test_simulate_bg_model_alpha_o2_slider` — upload `resources/BG_MWC_Huang-Peskin_SS.cellml`; POST a params CSV with `vessel_name=main`, `param_name=alpha_o2`, `min=0.005`, `max=0.05`; simulate at both extremes and assert `main/c_o2` differs.

**`apps/web/src/stores/useParamsForId.test.js`**

- `test_import_creates_slider_for_each_param`.
- `test_initial_value_uses_model_default_when_available`.
- `test_initial_value_uses_midpoint_when_model_default_null`.
- `test_log_scale_enabled_when_range_exceeds_threshold`.
- `test_clear_removes_all_imported_sliders`.

**`apps/web/src/components/ControlPanel.test.js`** (extend from Issue 2)

- `test_import_csv_replaces_manual_sliders_for_same_param`.

### Acceptance criteria

- [ ] Upload `resources/Lotka_Volterra_params_for_id.csv` (with `resources/Lotka_Volterra_forced.cellml`) and all 4 parameters appear as sliders with correct min/max.
- [ ] Adjusting a slider triggers `set_param_vals` with correct qname (e.g. `Lotka_Volterra_module/alpha`).
- [ ] Multi-token `vessel_name` rows expand to one slider per vessel.
- [ ] Works in the same session as `obs_data.json` (Issue 4): slider change re-runs protocol.
- [ ] All unit tests pass without Myokit.

### References

- `resources/Lotka_Volterra_params_for_id.csv` — primary fixture (vessel `Lotka_Volterra_module`, 4 params)
- `resources/Lotka_Volterra_forced.cellml` — CellML model matching that CSV
- `resources/BG_MWC_Huang-Peskin_SS.cellml` — secondary fixture for component `main` qname tests
- `circulatory_autogen/tutorial/docs/parameter-identification.md` — "Creating params_for_id file"
- `PrimitiveParsers.get_param_id_info` / `_build_param_id_info_from_df` — multi-vessel split logic
- `cellml_explorer.html` — log-scale heuristic (`av < 1e-2 || av > 1e4`)
