"""scripts/analysis_smoke.py, the release workflow's analysis e2e, gives every run a working dir.

The app refuses an analysis run without one (422, "Set a working dir before running"), so
a run started without it fails the e2e job, and `Publish release` waits on that job.
"""

import importlib.util
import re
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "analysis_smoke.py"


def _load():
    spec = importlib.util.spec_from_file_location("analysis_smoke", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_start_run_sends_the_working_dir(monkeypatch):
    smoke = _load()
    sent = {}

    def fake_req(method, url, *, data=None, **_):
        sent.update(method=method, url=url, data=data)
        return 200, {"job_id": "j1"}

    monkeypatch.setattr(smoke, "_req", fake_req)
    job = smoke._start_run("http://h", "calibration/run", "m1", {"dt": 0.01}, "/tmp/wd")

    assert job == "j1"
    assert sent["url"] == "http://h/api/calibration/run"
    assert sent["data"] == {"model_id": "m1", "settings": {"dt": 0.01, "working_dir": "/tmp/wd"}}


def test_every_run_goes_through_start_run():
    source = SCRIPT.read_text(encoding="utf-8")
    direct = re.findall(r'_req\("POST", f"\{base\}/api/(?:sensitivity|calibration|uq)/run"'
                        r'|_req\("POST", f"\{base\}/api/emulator/train"', source)
    assert not direct, f"runs started without a working dir: {direct}"
    assert source.count("_start_run(base, ") >= 5
