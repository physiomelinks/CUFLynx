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
import math
import os
import subprocess
import sys
import threading
import warnings as _warnings
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
# Divergence (issue #175)
# ---------------------------------------------------------------------------
# A solver that diverges does not always raise or return False. AADC's rk4 on a
# stiff model walks off to 1e138 and then to NaN, and the helper hands the NaNs
# back as an ordinary result: the API answered 200 with 1998 of 2001 samples
# serialised as JSON null, and the user got an empty plot and no reason for it.
# Whether a run produced numbers is something we can check ourselves, so we do.


def nonfinite_counts(outputs: dict) -> dict:
    """``{variable: (nonfinite, total)}`` for every series with a bad sample."""
    counts = {}
    for name, series in (outputs or {}).items():
        total = len(series or [])
        if not total:
            continue
        bad = sum(1 for v in series if v is None or not math.isfinite(v))
        if bad:
            counts[name] = (bad, total)
    return counts


def divergence_report(outputs: dict) -> tuple:
    """``(fatal, message)`` for a run's outputs; ``(False, "")`` when all finite.

    Fatal only when *nothing* finite came back: a run that diverges partway
    still shows the user where it went wrong, and truncating it for them would
    hide that. A wholly non-finite result has nothing to show and must not be
    reported as a success.
    """
    counts = nonfinite_counts(outputs)
    if not counts:
        return False, ""
    named = sorted(outputs or {})
    total_all = sum(len(outputs[n] or []) for n in named)
    bad_all = sum(bad for bad, _ in counts.values())
    every_series = len(counts) == len([n for n in named if outputs[n]])
    if every_series and bad_all == total_all:
        return True, (
            f"the solver returned no finite values — all {total_all} samples of every "
            f"output are NaN or infinite, so the integration diverged"
        )
    worst = ", ".join(
        f"{name} ({bad} of {total})" for name, (bad, total) in sorted(counts.items())[:4]
    )
    more = "" if len(counts) <= 4 else f", and {len(counts) - 4} more"
    return False, (
        f"The solver returned NaN or infinite values: {worst}{more}. "
        "The integration diverged partway; the plotted trace is incomplete."
    )


def _series_of(result: dict) -> dict:
    """Every series a run produced, whether it was a single run or a protocol.

    A protocol run keeps its outputs per experiment; a diverged experiment is
    just as unplottable as a diverged single run, so both are checked.
    """
    if result.get("outputs") is not None:
        return result.get("outputs") or {}
    series = {}
    for idx, exp in enumerate(result.get("experiments") or []):
        for name, values in (exp.get("outputs") or {}).items():
            series[f"{name} (experiment {idx + 1})"] = values
    return series


# The warning categories a solver uses to say something about the run: CA's
# stiffness check (UserWarning) and numpy's overflow/invalid-value notices
# (RuntimeWarning), which are the first sign of a diverging integration.
#
# Deliberately not every category. A blanket simplefilter("always") also
# un-ignores DeprecationWarning and ResourceWarning, which are ignored by default
# for good reason -- an unclosed-file notice from a dependency would land in the
# user's plot banner next to the reason their model is wrong.
SOLVER_WARNING_CATEGORIES = (UserWarning, RuntimeWarning)


def _force_solver_warnings() -> None:
    """Show solver warnings whatever filters the environment installed.

    Filters are ambient state: CA sets its own, and the app can be started under
    PYTHONWARNINGS=ignore. Neither should decide whether the user can be told
    that their trace is wrong.
    """
    for category in SOLVER_WARNING_CATEGORIES:
        _warnings.simplefilter("always", category)


def _warning_texts(caught) -> list:
    """The distinct warning messages from one run, in the order raised.

    Duplicated as ``sim_worker_runner._warning_texts`` — that file is executed by
    an external interpreter and cannot import this one. Keep the two in step.
    """
    texts: list[str] = []
    for entry in caught or []:
        # By category, not by filter. Which warnings are *raised* depends on
        # ambient state (pytest shows deprecations, a plain run does not), and
        # what the user is shown must not.
        category = getattr(entry, "category", None)
        if not (isinstance(category, type) and issubclass(category, SOLVER_WARNING_CATEGORIES)):
            continue
        text = " ".join(str(entry.message).split())
        if text and text not in texts:
            texts.append(text)
    return texts


