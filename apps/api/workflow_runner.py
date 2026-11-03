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
  "module_library_dirs": ["..."], "from_step": null, "only": null
}
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


def _rank() -> int:
    try:
        from mpi4py import MPI

        return MPI.COMM_WORLD.Get_rank()
    except Exception:
        return 0


def run(config: dict) -> dict:
    ensure_ca_path()
    run_workflow = ca_from("calibration_workflow", "run_calibration_workflow")
    return run_workflow(
        config["workflow"],
        output_dir=config["output_dir"],
        module_library_dirs=config.get("module_library_dirs") or [],
        from_step=config.get("from_step") or None,
        only=config.get("only") or None,
        progress=lambda event: print(event_line(event), flush=True),
    )


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(f"{FAIL_MARKER} usage: workflow_runner.py <config.json>", flush=True)
        return 2
    config = json.loads(Path(argv[1]).read_text())
    try:
        run(config)
    except Exception as exc:  # surface to the captured stdout for the UI
        print(f"{FAIL_MARKER} {exc}", flush=True)
        traceback.print_exc()
        _abort_mpi()
        return 1
    if _rank() == 0:
        print(DONE_MARKER, flush=True)
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
