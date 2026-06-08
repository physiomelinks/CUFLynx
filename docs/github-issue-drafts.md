# GitHub issue drafts (for review)

Confirm or edit, then create issues via `gh issue create`.

---

## Issue 1 — Split monolith into frontend + backend scaffold

**Title:** `feat: split cellml_explorer.html into frontend and backend apps`

**Labels:** `enhancement`, `architecture`

### Summary

Replace the single `cellml_explorer.html` with a two-app monorepo: a Vite/Vue frontend (`apps/web`) and a FastAPI backend (`apps/api`). Keep the HTML file working until feature parity is reached, then deprecate it.

### Plan

**1. Repo layout**

```
apps/
  web/          # Vue 3 + Vite + PrimeVue (Issue 2)
  api/          # FastAPI (Issues 3–5)
docs/
cellml_explorer.html   # preserved until parity
README.md
```

**2. Frontend scaffold — `apps/web`**

- Init with `yarn create vite apps/web --template vue` (no npm).
- `package.json` with `"packageManager": "yarn"`.
- Commit a `yarn.lock`.
- `apps/web/package.json` scripts: `dev`, `build`, `preview`, `test` (vitest).

**3. Backend scaffold — `apps/api`**

`pyproject.toml` (hatchling build backend, matching ICUHealthy pattern):

```toml
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[project]
name = "cellml-explorer-api"
version = "0.1.0"
requires-python = ">=3.10"
dependencies = [
    "fastapi>=0.110.0",
    "uvicorn[standard]>=0.27.0",
    "numpy>=1.24.0",
    "python-multipart>=0.0.9",   # for file upload
    "myokit>=1.39.1",
    "libcellml>=0.6.3",
    "scipy>=1.7.0",
]

[project.optional-dependencies]
dev = [
    "httpx>=0.27.0",
    "pytest>=8.0.0",
    "pytest-cov>=3.0.0",
]

[tool.pytest.ini_options]
testpaths = ["tests"]
pythonpath = ["."]
markers = [
    "integration: requires myokit and circulatory_autogen",
]
```

Install sibling as editable dep (not vendored):
```
pip install -e ../../../circulatory_autogen
```
or add to `sys.path` via env var `CIRCULATORY_AUTOGEN_SRC`, same approach as ICUHealthy `conftest.py`.

**4. Minimal API (stub, replaced in Issue 3)**

- `GET /api/health` → `{"status": "ok"}`
- `POST /api/models/upload` — multipart `.cellml` → return `{ model_id, name, variable_count }`.
- `POST /api/simulate` — stub returning empty arrays.
- CORS middleware allowing `http://localhost:5173`.

**5. Dev workflow**

Document in README:
```bash
# backend
cd apps/api && pip install -e ".[dev]"
uvicorn main:app --reload --port 8000

# frontend
cd apps/web && yarn && yarn dev
```

**6. CI skeleton**

- GitHub Actions: lint + unit tests (backend, no integration marker) + `yarn build`.

### Tests

**`apps/api/tests/conftest.py`**

- Session-scoped `simulation_deps_available()` fixture (checks circulatory_autogen on `sys.path` and `myokit` importable).
- `requires_simulation` fixture: `pytest.skip` if deps unavailable — guards all `@pytest.mark.integration` tests.
- `reset_helper_cache` autouse fixture to reset any cached `SimulationHelper` between tests.
- `TestClient(app)` fixture.

**`apps/api/tests/test_health.py`**

- `test_health` — GET `/api/health` returns 200 + `{"status": "ok"}`.
- `test_cors_header` — response includes `Access-Control-Allow-Origin`.

**`apps/api/tests/test_upload.py`**

Use fixtures from `resources/` (paths relative to repo root, resolved via a `RESOURCES_DIR` constant in `conftest.py`):

- `test_upload_bg_model_returns_metadata` — POST `resources/BG_MWC_Huang-Peskin_SS.cellml` → 200; response contains `model_id` string, `name == "my_model"`, `variable_count > 0`.
- `test_upload_lotka_volterra_returns_metadata` — POST `resources/Lotka_Volterra_forced.cellml` → 200; response contains `name == "Lotka_Volterra_forced"`, parameter names include `Lotka_Volterra_module/alpha`.
- `test_upload_invalid_file_returns_422` — POST a `.txt` file → 4xx.

Add to `conftest.py`:
```python
RESOURCES_DIR = Path(__file__).resolve().parents[3] / "resources"
BG_MODEL_PATH = RESOURCES_DIR / "BG_MWC_Huang-Peskin_SS.cellml"
LV_MODEL_PATH = RESOURCES_DIR / "Lotka_Volterra_forced.cellml"
```

