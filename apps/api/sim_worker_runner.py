"""Standalone live-simulation worker — a long-lived subprocess of the API (#167).

Live simulation used to run *inside* the API process, which meant it ran in
whatever interpreter started the app while calibration/sensitivity/UQ ran in the
one picked in Settings. "Switch Python" therefore only ever switched half the
app. This script is the other half: the same work, in the chosen interpreter.

Long-lived on purpose. The expensive thing is compiling the model — which is
what the helper/runner caches below exist to avoid — so a process per request
would recompile on every slider drag. It stays up and holds the caches, and the
parent kills it when the interpreter, CA directory or solver changes.

Protocol: one JSON object per line on stdin, one per line on stdout.

    -> {"id": 1, "op": "configure", "ca_src": "...", "dt": 0.01, ...}
    <- {"id": 1, "ok": true, "result": {...}}
    <- {"id": 1, "ok": false, "reason": "...", "captured": "..."}

Four verbs: ``configure``, ``simulate``, ``run_protocol``, ``ping``. Keep it that
way -- the failure mode for this design is a second API growing beside the first.

Everything circulatory_autogen prints is captured per request and returned in
``captured``, because the parent needs it to explain a failure (issue #138) and
because stdout here is the protocol channel and must carry nothing else.

Usage:  python -u sim_worker_runner.py
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import sys
import traceback
import warnings

# stdout is the wire. Anything that writes to it -- CA's own prints, a library's
# banner -- would corrupt a response, so the real stdout is claimed here and
# sys.stdout is pointed at stderr for the rest of the process's life.
_WIRE = os.fdopen(os.dup(sys.stdout.fileno()), "w", encoding="utf-8", newline="\n")
# Reassigning sys.stdout only redirects *Python* writes. A native library writes
# to file descriptor 1 directly and lands on the wire regardless -- AADC prints
# "AADC LicenseSpring exception encountered: ..." that way, and the parent failed
# the run with "sent something that is not a reply" while the real reply sat
# behind it in the pipe. Point the descriptor itself at stderr, so there is no
# route to the wire left except _WIRE.
try:
    os.dup2(sys.stderr.fileno(), 1)
except (OSError, ValueError, AttributeError):
    # No usable stderr descriptor (an odd embedding, a frozen GUI with no
    # console). The Python-level redirect below still covers CA's own prints.
    pass
sys.stdout = sys.stderr


class _Tee(io.TextIOBase):
    """Collect what CA prints while still letting it reach the server log."""

    def __init__(self, target):
        self._target = target
        self._buf: list[str] = []

    def write(self, s):  # noqa: D102 - TextIOBase interface
        self._buf.append(s)
        try:
            self._target.write(s)
        except Exception:  # noqa: BLE001 - logging must never fail a run
            pass
        return len(s)

    def flush(self):  # noqa: D102 - TextIOBase interface
        with contextlib.suppress(Exception):
            self._target.flush()

    def getvalue(self) -> str:
        return "".join(self._buf)


def _resolve_output_key(var2idx, name):
    """Resolve an output name against var2idx across CA backends.

    Deliberately duplicated from ``engine.py`` rather than imported: this script
    is executed by the *user's* interpreter, and in the packaged app the app's
    own modules are frozen into the bundle and cannot be imported from outside
    it. Keep the two in step -- a divergence shows up as a missing output.
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


