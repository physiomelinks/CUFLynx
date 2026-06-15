# CUFLynx — developer notes

Two-app monorepo:

```
apps/
  web/   Vue 3 + Vite + PrimeVue 4 frontend (explorer UI)
  api/   FastAPI backend; simulation delegated to circulatory_autogen (Myokit CVODE)
resources/   CellML test models + obs_data / params_for_id fixtures + traces
scripts/     install.py (setup) + run.py (launcher)
```

The backend runs models with [circulatory_autogen](https://github.com/physiomelinks/circulatory_autogen)
(`get_simulation_helper` / `ProtocolRunner`, Myokit CVODE). CellML *metadata*
parsing (variable/parameter classification, obs_data and params_for_id parsing)
is dependency-light, so the upload/parse endpoints and their unit tests run
without Myokit installed.

## How it runs (single server)

In production the FastAPI server serves the built frontend (`apps/web/dist`)
**and** the API under `/api/*`, so the whole app is one process on one URL.
`scripts/run.py` builds the frontend if needed, starts uvicorn and opens the
browser:

```bash
python scripts/run.py               # http://localhost:8000
python scripts/run.py --port 9000   # different port
python scripts/run.py --build       # force a fresh frontend build first
python scripts/run.py --no-browser  # don't auto-open the browser
```

Use the same Python interpreter you installed with (`scripts/install.py`) — that
interpreter serves the API. Start the server before building and `/` returns a
"frontend not built" hint.

## Dev mode (hot reload)

```bash
python scripts/run.py --dev
```

Runs the backend with `uvicorn --reload` **and** the Vite dev server together,
and opens **http://localhost:5173** (frontend HMR). Vite proxies `/api` to the
backend on `:8000`, so keep the backend on the default port in dev. Ctrl+C stops
both processes.

## Backend — `apps/api`

```bash
cd apps/api
pip install -e ".[dev]"                  # fastapi, pandas, numpy, myokit, libcellml, ...
uvicorn main:app --reload --port 8000    # serves /api/* and the built frontend
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
| `POST /api/calibration/run`, `/api/sensitivity/run`, `/api/uq/run` | start a job (poll `…/{job_id}/status`) |

The backend finds `circulatory_autogen` via the `CIRCULATORY_AUTOGEN_SRC` env
var, defaulting to the sibling clone next to this repository. Calibration /
sensitivity / UQ runs use the interpreter chosen in the app's top bar — point it
at your `circulatory_autogen` venv.

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
yarn build      # production build -> dist/, served by the API
yarn test       # vitest unit tests (mocked API)
yarn dev        # Vite dev server with hot-reload (proxies /api to :8000)
```

The app talks to its own origin by default; set `VITE_API_URL` only for a
split/remote backend.

The UI is a three-column explorer (sliders · chart · variables + imports) using
the PrimeVue Aura dark theme and `vue-chartjs`. Left tabs: Parameters ·
Sensitivity · Calibration · UQ; center tabs: Output plots · Progress · Analysis.
Drag-and-drop a CellML file to populate the variable list, then add sliders (or
import a `params_for_id.csv` to seed them). Importing an `obs_data.json` switches
the run to its protocol and overlays the ground-truth data items on the chart.
