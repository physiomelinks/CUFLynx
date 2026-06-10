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
    protocol_info=obs_data["protocol_info"],
    id_param_names=param_names,
    id_param_vals=param_vals,
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