# ---------------------------------------------------------------------------
# Sub-experiments (issue #181, second half)
#
# An obs_data data_item names both an experiment *and* a subexperiment, and CA
# scores it against that subexperiment's own trace -- it indexes a flat list
# built as sum(num_sub_per_exp[:exp]) + sub. CUFLynx returned one trace per
# experiment, with the subexperiments concatenated, so every item past the first
# subexperiment was scored against the wrong segment. On SN_simple that put a
# spike-frequency observable expecting 4.0 against the 0.0-spiking segment.
#
# The per-subexperiment results come from CA's own ProtocolExecutor, exactly as
# paramID gets them. Only the *join* is ported, for the plot payload, which
# still shows one trace per experiment.
# ---------------------------------------------------------------------------
def _sub_counts(protocol_info: dict) -> list:
    """num_sub_per_exp, defaulted the way CA defaults it."""
    sim_times = protocol_info.get("sim_times") or []
    n_exp = protocol_info.get("num_experiments") or len(sim_times)
    return protocol_info.get("num_sub_per_exp") or [
        len(sim_times[i]) if i < len(sim_times) else 1 for i in range(n_exp)
    ]


def _subexperiment_outputs(results_by_sub, protocol_info, outputs, key_for, var2idx, time_by_sub=None):
    """``[{experiment_idx, subexperiment_idx, outputs}]`` in CA's flat order."""
    subs = []
    # `time` is an operand of every windowed or peak-timing observable. CA gets
    # it per segment from the helper like any other variable, so it is resolved
    # here the same way -- taking the whole run's time vector instead (which is
    # what the per-experiment payload does) would hand a segment the wrong clock.
    subs = []
    for exp_idx, n_sub in enumerate(_sub_counts(protocol_info)):
        for sub_idx in range(n_sub):
            res = results_by_sub.get((exp_idx, sub_idx))
            values: dict = {}
            for var in outputs:
                key = key_for.get(var)
                if key is None or res is None:
                    continue
                idx = var2idx[key]
                if idx >= len(res):
                    continue
                series = res[idx]
                # A constant subexperiment result is a scalar; the cost's
                # operations expect a series either way.
                values[var] = (
                    [float(v) for v in series]
                    if hasattr(series, "__len__")
                    else [float(series)]
                )
            t_seg = (time_by_sub or {}).get((exp_idx, sub_idx))
            if t_seg is not None and len(t_seg):
                # get_results returns one entry per requested variable; ravel
                # because a helper may hand back a column rather than a row.
                import numpy as _np  # noqa: PLC0415

                values["time"] = [float(v) for v in _np.asarray(t_seg[0]).ravel()]
            subs.append(
                {
                    "experiment_idx": exp_idx,
                    "subexperiment_idx": sub_idx,
                    "outputs": values,
                }
            )
    return subs


def bind_protocol(runner, protocol_info):
    """Bind the protocol onto the helper, then re-read the variable order.

    Binding `pace` rebuilds the simulation, and the rebuilt model has one more
    variable, so every result row shifts: `soma_SN/V` read back as its neighbour
    `E_Na`, 145 mV out. `ProtocolRunner.run_protocols` refreshes `variable_names`
    for exactly this reason -- calling the executor directly means doing it here.
    `_applied_protocol_info` is CA's own marker, set so a later `run_protocols`
    on this runner does not rebuild the simulation again to bind what is bound.
    """
    helper = getattr(runner, "sim_helper", None)
    if helper is None or not hasattr(helper, "set_protocol_info"):
        return
    helper.set_protocol_info(protocol_info)
    runner._applied_protocol_info = protocol_info
    runner.variable_names = helper.get_all_variable_names()


