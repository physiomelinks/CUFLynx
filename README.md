# CUFLynx

This app is designed as the GUI for Circulatory Autogen link[https://github.com/physiomelinks/circulatory_autogen]. Here you can perform sensitivity analysis, calibration, uncertainty quantification and manually inspect influence of parameters on you (CellML) model outputs. 

## Quick start

The FastAPI server serves the built frontend, so the whole app runs as **one
process on one URL**. Two helper scripts (written in Python, so they work the
same on **Linux, macOS and Windows**) do everything:

```bash
python scripts/install.py    # one-time: install backend + frontend deps, build the UI
python scripts/run.py        # build if needed, start the server, open the browser
```

Use the same Python interpreter for both scripts — that interpreter serves the
API. `run.py` takes `--port N`, `--build` (force a fresh UI build) and
`--no-browser`.

Then the app opens at **http://localhost:8000**. The API is served under
`/api/*` on the same origin; everything else serves the built Vue app from
`apps/web/dist`.

Calibration / sensitivity / UQ runs use the Python interpreter chosen in the top
bar — point it at your `circulatory_autogen` venv. The backend locates the
`circulatory_autogen` source via `CIRCULATORY_AUTOGEN_SRC`, defaulting to the
sibling clone next to this repository.

<details>
<summary>Manual equivalent (without the scripts)</summary>

```bash
cd apps/api && pip install -e ".[dev]"   # backend deps (fastapi, numpy, myokit, ...)
cd ../web   && yarn && yarn build         # frontend -> apps/web/dist
cd ../api   && uvicorn main:app --port 8000
```

Start the server before building and `/` returns a "frontend not built" hint.
</details>

## Backend — `apps/api`

```bash
cd apps/api
pip install -e ".[dev]"          # fastapi, pandas, numpy, myokit, libcellml, ...
uvicorn main:app --reload --port 8000   # serves /api/* and the built frontend
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
yarn build      # production build -> dist/, served by the API (see Quick start)
yarn test       # vitest unit tests (mocked API)
yarn dev        # development server with hot-reload
```

For day-to-day frontend development, `yarn dev` runs Vite on
**http://localhost:5173** with hot-reload and proxies `/api` to a separately-run
`uvicorn main:app` on :8000. For running/using the app, prefer the single-server
Quick start above (build once, then just run uvicorn). The app talks to its own
origin by default; set `VITE_API_URL` only for a split/remote backend.

The UI is a three-column explorer (sliders · chart · variables + imports) using
the PrimeVue Aura dark theme and `vue-chartjs`. Drag-and-drop a CellML file to
populate the variable list, then add sliders (or import a `params_for_id.csv` to
seed them automatically). Importing an `obs_data.json` switches the run to its
protocol and overlays the ground-truth data items on the chart.
