"""Reduced-order models (ROMs), the Surrogate tab's second section: runs ``rom_runner.py`` as a subprocess.

A ROM is described by a *rom setup* file (libcuflynx ``rom_setup.schema.json``) and built by libcuflynx's
``cuflynx-rom setup`` command; CUFLynx only launches it and reads back what it wrote (``<root>/results.json``),
the same "results come from circulatory_autogen's own files" rule as the other analyses (#210).

Two kinds of call:

* quick ones -- ``probe`` (does this interpreter's libcuflynx have ROMs at all), ``generators``, ``check`` and
  ``describe`` -- run the runner to completion and return its result (:func:`call`);
* ``runs`` and ``fit`` are jobs in their own one-at-a-time slot (:class:`RomManager`), streamed like an emulator
  training run.

ROMs are not in the public libcuflynx release yet, so ``probe`` saying "unavailable" is the normal answer on a
released libcuflynx; the tab shows the reason and stays usable for everything else. Tests inject a fake runner via
``rom.runner_path``.
"""

from __future__ import annotations

import json
import os
import subprocess
import threading
import uuid
from pathlib import Path

from calibration import clear_run_config, finished_before_exiting, read_meta_line, write_run_config
from runtime_paths import default_python, runner_command, runner_launch_env, runner_path

RUNNER_PATH = str(runner_path("rom_runner.py"))
#: Seconds a quick call may take: check and describe read the model through libCellML, which is seconds on a large
#: flat model.
QUICK_TIMEOUT_S = 300


def _markers():
    from rom_runner import DONE_MARKER, FAIL_MARKER, RESULT_MARKER  # noqa: PLC0415

    return DONE_MARKER, FAIL_MARKER, RESULT_MARKER


class RomCallError(RuntimeError):
    """A quick call that failed: the runner's own reason."""


class RomJob:
    def __init__(self, job_id: str, action: str, root: str):
        self.id = job_id
        self.action = action
        self.root = root
        self.lines: list[str] = []
        self.state = "running"  # running | done | error | cancelled
        #: libcuflynx's results.json (fit), read back once the run finishes.
        self.results: dict | None = None
        self.error: str | None = None
        self.proc: subprocess.Popen | None = None
        self.config_path: str | None = None
        self.lock = threading.Lock()


class RomManager:
    def __init__(self):
        self.runner_path = RUNNER_PATH
        self.python = default_python()
        self._job: RomJob | None = None
        self._lock = threading.Lock()
        self._probe_cache: dict = {}

    def reset(self) -> None:
        """Terminate any running job and forget the cached probe (between tests, and when the CA dir changes)."""
        with self._lock:
            job = self._job
            self._job = None
            self._probe_cache.clear()
        if job and job.proc and job.proc.poll() is None:
            job.proc.terminate()

    @property
    def busy(self) -> bool:
        job = self._job
        return job is not None and job.state == "running"

    # ---- quick calls ----
    def call(self, config: dict) -> dict:
        """Run a quick action to completion and return its result, or raise RomCallError with the runner's reason."""
        done, fail, result = _markers()
        config_path = write_run_config(config, "rom_config.json")
        try:
            proc = subprocess.run(
                runner_command(self.python, self.runner_path, config_path),
                capture_output=True, text=True, timeout=QUICK_TIMEOUT_S, env=runner_launch_env(self.python),
            )
        except subprocess.TimeoutExpired as exc:
            raise RomCallError(f"rom {config['action']} took longer than {QUICK_TIMEOUT_S} s") from exc
        finally:
            clear_run_config(config_path)
        lines = proc.stdout.splitlines()
        failed = [ln[len(fail):].strip() for ln in lines if ln.startswith(fail)]
        if failed or proc.returncode != 0:
            tail = "\n".join((proc.stderr or "").strip().splitlines()[-3:])
            raise RomCallError(failed[0] if failed else f"rom runner exited with code {proc.returncode}: {tail}")
        out = read_meta_line(lines, result)
        return out

    def probe(self) -> dict:
        """``{"available", "reason", "interpreter"}``, cached per interpreter and CA directory."""
        key = (self.python, os.environ.get("CIRCULATORY_AUTOGEN_SRC"))
        if key not in self._probe_cache:
            try:
                got = self.call({"action": "probe"})
            except RomCallError as exc:
                got = {"available": False, "reason": f"Checking for reduced-order models failed: {exc}"}
            self._probe_cache[key] = {**got, "interpreter": self.python}
        return self._probe_cache[key]

    # ---- jobs ----
    def start(self, config: dict) -> str:
        with self._lock:
            if self.busy:
                raise RuntimeError("a reduced-order model job is already running")
            os.makedirs(config["root"], exist_ok=True)
            config_path = write_run_config(config, "rom_config.json")
            job = RomJob(uuid.uuid4().hex, config["action"], config["root"])
            job.config_path = config_path
            job.proc = subprocess.Popen(
                runner_command(self.python, self.runner_path, config_path),
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1,
                env=runner_launch_env(self.python),
            )
            self._job = job
        threading.Thread(target=self._reader, args=(job,), daemon=True).start()
        return job.id

    def _reader(self, job: RomJob) -> None:
        try:
            assert job.proc and job.proc.stdout is not None
            for line in job.proc.stdout:
                with job.lock:
                    job.lines.append(line.rstrip("\n"))
        finally:
            code = job.proc.wait() if job.proc else -1
            clear_run_config(job.config_path)
            self._finalize(job, code)

    def _finalize(self, job: RomJob, code: int) -> None:
        done, fail, result = _markers()
        with job.lock:
            if job.state == "cancelled":
                return
            finished = code == 0 or finished_before_exiting(job.lines, done, fail)
            meta = read_meta_line(job.lines, result)
            if finished:
                job.state = "done"
                if meta.get("results"):
                    job.results = read_results(meta["results"])
            else:
                job.state = "error"
                failed = [ln[len(fail):].strip() for ln in job.lines if ln.startswith(fail)]
                job.error = failed[0] if failed else f"runner exited with code {code}"

    def status(self, job_id: str, offset: int = 0) -> dict | None:
        job = self._job
        if job is None or job.id != job_id:
            return None
        with job.lock:
            lines = job.lines[offset:]
            return {"job_id": job.id, "action": job.action, "state": job.state, "lines": lines,
                    "next_offset": offset + len(lines), "root": job.root, "results": job.results,
                    "error": job.error}

    def cancel(self, job_id: str) -> bool:
        job = self._job
        if job is None or job.id != job_id:
            return False
        with job.lock:
            if job.state == "running":
                job.state = "cancelled"
                if job.proc and job.proc.poll() is None:
                    job.proc.terminate()
        return True


def read_results(path) -> dict | None:
    """libcuflynx's results.json of a ROM fit, or None when there is none (yet)."""
    p = Path(path)
    if p.is_dir():
        p = p / "results.json"
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def summary_rows(results: dict | None) -> list[dict]:
    """One row per fitted method: name, test score, calibration (selection) score, rank, fit seconds; the best
    small-step model and beat map flagged. The table the panel shows."""
    if not results:
        return []
    best = {results.get("best_small_step"), results.get("best_beat_map")}
    rows = []
    for name, m in (results.get("methods") or {}).items():
        rows.append({
            "method": name,
            "test_score": m.get("score"),
            "selection_score": m.get("selection"),
            "rank": m.get("rank"),
            "diverged": m.get("diverged"),
            "forecasts": m.get("forecasts"),
            "fit_s": m.get("fit_s"),
            "best": name in best,
        })
    return rows


# Module-level singleton shared by the FastAPI routes.
rom = RomManager()
