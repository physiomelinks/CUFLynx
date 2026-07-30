"""Simulation engine: a thin, cached wrapper over circulatory_autogen.

All Myokit/circulatory_autogen imports are *lazy* — they only happen the first
time a real simulation runs.  This keeps the parsing/upload endpoints (and the
whole unit-test tier) importable without the simulation stack.

Tests inject fakes by replacing :pyattr:`SimulationEngine.helper_factory` /
:pyattr:`SimulationEngine.runner_factory` on the module-level :data:`engine`
singleton (see ``tests/conftest.py``); no Myokit required for the unit tier.
"""

from __future__ import annotations

import contextlib
import io
import os
import sys
import threading
from pathlib import Path

from runtime_paths import is_frozen

DEFAULT_DT = 0.01
DEFAULT_MODEL_TYPE = "cellml_only"
DEFAULT_SOLVER = "CVODE_myokit"
# Only settings DEFAULT_SOLVER (Myokit's CVODE) actually honours. It used to also
# carry MaximumNumberOfSteps, which myokit_helper never reads — myokit.Simulation
# has no max-step-count knob — so every run was seeded with an inert setting that
# the Settings form then displayed as if it did something. See
# solver_options.UNSUPPORTED_SOLVER_INFO_KEYS.
DEFAULT_SOLVER_INFO = {"MaximumStep": 0.001}


class SimulationError(RuntimeError):
    """Raised when the underlying solver fails (maps to HTTP 500)."""


# ---------------------------------------------------------------------------
# Failure reporting (issue #138)
#
# A failed run used to reach the browser as "Request failed with status code
# 500" and nothing else, which says neither what broke nor what to change. The
# reason does exist, but not where an exception can find it: CA's
# ``myokit_helper.run()`` catches the solver error, *prints* it, and returns
# False, and ``ProtocolRunner.run_protocols`` then raises a bare "Protocol
# simulation failed." So the reason is recovered by watching stdout across the
# call, and the settings that produced it are attached from our own state.
# ---------------------------------------------------------------------------
class _Tee(io.TextIOBase):
    """Records everything written to it while still passing it through.

    A plain ``redirect_stdout`` would swallow CA's progress lines ("Running
    experiments...", per-experiment completion), which are the only feedback a
    long protocol run gives on the console. Teeing keeps them.
    """

    def __init__(self, target):
        self._target = target
        self._chunks: list[str] = []

    def write(self, s):  # noqa: D102 - TextIOBase interface
        self._chunks.append(s)
        try:
            return self._target.write(s)
        except Exception:  # noqa: BLE001 - a closed/absent stdout must not fail a run
            return len(s)

    def flush(self):  # noqa: D102 - TextIOBase interface
        with contextlib.suppress(Exception):
            self._target.flush()

    def getvalue(self) -> str:
        return "".join(self._chunks)


@contextlib.contextmanager
def _tee_stdout():
    """Yield a recorder of everything printed inside the block."""
    tee = _Tee(sys.stdout)
    with contextlib.redirect_stdout(tee):
        yield tee


# Lines worth quoting back: CA prefixes its swallowed solver errors this way,
# and CVODE's own flags are the actionable part.
_FAILURE_MARKERS = ("failed", "error", "exception", "cvode", "traceback")

# Enough for a CVODE flag plus its sentence, without pasting a whole run log
# into a browser toast.
_MAX_REASON_CHARS = 600


def _solver_reason(captured: str) -> str:
    """The solver's own words for a failure, pulled out of `captured` stdout.

    Returns "" when nothing there looks like a failure, so the caller can say so
    rather than quoting an unrelated progress line as if it were the cause.
    """
    lines = [ln.strip() for ln in (captured or "").splitlines() if ln.strip()]
    hits = [ln for ln in lines if any(m in ln.lower() for m in _FAILURE_MARKERS)]
    if not hits:
        return ""
    reason = "\n".join(hits[-4:])  # the last failure block, not the whole log
    return reason[:_MAX_REASON_CHARS] + ("..." if len(reason) > _MAX_REASON_CHARS else "")