class Worker:
    def __init__(self):
        self.ca_src = ""
        self.dt = 0.01
        self.model_type = "cellml_only"
        self.solver = "CVODE_myokit"
        self.solver_info: dict = {}
        self._helpers: dict = {}
        self._runners: dict = {}
        self._runner_protocol_info: dict = {}

    # -- setup ---------------------------------------------------------
    def configure(self, msg):
        """Adopt the parent's backend settings.

        Only ever called once per worker: the parent restarts the worker rather
        than reconfiguring it, because CA's modules are cached on first import
        and a mid-life change could not fully take effect anyway -- which is the
        very bug this design exists to remove.
        """
        self.ca_src = msg.get("ca_src") or ""
        self.dt = float(msg.get("dt", self.dt))
        self.model_type = msg.get("model_type") or self.model_type
        self.solver = msg.get("solver") or self.solver
        self.solver_info = dict(msg.get("solver_info") or {})
        if self.ca_src and self.ca_src not in sys.path:
            sys.path.insert(0, self.ca_src)
        return {"python": sys.executable, "version": sys.version.split()[0]}

    def _helper(self, model_id, model_path, sim_time, pre_time):
        helper = self._helpers.get(model_id)
        if helper is None:
            from solver_wrappers import get_simulation_helper  # noqa: PLC0415

            helper = get_simulation_helper(
                model_path=str(model_path),
                solver=self.solver,
                model_type=self.model_type,
                dt=self.dt,
                sim_time=sim_time,
                pre_time=pre_time,
                solver_info=self.solver_info,
            )
            self._helpers[model_id] = helper
        return helper

    def _runner(self, model_id, model_path):
        runner = self._runners.get(model_id)
        if runner is None:
            from protocol_runners import ProtocolRunner  # noqa: PLC0415

            runner = ProtocolRunner(
                str(model_path),
                inp_data_dict={
                    "dt": self.dt,
                    "solver_info": self.solver_info,
                    "model_type": self.model_type,
                },
                solver=self.solver,
                model_type=self.model_type,
            )
            self._runners[model_id] = runner
        return runner

    # -- the work ------------------------------------------------------
    def simulate(self, msg, tee):
        model_id = msg["model_id"]
        sim_time = float(msg["sim_time"])
        pre_time = float(msg["pre_time"])
        helper = self._helper(model_id, msg["model_path"], sim_time, pre_time)

        helper.reset_and_clear()
        helper.update_times(self.dt, 0.0, sim_time, pre_time)

        params = msg.get("params") or {}
        if params:
            names = list(params.keys())
            helper.set_param_vals(names, [params[n] for n in names])

        with contextlib.redirect_stdout(tee):
            ok = helper.run()
        if ok is False:
            # CA swallows the solver error and returns False; the reason is only
            # in what it printed, which the parent turns into a message.
            return None

        if hasattr(helper, "get_time"):
            time = [float(t) for t in helper.get_time(include_pre_time=False)]
        else:
            time = [float(t) for t in helper.get_results(["time"], flatten=True)[0]]

        out: dict = {}
        for var in msg.get("outputs") or []:
            try:
                series = helper.get_results([var], flatten=True)[0]
            except (KeyError, ValueError, IndexError):
                # best_effort means "everything you can give me" rather than a
                # named list; an explicit request still fails loudly.
                if msg.get("best_effort_outputs"):
                    continue
                raise
            out[var] = [float(v) for v in series]
        return {"time": time, "outputs": out}

    def run_protocol(self, msg, tee):
        model_id = msg["model_id"]
        protocol_info = msg["protocol_info"]
        runner = self._runner(model_id, msg["model_path"])

        # Binding `pace` recreates the simulation, so only do it when the
        # protocol actually changed. Compared by value here, not identity: the
        # parent's dict crossed a process boundary and is a new object each time.
        if self._runner_protocol_info.get(model_id) != protocol_info:
            _bind_protocol(runner, protocol_info)
            self._runner_protocol_info[model_id] = json.loads(json.dumps(protocol_info))

        params = msg.get("params") or {}
        names = list(params.keys()) if params else None
        vals = [params[n] for n in names] if names else None

        with contextlib.redirect_stdout(tee):
            results_by_sub, t_list, time_by_sub = _run_protocol_by_sub(
                runner, protocol_info, names, vals
            )
            if results_by_sub is None:
                t_list, res_list, _sim_times = runner.run_protocols(
                    str(msg["model_path"]),
                    protocol_info=protocol_info,
                    id_param_names=names,
                    id_param_vals=vals,
                )
            else:
                res_list = _join_subexperiments(
                    results_by_sub, t_list, protocol_info, self.dt
                )
        var2idx = runner.get_var2idx_dict()

        outputs = msg.get("outputs") or []
        key_for = {var: _resolve_output_key(var2idx, var) for var in outputs}
        experiments = []
        for exp_idx, t in enumerate(t_list):
            res = res_list[exp_idx]
            exp_outputs: dict = {}
            for var in outputs:
                key = key_for.get(var)
                if key is None or res is None:
                    continue
                idx = var2idx[key]
                if idx >= len(res):
                    continue
                exp_outputs[var] = [float(v) for v in res[idx]]
            experiments.append(
                {"time": [float(v) for v in t] if t is not None else [], "outputs": exp_outputs}
            )
        result = {"experiments": experiments}
        if results_by_sub is not None:
            result["subexperiments"] = _subexperiment_outputs(
                results_by_sub, protocol_info, outputs, key_for, var2idx, time_by_sub
            )
        return result


