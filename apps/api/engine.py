"""Simulation engine: a thin, cached wrapper over circulatory_autogen.

All Myokit/circulatory_autogen imports are *lazy* — they only happen the first
time a real simulation runs.  This keeps the parsing/upload endpoints (and the
whole unit-test tier) importable without the simulation stack.

Tests inject fakes by replacing :pyattr:`SimulationEngine.helper_factory` /
:pyattr:`SimulationEngine.runner_factory` on the module-level :data:`engine`
singleton (see ``tests/conftest.py``); no Myokit required for the unit tier.
"""

from __future__ import annotations

import os
import sys
import threading
from pathlib import Path

from runtime_paths import is_frozen

DEFAULT_DT = 0.01
DEFAULT_MODEL_TYPE = "cellml_only"
DEFAULT_SOLVER = "CVODE_myokit"
DEFAULT_SOLVER_INFO = {"MaximumStep": 0.001, "MaximumNumberOfSteps": 5000}


class SimulationError(RuntimeError):
    """Raised when the underlying solver fails (maps to HTTP 500)."""


def _circulatory_autogen_src() -> str:
    """Locate the circulatory_autogen ``src`` directory.

    Order: ``CIRCULATORY_AUTOGEN_SRC`` env var, then the conventional sibling
    location next to this repository.

    Returns "" in the packaged app when nothing is configured: there is no
    checkout to be a sibling *of* (paths derived from ``__file__`` point inside
    the bundle), so the sibling guess would yield nonsense like
    ``/circulatory_autogen``. An empty string means "not configured" — callers
    report ca_exists=False and the user picks a directory in Settings.
    """
    env = os.environ.get("CIRCULATORY_AUTOGEN_SRC")
    if env:
        return env
    if is_frozen():
        return ""
    # apps/api/engine.py -> parents[2] == repo root; its parent holds siblings.
    repo_root = Path(__file__).resolve().parents[2]
    return str(repo_root.parent / "circulatory_autogen" / "src")


def _ensure_ca_on_path() -> None:
    src = _circulatory_autogen_src()
    if not src:
        return  # unconfigured; never put "" on sys.path (that means the CWD)
    if src not in sys.path:
        sys.path.insert(0, src)


def _default_helper_factory(
    *, model_path, dt, sim_time, pre_time, solver_info, model_type=DEFAULT_MODEL_TYPE, solver=DEFAULT_SOLVER
):
    _ensure_ca_on_path()
    from solver_wrappers import get_simulation_helper  # noqa: E402

    return get_simulation_helper(
        model_path=str(model_path),
        solver=solver,
        model_type=model_type,
        dt=dt,
        sim_time=sim_time,
        pre_time=pre_time,
        solver_info=solver_info,
    )


def _default_runner_factory(
    *, model_path, dt, solver_info, model_type=DEFAULT_MODEL_TYPE, solver=DEFAULT_SOLVER
):
    _ensure_ca_on_path()
    from protocol_runners import ProtocolRunner  # noqa: E402

    return ProtocolRunner(
        str(model_path),
        inp_data_dict={"dt": dt, "solver_info": solver_info, "model_type": model_type},
        solver=solver,
        model_type=model_type,
    )


def _resolve_output_key(var2idx, name):
    """Resolve an output name against var2idx across CA backends.

    Handles both the separator difference — Myokit uses dotted ``comp.var`` while
    the python / casadi ProtocolRunners use ``comp/var`` — and the ``component``
    vs ``component_module`` vessel convention of circulatory_autogen flat CellML
    models. Local implementation so no CA import is needed (unit/CI safe).
    """
    candidates = [name]
    for sep in ("/", "."):
        if sep in name:
            comp, var = name.split(sep, 1)
            comp_bare = comp[:-7] if comp.endswith("_module") else comp
            for out_sep in (".", "/"):
                candidates.append(f"{comp}{out_sep}{var}")
                candidates.append(f"{comp_bare}{out_sep}{var}")
                candidates.append(f"{comp_bare}_module{out_sep}{var}")
            candidates.append(var)
            break
    seen = set()
    for cand in candidates:
        if cand not in seen:
            seen.add(cand)
            if cand in var2idx:
                return cand
    return None