**`apps/web/src/lib/api.test.js`** (Vitest)

- `test_health_endpoint_called` — mock `axios.get` returning `{status:'ok'}`, assert composable returns `true`.
- `test_upload_cellml_resolves_model_id` — mock `axios.post`, assert `uploadCellML` resolves with a `model_id`.

### Acceptance criteria

- [ ] `yarn dev` starts frontend on `localhost:5173`; `uvicorn` starts API on `localhost:8000`.
- [ ] `GET /api/health` returns 200 from both browser and `pytest`.
- [ ] CellML file upload returns metadata.
- [ ] All non-integration tests pass with `pytest -m "not integration"`.

### References

- `ICUHealthy/backend/main.py`, `ICUHealthy/backend/pyproject.toml`, `ICUHealthy/backend/tests/conftest.py`
- `circulatory_autogen/pyproject.toml`

---

## Issue 2 — PrimeVue frontend

**Title:** `feat: implement web UI with Vue 3 + PrimeVue 4`

**Labels:** `enhancement`, `frontend`

### Summary

Build `apps/web` with Vue 3, Vite, **PrimeVue 4** (Aura theme) and `vue-chartjs`, matching the ICUHealthy frontend stack and porting the three-column CellML explorer UX.

### Plan

**1. Dependencies (`apps/web/package.json`)**

```json
{
  "dependencies": {
    "@primevue/themes": "^4.x",
    "axios": "^1.x",
    "chart.js": "^4.x",
    "primeicons": "^7.x",
    "primevue": "^4.x",
    "vue": "^3.x",
    "vue-chartjs": "^5.x"
  },
  "devDependencies": {
    "@vitejs/plugin-vue": "^5.x",
    "@vue/test-utils": "^2.x",
    "jsdom": "^25.x",
    "vite": "^6.x",
    "vitest": "^2.x"
  }
}
```

Install with `yarn` (no `npm install`).

**2. App shell — `App.vue`**

Three-column layout (left sliders, centre chart, right variables + imports), top bar with model name and run controls. Match visual tone of `cellml_explorer.html` dark theme using PrimeVue Aura dark preset.

**3. Components**

| File | Role | PrimeVue components |
|------|------|---------------------|
| `ControlPanel.vue` | Active sliders with log/linear toggle and range editing | `Slider`, `InputNumber`, `ToggleButton` |
| `VariableList.vue` | Tabs: Params / Outputs / ODEs; `+` to add slider | `TabView`, `DataTable` or `Listbox` |
| `PlotPanel.vue` | `vue-chartjs` line chart; axis selectors; experimental data overlay | `Select` (axis), chart canvas |
| `FileImport.vue` | Drag-and-drop zones for CellML, `obs_data.json`, `params_for_id.csv`, legacy CSV | `FileUpload`, `Message` |
| `StatusBar.vue` | Sim status, timing, error messages | `Message`, `ProgressSpinner` |

**4. State (`src/stores/` or composables)**

- `useModel` — `model_id`, `name`, `variables` (params/odes/outputs).
- `useSliders` — active sliders map; debounced (300 ms) trigger to `POST /api/simulate` or `POST /api/protocol/run`.
- `useSimResult` — time series per variable from last run.
- `useObsData` — parsed `obs_data.json` content.
- `useParamsForId` — parsed `params_for_id.csv` content.

**5. API client (`src/lib/api.js`)**

Typed `axios` wrappers for all backend routes. Base URL from `VITE_API_URL` env var (default `http://localhost:8000`).

### Tests

**`apps/web/src/lib/api.test.js`** (Vitest + jsdom)

- `test_upload_cellml` — mocks `axios.post`, asserts `uploadCellML` resolves with `model_id`.
- `test_simulate_called_with_params` — mocks `axios.post`, asserts param dict forwarded correctly.

**`apps/web/src/stores/useSliders.test.js`**

- `test_add_slider_increments_count` — adding a slider updates `Object.keys(sliders).length`.
- `test_remove_slider_removes_key` — removing a slider deletes its entry.
- `test_slider_value_within_range` — value clamped to `[min, max]`.
- `test_log_slider_heuristic` — auto-enables log scale when range span > 1e4.

**`apps/web/src/components/ControlPanel.test.js`**

- `test_renders_slider_for_each_active_param` — mount with 3 sliders in state, assert 3 PrimeVue `Slider` instances.
- `test_slider_change_emits_update` — drag slider, assert `update:sliders` emitted with new value.

**`apps/web/src/components/FileImport.test.js`**

- `test_cellml_drop_calls_upload` — simulate drop event with `.cellml` file, assert `uploadCellML` called.
- `test_invalid_extension_shows_error` — drop `.txt`, assert error message rendered.

