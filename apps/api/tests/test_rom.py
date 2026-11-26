"""Reduced-order models (ROMs) in the Surrogate tab: the runner, the manager and the routes.

ROMs live in libcuflynx's ``rom`` package, which is not in the public release yet. So the runner is tested against a
stand-in ``libcuflynx.rom.setup_cli`` (the contract: ``main(argv)`` of ``cuflynx-rom setup``, printing JSON with
``--json``), and the manager and routes against a fake runner script. The one thing a public install must get right
is the *unavailable* answer, which is tested for real.
"""

import json
import sys
import time
import types
from pathlib import Path

import pytest

import rom as rom_mod
import rom_runner


# ---------------------------------------------------------------------------
# The runner, on a stand-in libcuflynx.rom.setup_cli
# ---------------------------------------------------------------------------
class FakeCli:
    """``cuflynx-rom setup``'s main(argv), recording each call."""

    def __init__(self, root=None):
        self.calls = []

    def main(self, argv):
        self.calls.append(list(argv))
        action = argv[0]
        if action == "generators":
            print(json.dumps({"short_courses": {"settings": ["run_s", "columns"], "description": "d",
                                                "user_defined": False}}))
        elif action == "check":
            print(json.dumps({"ok": False, "problems": [{"where": "W[0]", "message": "not a rom variable"}]}))
            return 1
        elif action == "describe":
            print(json.dumps({"ok": True, "name": "toy", "fitted_states": ["a/q"]}))
        elif action in ("runs", "fit"):
            root = Path(argv[argv.index("--root") + 1])
            root.mkdir(parents=True, exist_ok=True)
            print("fitting...")
            if action == "fit":
                (root / "results.json").write_text(json.dumps({"methods": {"beat map": {"score": 0.5}}}))
            print(json.dumps({"result": str(root)}))
        return 0


@pytest.fixture
def fake_cli(monkeypatch):
    cli = FakeCli()
    monkeypatch.setattr(rom_runner, "setup_cli", lambda: cli)
    return cli


def test_probe_says_available_with_a_setup_cli(fake_cli):
    assert rom_runner.run({"action": "probe"}) == {"available": True, "reason": None}


def test_probe_without_rom_names_the_fix(monkeypatch):
    monkeypatch.setattr(rom_runner, "setup_cli", lambda: None)
    out = rom_runner.run({"action": "probe"})
    assert out["available"] is False and "not in the public release" in out["reason"]
    with pytest.raises(RuntimeError, match="no reduced-order models"):
        rom_runner.run({"action": "check", "setup": "/x.json"})


def test_setup_cli_is_none_only_when_the_rom_module_is_missing(monkeypatch):
    """A libcuflynx without rom is 'unavailable'; a rom that cannot import its own dependency is that error."""
    import importlib

    monkeypatch.setattr(rom_runner, "ensure_ca_path", lambda: None)

    def missing(name, *a, **k):
        raise ModuleNotFoundError(f"No module named '{name}'", name="libcuflynx.rom")
    monkeypatch.setattr(importlib, "import_module", missing)
    assert rom_runner.setup_cli() is None

    def no_casadi(name, *a, **k):
        raise ModuleNotFoundError("No module named 'casadi'", name="casadi")
    monkeypatch.setattr(importlib, "import_module", no_casadi)
    with pytest.raises(ModuleNotFoundError, match="casadi"):
        rom_runner.setup_cli()


def test_quick_actions_return_the_cli_json(fake_cli):
    assert "short_courses" in rom_runner.run({"action": "generators"})
    assert rom_runner.run({"action": "check", "setup": "/s.json"})["problems"][0]["where"] == "W[0]"
    assert rom_runner.run({"action": "describe", "setup": "/s.json"})["fitted_states"] == ["a/q"]
    rom_runner.run({"action": "generators", "external": "/f.py"})
    assert fake_cli.calls[-1] == ["generators", "--json", "--external", "/f.py"]


def test_fit_passes_methods_and_seed_and_names_the_results(fake_cli, tmp_path):
    out = rom_runner.run({"action": "fit", "setup": "/s.json", "root": str(tmp_path / "r"),
                          "methods": ["beat map", "discrete T/50"], "seed": 3})
    assert fake_cli.calls[-1] == ["fit", "/s.json", "--root", str(tmp_path / "r"), "--seed", "3",
                                  "--methods", "beat map,discrete T/50"]
    assert out["results"] == str(tmp_path / "r" / "results.json")


