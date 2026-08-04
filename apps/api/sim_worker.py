"""Supervise the live-simulation worker process (#167).

The worker runs the model in the interpreter the user picked in Settings, so
that choice finally means the same thing for sliders as it does for calibration.
This module is the parent half: start it, talk to it, notice when it dies, and
replace it when the settings it was started with no longer hold.

Deliberately thin. It knows how to keep a process alive and how to move JSON
across a pipe; what the messages *mean* is the worker's business and the
engine's. The narrowness is the point -- the failure mode for this design is a
second API growing beside the first.
"""

from __future__ import annotations

import contextlib
import json
import queue
import subprocess
import threading
from collections import deque

from runtime_paths import runner_command, runner_path, subprocess_env

RUNNER_NAME = "sim_worker_runner.py"

# Kept from the worker's stderr so a crash can be explained. Bounded because CA
# is chatty and this is a diagnostic, not a log.
STDERR_LINES = 60

# A model can legitimately take a long time to compile on first use -- Myokit
# builds a C extension, AADC records a tape -- so this is a "something is wrong"
# bound rather than a performance one. It exists because a worker CAN hang
# without dying: an AADC licence check that cannot reach the network sits at 0%
# CPU forever, and a blocking read would hold the engine lock with it, taking
# every later simulation down too.
DEFAULT_TIMEOUT = 900.0

# Put on the reply queue by the reader thread when the worker's stdout closes.
_EOF = object()


class WorkerError(RuntimeError):
    """The worker could not be started, or died with a request in flight."""


