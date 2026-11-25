"""Linux jobs that install libcuflynx run on ubuntu-24.04.

libcuflynx requires libcellml 0.7.2, whose wheel links the system's ``libxml2.so.2``.
ubuntu-26.04 (what ``ubuntu-latest`` became) ships only libxml2's newer soname, so
``import libcellml`` fails there before any test runs.
"""
import pathlib

import yaml

WORKFLOW_DIR = pathlib.Path(__file__).resolve().parents[3] / ".github" / "workflows"
CA_REPO = "physiomelinks/circulatory_autogen"


def _linux_jobs_installing_libcuflynx():
    found = []
    for path in sorted(WORKFLOW_DIR.glob("*.yml")):
        doc = yaml.safe_load(path.read_text()) or {}
        for name, job in (doc.get("jobs") or {}).items():
            runs_on = job.get("runs-on")
            if not (isinstance(runs_on, str) and runs_on.startswith("ubuntu")):
                continue
            if any(CA_REPO + "@" in (step.get("run") or "") for step in job.get("steps") or []):
                found.append((path.name, name, runs_on))
    return found


def test_linux_jobs_that_install_libcuflynx_run_on_ubuntu_24_04():
    jobs = _linux_jobs_installing_libcuflynx()
    assert jobs, "found no Linux job installing libcuflynx; has the install line changed?"
    wrong = [job for job in jobs if job[2] != "ubuntu-24.04"]
    assert not wrong, f"these jobs cannot import libcellml 0.7.2's wheel: {wrong}"
