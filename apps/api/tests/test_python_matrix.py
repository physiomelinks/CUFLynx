"""What the backend is tested on, and the ceiling that decides it (#278).

The backend jobs pinned a single interpreter each (3.10 on Linux, 3.11 on Windows), so nothing
exercised a newer one and the first evidence that a Python upgrade broke something would have
been a user's bug report -- or a runner image bump.

`requires-python` is not what bounds this. Wheel availability for the compiled dependencies is:
libcellml 0.6.3 has wheels to cp313, casadi to cp314, PyInstaller declares `<3.16`. The binding
constraint is **autoemulate, which declares `requires-python <3.13`** -- so the engine
(circulatory_autogen#459) stops at 3.12, and testing this app past the engine's own ceiling would
be testing an interpreter libcuflynx cannot fully support.

The release build is deliberately *not* in the matrix. It produces the shipped artifact, and
moving it is a separate decision with its own risk -- see the note beside its pin, which is where
the 3.12 setuptools/distutils problem is written down.
"""
import pathlib
import re

import yaml

REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]
WORKFLOW_DIR = REPO_ROOT / ".github" / "workflows"

#: libcuflynx requires >=3.10; autoemulate stops the engine at 3.12.
SUPPORTED = [(3, 10), (3, 11), (3, 12)]

MATRIX_JOBS = {"backend-unit"}

#: Every OS the unit tier runs on. This used to be two hand-maintained jobs
#: (`backend-unit` and `backend-unit-windows`) with macOS on neither, which is how
#: the macOS halves of #340 and #330 reached users -- nothing above the Linux unit
#: tier ran anywhere else. They are now one matrix, so the axes cannot drift apart,
#: and this is the guard that they stay complete.
SUPPORTED_OS = ["macos-latest", "ubuntu-latest", "windows-latest"]


def _version(text):
    m = re.fullmatch(r"(\d+)\.(\d+)", str(text).strip())
    return (int(m.group(1)), int(m.group(2))) if m else None


def _jobs(workflow):
    doc = yaml.safe_load((WORKFLOW_DIR / workflow).read_text()) or {}
    return doc.get("jobs") or {}


def test_the_backend_unit_job_runs_the_full_matrix():
    """Every supported Python, on every OS.

    All three platforms together, because the divergence that matters is usually
    platform-specific -- a path, a signal, a compiler, or an embedder.
    """
    jobs = _jobs("ci.yml")
    for name in sorted(MATRIX_JOBS):
        assert name in jobs, f"{name} is gone; this guard needs re-pointing"
        matrix = (jobs[name].get("strategy") or {}).get("matrix", {})
        versions = [_version(v) for v in matrix.get("python-version", [])]
        assert versions == SUPPORTED, (
            f"{name} tests {versions}, expected {SUPPORTED}"
        )
        assert sorted(matrix.get("os", [])) == SUPPORTED_OS, (
            f"{name} runs on {sorted(matrix.get('os', []))}, expected {SUPPORTED_OS}"
        )


def test_the_frontend_runs_on_every_os_too():
    """The frontend suite carries source contracts that read files off disk
    (externalLinks.test.js), so a path or line-ending assumption that only breaks
    on one platform is exactly what its matrix is for."""
    frontend = _jobs("ci.yml").get("frontend")
    assert frontend, "the frontend job is gone; this guard needs re-pointing"
    matrix = (frontend.get("strategy") or {}).get("matrix", {})
    assert sorted(matrix.get("os", [])) == SUPPORTED_OS


def test_the_matrix_jobs_actually_use_the_matrix():
    """A matrix that a job does not read is three identical runs wearing different names."""
    jobs = _jobs("ci.yml")
    for name in sorted(MATRIX_JOBS):
        pins = [
            (s.get("with") or {}).get("python-version")
            for s in jobs[name].get("steps") or []
            if "python-version" in (s.get("with") or {})
        ]
        assert pins, f"{name} sets up no Python at all"
        for pin in pins:
            assert "matrix.python-version" in str(pin), (
                f"{name} sets up Python {pin!r} rather than taking it from its matrix"
            )


def test_no_backend_job_outruns_the_engines_ceiling():
    """autoemulate declares <3.13. Testing this app on an interpreter libcuflynx cannot
    fully support finds problems that are not ours, and misses the one that is."""
    over = [v for v in SUPPORTED if v > (3, 12)]
    assert not over, (
        f"the matrix reaches {over}, past autoemulate's <3.13 ceiling. If the engine has moved "
        "(circulatory_autogen#459), raise this deliberately -- and check the emulator jobs there."
    )


def test_the_release_build_stays_on_one_interpreter():
    """It produces the shipped artifact. Moving it is a separate decision, taken after the
    matrix has been green on the new version for a release cycle -- not a side effect of
    widening the test matrix."""
    for name, job in _jobs("release.yml").items():
        for step in job.get("steps") or []:
            pin = (step.get("with") or {}).get("python-version")
            if pin is not None:
                assert _version(pin) is not None, (
                    f"release.yml:{name} takes its interpreter from {pin!r} rather than "
                    "pinning one literally"
                )