class SimulationEngine:
    """Caches one compiled helper and one ProtocolRunner per ``model_id``."""

    def __init__(self):
        self.dt = DEFAULT_DT
        # Backend solver selection (set from /api/config). model_type is CA's
        # generated_model_format; solver must be compatible with it.
        self.model_type = DEFAULT_MODEL_TYPE
        self.solver = DEFAULT_SOLVER
        self.solver_info = dict(DEFAULT_SOLVER_INFO)
        self.helper_factory = _default_helper_factory
        self.runner_factory = _default_runner_factory
        self._helpers: dict[str, object] = {}
        self._runners: dict[str, object] = {}
        # model_id -> last protocol_info object whose pace binding is active on
        # the cached runner's helper (avoids re-binding/recreating every run).
        self._runner_protocol_info: dict[str, object] = {}
        self._lock = threading.Lock()

    def reset(self) -> None:
        """Drop all cached helpers/runners (used between tests)."""
        with self._lock:
            self._helpers.clear()
            self._runners.clear()
            self._runner_protocol_info.clear()

    # ------------------------------------------------------------------
    # Single run
    # ------------------------------------------------------------------
    def simulate(
        self,
        model_id: str,
        model_path: str,
        params: dict[str, float],
        sim_time: float,
        pre_time: float,
        outputs: list[str],
    ) -> dict:
        with self._lock:
            helper = self._helpers.get(model_id)
            if helper is None:
                helper = self.helper_factory(
                    model_path=str(model_path),
                    dt=self.dt,
                    sim_time=float(sim_time),
                    pre_time=float(pre_time),
                    solver_info=self.solver_info,
                    model_type=self.model_type,
                    solver=self.solver,
                )
                self._helpers[model_id] = helper

            helper.reset_and_clear()
            helper.update_times(self.dt, 0.0, float(sim_time), float(pre_time))

            if params:
                names = list(params.keys())
                vals = [params[n] for n in names]
                helper.set_param_vals(names, vals)

            ok = helper.run()
            if ok is False:
                raise SimulationError("simulation failed")

            # Myokit/OpenCOR/python helpers expose get_time; the CasADi helper
            # doesn't, but resolves the logged time vector as the 'time' variable.
            if hasattr(helper, "get_time"):
                time = [float(t) for t in helper.get_time(include_pre_time=False)]
            else:
                time = [float(t) for t in helper.get_results(["time"], flatten=True)[0]]
            out: dict[str, list[float]] = {}
            for var in outputs:
                series = helper.get_results([var], flatten=True)[0]
                out[var] = [float(v) for v in series]

        return {"time": time, "outputs": out}

    # ------------------------------------------------------------------
    # Multi-experiment protocol
    # ------------------------------------------------------------------
    def run_protocol(
        self,
        model_id: str,
        model_path: str,
        protocol_info: dict,
        params: dict[str, float],
        outputs: list[str],
    ) -> dict:
        with self._lock:
            runner = self._runners.get(model_id)
            if runner is None:
                runner = self.runner_factory(
                    model_path=str(model_path),
                    dt=self.dt,
                    solver_info=self.solver_info,
                    model_type=self.model_type,
                    solver=self.solver,
                )
                self._runners[model_id] = runner

            # Bind any time-varying protocol traces onto the helper before the
            # run. set_protocol_info recreates the simulation with the `pace`
            # binding, so only call it when the protocol actually changed.
            helper = getattr(runner, "sim_helper", None)
            if helper is not None and hasattr(helper, "set_protocol_info"):
                if self._runner_protocol_info.get(model_id) is not protocol_info:
                    helper.set_protocol_info(protocol_info)
                    self._runner_protocol_info[model_id] = protocol_info

            names = list(params.keys()) if params else None
            vals = [params[n] for n in names] if names else None

            t_list, res_list, _sim_times = runner.run_protocols(
                str(model_path),
                protocol_info=protocol_info,
                id_param_names=names,
                id_param_vals=vals,
            )
            var2idx = runner.get_var2idx_dict()

        # Resolve each requested output name once against var2idx.
        key_for = {var: _resolve_output_key(var2idx, var) for var in outputs}

        experiments = []
        for exp_idx, t in enumerate(t_list):
            res = res_list[exp_idx]
            exp_outputs: dict[str, list[float]] = {}
            for var in outputs:
                key = key_for.get(var)
                if key is None or res is None:
                    continue
                idx = var2idx[key]
                if idx >= len(res):
                    continue
                exp_outputs[var] = [float(v) for v in res[idx]]
            time = [float(v) for v in t] if t is not None else []
            experiments.append({"time": time, "outputs": exp_outputs})

        return {"experiments": experiments}


# Module-level singleton shared by the FastAPI routes.
engine = SimulationEngine()