### Acceptance criteria

- [ ] `yarn dev` shows three-column layout with PrimeVue Aura dark theme.
- [ ] Upload CellML via drag-and-drop populates the variable list tabs.
- [ ] Moving a slider debounces and calls simulate.
- [ ] `yarn test` passes all unit tests (mocked API, no real backend needed).
- [ ] `yarn build` exits 0.

### References

- `ICUHealthy/frontend/package.json`, `ControlPanel.vue`, `VitalsDisplay.vue`
- `cellml_explorer.html` — layout, slider logic, CSV overlay (source of truth for UX until ported)

---

## Issue 3 — Python backend with circulatory_autogen `protocol_runner`

**Title:** `feat: backend simulation via circulatory_autogen ProtocolRunner (Myokit)`

**Labels:** `enhancement`, `backend`

### Summary

Wire `apps/api` to use **circulatory_autogen** `get_simulation_helper` + `ProtocolRunner` (Myokit CVODE) instead of any custom solver. This replaces the stub `POST /api/simulate` from Issue 1.

### Plan

**1. Dependency and path setup**

`apps/api/pyproject.toml` — editable dep already listed. At runtime, ensure `circulatory_autogen/src` is on `sys.path` before importing:

```python
import os, sys
CA_SRC = os.environ.get(
    "CIRCULATORY_AUTOGEN_SRC",
    str(Path(__file__).resolve().parents[3] / "circulatory_autogen" / "src")
)
if CA_SRC not in sys.path:
    sys.path.insert(0, CA_SRC)

from solver_wrappers import get_simulation_helper
from protocol_runners import ProtocolRunner
```

**2. Helper lifecycle (follow ICUHealthy `acquire_helper`)**

- Build helper once per uploaded model: `get_simulation_helper(model_path=..., solver="CVODE_myokit", model_type="cellml_only", dt=..., sim_time=..., pre_time=..., solver_info=...)`. Note `model_type="cellml_only"` is required for flat CellML input.
- Cache per `model_id` in a module-level dict, protected by a `threading.Lock`.
- Before each run: `helper.reset_and_clear()`, then `helper._setup_time(dt, sim_time, pre_time, start_time=0.0)`.
- Set params: `helper.set_param_vals(param_names, param_vals)` — names must be `"component/param"` Myokit qualified names.
- Run: `helper.run()`.
- Read results: `helper.get_time(include_pre_time=False)`, `helper.get_results([var], flatten=True)`.

**3. ProtocolRunner for multi-experiment runs**

```python
runner = ProtocolRunner(model_path, inp_data_dict, solver="CVODE_myokit")
t_list, res_list, sim_times = runner.run_protocols(
    model_path,
    protocol_info=obs_data["protocol_info"],   # from uploaded obs_data.json
    id_param_names=param_names,                # list of "component/param" strings
    id_param_vals=param_vals,                  # matching float array
)
var2idx = runner.get_var2idx_dict()
```

Returns `t_list[exp_idx]` (time array with pre_time removed) and `res_list[exp_idx][var_idx]`.

**4. API endpoints**

| Endpoint | Body | Response |
|----------|------|----------|
| `GET /api/models/{model_id}/variables` | — | `{ params, odes, algebraic, all_names }` from `runner.get_variable_names()` |
| `POST /api/simulate` | `{ model_id, params: {"comp/var": float}, sim_time, pre_time }` | `{ time: [], outputs: {var: []} }` |
| `POST /api/protocol/run` | `{ model_id, protocol_info: {...}, params: {"comp/var": float} }` | `{ experiments: [{ time: [], outputs: {} }] }` |

**5. Error handling**

- `model_id` not found → 404.
- Myokit compile or runtime error → 500 with `{ detail: str(exc) }`.
- Invalid param name (not in model) → 422 before simulation starts.

### Tests

All test fixtures use files from `resources/` in the repo root. Add to `conftest.py`:

```python
RESOURCES_DIR = Path(__file__).resolve().parents[3] / "resources"
BG_MODEL_PATH = RESOURCES_DIR / "BG_MWC_Huang-Peskin_SS.cellml"
LV_MODEL_PATH = RESOURCES_DIR / "Lotka_Volterra_forced.cellml"
LV_OBS_DATA_PATH = RESOURCES_DIR / "Lotka_Volterra_obs_data.json"
LV_PARAMS_CSV_PATH = RESOURCES_DIR / "Lotka_Volterra_params_for_id.csv"
```

**`apps/api/tests/test_simulate.py`**

*Unit (no Myokit, mocked helper):*