def test_main_prints_the_result_then_done(fake_cli, tmp_path, capsys):
    cfg = tmp_path / "c.json"
    cfg.write_text(json.dumps({"action": "probe"}))
    assert rom_runner.main(["rom_runner.py", str(cfg)]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines[-2].startswith(rom_runner.RESULT_MARKER) and lines[-1] == rom_runner.DONE_MARKER


# ---------------------------------------------------------------------------
# The manager and routes, on a fake runner script
# ---------------------------------------------------------------------------
FAKE_RUNNER = '''
import json, sys
from pathlib import Path
cfg = json.loads(Path(sys.argv[1]).read_text())
a = cfg["action"]
R, D, F = "__ROM_RESULT__ ", "__ROM_DONE__", "__ROM_FAILED__"
mode = Path(__file__).with_name("mode.txt").read_text().strip()
if a == "probe":
    print(R + json.dumps({"available": mode != "unavailable", "reason": None if mode != "unavailable" else "no rom here"}))
elif a == "check":
    print(R + json.dumps({"ok": mode != "problems", "problems": [] if mode != "problems" else
                          [{"where": "beat.clock.period", "message": "not a variable"}]}))
elif a == "describe":
    print(R + json.dumps({"ok": True, "name": "toy", "fitted_states": ["a/q", "b/q"]}))
elif a == "generators":
    print(R + json.dumps({"short_courses": {"settings": ["columns"], "description": "d", "user_defined": False}}))
elif a in ("runs", "fit"):
    if mode == "fail":
        print(F + " CVODES failed at t = 3"); sys.exit(1)
    root = Path(cfg["root"]); root.mkdir(parents=True, exist_ok=True)
    print("rom setup toy: true runs")
    res = None
    if a == "fit":
        res = str(root / "results.json")
        Path(res).write_text(json.dumps({"methods": {
            "beat map": {"score": 0.4, "selection": 0.3, "rank": 4, "diverged": 0, "forecasts": 2, "fit_s": 1.0},
            "discrete T/50": {"score": 1.1, "selection": 0.1, "rank": None, "diverged": 1, "forecasts": 2,
                              "fit_s": 2.0}},
            "best_beat_map": "beat map", "best_small_step": "discrete T/50"}))
    print(R + json.dumps({"root": str(root), "results": res}))
print(D)
'''


@pytest.fixture
def fake_runner(tmp_path):
    script = tmp_path / "fake_rom_runner.py"
    script.write_text(FAKE_RUNNER)
    mode = tmp_path / "mode.txt"
    mode.write_text("ok")
    rom_mod.rom.runner_path = str(script)
    rom_mod.rom.python = sys.executable
    rom_mod.rom.reset()
    return mode


@pytest.fixture
def setup_file(tmp_path):
    p = tmp_path / "studies" / "toy.rom_setup.json"
    p.parent.mkdir()
    p.write_text("{}")
    return p


def wait(client, job_id):
    for _ in range(200):
        s = client.get(f"/api/rom/{job_id}/status").json()
        if s["state"] != "running":
            return s
        time.sleep(0.05)
    raise AssertionError("the job did not finish")


def test_availability_is_cached_and_refreshed(client, fake_runner):
    assert client.get("/api/rom/availability").json()["available"] is True
    fake_runner.write_text("unavailable")
    assert client.get("/api/rom/availability").json()["available"] is True  # cached
    got = client.get("/api/rom/availability?refresh=true").json()
    assert got["available"] is False and got["reason"] == "no rom here"


def test_an_unavailable_rom_refuses_with_the_reason(client, fake_runner, setup_file):
    fake_runner.write_text("unavailable")
    r = client.post("/api/rom/check", json={"setup": str(setup_file)})
    assert r.status_code == 409 and r.json()["detail"] == "no rom here"
    assert client.post("/api/rom/run", json={"setup": str(setup_file)}).status_code == 409


def test_check_describes_a_good_setup_and_names_its_folder(client, fake_runner, setup_file):
    got = client.post("/api/rom/check", json={"setup": str(setup_file)}).json()
    assert got["ok"] and got["description"]["fitted_states"] == ["a/q", "b/q"]
    assert got["default_root"] == str(setup_file.parent / "rom_toy") and got["has_results"] is False


def test_check_lists_problems_without_describing(client, fake_runner, setup_file):
    fake_runner.write_text("problems")
    got = client.post("/api/rom/check", json={"setup": str(setup_file)}).json()
    assert not got["ok"] and got["description"] is None
    assert got["problems"] == [{"where": "beat.clock.period", "message": "not a variable"}]


@pytest.mark.parametrize("setup, words", [("relative.json", "absolute"), ("/no/such/file.json", "no rom setup")])
def test_check_refuses_a_bad_path(client, fake_runner, setup, words):
    r = client.post("/api/rom/check", json={"setup": setup})
    assert r.status_code == 422 and words in r.json()["detail"]


def test_fit_job_reads_back_results_and_rows(client, fake_runner, setup_file):
    started = client.post("/api/rom/run", json={"setup": str(setup_file), "action": "fit"}).json()
    assert started["root"] == str(setup_file.parent / "rom_toy")
    s = wait(client, started["job_id"])
    assert s["state"] == "done" and "rom setup toy: true runs" in s["lines"]
    rows = {r["method"]: r for r in s["rows"]}
    assert rows["beat map"] == {"method": "beat map", "test_score": 0.4, "selection_score": 0.3, "rank": 4,
                                "diverged": 0, "forecasts": 2, "fit_s": 1.0, "best": True}
    assert rows["discrete T/50"]["best"] is True
    again = client.get("/api/rom/results", params={"root": started["root"]}).json()
    assert len(again["rows"]) == 2
    assert client.post("/api/rom/check", json={"setup": str(setup_file)}).json()["has_results"] is True


def test_a_failed_job_says_why(client, fake_runner, setup_file):
    fake_runner.write_text("fail")
    job = client.post("/api/rom/run", json={"setup": str(setup_file)}).json()["job_id"]
    s = wait(client, job)
    assert s["state"] == "error" and s["error"] == "CVODES failed at t = 3"


def test_run_refuses_an_unknown_action(client, fake_runner, setup_file):
    r = client.post("/api/rom/run", json={"setup": str(setup_file), "action": "train"})
    assert r.status_code == 422


def test_the_real_runner_says_unavailable_without_rom(client):
    """On a libcuflynx without libcuflynx.rom (the public release), the real runner answers 'unavailable' with the
    fix, rather than failing. Skipped where the configured libcuflynx has ROMs."""
    got = client.get("/api/rom/availability?refresh=true").json()
    if got["available"]:
        pytest.skip("this libcuflynx has reduced-order models")
    assert "not in the public release" in got["reason"]