# ---------------------------------------------------------------------------
# Sub-experiments (issue #181)
#
# Duplicated from engine.py -- an *external* interpreter executes this file and
# cannot import the app's modules (the same reason _resolve_output_key is
# duplicated). Keep the two in step; test_subexperiment_cost asserts the joined
# traces still match CA's own on both paths.
# ---------------------------------------------------------------------------
def _sub_counts(protocol_info):
    sim_times = protocol_info.get("sim_times") or []
    n_exp = protocol_info.get("num_experiments") or len(sim_times)
    return protocol_info.get("num_sub_per_exp") or [
        len(sim_times[i]) if i < len(sim_times) else 1 for i in range(n_exp)
    ]


def _bind_protocol(runner, protocol_info):
    """Bind the protocol, then re-read the variable order. See engine.bind_protocol.

    Binding `pace` rebuilds the simulation with one more variable, so every
    result row shifts and each variable reads as its neighbour unless the map is
    refreshed -- which run_protocols did for us and the executor does not.
    """
    helper = getattr(runner, "sim_helper", None)
    if helper is None or not hasattr(helper, "set_protocol_info"):
        return
    helper.set_protocol_info(protocol_info)
    runner._applied_protocol_info = protocol_info
    runner.variable_names = helper.get_all_variable_names()


def _run_protocol_by_sub(runner, protocol_info, names, vals):
    """CA's ProtocolExecutor, which keeps the segments run_protocols joins."""
    # The runner's own executor, never a fresh one -- a second executor over the
    # same helper made repeated runs non-reproducible. Absent means a CA too old
    # to have one, so fall back to run_protocols. See engine._run_protocol_by_sub.
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
        raise RuntimeError("Protocol simulation failed.")
    return results_by_sub, t_by_exp, extra_by_sub


def _join_subexperiments(results_by_sub, t_by_exp, protocol_info, dt):
    import numpy as np

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
                        new_data = np.ones(round(sim_times[exp_idx][sub_idx] / dt)) * new_data
                    else:
                        new_data = new_data[1:]
                    res_vec[var_idx] = np.concatenate([res_vec[var_idx], new_data])
        res_list.append(res_vec)
    return res_list


