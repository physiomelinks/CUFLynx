# cellml_file_slider_visualization — Agent summary

## Purpose

Interactive **manual parameter exploration** for CellML models: sliders change constants, simulations update plots, and experimental CSV data can be overlaid for rough calibration before formal parameter identification.

## Current state (legacy)

| Artifact | Role |
|----------|------|
| `cellml_explorer.html` | Single-file app (~1.2k lines): UI + CellML XML parser + MathML eval + RK4 + Web Worker + canvas plot + CSV overlay |
| `resources/BG_MWC_Huang-Peskin_SS.cellml` | Primary test model (CellML 1.1, component `main`; `main/p_o2` is state, `main/alpha_o2` is parameter) |
| `resources/Lotka_Volterra_forced.cellml` | Integration test model (CellML 2.0, component `Lotka_Volterra_module`; params `alpha`,`beta`,`delta`,`gamma`) |
| `resources/Lotka_Volterra_obs_data.json` | Test obs_data fixture (1 experiment, 2 constant data_items for `x_max` / `y_max`) |
| `resources/Lotka_Volterra_params_for_id.csv` | Test params fixture (4 rows, vessel `Lotka_Volterra_module`) |
| `*.csv` (root) | Legacy experimental traces (e.g. Dash2016, winslow_rw2) |

**No server, no build step.** User opens the HTML file and uploads CellML + optional CSV via `FileReader`.

### In-browser pipeline

1. **Parse CellML** — walk XML for variables, ODEs (`<diff>`), algebraics (`<eq>`), parameters (`initial_value`).
2. **Serialize MathML** — DOM → JSON trees for the worker.
3. **Simulate** — fixed-step RK4; slider values override parameter constants.
4. **Plot** — canvas; optional CSV scatter overlay.

Limitations: subset CellML/MathML, RK4 only, 3-pass algebraic solve. Not suitable for production circulatory models without backend integration.

## Planned architecture (see `docs/github-issue-drafts.md`)

```
apps/web/          Vue 3 + Vite + PrimeVue
apps/api/          FastAPI, depends on sibling circulatory_autogen
```

**Backend engine (target):** [circulatory_autogen](https://github.com/...) `protocol_runners.ProtocolRunner` + `solver_wrappers.get_simulation_helper` (Myokit CVODE). Not the in-browser RK4.

**Reference implementation:** sibling repo `ICUHealthy` — FastAPI + cached `get_simulation_helper`, `helper.set_param_vals(param_names, param_vals)`, `helper.run()`, `helper.get_results()`.

**Config formats (circulatory_autogen):**

- `obs_data.json` — `protocol_info` (experiments/subexperiments) + `data_items` (ground truth / comparison targets) + optional `prediction_items`.
- `*_params_for_id.csv` — columns: `vessel_name`, `param_name`, `param_type`, `min`, `max`, `name_for_plotting`; full Myokit names are `vessel_name/param_name`.

Docs: `circulatory_autogen/tutorial/docs/parameter-identification.md`, `circulatory_autogen/claude.md`, `src/utilities/obs_data_helpers.py`.

## Key files

- `cellml_explorer.html` — source of truth for current UX until ported
- `README.md` — user-facing quick start
- `docs/github-issue-drafts.md` — proposed GitHub issues (drafts)

## Conventions for agents

- Prefer **minimal diffs**; match ICUHealthy / circulatory_autogen patterns when adding API or UI.
- Do not extend the custom in-browser CellML parser for new features — delegate simulation to **protocol_runner**.
- Parameter names for Myokit must use **`component/param`** form from `params_for_id` (`PrimitiveParsers.get_param_id_info`).
- Slider debouncing: interactive exploration needs low-latency sim; protocol runs may take seconds on first compile (cache helper like ICUHealthy `acquire_helper`).

## Security caveats (localhost-only assumptions)

The backend assumes a single-user, localhost deployment and exposes the host filesystem to any client that can reach the API:

- **`GET /api/fs/list`** (`apps/api/main.py`) — the in-app file/folder browser (Python interpreter + outputs dir pickers) lists arbitrary server directories, defaulting to `$HOME`. No path confinement.
- **`config_outputs_dir`** (calibration) — writes calibration outputs to any absolute path the client supplies.

These are acceptable for the current local use. **If the API is ever served beyond localhost, gate/confine both** under a configured root (and authenticate).

## Related repos (local paths may vary)

| Repo | Use |
|------|-----|
| `circulatory_autogen` | `ProtocolRunner`, `ProtocolExecutor`, `get_simulation_helper`, `obs_data.json` / `params_for_id` parsers |
| `ICUHealthy` | Example FastAPI + PrimeVue + `set_param_vals` integration |

## Tests

No automated test suite yet. Test fixtures live in `resources/`:
- **BG model** (`resources/BG_MWC_Huang-Peskin_SS.cellml`) — used in upload, simulate, and variable-list integration tests.
- **Lotka-Volterra** (`resources/Lotka_Volterra_forced.cellml` + `Lotka_Volterra_obs_data.json` + `Lotka_Volterra_params_for_id.csv`) — primary integration fixture for protocol runs and param slider tests.

Backend pytest: `pytest -m "not integration"` (unit only, no Myokit required). Integration tests need Myokit + `circulatory_autogen` on `sys.path`; skip automatically via `requires_simulation` fixture.
