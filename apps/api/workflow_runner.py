"""Calibration workflow runner -- spawned as a subprocess by the API.

Runs circulatory_autogen's ``run_calibration_workflow`` on a calibration_workflow.json.
Every step is CA's own param_id run, and CA writes the run directory: each step's
results, the workflow as run, and the merged parameter set. This script only
forwards CA's progress events to stdout, one small line each, which the manager
turns into per-step state for the tabs.

Usage:  python -u workflow_runner.py <config.json>

config.json:
{
  "workflow": "...calibration_workflow.json", "output_dir": "...",
  "module_library_dirs": ["..."], "from_step": null, "only": null, "run_steps": null,
  "write_calibrated": false
}

A panel's run of one study-workflow step (``study_workflow``) hands this runner its own
config with ``workflow_output_dir`` (the workflow's run directory; ``output_dir`` is then the
working dir) and ``markers``: the done and fail markers that panel's manager reads, printed
beside this runner's own.
"""

from __future__ import annotations

import json
import os
import sys
import traceback
from pathlib import Path

from ca_imports import ca_from, ensure_ca_path

os.environ.setdefault("MPLBACKEND", "Agg")

DONE_MARKER = "__WORKFLOW_DONE__"
FAIL_MARKER = "__WORKFLOW_FAILED__"
EVENT_MARKER = "__WORKFLOW_EVENT__"

# Under mpiexec every rank shares the stdout pipe, and a line longer than
# PIPE_BUF can interleave with another rank's output; CA only reports progress
# on rank 0, and this keeps each line well under the limit.
_MAX_EVENT_CHARS = 2000


def event_line(event: dict) -> str:
    """One ``__WORKFLOW_EVENT__{json}`` line, truncated to stay small."""
    event = dict(event)
    if isinstance(event.get("error"), str) and len(event["error"]) > 800:
        event["error"] = event["error"][:800] + " ..."
    text = json.dumps(event, default=str)
    if len(text) > _MAX_EVENT_CHARS:
        event = {k: event[k] for k in ("event", "step", "index", "total", "error") if k in event}
        text = json.dumps(event, default=str)
    return EVENT_MARKER + text


def unset_parameters(exc: BaseException) -> dict | None:
    """``{"step_id", "names"}`` of CA's ``UnsetParametersError``: a step whose model has
    parameters nothing gives a value, named as the target's parameters file names them
    (``C/x``). None for any other error."""
    names, step_id = getattr(exc, "names", None), getattr(exc, "step_id", None)
    if type(exc).__name__ != "UnsetParametersError" or not step_id or not isinstance(names, list):
        return None
    return {"step_id": step_id, "names": [str(n) for n in names]}


def _rank() -> int:
    try:
        from mpi4py import MPI

        return MPI.COMM_WORLD.Get_rank()
    except Exception:
        return 0


def run(config: dict) -> dict:
    ensure_ca_path()
    run_workflow = ca_from("calibration_workflow", "run_calibration_workflow")
    # CA writes the merged values beside the target (<prefix>_calibrated_parameters.json)
    # after a complete run; only when asked, as that is outside the run directory.
    extra = {"write_calibrated": True} if config.get("write_calibrated") else {}
    common = dict(output_dir=config.get("workflow_output_dir") or config["output_dir"],
                  module_library_dirs=config.get("module_library_dirs") or [],
                  progress=lambda event: print(event_line(event), flush=True))
    steps = config.get("run_steps")
    if steps:
        # a changed step, then each step that takes from it: each alone, in this order, so
        # each finds what it takes from current and the workflow's other steps do not run
        result = None
        for i, step in enumerate(steps):
            result = run_workflow(config["workflow"], only=step, **common,
                                  **(extra if i == len(steps) - 1 else {}))
        return result
    return run_workflow(
        config["workflow"],
        from_step=config.get("from_step") or None,
        only=config.get("only") or None,
        **common,
        **extra,
    )


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(f"{FAIL_MARKER} usage: workflow_runner.py <config.json>", flush=True)
        return 2
    config = json.loads(Path(argv[1]).read_text())
    markers = config.get("markers") or {}
    try:
        run(config)
    except Exception as exc:  # surface to the captured stdout for the UI
        unset = unset_parameters(exc)
        if unset and _rank() == 0:
            # the step and its parameters, so the GUI can open the step's protocol on them
            print(event_line({"event": "unset_parameters", "step": unset["step_id"],
                              "names": unset["names"]}), flush=True)
        for marker in (markers.get("fail"), FAIL_MARKER):
            if marker:
                print(f"{marker} {exc}", flush=True)
        traceback.print_exc()
        _abort_mpi()
        return 1
    if _rank() == 0:
        for marker in (markers.get("done"), DONE_MARKER):
            if marker:
                print(marker, flush=True)
    return 0


def _abort_mpi() -> None:
    """Abort all MPI ranks so a failure on one rank doesn't hang the others."""
    try:
        from mpi4py import MPI

        if MPI.COMM_WORLD.Get_size() > 1:
            MPI.COMM_WORLD.Abort(1)
    except Exception:
        pass


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