# Actionable follow-ups keyed by what CVODE actually said. Matching on the flag
# rather than the prose keeps these stable across Sundials wordings.
_HINTS = (
    (
        # A missing backend library is not a solver-settings problem, and the
        # generic "try a smaller MaximumStep" advice sent users to fiddle with
        # numbers that could never have helped. It matters *which* interpreter is
        # missing it: live simulation runs in the app's own Python, while
        # calibration / sensitivity / UQ run in the one chosen in Settings, so a
        # library present in only one gives exactly this -- analysis runs that
        # work and a live plot that does not.
        ("is not installed", "no module named", "importerror", "modulenotfounderror"),
        "That backend's library is missing from the interpreter running live "
        "simulations (the app's own Python) — Settings → Python only affects "
        "calibration / sensitivity / UQ, which run separately. Install it there "
        "too, or pick a model format whose backend is available for live plotting.",
    ),
    (
        ("cv_too_much_acc", "too much accuracy"),
        "The requested tolerance is tighter than the solver can hold — raise rtol/atol "
        "in Settings.",
    ),
    (
        ("cv_too_much_work", "mxstep", "maximum number of steps"),
        # Not "raise MaximumNumberOfSteps": Myokit's integrator has no such knob,
        # so on the default backend that would send the user to an inert control.
        "The solver hit its step budget before reaching the next output point — "
        "lower MaximumStep in Settings, or shorten the simulation time.",
    ),
    (
        ("cv_conv_failure", "cv_err_failure", "convergence"),
        "The solver could not converge — a smaller MaximumStep, or looser rtol/atol, "
        "usually helps; a parameter far outside its physiological range can also make "
        "the model unsolvable.",
    ),
    (
        ("cv_rhsfunc_fail", "nan", "inf", "overflow", "division"),
        "The model produced a non-finite value — check for a parameter at or near "
        "zero (divisions) or otherwise outside its intended range.",
    ),
    (
        ("units", "conversion", "incompatible"),
        "This looks like a unit/conversion problem in the model rather than a solver "
        "setting — check the units of the connected variables.",
    ),
    (
        # A solver plugin that will not load is an installation problem, not a
        # numerical one. CasADi ships its integrators as separate shared
        # libraries, and a wheel without libcasadi_integrator_CVODE.so fails here
        # whatever the tolerances are.
        ("cannot load shared library", "load_plugin", "cannot open shared object"),
        "That solver's plugin library could not be loaded, so this backend is not "
        "usable in the Python this app is running — nothing in Settings will fix "
        "it. Switch the model format to cellml_only with solver CVODE_myokit, or "
        "reinstall the backend's package (for CasADi, a build that includes its "
        "CVODE integrator plugin).",
    ),
    (
        # circulatory_autogen refuses a time-varying protocol input on the CasADi,
        # AADC and solve_ivp backends; only CVODE_myokit can drive a variable from
        # a trace. Without this the generic tail advised tightening tolerances,
        # which cannot possibly help and sends the user to the wrong dial.
        ("cannot drive a variable from a time series", "protocol trace name"),
        "This protocol drives a variable over time, which only the CVODE_myokit "
        "solver can do — switch the backend to cellml_only / CVODE_myokit in "
        "Settings, or replace the time-varying input with a constant per "
        "sub-experiment in the obs_data protocol.",
    ),
)


def _n_experiments(protocol_info) -> int | None:
    """How many experiments a protocol run was attempting, for the message."""
    try:
        pre = protocol_info.get("pre_times") if hasattr(protocol_info, "get") else None
        return len(pre) if pre is not None else None
    except Exception:  # noqa: BLE001 - context only; never fail the failure path
        return None


