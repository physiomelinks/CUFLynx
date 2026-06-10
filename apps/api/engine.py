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

DEFAULT_DT = 0.01
DEFAULT_SOLVER = "CVODE_myokit"
DEFAULT_SOLVER_INFO = {"MaximumStep": 0.001, "MaximumNumberOfSteps": 5000}


class SimulationError(RuntimeError):
    """Raised when the underlying solver fails (maps to HTTP 500)."""


def _circulatory_autogen_src() -> str:
    """Locate the circulatory_autogen ``src`` directory.

    Order: ``CIRCULATORY_AUTOGEN_SRC`` env var, then the conventional sibling
    location next to this repository.
    """
    env = os.environ.get("CIRCULATORY_AUTOGEN_SRC")
    if env:
        return env
    # apps/api/engine.py -> parents[2] == repo root; its parent holds siblings.
    repo_root = Path(__file__).resolve().parents[2]
    return str(repo_root.parent / "circulatory_autogen" / "src")


def _ensure_ca_on_path() -> None:
    src = _circulatory_autogen_src()
    if src not in sys.path:
        sys.path.insert(0, src)


def _default_helper_factory(*, model_path, dt, sim_time, pre_time, solver_info):
    _ensure_ca_on_path()
    from solver_wrappers import get_simulation_helper  # noqa: E402

    return get_simulation_helper(
        model_path=str(model_path),
        solver=DEFAULT_SOLVER,
        model_type="cellml_only",
        dt=dt,
        sim_time=sim_time,
        pre_time=pre_time,
        solver_info=solver_info,
    )


def _default_runner_factory(*, model_path, dt, solver_info):
    _ensure_ca_on_path()
    from protocol_runners import ProtocolRunner  # noqa: E402

    return ProtocolRunner(
        str(model_path),
        inp_data_dict={"dt": dt, "solver_info": solver_info},
        solver=DEFAULT_SOLVER,
    )


def _to_dot(qname: str) -> str:
    """Convert a ``component/var`` qname to Myokit's ``component.var`` form."""
    return qname.replace("/", ".")


class SimulationEngine:
    """Caches one compiled helper and one ProtocolRunner per ``model_id``."""

    def __init__(self):
        self.dt = DEFAULT_DT
        self.solver_info = dict(DEFAULT_SOLVER_INFO)
        self.helper_factory = _default_helper_factory
        self.runner_factory = _default_runner_factory
        self._helpers: dict[str, object] = {}
        self._runners: dict[str, object] = {}
        self._lock = threading.Lock()

    def reset(self) -> None:
        """Drop all cached helpers/runners (used between tests)."""
        with self._lock:
            self._helpers.clear()
            self._runners.clear()

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

            time = [float(t) for t in helper.get_time(include_pre_time=False)]
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
                )
                self._runners[model_id] = runner

            names = list(params.keys()) if params else None
            vals = [params[n] for n in names] if names else None

            t_list, res_list, _sim_times = runner.run_protocols(
                str(model_path),
                protocol_info=protocol_info,
                id_param_names=names,
                id_param_vals=vals,
            )
            var2idx = runner.get_var2idx_dict()

        experiments = []
        for exp_idx, t in enumerate(t_list):
            res = res_list[exp_idx]
            exp_outputs: dict[str, list[float]] = {}
            for var in outputs:
                idx = var2idx.get(_to_dot(var), var2idx.get(var))
                if idx is None or res is None or idx >= len(res):
                    continue
                exp_outputs[var] = [float(v) for v in res[idx]]
            time = [float(v) for v in t] if t is not None else []
            experiments.append({"time": time, "outputs": exp_outputs})

        return {"experiments": experiments}


# Module-level singleton shared by the FastAPI routes.
engine = SimulationEngine()
