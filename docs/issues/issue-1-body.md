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
    "python-multipart>=0.0.9",
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