class SimWorker:
    """One worker process, restarted whenever the settings behind it change.

    Restarted rather than reconfigured: CA caches its modules on first import,
    so a mid-life change of CA directory could not fully take effect -- which is
    the very bug this whole design exists to remove. A fresh process has no
    stale imports to argue with.
    """

    def __init__(self, python: str, settings: dict, timeout: float = DEFAULT_TIMEOUT):
        self.python = python
        self.settings = dict(settings)
        self.timeout = timeout
        self._proc: subprocess.Popen | None = None
        self._stderr: deque = deque(maxlen=STDERR_LINES)
        self._stderr_thread: threading.Thread | None = None
        self._replies: queue.Queue = queue.Queue()
        self._stdout_thread: threading.Thread | None = None
        self._next_id = 0
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    @property
    def alive(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def matches(self, python: str, settings: dict) -> bool:
        """Whether a worker started like this can serve these settings."""
        return self.python == python and self.settings == settings

    def start(self) -> None:
        script = str(runner_path(RUNNER_NAME))
        # runner_command covers the three interpreter cases (external / frozen
        # self-reinvoke / this interpreter) and subprocess_env stops an external
        # Python inheriting the bundle's loader paths and importing the bundle's
        # own native libraries. Both already exist for the analysis runners; a
        # worker that rebuilt either of them would rediscover their bugs.
        cmd = runner_command(self.python or None, script, "")
        cmd = [c for c in cmd if c != ""]
        try:
            self._proc = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
                env=subprocess_env(),
            )
        except OSError as exc:
            raise WorkerError(
                f"could not start the simulation worker with {self.python or 'this interpreter'}: {exc}"
            ) from exc

        self._stderr.clear()
        self._replies = queue.Queue()
        self._stderr_thread = threading.Thread(target=self._drain_stderr, daemon=True)
        self._stderr_thread.start()
        self._stdout_thread = threading.Thread(target=self._read_replies, daemon=True)
        self._stdout_thread.start()

        reply = self.call("configure", **self.settings)
        if not reply.get("ok"):
            raise WorkerError(f"the simulation worker refused its settings: {reply.get('reason')}")

    def _read_replies(self) -> None:
        """Move replies off the pipe on a thread, so a read can have a deadline.

        ``stdout.readline()`` cannot be given a timeout portably, and select()
        does not work on pipes on Windows -- a queue does, everywhere.
        """
        proc = self._proc
        if proc is None or proc.stdout is None:
            return
        for line in proc.stdout:
            if not line.strip():
                continue
            try:
                json.loads(line)
            except ValueError:
                # Not a reply. The child points fd 1 at stderr so nothing else
                # should reach this pipe, but a library that reopens or inherits
                # the descriptor another way still could -- and losing the run
                # over a stray banner (AADC printed one) is a worse outcome than
                # logging it and reading on. Mirrored like stderr so it is not
                # simply swallowed.
                self._stderr.append(line.rstrip("\n"))
                print(f"[sim-worker] {line}", end="")
                continue
            self._replies.put(line)
        self._replies.put(_EOF)

    def _drain_stderr(self) -> None:
        """Mirror the worker's stderr to ours, keeping the tail for diagnosis.

        Without this the pipe fills and the worker blocks mid-run -- CA prints
        enough during a compile to reach the buffer on its own.
        """
        proc = self._proc
        if proc is None or proc.stderr is None:
            return
        for line in proc.stderr:
            self._stderr.append(line.rstrip("\n"))
            print(f"[sim-worker] {line}", end="")

    def stop(self) -> None:
        proc, self._proc = self._proc, None
        if proc is None:
            return
        try:
            if proc.poll() is None and proc.stdin is not None:
                proc.stdin.write(json.dumps({"op": "shutdown"}) + "\n")
                proc.stdin.flush()
                proc.wait(timeout=5)
        except (OSError, ValueError, subprocess.TimeoutExpired):
            pass
        finally:
            if proc.poll() is None:
                proc.kill()
                with contextlib.suppress(subprocess.TimeoutExpired):
                    proc.wait(timeout=5)

    # ------------------------------------------------------------------
    # The wire
    # ------------------------------------------------------------------
    def call(self, op: str, **payload) -> dict:
        """Send one request and return the worker's reply.

        Serialised: one worker, one model cache, one request at a time. That
        matches the in-process engine, which held a lock around exactly the same
        work for exactly the same reason.
        """
        with self._lock:
            proc = self._proc
            if proc is None or proc.poll() is not None:
                raise WorkerError(self._died_message())

            self._next_id += 1
            request = {"id": self._next_id, "op": op, **payload}
            try:
                proc.stdin.write(json.dumps(request) + "\n")
                proc.stdin.flush()
            except (BrokenPipeError, OSError, ValueError) as exc:
                raise WorkerError(self._died_message()) from exc

            try:
                line = self._replies.get(timeout=self.timeout)
            except queue.Empty:
                # Hung rather than dead. Kill it: its state is unknown, and the
                # alternative is holding this lock until the process is reaped by
                # something else.
                tail = "\n".join(self._stderr)
                self.stop()
                raise WorkerError(
                    f"the simulation worker stopped responding after {self.timeout:g}s "
                    f"and was stopped. It runs in {self.python or 'this interpreter'}; "
                    f"a first compile can be slow, but this is long enough that "
                    f"something is stuck." + (f"\n{tail}" if tail else "")
                ) from None
            if line is _EOF:
                raise WorkerError(self._died_message())
            try:
                return json.loads(line)
            except ValueError as exc:
                raise WorkerError(
                    f"the simulation worker sent something that is not a reply: {line[:200]!r}"
                ) from exc

    def _died_message(self) -> str:
        proc = self._proc
        code = proc.poll() if proc is not None else None
        tail = "\n".join(self._stderr)
        head = (
            f"the simulation worker exited (code {code})"
            if code is not None
            else "the simulation worker is not running"
        )
        # The tail is the whole point: a worker that dies on `import myokit`
        # should say so rather than presenting as an empty pipe.
        return f"{head}.\n{tail}" if tail else f"{head}."


def worker_settings(*, ca_src: str, dt: float, model_type: str, solver: str, solver_info: dict) -> dict:
    """The settings a worker is started with, and compared against to reuse it.

    Every field here is one the worker cannot change after start-up, so a change
    to any of them means a new process.
    """
    return {
        "ca_src": ca_src or "",
        "dt": float(dt),
        "model_type": model_type,
        "solver": solver,
        "solver_info": dict(solver_info or {}),
    }