def _failure_hint(reason: str) -> str:
    low = (reason or "").lower()
    for needles, hint in _HINTS:
        if any(n in low for n in needles):
            return hint
    return (
        "Try a smaller MaximumStep or looser rtol/atol in Settings, and check that no "
        "parameter has been driven outside its intended range."
    )


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
    # Failure reporting (issue #138)
    # ------------------------------------------------------------------
    def settings_summary(self, **extra) -> str:
        """The backend settings a failure should be read against.

        Named explicitly because the same model succeeds or fails depending on
        them: without this the user cannot tell "my MaximumStep is wrong" from
        "my model is wrong".
        """
        bits = [f"solver={self.solver}", f"model format={self.model_type}", f"dt={self.dt}"]
        bits += [f"{k}={v}" for k, v in sorted(self.solver_info.items()) if k != "dt"]
        bits += [f"{k}={v}" for k, v in extra.items() if v is not None]
        return ", ".join(bits)

    def failure_message(self, reason: str, **extra) -> str:
        """Compose the user-facing message for a failed run."""
        head = f"Simulation failed: {reason}" if reason else "Simulation failed."
        parts = [head, f"Settings in force: {self.settings_summary(**extra)}."]
        if not reason:
            parts.insert(
                1,
                "The solver gave no reason — check the server log for output from the "
                "simulation backend.",
            )
        parts.append(_failure_hint(reason))
        return "\n".join(parts)

    def describe_exception(self, exc: BaseException, captured: str = "", **extra) -> str:
        """Message for a run that raised rather than returning a failure flag.

        The exception text alone is often uninformative ("Protocol simulation
        failed."), because CA prints the real cause and raises a summary, so any
        solver output captured alongside it is preferred and the exception is
        kept as a fallback.
        """
        reason = _solver_reason(captured) or f"{type(exc).__name__}: {exc}"
        return self.failure_message(reason, **extra)

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
        best_effort_outputs: bool = False,
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

            # CA's helper swallows the solver error and returns False, so the
            # only record of *why* is what it printed (issue #138).
            tee = _Tee(sys.stdout)
            try:
                with contextlib.redirect_stdout(tee):
                    ok = helper.run()
            except Exception as exc:  # noqa: BLE001 - re-raised with context below
                raise SimulationError(
                    self.describe_exception(
                        exc, tee.getvalue(), sim_time=sim_time, pre_time=pre_time
                    )
                ) from exc
            if ok is False:
                raise SimulationError(
                    self.failure_message(
                        _solver_reason(tee.getvalue()),
                        sim_time=sim_time,
                        pre_time=pre_time,
                    )
                )

            # Myokit/OpenCOR/python helpers expose get_time; the CasADi helper
            # doesn't, but resolves the logged time vector as the 'time' variable.
            if hasattr(helper, "get_time"):
                time = [float(t) for t in helper.get_time(include_pre_time=False)]
            else:
                time = [float(t) for t in helper.get_results(["time"], flatten=True)[0]]
            out: dict[str, list[float]] = {}
            for var in outputs:
                try:
                    series = helper.get_results([var], flatten=True)[0]
                except (KeyError, ValueError, IndexError):
                    # `best_effort_outputs` means "everything you can give me"
                    # rather than a specific list (saving a run, #148/#150). Some
                    # variables the CellML parser classifies as algebraic are not
                    # resolvable outputs in the solver -- 3compartment's
                    # pvn_module.R_v among them -- and failing the whole request
                    # for one of those turned the wider save into no save at all.
                    # An explicit request still fails loudly: a typo there is a
                    # mistake worth reporting.
                    if best_effort_outputs:
                        continue
                    raise
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

            # run_protocols raises a bare "Protocol simulation failed." while the
            # helper prints the actual solver error, so without capturing stdout
            # this reached the browser as a bodyless 500 (issue #138).
            tee = _Tee(sys.stdout)
            try:
                with contextlib.redirect_stdout(tee):
                    t_list, res_list, _sim_times = runner.run_protocols(
                        str(model_path),
                        protocol_info=protocol_info,
                        id_param_names=names,
                        id_param_vals=vals,
                    )
            except Exception as exc:  # noqa: BLE001 - re-raised with context below
                raise SimulationError(
                    self.describe_exception(
                        exc, tee.getvalue(), experiments=_n_experiments(protocol_info)
                    )
                ) from exc
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