def _run_protocol_by_sub(runner, protocol_info, names, vals):
    """Run the protocol through CA's ProtocolExecutor, keeping the segments.

    ``ProtocolRunner`` builds one of these internally and then throws the
    segments away; this is the same public class, on the same helper, called the
    way paramID calls it.
    """
    # The runner's own executor, never a fresh one: ProtocolRunner builds it once
    # at construction and reuses it, and a second executor over the same helper
    # made repeated runs non-reproducible (run 3 diverged from run 1 by 145 mV).
    # So its absence means a CA too old to have one, and the answer is to fall
    # back to run_protocols -- the joined traces are still right, and obs_cost
    # scores per experiment as it did before -- not to build one here.
    executor = getattr(runner, "_executor", None)
    if executor is None:
        return None, None, None
    success, results_by_sub, extra_by_sub, t_by_exp = executor.run_protocol(
        protocol_info,
        result_variables=None,
        # `time` is an operand of every windowed or peak-timing observable, and
        # it is not a model variable in var2idx -- the per-experiment payload
        # takes it from t_list, which is the *joined* clock and would put a
        # segment's window in the wrong place. CA reads it per segment through
        # get_results; extra_result_variables collects it in the same pass.
        extra_result_variables=["time"],
        id_param_names=names,
        id_param_vals=vals,
    )
    if not success:
        # Same failure run_protocols raises, so the message the user sees, and
        # the stdout capture that explains it, are unchanged (#138).
        raise RuntimeError("Protocol simulation failed.")
    return results_by_sub, t_by_exp, extra_by_sub


def join_subexperiments(results_by_sub, t_by_exp, protocol_info, dt):
    """One result vector per experiment, subexperiments joined end to end.

    A port of ``ProtocolRunner.run_protocols``'s join -- scalar results padded to
    the segment length, and the repeated first sample dropped where segments
    meet. Ported rather than called because run_protocols returns only the joined
    form, and the cost needs the segments; ``test_subexperiment_cost`` asserts
    this agrees with CA's own output so the two cannot drift (CA issue filed to
    return both, after which this goes).
    """
    import numpy as np  # noqa: PLC0415 - only on the simulation path

    sim_times = protocol_info.get("sim_times") or []
    num_sub_per_exp = _sub_counts(protocol_info)
    res_list = []
    for exp_idx in range(len(t_by_exp)):
        res_vec = None
        for sub_idx in range(num_sub_per_exp[exp_idx]):
            sub_res = results_by_sub.get((exp_idx, sub_idx))
            if sub_res is None:
                continue
            if res_vec is None:
                res_vec = list(sub_res)
                for var_idx in range(len(res_vec)):
                    if not hasattr(res_vec[var_idx], "__len__"):
                        n = len(t_by_exp[exp_idx]) if t_by_exp[exp_idx] is not None else 1
                        res_vec[var_idx] = np.ones(n) * res_vec[var_idx]
            else:
                for var_idx in range(len(res_vec)):
                    new_data = sub_res[var_idx]
                    if not hasattr(new_data, "__len__"):
                        n_sub = round(sim_times[exp_idx][sub_idx] / dt)
                        new_data = np.ones(n_sub) * new_data
                    else:
                        new_data = new_data[1:]
                    res_vec[var_idx] = np.concatenate([res_vec[var_idx], new_data])
        res_list.append(res_vec)
    return res_list


# ---------------------------------------------------------------------------
# Live-simulation backend availability
#
# Live plots run *in this process*, while calibration / sensitivity / UQ run as
# subprocesses in the interpreter chosen in Settings. So a backend library the
# user installed for analysis is not necessarily importable here -- and picking
# such a format used to make every live simulation fail, including the first one
# after a restart, because the choice is persisted.
#
# The chosen format still governs analysis, which is where it matters. The live
# preview falls back to a backend this process can actually run, and says so, so
# the app stays usable instead of failing on open.
#
# The modules named here are what *this process* must import to run each format;
# that is CUFLynx's own concern (which deps the app bundles), not CA data, so it
# is not introspected from CA.
# ---------------------------------------------------------------------------
_BACKEND_MODULE = {
    "cellml_only": "myokit",
    "python": "scipy",
    "casadi_python": "casadi",
    "aadc_python": "aadc",
}

# Tried in order when the chosen format cannot run here. python/solve_ivp last:
# it needs no compiler, so it is the one that works when nothing else does.
_LIVE_FALLBACKS = (
    ("cellml_only", "CVODE_myokit"),
    ("casadi_python", "casadi_integrator"),
    ("python", "solve_ivp"),
)


