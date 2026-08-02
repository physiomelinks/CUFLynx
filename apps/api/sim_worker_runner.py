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

# stdout is the wire. Anything that writes to it -- CA's own prints, a library's
# banner -- would corrupt a response, so the real stdout is claimed here and
# sys.stdout is pointed at stderr for the rest of the process's life.
_WIRE = os.fdopen(os.dup(sys.stdout.fileno()), "w", encoding="utf-8", newline="\n")
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
        helper = getattr(runner, "sim_helper", None)
        if helper is not None and hasattr(helper, "set_protocol_info"):
            if self._runner_protocol_info.get(model_id) != protocol_info:
                helper.set_protocol_info(protocol_info)
                self._runner_protocol_info[model_id] = json.loads(json.dumps(protocol_info))

        params = msg.get("params") or {}
        names = list(params.keys()) if params else None
        vals = [params[n] for n in names] if names else None

        with contextlib.redirect_stdout(tee):
            t_list, res_list, _sim_times = runner.run_protocols(
                str(msg["model_path"]),
                protocol_info=protocol_info,
                id_param_names=names,
                id_param_vals=vals,
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
        return {"experiments": experiments}


def _handle(worker, msg):
    op = msg.get("op")
    tee = _Tee(sys.stderr)
    try:
        if op == "ping":
            return {"ok": True, "result": {"pid": os.getpid()}}
        if op == "configure":
            return {"ok": True, "result": worker.configure(msg)}
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
    return {"ok": True, "result": result, "captured": tee.getvalue()}


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
