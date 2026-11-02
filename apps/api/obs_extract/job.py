"""Runs an extraction in the background, with a log the user reads as it goes.

The per-file, per-sweep, per-feature commentary *is* part of the product. "sweep
3 returned NaN, not emitted", "this operation does not accept spike_min_thresh",
"no stimulus was detected" -- that stream is how you tell whether the config is
right, and collapsing it into a warnings array on a single response loses both
the ordering and the progress. A few hundred recordings also take minutes, which
an HTTP request should not.

So: the same observable shape as the four existing job managers -- a busy guard,
``job.lines`` under a lock, ``status(job_id, offset)``, ``cancel`` -- which lets
the frontend store be a near-copy of ``useSensitivity.js``.

**A thread, not a subprocess.** The other four runners exist to escape this
process's dependency set: CA's simulation stack, MPI, myokit's JIT. Extraction
needs none of that -- numpy, scipy, the file readers and CA's *numpy* operation
registry, which is the light one. A subprocess would buy an interpreter picker,
a spec entry and a serialisation boundary, and nothing else.

The cost of a thread is that it cannot be killed, so cancellation is
cooperative: a flag checked between datasets and between sweeps. That is why
:func:`build_obs_data` takes a ``cancelled`` predicate rather than being wrapped
in something that could interrupt it.
"""

from __future__ import annotations

import os
import threading
import traceback
import uuid
from dataclasses import dataclass, field

from .build import build_obs_data
from .config import save as save_config
from .errors import ObsExtractError
from .report import write_report


@dataclass
class ExtractJob:
    id: str
    output_dir: str
    state: str = "running"  # running | done | error | cancelled
    lines: list[str] = field(default_factory=list)
    result: dict | None = None
    error: str = ""
    warning: str = ""
    cancel_event: threading.Event = field(default_factory=threading.Event)
    lock: threading.Lock = field(default_factory=threading.Lock)

    def log(self, message: str) -> None:
        with self.lock:
            self.lines.append(str(message))


class ObsExtractManager:
    """One extraction at a time, like every other analysis in this app."""

    def __init__(self) -> None:
        self._job: ExtractJob | None = None
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()

    @property
    def busy(self) -> bool:
        job = self._job
        return bool(job and job.state == "running")

    def start(self, config: dict, *, operation_funcs, variables=None,
              output_dir: str, cuflynx_version: str = "") -> str:
        """Begin an extraction; returns the job id."""
        with self._lock:
            if self.busy:
                raise RuntimeError("an extraction is already running")
            os.makedirs(output_dir, exist_ok=True)
            job = ExtractJob(uuid.uuid4().hex, output_dir)
            self._job = job
        thread = threading.Thread(
            target=self._run, name=f"obs-extract-{job.id[:8]}", daemon=True,
            args=(job, config, operation_funcs, variables, cuflynx_version))
        self._thread = thread
        thread.start()
        return job.id

    def _run(self, job, config, operation_funcs, variables, cuflynx_version) -> None:
        try:
            job.log(f"[info] extracting into {job.output_dir}")
            doc, outcome = build_obs_data(
                config, operation_funcs=operation_funcs, variables=variables,
                output_dir=job.output_dir, log=job.log,
                cancelled=job.cancel_event.is_set)

            config_path = os.path.join(job.output_dir, "obs_extraction_config.json")
            save_config(config, config_path)
            job.log(f"[info] config saved to {config_path}")

            docs_dir = os.path.join(
                job.output_dir,
                (config.get("outputs") or {}).get("docs_subdir")
                or f"{config.get('name') or 'extraction'}_docs")
            report = write_report(config, outcome, docs_dir, log=job.log)
            job.log(f"[info] report written to {report.tex_path}")

            job.result = {
                "obs_data": doc,
                "config_path": config_path,
                "tex_path": report.tex_path,
                "pdf_path": report.pdf_path,
                "n_experiments": outcome.n_experiments,
                "n_data_items": outcome.n_data_items,
                "datasets_used": outcome.datasets_used,
                "sweeps_used": outcome.sweeps_used,
                "skipped": outcome.skipped,
                "warnings": outcome.warnings + report.notes,
                "notes": outcome.notes,
            }
            if outcome.warnings:
                job.warning = "; ".join(outcome.warnings[:3])
            job.state = "cancelled" if job.cancel_event.is_set() else "done"
            job.log(f"[info] {outcome.n_data_items} data item(s) from "
                    f"{outcome.n_experiments} experiment(s)")
        except ObsExtractError as exc:
            # The user's config or their files: a plain message, no traceback.
            job.error = str(exc)
            job.state = "cancelled" if job.cancel_event.is_set() else "error"
            job.log(f"[error] {exc}")
        except Exception as exc:  # noqa: BLE001 - a fault; keep the traceback
            job.error = f"{type(exc).__name__}: {exc}"
            job.state = "error"
            job.log("[error] " + job.error)
            for line in traceback.format_exc().splitlines():
                job.log("  " + line)

    def status(self, job_id: str, offset: int = 0) -> dict | None:
        job = self._job
        if job is None or job.id != job_id:
            return None
        with job.lock:
            lines = job.lines[max(0, int(offset)):]
            next_offset = len(job.lines)
        return {
            "job_id": job.id, "state": job.state, "lines": lines,
            "next_offset": next_offset, "result": job.result,
            "error": job.error, "warning": job.warning,
        }

    def cancel(self, job_id: str) -> bool:
        job = self._job
        if job is None or job.id != job_id:
            return False
        job.cancel_event.set()
        job.log("[info] cancelling after the current sweep")
        return True

    def reset(self, timeout: float = 5.0) -> None:
        """Stop any running job and forget it -- for tests and for a CA change."""
        job, thread = self._job, self._thread
        if job is not None:
            job.cancel_event.set()
        if thread is not None and thread.is_alive():
            thread.join(timeout=timeout)
        self._job = None
        self._thread = None


#: The process-wide manager the routes talk to, matching the four analysis
#: subsystems' module-level singletons.
obs_extract_jobs = ObsExtractManager()