def backend_importable(model_type: str) -> bool:
    """Whether *this* process can run ``model_type``.

    find_spec rather than import: probing must not pull a heavy (or licensed)
    library into the API process as a side effect.
    """
    module = _BACKEND_MODULE.get(model_type)
    if not module:
        return True  # unknown format: assume the caller knows better than us
    try:
        import importlib.util  # noqa: PLC0415

        return importlib.util.find_spec(module) is not None
    except (ImportError, ValueError):
        return False


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


# path -> sys.prefix, so the probe below costs one subprocess per interpreter
# rather than one per settings change.
_PREFIX_CACHE: dict[str, str] = {}


def _interpreter_prefix(python: str) -> str:
    """``sys.prefix`` of another interpreter, or "" if it cannot be asked."""
    cached = _PREFIX_CACHE.get(python)
    if cached is not None:
        return cached
    try:
        out = subprocess.run(
            [python, "-c", "import sys;print(sys.prefix)"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        prefix = out.stdout.strip() if out.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError):
        prefix = ""
    _PREFIX_CACHE[python] = prefix
    return prefix


def _is_this_interpreter(python: str) -> bool:
    """Whether ``python`` is the *environment* already serving the API.

    Compared by ``sys.prefix``, not by executable path. A venv's ``bin/python``
    is usually a symlink to the base interpreter, so realpath makes a venv look
    identical to the interpreter it was created from -- and the venv is the whole
    point, since that is where the user installed the packages they picked it
    for. The same mistake once made venvs vanish from the interpreter picker.

    Never true in the packaged app: ``sys.executable`` there is the bundle, not
    a Python, so an external choice always differs and always gets a worker.
    """
    if is_frozen():
        return False
    prefix = _interpreter_prefix(python)
    return bool(prefix) and prefix == sys.prefix


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
        # The interpreter live simulation should run in (#167). None/"" keeps the
        # old in-process path, which is the frozen app's default and what a user
        # who never opens Settings gets.
        self.worker_python: str | None = None
        self._worker = None
        self._helpers: dict[str, object] = {}
        self._runners: dict[str, object] = {}
        # model_id -> last protocol_info object whose pace binding is active on
        # the cached runner's helper (avoids re-binding/recreating every run).
        self._runner_protocol_info: dict[str, object] = {}
        self._lock = threading.Lock()

    def live_backend(self) -> tuple:
        """``(model_type, solver, fell_back_from)`` for a live simulation.

        ``fell_back_from`` is None when the configured format runs here, and the
        configured format otherwise -- so the caller can tell the user the plot
        is a preview from a different backend rather than silently showing one.
        """
        # With a worker, what this process can import is beside the point: the
        # model runs in the chosen interpreter, and the fallback below would
        # swap the backend -- and, through resolve_model_path, hand the worker a
        # model generated for a format it was not configured to read. That is
        # how selecting aadc_python produced "'NoneType' object has no attribute
        # 'loader'": a .cellml path passed to a helper that imports Python.
        if self.uses_worker() or backend_importable(self.model_type):
            return self.model_type, self.solver, None
        for fmt, solver in _LIVE_FALLBACKS:
            if backend_importable(fmt):
                return fmt, solver, self.model_type
        # Nothing importable at all: keep the configured choice so the failure
        # names the real problem rather than a substitute we also cannot run.
        return self.model_type, self.solver, None

    def uses_worker(self) -> bool:
        """Whether live simulation will run in a worker rather than here (#167)."""
        python = (self.worker_python or "").strip()
        return bool(python) and not _is_this_interpreter(python)

    def reset(self) -> None:
        """Drop all cached helpers/runners (used between tests).

        Stops the worker too: its caches are the same caches, and it holds an
        imported copy of CA that a reset is usually trying to be rid of.
        """
        with self._lock:
            self._helpers.clear()
            self._runners.clear()
            self._runner_protocol_info.clear()
            worker, self._worker = self._worker, None
        if worker is not None:
            worker.stop()

    # ------------------------------------------------------------------
    # Worker delegation (#167)
    # ------------------------------------------------------------------
    def _worker_settings(self) -> dict:
        from sim_worker import worker_settings  # noqa: PLC0415 - optional path

        return worker_settings(
            ca_src=_circulatory_autogen_src(),
            dt=self.dt,
            model_type=self.model_type,
            solver=self.solver,
            solver_info=self.solver_info,
        )

    def _acquire_worker(self):
        """The live worker for the current settings, starting it if needed.

        Returns None when live simulation should stay in-process: no interpreter
        chosen. Any *failure* to start one is raised rather than silently falling
        back -- a user who picked an interpreter and quietly got a different one
        is exactly the confusion this exists to end.
        """
        python = (self.worker_python or "").strip()
        if not python:
            return None
        if _is_this_interpreter(python):
            # The split only exists when the two tiers differ. Running a worker
            # to reach the interpreter already running costs a process and a
            # model compile and changes nothing about what is importable.
            return None

        from sim_worker import SimWorker, WorkerError  # noqa: PLC0415

        settings = self._worker_settings()
        worker = self._worker
        if worker is not None and (not worker.alive or not worker.matches(python, settings)):
            worker.stop()
            worker = self._worker = None
            # A settings change invalidates the parent's mirror of the caches too.
            self._helpers.clear()
            self._runners.clear()
            self._runner_protocol_info.clear()
        if worker is None:
            worker = SimWorker(python, settings)
            try:
                worker.start()
            except WorkerError as exc:
                raise SimulationError(
                    self.failure_message(
                        str(exc)
                        + "\n(Live simulation runs in the interpreter chosen in Settings. "
                        "Pick one that can import circulatory_autogen's simulation stack, "
                        "or clear it to run in the app's own interpreter.)"
                    )
                ) from exc
            self._worker = worker
        return worker

    def _worker_call(self, op: str, extra: dict, **payload) -> dict:
        """One request, with the reply turned back into today's error text.

        The worker returns the captured solver output and a fallback reason and
        leaves the wording here, so the issue #138 error quality lives in one
        place rather than being reimplemented on the far side of a pipe.
        """
        from sim_worker import WorkerError  # noqa: PLC0415

        worker = self._acquire_worker()
        if worker is None:
            return {}
        try:
            reply = worker.call(op, **payload)
        except WorkerError as exc:
            self._worker = None
            raise SimulationError(self.failure_message(str(exc), **extra)) from exc

        if reply.get("ok"):
            return {
                "result": reply.get("result") or {},
                "warnings": list(reply.get("warnings") or []),
            }
        reason = _solver_reason(reply.get("captured") or "") or (reply.get("reason") or "")
        raise SimulationError(self.failure_message(reason, **extra))

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

    def finish_run(self, result: dict, warned: list, **extra) -> dict:
        """Attach a run's warnings and refuse a result that is entirely NaN.

        One place for both paths: the worker collects warnings in the child and
        the in-process path collects them here, but what they *mean* -- and what
        counts as a run worth returning -- must not differ by which interpreter
        happened to run it (#175).
        """
        messages = list(warned or [])
        fatal, note = divergence_report(_series_of(result))
        if fatal:
            raise SimulationError(self.failure_message(note, **extra))
        if note:
            # First: it is the finding about *this* run, and a real one came back
            # behind six lines of AADC licence notice and stiffness detail.
            messages.insert(0, note)
        if messages:
            result["warnings"] = messages
        return result

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
        # The worker first, and without live_backend(): its fallback answers
        # "what can *this* process import", which is the wrong question once the
        # model runs somewhere else. The worker gets the configured backend and
        # fails loudly if its interpreter cannot run it -- which is the whole
        # reason for choosing that interpreter.
        remote = self._worker_call(
            "simulate",
            {"sim_time": sim_time, "pre_time": pre_time},
            model_id=model_id,
            model_path=str(model_path),
            params=params or {},
            sim_time=float(sim_time),
            pre_time=float(pre_time),
            outputs=list(outputs or []),
            best_effort_outputs=bool(best_effort_outputs),
        )
        if remote:
            return self.finish_run(
                remote["result"], remote.get("warnings"),
                sim_time=sim_time, pre_time=pre_time,
            )

        model_type, solver, fell_back = self.live_backend()
        with self._lock:
            # Key the cache on the backend too: falling back must not hand back a
            # helper compiled for the format we could not run.
            cache_key = (model_id, model_type, solver)
            helper = self._helpers.get(cache_key)
            if helper is None:
                helper = self.helper_factory(
                    model_path=str(model_path),
                    dt=self.dt,
                    sim_time=float(sim_time),
                    pre_time=float(pre_time),
                    solver_info=self.solver_info,
                    model_type=model_type,
                    solver=solver,
                )
                self._helpers[cache_key] = helper

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
                # Warnings are the other half of what CA has to say -- the
                # stiffness check that names a wrong-looking trace as wrong is a
                # warning, not a print (#175).
                with _warnings.catch_warnings(record=True) as caught:
                    _force_solver_warnings()
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

        result = {"time": time, "outputs": out}
        if fell_back:
            result["backend_fallback"] = {
                "requested": fell_back,
                "used": model_type,
                "solver": solver,
            }
        return self.finish_run(
            result, _warning_texts(caught), sim_time=sim_time, pre_time=pre_time
        )

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
        # See simulate(): the worker runs the configured backend, and the
        # in-process fallback below is for when there is no worker.
        remote = self._worker_call(
            "run_protocol",
            {"experiments": _n_experiments(protocol_info)},
            model_id=model_id,
            model_path=str(model_path),
            protocol_info=protocol_info,
            params=params or {},
            outputs=list(outputs or []),
        )
        if remote:
            return self.finish_run(
                remote["result"], remote.get("warnings"),
                experiments=_n_experiments(protocol_info),
            )

        model_type, solver, fell_back = self.live_backend()
        with self._lock:
            cache_key = (model_id, model_type, solver)
            runner = self._runners.get(cache_key)
            if runner is None:
                runner = self.runner_factory(
                    model_path=str(model_path),
                    dt=self.dt,
                    solver_info=self.solver_info,
                    model_type=model_type,
                    solver=solver,
                )
                self._runners[cache_key] = runner

            # Bind any time-varying protocol traces onto the helper before the
            # run. set_protocol_info recreates the simulation with the `pace`
            # binding, so only call it when the protocol actually changed.
            if self._runner_protocol_info.get(cache_key) is not protocol_info:
                bind_protocol(runner, protocol_info)
                self._runner_protocol_info[cache_key] = protocol_info

            names = list(params.keys()) if params else None
            vals = [params[n] for n in names] if names else None

            # run_protocols raises a bare "Protocol simulation failed." while the
            # helper prints the actual solver error, so without capturing stdout
            # this reached the browser as a bodyless 500 (issue #138).
            tee = _Tee(sys.stdout)
            try:
                # See simulate(): CA's stiffness check is a warning, not a print.
                with _warnings.catch_warnings(record=True) as caught:
                    _force_solver_warnings()
                    with contextlib.redirect_stdout(tee):
                        # CA's own executor rather than run_protocols: it is what
                        # paramID scores from, and it keeps the subexperiments
                        # apart instead of joining them (#181).
                        results_by_sub, t_list, time_by_sub = _run_protocol_by_sub(
                            runner, protocol_info, names, vals
                        )
                        if results_by_sub is None:
                            t_list, res_list, _sim_times = runner.run_protocols(
                                str(model_path),
                                protocol_info=protocol_info,
                                id_param_names=names,
                                id_param_vals=vals,
                            )
                        else:
                            res_list = join_subexperiments(
                                results_by_sub, t_list, protocol_info, self.dt
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

        # The segments, for scoring. The plots still get one trace per
        # experiment; a data_item is scored against the subexperiment it names.
        subexperiments = (
            _subexperiment_outputs(
                results_by_sub, protocol_info, outputs, key_for, var2idx, time_by_sub
            )
            if results_by_sub is not None
            else []
        )

        out: dict = {"experiments": experiments}
        if subexperiments:
            out["subexperiments"] = subexperiments
        if fell_back:
            out["backend_fallback"] = {
                "requested": fell_back,
                "used": model_type,
                "solver": solver,
            }
        return self.finish_run(
            out, _warning_texts(caught), experiments=_n_experiments(protocol_info)
        )


# Module-level singleton shared by the FastAPI routes.
engine = SimulationEngine()
