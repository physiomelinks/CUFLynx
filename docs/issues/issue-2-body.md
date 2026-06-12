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