def _subexperiment_outputs(results_by_sub, protocol_info, outputs, key_for, var2idx, time_by_sub=None):
    subs = []
    for exp_idx, n_sub in enumerate(_sub_counts(protocol_info)):
        for sub_idx in range(n_sub):
            res = results_by_sub.get((exp_idx, sub_idx))
            values = {}
            for var in outputs:
                key = key_for.get(var)
                if key is None or res is None:
                    continue
                idx = var2idx[key]
                if idx >= len(res):
                    continue
                series = res[idx]
                values[var] = (
                    [float(v) for v in series] if hasattr(series, "__len__") else [float(series)]
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


def _warning_texts(caught) -> list:
    """The distinct warning messages from one run, in the order raised.

    Duplicated as ``engine._warning_texts`` for the in-process path: this file is
    executed by an *external* interpreter and cannot import the app's modules
    (same reason as ``_resolve_output_key``). Keep the two in step.
    """
    texts: list[str] = []
    for entry in caught or []:
        # Only what a solver speaks in -- see engine.SOLVER_WARNING_CATEGORIES.
        # By category rather than by filter, because which warnings are raised
        # depends on ambient state and what the user is shown must not.
        category = getattr(entry, "category", None)
        if not (isinstance(category, type) and issubclass(category, (UserWarning, RuntimeWarning))):
            continue
        # A stiff model raises the same warning on every step; the user needs to
        # read it once, not several hundred times.
        text = " ".join(str(entry.message).split())
        if text and text not in texts:
            texts.append(text)
    return texts


#: One loaded emulator bundle, keyed by directory. Predicting is a matrix
#: multiply; loading is a joblib unpickle that drags in torch, so it happens once
#: per worker rather than once per slider drag.
_EMULATOR_CACHE = {}


def _emulator_predict(msg):
    """Predicted scalar features for one theta, from a trained emulator bundle.

    A fifth verb, deliberately, rather than a fifth process: this is a *live*
    computation -- it answers while a slider is being dragged, beside the model's
    own features -- and the live tier is exactly what this worker is. It also has
    to run in the interpreter the user chose, because loading the bundle needs the
    autoemulate/torch that CUFLynx does not bundle.

    Returns the emulator's own feature labels alongside the values: the caller
    aligns by label rather than assuming its obs_data ordering matches the one
    the emulator was trained on.
    """
    emulator_dir = msg["emulator_dir"]
    bundle = _EMULATOR_CACHE.get(emulator_dir)
    if bundle is None:
        from emulators.emulator_bundle import EmulatorBundle

        bundle = EmulatorBundle.load(emulator_dir)
        _EMULATOR_CACHE[emulator_dir] = bundle
    # 'warn', not the configured policy: this is a diagnostic overlay next to the
    # real model output, and refusing to draw it is a worse answer than drawing it
    # with the caller told it is extrapolating. An analysis run still refuses.
    values = bundle.predict(msg["theta"], out_of_bounds="warn")
    in_box = all(
        lo <= float(v) <= hi
        for v, lo, hi in zip(msg["theta"], bundle.param_mins, bundle.param_maxs)
    )
    return {
        "labels": list(bundle.feature_labels),
        "values": [float(v) for v in values],
        "in_box": bool(in_box),
    }


def _handle(worker, msg):
    op = msg.get("op")
    tee = _Tee(sys.stderr)
    try:
        if op == "ping":
            return {"ok": True, "result": {"pid": os.getpid()}}
        if op == "configure":
            return {"ok": True, "result": worker.configure(msg)}
        if op == "emulator_predict":
            return {"ok": True, "result": _emulator_predict(msg)}
        # CA warns about things the run cannot tell you itself -- above all that a
        # model is too stiff for the chosen integrator, which is the difference
        # between "this trace is wrong" and "this trace is wrong *and here is why,
        # and here are two solvers that work*". It reaches the server log and
        # stopped there; collected here it can reach the user (#175).
        with warnings.catch_warnings(record=True) as caught:
            # Only the categories a solver speaks in -- see
            # engine.SOLVER_WARNING_CATEGORIES, kept in step with this. A blanket
            # "always" would also surface ResourceWarning/DeprecationWarning noise
            # from dependencies, in the same banner as the real reason.
            for category in (UserWarning, RuntimeWarning):
                warnings.simplefilter("always", category)
            if op == "simulate":
                result = worker.simulate(msg, tee)
            elif op == "run_protocol":
                result = worker.run_protocol(msg, tee)
            else:
                return {"ok": False, "reason": f"unknown op {op!r}", "captured": ""}
    except Exception as exc:  # noqa: BLE001 - every failure owes the parent a reason
        traceback.print_exc(file=sys.stderr)
        return {
            "ok": False,
            "reason": f"{type(exc).__name__}: {exc}",
            "captured": tee.getvalue(),
        }
    if result is None:
        # The solver reported failure without raising. An empty reason tells the
        # parent to fall back to whatever CA printed.
        return {"ok": False, "reason": "", "captured": tee.getvalue()}
    return {
        "ok": True,
        "result": result,
        "captured": tee.getvalue(),
        "warnings": _warning_texts(caught),
    }


def main() -> int:
    worker = Worker()
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except ValueError as exc:
            reply = {"id": None, "ok": False, "reason": f"bad request: {exc}", "captured": ""}
        else:
            if msg.get("op") == "shutdown":
                return 0
            reply = {"id": msg.get("id"), **_handle(worker, msg)}
        _WIRE.write(json.dumps(reply) + "\n")
        _WIRE.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