- `test_simulate_endpoint_calls_set_param_vals` — inject fake helper via dependency override; assert `set_param_vals` called with correct names and values.
- `test_simulate_returns_time_and_outputs_shape` — fake helper returns fixed arrays; assert response lists have correct length.
- `test_simulate_unknown_model_returns_404` — POST with unknown `model_id`.
- `test_simulate_invalid_param_name_returns_422` — POST param key without `/` separator.

*Integration (`@pytest.mark.integration`, skip via `requires_simulation`):*

- `test_simulate_bg_model_returns_finite_values` — upload `resources/BG_MWC_Huang-Peskin_SS.cellml`, run for `sim_time=20, pre_time=0`; assert `main/p_o2` time series is finite, monotonically increasing, length > 0.
- `test_simulate_lotka_volterra_returns_finite_values` — upload `resources/Lotka_Volterra_forced.cellml`, run for 5 s; assert `Lotka_Volterra_module/x` and `Lotka_Volterra_module/y` finite and non-empty.
- `test_simulate_different_alpha_gives_different_lv_traces` — run Lotka-Volterra twice with `Lotka_Volterra_module/alpha` = 2.0 and 6.0; assert `max(x)` differs by > 1 %.

**`apps/api/tests/test_protocol_run.py`**

*Integration:*

- `test_protocol_run_lotka_volterra_obs_data` — upload `Lotka_Volterra_forced.cellml` + POST `protocol_info` from `resources/Lotka_Volterra_obs_data.json`; assert response has 1 experiment, time array is monotonically increasing, `Lotka_Volterra_module/x` present.
- `test_protocol_run_respects_pre_time` — set `pre_times=[1.0]`, assert returned time starts near 0 (pre_time stripped).
- `test_protocol_run_bg_model_single_segment` — upload `BG_MWC_Huang-Peskin_SS.cellml`, POST a single-experiment protocol (`pre_times=[0.0]`, `sim_times=[[20]]`, no `params_to_change`); assert `main/p_o2` present and runs to ~20 s.

**`apps/api/tests/test_variables.py`**

- `test_bg_model_variables_contains_param_and_ode` — upload `BG_MWC_Huang-Peskin_SS.cellml`; GET `/{model_id}/variables`; assert `params` contains `main/alpha_o2` and `odes` contains `main/p_o2`.
- `test_lv_variables_contains_alpha` — upload `Lotka_Volterra_forced.cellml`; assert `params` contains `Lotka_Volterra_module/alpha` and `odes` contains `Lotka_Volterra_module/x`.

### Acceptance criteria

- [ ] `POST /api/simulate` returns finite time series for both `resources/BG_MWC_Huang-Peskin_SS.cellml` and `resources/Lotka_Volterra_forced.cellml`.
- [ ] `POST /api/protocol/run` with `resources/Lotka_Volterra_obs_data.json` runs the single experiment and returns correct structure.
- [ ] Changing `Lotka_Volterra_module/alpha` changes simulation output.
- [ ] All unit tests pass without Myokit installed; integration tests skipped cleanly.

### References

- `resources/Lotka_Volterra_forced.cellml`, `resources/Lotka_Volterra_obs_data.json`, `resources/BG_MWC_Huang-Peskin_SS.cellml`
- `circulatory_autogen/src/protocol_runners/protocol_runner.py`
- `circulatory_autogen/src/solver_wrappers/__init__.py`
- `ICUHealthy/backend/main.py` — `acquire_helper`, `apply_params`, `extract_response`
- `circulatory_autogen/tests/test_protocol_funcs.py`, `tests/test_solvers.py`

---

## Issue 4 — Import `obs_data.json` for protocols and comparison data

**Title:** `feat: import obs_data.json to drive protocols and overlay experimental data`

**Labels:** `enhancement`, `data`

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

---

## Issue 5 — Import `params_for_id.csv` for parameters and slider ranges

**Title:** `feat: import params_for_id.csv to auto-populate sliders with ranges`

**Labels:** `enhancement`, `data`

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

---

## Suggested implementation order

1. **Issue 1** — scaffold only, stub API
2. **Issue 2** — UI shell, mocked API
3. **Issue 3** — real simulation backend
4. **Issue 5** — params CSV → sliders (unblocks realistic parameter ranges)
5. **Issue 4** — obs_data protocol + overlay (builds on 3 + 5)

## Milestone suggestion

**"Explorer v2 — PrimeVue + protocol_runner"** covering all five issues.

## Creating issues

```bash
gh issue create \
  --title "feat: split cellml_explorer.html into frontend and backend apps" \
  --label "enhancement,architecture" \
  --body "$(cat docs/issue-1-body.md)"   # copy body from this file per issue
```
