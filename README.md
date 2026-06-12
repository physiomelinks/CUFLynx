# cellml_file_slider_visualization

Interactive **manual parameter exploration** for CellML models: sliders change
constants, simulations update plots, and experimental data can be overlaid for
rough calibration before formal parameter identification.

The project is moving from a single-file prototype (`cellml_explorer.html`) to a
two-app monorepo:

```
apps/
  web/    Vue 3 + Vite + PrimeVue 4 frontend (three-column explorer UI)
  api/    FastAPI backend; simulation delegated to circulatory_autogen (Myokit CVODE)
resources/   CellML test models + obs_data / params_for_id fixtures
cellml_explorer.html   legacy single-file app (preserved until feature parity)
```

Unlike the legacy RK4-in-the-browser approach, the backend runs models with
[circulatory_autogen](https://github.com/) `get_simulation_helper` /
`ProtocolRunner` (Myokit CVODE). CellML *metadata* parsing (variable/parameter
classification, obs_data and params_for_id parsing) is dependency-light, so the
upload/parse endpoints and their unit tests run without Myokit installed.

## Backend — `apps/api`

```bash
cd apps/api
pip install -e ".[dev]"          # fastapi, pandas, numpy, myokit, libcellml, ...
uvicorn main:app --reload --port 8000
```

Key endpoints:

| Endpoint | Purpose |
|----------|---------|
| `GET  /api/health` | liveness probe |
| `POST /api/models/upload` | upload a `.cellml` file → metadata (name, variables) |
| `GET  /api/models/{id}/variables` | classified params / odes / algebraic |
| `POST /api/simulate` | single run; `{ model_id, params, sim_time, pre_time }` |
| `POST /api/protocol/run` | multi-experiment protocol run |
| `POST /api/obs_data/upload` | load `obs_data.json` (drives protocol + overlays) |
| `POST /api/params_for_id/upload` | load `params_for_id.csv` → slider specs |

The backend finds `circulatory_autogen` via the `CIRCULATORY_AUTOGEN_SRC` env
var, defaulting to the sibling clone next to this repository.

### Tests

```bash
cd apps/api
pytest -m "not integration"   # unit tier — no Myokit required
pytest -m integration         # real CellML simulation (needs myokit + libcellml)
```

## Frontend — `apps/web`

```bash
cd apps/web
yarn            # install (uses yarn, not npm)
yarn dev        # http://localhost:5173 (proxies /api to :8000)
yarn test       # vitest unit tests (mocked API)
yarn build      # production build
```

The UI is a three-column explorer (sliders · chart · variables + imports) using
the PrimeVue Aura dark theme and `vue-chartjs`. Drag-and-drop a CellML file to
populate the variable list, then add sliders (or import a `params_for_id.csv` to
seed them automatically). Importing an `obs_data.json` switches the run to its
protocol and overlays the ground-truth data items on the chart.

## Legacy single-file app

`cellml_explorer.html` still works standalone — open it in a browser and upload a
CellML file plus an optional CSV. It is preserved until the new apps reach
feature parity.
