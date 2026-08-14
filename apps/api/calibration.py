"""Calibration job manager: runs ``calibration_runner.py`` as a subprocess and
streams its stdout into a buffer the API can poll.

One job at a time (``start`` raises if busy). Tests inject a fake runner by
setting ``calibration.runner_path`` (mirrors the engine's factory seam).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from pathlib import Path

import ca_run_history
from runtime_paths import (
    bundled_mpiexec,
    default_python,
    runner_command,
    runner_launch_env,
    runner_path,
    subprocess_env,
)

RUNNER_PATH = str(runner_path("calibration_runner.py"))


def _warn_no_mpiexec(num_cores: int) -> None:
    """Warn (to the server log) that a requested parallel run fell back to a
    single core because ``mpiexec`` is not installed."""
    print(
        f"warning: num_cores={num_cores} requested but 'mpiexec' was not found "
        "on PATH; running on a single core instead. Install an MPI runtime "
        "(see scripts/install.py) to enable parallel runs.",
        file=sys.stderr,
        flush=True,
    )


def resolve_mpiexec(python: str | None) -> str | None:
    """Locate the ``mpiexec`` belonging to ``python``'s own environment.

    MPI only works when the launcher and the ``mpi4py`` runtime come from the
    same MPI implementation. Resolving the launcher via PATH breaks that: the
    runtime is chosen independently by ``mpi4py``'s dlopen of ``libmpi`` (which
    follows ``LD_LIBRARY_PATH``), so a system Open MPI ``mpiexec`` can end up
    launching an interpreter whose ``mpi4py`` bound MPICH. Every rank then
    aborts at ``MPI_Init`` with "unsupported PMI version PMIx".

    Prefer the launcher installed next to the selected interpreter, since that
    is the same environment that provides ``mpi4py`` (``pip install mpi4py
    mpich`` drops both into ``<sys.prefix>/bin``). Only fall back to PATH, which
    preserves the previous behaviour when the environment ships no launcher.

    ``python`` may be None: :func:`runtime_paths.default_python` returns None in
    the packaged app, meaning "no external interpreter -- run the analysis in the
    bundle itself". There is then no environment to resolve a launcher *from*, so
    fall back to PATH. Do not drop this guard: without it ``os.sep in python``
    raises TypeError and the caller's num_cores>1 request dies as an HTTP 500,
    which is exactly what the mpiexec-missing fallback exists to prevent.

    Returns the launcher path, or None when none can be found.
    """
    if not python:
        # Packaged app, no external interpreter: the ranks are the bundle itself,
        # loading the bundle's MPICH. Prefer the MPICH Hydra launcher bundled
        # beside the app so launcher and runtime are the same MPI by construction;
        # a PATH mpiexec from a different MPI (e.g. system Open MPI) would abort
        # every rank at MPI_Init with "unsupported PMI version PMIx".
        return bundled_mpiexec() or shutil.which("mpiexec")
    # `python` may be a bare command name; resolve it to a real path first so
    # that its directory is meaningful.
    exe = python if os.sep in python else (shutil.which(python) or python)
    for bindir in _interpreter_bindirs(exe):
        for name in ("mpiexec", "mpirun"):
            # shutil.which(path=...) confines the search to that one dir and
            # still handles Windows extensions + the exec bit.
            found = shutil.which(name, path=str(bindir))
            if found:
                return found
    return shutil.which("mpiexec")


def _interpreter_bindirs(exe: str) -> list[Path]:
    """Directories that may hold ``exe``'s own launcher, nearest first.

    The *literal* directory must be searched first and must not be replaced by
    the resolved one. A venv's ``bin/python`` is a symlink to the interpreter it
    was created from, so resolving it walks straight back out of the venv::

        <venv>/bin/python -> python3 -> /usr/bin/python3.10

    Searching the resolved directory therefore lands in ``/usr/bin`` and finds
    the *system* launcher while the venv's own ``mpiexec`` sits unused right
    beside the symlink -- which is exactly the launcher/runtime mismatch this
    function exists to prevent, and it made the interpreter-relative lookup a
    no-op for every standard venv on Linux and macOS.

    The resolved directory is still tried second, for a symlink that lives
    outside the environment it points into (e.g. ``~/bin/mypython``).
    """
    dirs: list[Path] = []
    try:
        literal = Path(os.path.abspath(exe)).parent
    except (OSError, ValueError):
        return dirs
    dirs.append(literal)
    try:
        resolved = Path(exe).resolve().parent
    except (OSError, ValueError):
        return dirs
    if resolved != literal:
        dirs.append(resolved)
    return dirs


def finished_before_exiting(lines: list, done_marker: str, fail_marker: str) -> bool:
    """Whether the runner completed its work before a non-zero exit.

    A runner that printed its DONE marker has already written its results, so a
    non-zero exit *after* that is a teardown failure, not a failed analysis.

    MPI is what forced this. MPICH's ``MPI_Finalize`` can abort while flushing
    its network queue -- on macOS with libfabric selecting a real NIC for what
    is a single-machine run, ``OFI poll failed (default nic=en5)`` -- long after
    every rank has finished and ``results.json`` is on disk. Gating purely on
    the exit code threw that completed calibration away and told the user
    "runner exited with code 808576911".

    Deliberately narrow: the FAIL marker still wins, and the caller must still
    find and parse the results. This forgives the *epilogue*, not a crash.
    """
    if any(fail_marker in line for line in lines):
        return False
    return any(done_marker in line for line in lines)


def teardown_warning(code: int, lines: list, tail: int = 3) -> str:
    """Message for a run that finished and then failed on the way out."""
    noise = [ln for ln in lines[-tail:] if ln.strip()]
    detail = f" Last output: {' | '.join(noise)}" if noise else ""
    return (
        f"The analysis completed and its results were saved, but the runner "
        f"exited with code {code} while shutting down.{detail}"
    )


def read_meta_line(lines: list, marker: str) -> dict:
    """The runner's one line of run metadata, or ``{}``.

    A finished run's *results* are read from circulatory_autogen's own files
    (#210). What is left over is a little metadata no file holds -- which method
    ran, the point a local sensitivity was linearised about -- and it travels on
    the stdout the manager already reads rather than in another file beside the
    outputs. Deliberately small: under ``mpiexec`` every rank shares this pipe,
    and a line over ``PIPE_BUF`` could interleave with another rank's output.

    Last one wins, so a rerun within one process cannot be read as the first.
    Never raises: a garbled line means the metadata is missing, which costs a
    display detail rather than a finished run.
    """
    for line in reversed(lines):
        if line.startswith(marker):
            try:
                return json.loads(line[len(marker):])
            except ValueError:
                return {}
    return {}


def write_run_config(config: dict, filename: str) -> str:
    """Write a runner's config payload and return the path to hand it as argv[1].

    Into a private temp directory, **not** the user's outputs directory. Nothing
    ever reads this file back: it exists only because a config dict cannot be
    passed on a command line, and because every MPI rank has to read it at
    startup (``build_command`` prepends ``mpiexec -n N``), which rules out a pipe.
    So it has to outlive the spawn, not the run — and writing it beside the
    outputs put a file there that is no part of the study and that the user has
    no use for.

    Shared by all three managers so there is one answer to where it goes.
    :func:`clear_run_config` removes it once the process has exited.
    """
    directory = tempfile.mkdtemp(prefix="cuflynx-run-")
    path = os.path.join(directory, filename)
    with open(path, "w") as fh:
        json.dump(config, fh)
    return path


def clear_run_config(config_path: str | None) -> None:
    """Remove the temp directory :func:`write_run_config` created. Never raises:
    a leftover temp file must not turn a finished run into a failed one."""
    if not config_path:
        return
    shutil.rmtree(os.path.dirname(config_path), ignore_errors=True)


def _read_interrupted_best_params(output_dir: str, params_path: str | None) -> dict | None:
    """Best-so-far params of a run that ended early, mapped to ``{qname: value}``.

    Reads CA's incrementally-saved ``best_param_vals.npy`` (a bare value array,
    ordered as the params_for_id rows) and pairs it with the entries parsed from the
    params CSV -- one per row, matching the file's one value per row. A grouped row
    (several vessels varying together, #193) contributes its one value under every
    member qname, the same expansion the finished run's results.json gets from CA's
    list-of-lists ``param_names``. Returns None if the file/params are missing or
    the counts don't line up, so a misaligned guess is never returned.
    Best-effort — never raises.
    """
    if not params_path:
        return None
    run_dir = ca_run_history.find_run_dir(output_dir)
    npy = os.path.join(run_dir, ca_run_history.BEST_PARAM_VALS_FILE) if run_dir else None
    if npy and not os.path.isfile(npy):
        npy = None
    if not npy:
        return None
    try:
        import numpy as np  # noqa: E402 (heavy; only on this recovery path)
        from params_for_id import parse_params_for_id  # noqa: E402

        vals = np.load(npy).reshape(-1)
        entries = parse_params_for_id(Path(params_path).read_bytes())
        if not entries or len(entries) != len(vals):
            return None
        return {
            qname: float(v)
            for entry, v in zip(entries, vals)
            for qname in entry.qnames
        }
    except Exception:  # noqa: BLE001 - a best-effort recovery must never fail a run
        return None


REQUIRED_MODULES = ["myokit", "libcellml"]
OPTIONAL_MODULES = ["nevergrad", "mpi4py"]


def _candidate_python_paths() -> list[str]:
    """Best-effort list of Python interpreters on this machine.

    ``default_python()`` (rather than ``sys.executable``) seeds the list so the
    packaged desktop build never offers its own frozen bundle as an interpreter.
    """
    import glob

    cands = [default_python()]
    for name in (
        "python3",
        "python",
        "python3.13",
        "python3.12",
        "python3.11",
        "python3.10",
        "python3.9",
    ):
        found = shutil.which(name)
        if found:
            cands.append(found)

    home = os.path.expanduser("~")
    for base in ("miniconda3", "anaconda3", "miniforge3", "mambaforge"):
        cands.append(os.path.join(home, base, "bin", "python"))
        cands.extend(glob.glob(os.path.join(home, base, "envs", "*", "bin", "python")))
    cands.extend(glob.glob(os.path.join(home, ".conda", "envs", "*", "bin", "python")))
    cands.extend(glob.glob("/opt/conda/envs/*/bin/python"))
    if os.environ.get("CONDA_PREFIX"):
        cands.append(os.path.join(os.environ["CONDA_PREFIX"], "bin", "python"))

    out: list[str] = []
    seen: set[str] = set()
    for c in cands:
        if not c:
            continue
        real = os.path.realpath(c)
        if os.path.isfile(real) and os.access(real, os.X_OK) and real not in seen:
            seen.add(real)
            out.append(c)
    return out


# MPI families, by the words their version banners use. Launcher and runtime
# have to come from the same one; which one it is does not otherwise matter.
_MPI_FAMILIES = (
    ("openmpi", ("open mpi", "openrte", "open-rte", "ompi")),
    ("mpich", ("mpich", "hydra")),
    ("intelmpi", ("intel(r) mpi", "intel mpi")),
    ("msmpi", ("microsoft mpi", "msmpi")),
)


def _mpi_family(banner: str) -> str:
    """The MPI implementation a version banner describes, or "" if unrecognised."""
    low = (banner or "").lower()
    for family, needles in _MPI_FAMILIES:
        if any(n in low for n in needles):
            return family
    return ""


def _launcher_family(launcher: str) -> str:
    # subprocess_env(): the launcher must be asked what it is in the environment
    # it will actually run in, not inside the bundle's loader paths. See
    # _runtime_family for what inheriting them does.
    try:
        out = subprocess.run(
            [launcher, "--version"], capture_output=True, text=True, timeout=10,
            env=subprocess_env(),
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return _mpi_family(out.stdout + out.stderr)


def _runtime_family(path: str) -> str:
    """The MPI ``mpi4py`` is bound to in ``path``'s environment.

    Asked with :func:`subprocess_env`, which is load-bearing in the packaged app.
    PyInstaller points ``LD_LIBRARY_PATH`` at the unpacked bundle, and a child
    process inherits it -- so an external interpreter's ``mpi4py`` dlopens the
    *bundle's* ``libmpi`` (MPICH) instead of the one its own environment provides.
    The probe then reports mpich for a venv built against the system Open MPI,
    disagrees with the launcher, and concludes multi-core will not work -- for an
    environment that runs it perfectly, because the run itself is spawned through
    ``runner_launch_env`` and never sees those variables.
    """
    try:
        out = subprocess.run(
            [path, "-c", "from mpi4py import MPI;print(MPI.Get_library_version())"],
            capture_output=True,
            text=True,
            timeout=20,
            env=subprocess_env(),
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return _mpi_family(out.stdout) if out.returncode == 0 else ""


def _interpreter_mpi(path: str, has_mpi4py: bool = True) -> tuple[bool, str | None]:
    """Whether picking ``path`` actually enables multi-core runs, plus the
    launcher :func:`resolve_mpiexec` would use for it.

    The question the tick answers is "will multi-core work if I choose this?",
    and the only thing that decides it is whether the launcher and ``mpi4py``
    come from the *same MPI*: mismatch them and every rank aborts at MPI_Init
    with "unsupported PMI version PMIx".

    This used to be approximated by asking whether the launcher sat inside the
    interpreter's own bindirs. That proxy is right for a self-contained
    environment (``pip install mpi4py mpich`` puts both in ``<sys.prefix>/bin``)
    and wrong for the commonest Linux arrangement there is: a venv whose mpi4py
    was built against the system MPI, with no launcher of its own. Such a venv
    runs multi-core perfectly and was reported as having no MPI.

    So ask the two of them directly, and compare. Only when neither can say --
    an unrecognised banner, a launcher that will not report -- fall back to the
    old location test, which is a reasonable guess and was the whole answer
    before.
    """
    if not has_mpi4py:
        # No mpi4py, no multi-core, whatever launchers are lying around.
        return False, resolve_mpiexec(path)
    launcher = resolve_mpiexec(path)
    if not launcher:
        return False, None

    runtime = _runtime_family(path)
    launcher_family = _launcher_family(launcher)
    if runtime and launcher_family:
        return runtime == launcher_family, launcher

    exe = path if os.sep in path else (shutil.which(path) or path)
    own = {os.path.normcase(str(d)) for d in _interpreter_bindirs(exe)}
    matched = os.path.normcase(os.path.dirname(os.path.abspath(launcher))) in own
    return matched, launcher


def _probe_python(path: str) -> dict | None:
    """Return {path, version, ready, missing, mpi, mpiexec} for an interpreter,
    or None.

    ``mpi`` is True when the interpreter's own environment provides a matched
    MPI launcher (see :func:`_interpreter_mpi`), i.e. picking it enables
    multi-core runs; ``mpiexec`` is the launcher path that would be used.
    """
    try:
        # Every probe of an *external* interpreter runs through subprocess_env, for
        # the same reason the runners do: inside the bundle's loader paths it would
        # import the bundle's native libraries rather than its own.
        ver = subprocess.run(
            [path, "-c", "import sys;print('.'.join(map(str, sys.version_info[:3])));print(sys.prefix)"],
            capture_output=True,
            text=True,
            timeout=10,
            env=subprocess_env(),
        )
        if ver.returncode != 0:
            return None
        # version on the first line, sys.prefix on the second (see the -c above).
        lines = ver.stdout.strip().splitlines()
        version = lines[0].strip() if lines else ""
        prefix = lines[1].strip() if len(lines) > 1 else ""


        mods = REQUIRED_MODULES + OPTIONAL_MODULES
        check = subprocess.run(
            [
                path,
                "-c",
                "import importlib.util as u;"
                f"print(','.join(m for m in {mods!r} if u.find_spec(m) is None))",
            ],
            capture_output=True,
            text=True,
            timeout=20,
            env=subprocess_env(),
        )
        missing = (
            [m for m in check.stdout.strip().split(",") if m]
            if check.returncode == 0
            else mods
        )
        ready = all(m not in missing for m in REQUIRED_MODULES)
        mpi, mpiexec = _interpreter_mpi(path, has_mpi4py="mpi4py" not in missing)
        return {
            "path": path,
            "version": version,
            # The environment this interpreter belongs to. Two interpreters are
            # the same *environment* only when their sys.prefix matches -- a
            # venv's bin/python is a symlink to the interpreter it was built
            # from, so comparing resolved binaries collapses every venv onto its
            # base and hides it from the picker entirely.
            "prefix": prefix,
            "ready": ready,
            "missing": missing,
            "mpi": mpi,
            "mpiexec": mpiexec,
        }
    except Exception:  # noqa: BLE001 - a bad interpreter just gets skipped
        return None


_python_cache: list[dict] | None = None


def reset_python_cache() -> None:
    """Forget the probed interpreters, so the next list re-probes.

    Called when the configured interpreter changes: the list now includes that
    interpreter, and its readiness/MPI status is what the picker shows.
    """
    global _python_cache
    _python_cache = None


def list_python_interpreters(refresh: bool = False) -> list[dict]:
    """Discover + probe available interpreters (cached for the process)."""
    global _python_cache
    if _python_cache is not None and not refresh:
        return _python_cache
    result = []
    seen: set[str] = set()
    # The interpreter the user actually chose comes first and is always probed,
    # even when discovery would never have found it: a browsed venv otherwise
    # appeared as a bare "Custom" entry with no version, no readiness and no MPI
    # status, because nothing had looked at it.
    configured = (calibration.python or "").strip()
    candidates = ([configured] if configured else []) + _candidate_python_paths()
    for path in candidates:
        info = _probe_python(path)
        if not info:
            continue
        # De-duplicate by environment rather than by resolved binary: a venv
        # shares its binary with the interpreter it was created from but has its
        # own site-packages, so realpath de-duplication dropped it silently.
        key = info.get("prefix") or os.path.realpath(path)
        if key in seen:
            continue
        seen.add(key)
        result.append(info)
    _python_cache = result
    return result


class CalibrationJob:
    def __init__(self, job_id: str, output_dir: str, model_id: str | None = None,
                 params_path: str | None = None):
        self.id = job_id
        self.output_dir = output_dir
        self.model_id = model_id
        # The params_for_id CSV, so a cancelled run's best-so-far (best_param_vals.npy,
        # a bare value array) can be mapped back to {qname: value} — see #83.
        self.params_path = params_path
        self.lines: list[str] = []
        self.state = "running"  # running | done | error | cancelled
        self.best_params: dict | None = None
        # Modifier metadata from the run ({name, anchor, targets, operation,
        # baselines, theta} per modifier) so the frontend can apply theta to the
        # anchor slider and expand physical values for the best-fit run (#208).
        self.modifiers: list | None = None
        self.cost = None
        # Calibrated CellML saved on finish (best-fit values baked in, issue #114).
        self.calibrated_model_path: str | None = None
        # Post-calibration per-observable fit errors (Analysis-tab bar charts).
        self.percent_error: list | None = None
        self.std_error: list | None = None
        self.error_labels: list = []
        self.error: str | None = None
        # Set when the run finished but its process failed on the way out (an
        # MPI finalize abort, say): the results stand, and the user is told.
        self.warning: str | None = None
        self.proc: subprocess.Popen | None = None
        # The temp file the runner was handed as argv[1], removed when it exits.
        self.config_path: str | None = None
        # Names the calibrated CellML the runner writes, so its path is derived
        # here rather than reported back.
        self.file_prefix: str | None = None
        # When the run started; results older than this belong to a previous one.
        self.started_at: float | None = None
        self.lock = threading.Lock()


class CalibrationManager:
    def __init__(self):
        self.runner_path = RUNNER_PATH
        self.python = default_python()
        self._job: CalibrationJob | None = None
        self._lock = threading.Lock()

    def reset(self) -> None:
        """Terminate any running job and clear state (used between tests)."""
        with self._lock:
            job = self._job
            self._job = None
        if job and job.proc and job.proc.poll() is None:
            job.proc.terminate()

    @property
    def busy(self) -> bool:
        job = self._job
        return job is not None and job.state == "running"

    def last_completed_best_params(self, model_id: str) -> dict | None:
        """Best-fit params of the most recent finished calibration for ``model_id``,
        for reuse as a start point / nominal (sensitivity 'best_fit', UQ, and #83's
        'start from previous best fit'). ``None`` if none is available.

        A **cancelled** run counts too, as long as its best-so-far was recovered
        (:func:`_read_interrupted_best_params`) — that's what lets a calibration
        stopped partway be continued from. A still-running job never qualifies.
        """
        job = self._job
        if job is None or job.model_id != model_id or job.state not in ("done", "cancelled"):
            return None
        return job.best_params or None

    def build_command(self, config: dict, config_path: str) -> list[str]:
        """Single-process by default; ``mpiexec -n N`` when num_cores > 1.

        The genetic algorithm parallelises population evaluation across MPI
        ranks, exactly like circulatory_autogen's run_param_id.sh.

        The launcher is resolved from the selected interpreter's environment
        (see :func:`resolve_mpiexec`) so it matches that interpreter's mpi4py.

        If ``num_cores > 1`` but no ``mpiexec`` can be found (common on Windows,
        where MPI is rarely installed), fall back to a single-core run rather
        than launching a non-existent ``mpiexec`` — which would raise
        ``FileNotFoundError`` and surface to the client as an HTTP 500.
        """
        # An explicit interpreter runs the runner script; with none, the frozen app
        # runs it in-process (runner mode) and from source uses the serving Python.
        python = config.get("python") or self.python
        base = runner_command(python, self.runner_path, config_path)
        num_cores = int(config.get("num_cores", 1) or 1)
        if num_cores > 1:
            mpiexec = resolve_mpiexec(python)
            if mpiexec is None:
                _warn_no_mpiexec(num_cores)
                return base
            return [mpiexec, "-n", str(num_cores), *base]
        return base

    def start(self, config: dict) -> str:
        with self._lock:
            if self.busy:
                raise RuntimeError("a calibration job is already running")
            output_dir = config["output_dir"]
            os.makedirs(output_dir, exist_ok=True)
            # A reused output_dir may hold a previous run's progress history; clear
            # it so this run's live plots start fresh instead of reading stale data.
            ca_run_history.clear_run_history(output_dir)
            config_path = write_run_config(config, "calib_config.json")

            job = CalibrationJob(
                uuid.uuid4().hex, output_dir, config.get("model_id"), config.get("params_path"),
            )
            job.config_path = config_path
            # Both halves of the calibrated model's filename are known here, so
            # the path never has to be round-tripped back from the runner.
            job.file_prefix = config.get("file_prefix")
            # When this run started, so a previous run's results left in the same
            # outputs directory cannot be reported as this one's.
            job.started_at = time.time()
            env = runner_launch_env(config.get("python") or self.python)
            job.proc = subprocess.Popen(
                self.build_command(config, config_path),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                env=env,
            )
            self._job = job

        threading.Thread(target=self._reader, args=(job,), daemon=True).start()
        return job.id

    def _reader(self, job: CalibrationJob) -> None:
        try:
            assert job.proc and job.proc.stdout is not None
            for line in job.proc.stdout:
                with job.lock:
                    job.lines.append(line.rstrip("\n"))
        finally:
            code = job.proc.wait() if job.proc else -1
            self._finalize(job, code)
            clear_run_config(job.config_path)

    def _finalize(self, job: CalibrationJob, code: int) -> None:
        with job.lock:
            if job.state == "cancelled":
                # Stopped early — recover the best-so-far so it can be continued
                # from (#83). Never overrides the cancelled state.
                job.best_params = _read_interrupted_best_params(job.output_dir, job.params_path)
                return
            # A non-zero exit *after* the DONE marker is a teardown failure, not
            # a failed run -- see finished_before_exiting.
            from calibration_runner import DONE_MARKER, FAIL_MARKER  # noqa: PLC0415

            finished = code == 0 or finished_before_exiting(
                job.lines, DONE_MARKER, FAIL_MARKER
            )
            # Read from circulatory_autogen's own outputs rather than from a
            # summary the runner serialised for us (#210). Everything here is a
            # file CA wrote: best_param_vals.npy / best_cost.npy,
            # param_modifiers.json with its resolved baselines, and the
            # percent/std/name error triple. The gate is "did CA write the run"
            # rather than "did we manage to copy it", which is strictly more
            # robust to an MPI teardown abort.
            if finished and ca_run_history.has_results(job.output_dir, job.started_at):
                try:
                    best = ca_run_history.best_param_values(job.output_dir)
                    job.best_params = best["params"]
                    job.cost = best["cost"]
                    job.modifiers = ca_run_history.modifiers(job.output_dir)
                    errors = ca_run_history.error_vectors(job.output_dir)
                    job.percent_error = errors["percent_error"]
                    job.std_error = errors["std_error"]
                    job.error_labels = errors["error_labels"]
                    job.calibrated_model_path = ca_run_history.calibrated_model_path(
                        job.output_dir, job.file_prefix
                    )
                    job.state = "done"
                    if code != 0:
                        job.warning = teardown_warning(code, job.lines)
                except Exception as exc:  # noqa: BLE001
                    job.state = "error"
                    job.error = f"failed to read results: {exc}"
            else:
                job.state = "error"
                job.error = job.error or f"runner exited with code {code}"

    def status(self, job_id: str, offset: int = 0) -> dict | None:
        job = self._job
        if job is None or job.id != job_id:
            return None
        with job.lock:
            lines = job.lines[offset:]
            return {
                "job_id": job.id,
                "state": job.state,
                "lines": lines,
                "next_offset": offset + len(lines),
                "best_params": job.best_params,
                "modifiers": job.modifiers,
                "cost": job.cost,
                "calibrated_model_path": job.calibrated_model_path,
                "percent_error": job.percent_error,
                "std_error": job.std_error,
                "error_labels": job.error_labels,
                "error": job.error,
                "warning": job.warning,
            }

    def progress(self, job_id: str) -> dict | None:
        """Per-generation cost/param history for the live progress charts.

        Reads the history CSVs the runner subprocess writes (no lock needed —
        a separate process owns the files). ``state`` lets the client stop
        polling once the run is no longer ``running``.
        """
        job = self._job
        if job is None or job.id != job_id:
            return None
        hist = ca_run_history.progress_history(job.output_dir)
        return {"job_id": job.id, "state": job.state, **hist}

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


# Module-level singleton shared by the FastAPI routes.
calibration = CalibrationManager()
