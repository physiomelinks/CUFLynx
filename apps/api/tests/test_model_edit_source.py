"""Edit source: the study's own copy of the model, and opening it.

"View source" used to hand the browser the uploaded file out of the TTL-pruned
temp directory. It is now "Edit source": the source is kept under the study's
outputs directory, opened there in the user's own editor, and — for an
``external_python`` model — *that* copy is the one every tier runs. These tests
hold the three halves of that together:

* the copy exists and is what ``resolve_model_path`` answers, on the live engine
  and in all three analysis run configs alike;
* the route opens it, says where it is, and degrades rather than failing when
  there is no editor to open;
* an edit made behind the app's back invalidates the caches that would otherwise
  keep running the previous version.

Unit tier: nothing simulates, and no editor is ever actually spawned.
"""

from pathlib import Path

import pytest

import editor_launch
import engine as engine_mod
import main
import model_codegen
import user_funcs

FIXTURE = Path(__file__).resolve().parent / "data" / "heat1d_external_model.py"


def upload_py(client, output_dir=None, source=None):
    url = "/api/models/upload"
    if output_dir is not None:
        url += f"?output_dir={output_dir}"
    resp = client.post(
        url,
        files={
            "file": (
                "heat1d.py",
                FIXTURE.read_bytes() if source is None else source.encode("utf-8"),
                "text/x-python",
            )
        },
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["model_id"]


@pytest.fixture
def no_launch(monkeypatch):
    """Record what the route asked to open; never spawn anything.

    A test that actually started an editor would open a window on the machine
    running the suite, and on CI would open nothing and say it had.
    """
    calls = []

    def _fake(path, **_kwargs):
        calls.append(str(path))
        return {"opened": True, "editor": "fake-editor", "reason": ""}

    monkeypatch.setattr(editor_launch, "open_in_editor", _fake)
    return calls


# ---------------------------------------------------------------------------
# Which file is the model
# ---------------------------------------------------------------------------
def test_the_upload_writes_the_study_copy_and_that_is_what_runs(client, tmp_path):
    model_id = upload_py(client, output_dir=tmp_path)
    study_copy = tmp_path / "user_funcs" / "user_model.py"
    assert study_copy.read_bytes() == FIXTURE.read_bytes()
    resolved = model_codegen.resolve_model_path(
        str(main.UPLOAD_DIR / f"{model_id}.py"),
        "external_python",
        model_id=model_id,
        output_dir=str(tmp_path),
    )
    assert resolved == str(study_copy)


def test_without_an_outputs_dir_the_uploaded_temp_copy_is_still_the_model(client):
    """The fallback matters: a session that has not chosen an outputs directory
    must still be able to run its model."""
    model_id = upload_py(client)
    uploaded = str(main.UPLOAD_DIR / f"{model_id}.py")
    assert (
        model_codegen.resolve_model_path(
            uploaded, "external_python", model_id=model_id, output_dir=None
        )
        == uploaded
    )
    # ...and so must one whose outputs directory holds no copy yet.
    assert (
        model_codegen.resolve_model_path(
            uploaded, "external_python", model_id=model_id, output_dir="/nonexistent/study"
        )
        == uploaded
    )


def test_the_live_run_resolves_the_study_copy(client, tmp_path, monkeypatch):
    """The engine is handed the same file the editor opened, not the upload."""
    model_id = upload_py(client, output_dir=tmp_path)
    engine_mod.engine.model_type = "external_python"
    engine_mod.engine.solver = "external"

    seen = {}
    monkeypatch.setattr(
        engine_mod.engine,
        "simulate",
        lambda **kw: seen.update(kw) or {"time": [], "outputs": {}},
    )
    resp = client.post(
        "/api/simulate",
        json={"model_id": model_id, "params": {}, "config_outputs_dir": str(tmp_path)},
    )
    assert resp.status_code == 200, resp.text
    assert seen["model_path"] == str(tmp_path / "user_funcs" / "user_model.py")


@pytest.mark.parametrize(
    "route,manager",
    [
        ("/api/calibration/run", "calibration"),
        ("/api/sensitivity/run", "sensitivity"),
        ("/api/emulator/train", "emulator"),
    ],
)
def test_every_analysis_runner_resolves_the_same_study_copy(
    client, tmp_path, monkeypatch, route, manager
):
    """Two tiers disagreeing about which file is the model would be a worse bug
    than the one Edit source fixes, so each runner is asked directly."""
    import json

    model_id = upload_py(client, output_dir=tmp_path)
    obs = tmp_path / "obs_data.json"
    obs.write_text(json.dumps({"data_items": []}))
    params = tmp_path / "params_for_id.csv"
    params.write_text("vessel_name,param_name,param_type,min,max\nheat,k,constant,0.1,2.0\n")
    record = main._models[model_id]
    record.obs_path = obs
    record.params_path = params

    engine_mod.engine.model_type = "external_python"
    engine_mod.engine.solver = "external"
    captured = {}
    monkeypatch.setattr(
        getattr(main, manager), "start", lambda config: captured.update(config) or "job-1"
    )
    resp = client.post(
        route,
        json={"model_id": model_id, "settings": {"config_outputs_dir": str(tmp_path)}},
    )
    assert resp.status_code == 200, resp.text
    assert captured["model_path"] == str(tmp_path / "user_funcs" / "user_model.py")


# ---------------------------------------------------------------------------
# The route
# ---------------------------------------------------------------------------
def test_edit_opens_the_study_copy_and_reports_where_it_is(client, tmp_path, no_launch):
    model_id = upload_py(client, output_dir=tmp_path)
    resp = client.post(
        f"/api/models/{model_id}/edit", json={"config_outputs_dir": str(tmp_path)}
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    study_copy = tmp_path / "user_funcs" / "user_model.py"
    assert body["path"] == str(study_copy)
    assert body["opened"] is True
    assert body["editor"] == "fake-editor"
    # The claim the instructional message is built on.
    assert body["runs"] is True
    assert no_launch == [str(study_copy)]


def test_edit_creates_the_copy_when_the_outputs_dir_came_later(client, tmp_path, no_launch):
    """Uploaded with no outputs directory, then one is chosen: Edit is where the
    study copy gets made, and from then on it is the model."""
    model_id = upload_py(client)
    assert not (tmp_path / "user_funcs" / "user_model.py").exists()
    resp = client.post(
        f"/api/models/{model_id}/edit", json={"config_outputs_dir": str(tmp_path)}
    )
    assert resp.status_code == 200, resp.text
    assert (tmp_path / "user_funcs" / "user_model.py").read_bytes() == FIXTURE.read_bytes()


def test_edit_never_overwrites_an_existing_edit(client, tmp_path, no_launch):
    """Pressing the button twice must not throw away what the first press led to."""
    model_id = upload_py(client, output_dir=tmp_path)
    study_copy = tmp_path / "user_funcs" / "user_model.py"
    study_copy.write_text("# the user's edit\n")
    client.post(f"/api/models/{model_id}/edit", json={"config_outputs_dir": str(tmp_path)})
    assert study_copy.read_text() == "# the user's edit\n"


def test_no_outputs_dir_is_refused_rather_than_written_to_a_temp_dir(client, no_launch):
    model_id = upload_py(client)
    resp = client.post(f"/api/models/{model_id}/edit", json={"config_outputs_dir": ""})
    assert resp.status_code == 422
    detail = resp.json()["detail"]
    assert "outputs directory" in detail
    # Nothing was opened: there was nowhere to open.
    assert no_launch == []


def test_a_relative_outputs_dir_is_refused(client, no_launch):
    model_id = upload_py(client)
    resp = client.post(
        f"/api/models/{model_id}/edit", json={"config_outputs_dir": "relative/study"}
    )
    assert resp.status_code == 422
    assert "absolute" in resp.json()["detail"]


def test_a_cellml_model_has_no_source_to_edit(client, tmp_path, no_launch):
    from conftest import BG_MODEL_PATH, upload_model

    model_id = upload_model(client, BG_MODEL_PATH)["model_id"]
    resp = client.post(
        f"/api/models/{model_id}/edit", json={"config_outputs_dir": str(tmp_path)}
    )
    assert resp.status_code == 404
    assert "PhLynx" in resp.json()["detail"]


@pytest.mark.parametrize("model_id", [".hidden", "-dash", "a$b", "x" * 200, "%2e%2e"])
def test_the_model_id_never_becomes_a_path_segment(client, tmp_path, model_id, no_launch):
    """The same rule ``solver_plots`` follows: a client string is never joined
    onto a path unchecked. A ``/`` in the id never reaches the handler at all —
    it fails to match the route — so the ids here are the single segments that
    do reach it and must still be refused."""
    resp = client.post(
        f"/api/models/{model_id}/edit", json={"config_outputs_dir": str(tmp_path)}
    )
    assert resp.status_code == 404
    assert no_launch == []


def test_a_launch_that_cannot_happen_still_answers_with_the_path(
    client, tmp_path, monkeypatch
):
    """A headless backend has no handler to run. The path is the useful half of
    the answer and is true either way, so this is a 200, not a 500."""
    monkeypatch.setattr(
        editor_launch,
        "open_in_editor",
        lambda path, **kw: {"opened": False, "editor": None, "reason": "no desktop session"},
    )
    model_id = upload_py(client, output_dir=tmp_path)
    resp = client.post(
        f"/api/models/{model_id}/edit", json={"config_outputs_dir": str(tmp_path)}
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["opened"] is False
    assert body["reason"] == "no desktop session"
    assert body["path"] == str(tmp_path / "user_funcs" / "user_model.py")


def test_a_myokit_source_is_editable_but_says_it_is_not_what_runs(
    client, tmp_path, no_launch
):
    """A .mmt is converted to CellML at the door, so editing it changes the file
    of record and not the simulation -- which the reply has to admit."""
    main._save_model_source("mmtmodel", ".mmt", b"[[model]]\n")
    main._models["mmtmodel"] = main._ModelRecord(
        "mmtmodel", main.UPLOAD_DIR / "mmtmodel.cellml", _StubMeta()
    )
    resp = client.post(
        "/api/models/mmtmodel/edit", json={"config_outputs_dir": str(tmp_path)}
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["path"] == str(tmp_path / "user_funcs" / "user_model.mmt")
    assert body["runs"] is False


class _StubMeta:
    name = "mmt model"
    variable_count = 0
    params: list = []
    odes: list = []
    algebraic: list = []


# ---------------------------------------------------------------------------
# GET /source still works, and shows the same file
# ---------------------------------------------------------------------------
def test_the_source_route_still_serves_the_file(client):
    model_id = upload_py(client)
    resp = client.get(f"/api/models/{model_id}/source")
    assert resp.status_code == 200, resp.text
    assert resp.text == FIXTURE.read_text()


def test_the_source_route_shows_the_study_copy_when_there_is_one(client, tmp_path):
    """Otherwise "view" and "edit" would disagree about what the model is."""
    model_id = upload_py(client, output_dir=tmp_path)
    (tmp_path / "user_funcs" / "user_model.py").write_text("# edited\n")
    resp = client.get(
        f"/api/models/{model_id}/source", params={"config_outputs_dir": str(tmp_path)}
    )
    assert resp.status_code == 200, resp.text
    assert resp.text == "# edited\n"


# ---------------------------------------------------------------------------
# An edit takes effect
# ---------------------------------------------------------------------------
def test_an_edited_model_file_drops_the_cached_helper(tmp_path):
    """The engine caches a helper per (model, backend, solver), and the helper is
    a snapshot of a file the user now edits outside the app. Without this the
    next slider drag runs the previous model and says nothing."""
    model = tmp_path / "user_model.py"
    model.write_text("# v1\n")
    built = []
    engine_mod.engine.model_type = "external_python"
    engine_mod.engine.solver = "external"
    engine_mod.engine.helper_factory = lambda **kw: built.append(kw["model_path"]) or _StubHelper()

    for _ in range(2):
        engine_mod.engine.simulate(
            model_id="m1", model_path=str(model), params={}, sim_time=1.0,
            pre_time=0.0, outputs=[],
        )
    assert len(built) == 1, "an unchanged model must still be cached"

    _touch_newer(model, "# v2\n")
    engine_mod.engine.simulate(
        model_id="m1", model_path=str(model), params={}, sim_time=1.0,
        pre_time=0.0, outputs=[],
    )
    assert len(built) == 2, "the edit must invalidate the cached helper"


def test_moving_to_the_study_copy_also_invalidates(tmp_path):
    """Same model_id, same backend, different file: setting an outputs directory
    mid-session changes which copy runs, and the cache key alone cannot see it."""
    first = tmp_path / "uploaded.py"
    first.write_text("# v1\n")
    second = tmp_path / "user_funcs" / "user_model.py"
    second.parent.mkdir()
    second.write_text("# v1\n")
    built = []
    engine_mod.engine.helper_factory = lambda **kw: built.append(kw["model_path"]) or _StubHelper()
    for path in (first, second):
        engine_mod.engine.simulate(
            model_id="m1", model_path=str(path), params={}, sim_time=1.0,
            pre_time=0.0, outputs=[],
        )
    assert built == [str(first), str(second)]


def test_an_edited_model_file_drops_the_cached_protocol_runner(tmp_path):
    model = tmp_path / "user_model.py"
    model.write_text("# v1\n")
    built = []
    engine_mod.engine.runner_factory = lambda **kw: built.append(kw["model_path"]) or _StubRunner()
    protocol = {"experiment_labels": ["e"], "params_to_change": {}}
    for _ in range(2):
        engine_mod.engine.run_protocol(
            model_id="m1", model_path=str(model), protocol_info=protocol,
            params={}, outputs=[],
        )
    assert len(built) == 1
    _touch_newer(model, "# v2\n")
    engine_mod.engine.run_protocol(
        model_id="m1", model_path=str(model), protocol_info=protocol, params={}, outputs=[],
    )
    assert len(built) == 2


def test_the_worker_invalidates_on_the_same_rule(tmp_path):
    """The sim worker holds its own caches in the user's interpreter; whichever
    tier kept a stale copy would be the one silently running the old model."""
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "sim_worker_runner_under_test",
        Path(__file__).resolve().parents[1] / "sim_worker_runner.py",
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    model = tmp_path / "user_model.py"
    model.write_text("# v1\n")
    worker = module.Worker()
    worker._helpers["m1"] = "helper-v1"
    worker._runners["m1"] = "runner-v1"
    worker._runner_protocol_info["m1"] = {"stale": True}
    worker._drop_if_model_changed("m1", str(model))
    assert worker._helpers["m1"] == "helper-v1", "an unchanged file must stay cached"

    _touch_newer(model, "# v2\n")
    worker._drop_if_model_changed("m1", str(model))
    assert "m1" not in worker._helpers
    assert "m1" not in worker._runners
    assert "m1" not in worker._runner_protocol_info


def test_model_stamp_survives_a_missing_file():
    """An unreadable model is the run's problem to report, not the cache's."""
    assert engine_mod.model_stamp("/nonexistent/model.py") == ("/nonexistent/model.py", 0, -1)


def _touch_newer(path: Path, text: str) -> None:
    """Rewrite ``path`` with an unmistakably newer mtime.

    Explicit rather than trusting the clock: a same-second rewrite on a
    coarse-grained filesystem would leave the stamp unchanged and turn a real
    regression green.
    """
    stamp = path.stat()
    path.write_text(text)
    import os

    os.utime(path, ns=(stamp.st_atime_ns + 10**9, stamp.st_mtime_ns + 10**9))


class _StubHelper:
    def reset_and_clear(self):
        pass

    def update_times(self, *_args):
        pass

    def set_param_vals(self, names, vals):
        pass

    def run(self):
        return True

    def get_time(self, include_pre_time=False):
        return [0.0]

    def get_results(self, variables, flatten=False):
        return []


class _StubRunner:
    def run_protocols(self, model_path, protocol_info=None, id_param_names=None,
                      id_param_vals=None):
        return [[0.0]], [[]], [1.0]

    def get_var2idx_dict(self):
        return {}


# ---------------------------------------------------------------------------
# Launching the editor
# ---------------------------------------------------------------------------
def test_visual_beats_editor_beats_the_platform_handler(monkeypatch):
    spawned = []
    monkeypatch.setattr(
        editor_launch, "_spawn", lambda argv: spawned.append(argv) or (True, "")
    )
    result = editor_launch.open_in_editor(
        "/study/user_model.py",
        env={"VISUAL": "code -w", "EDITOR": "vim", "DISPLAY": ":0"},
        platform="linux",
    )
    assert result["opened"] is True
    assert result["editor"] == "code"
    # Split as a shell would, and the path is its own argv element -- never
    # interpolated into a string, never handed to a shell.
    assert spawned == [["code", "-w", "/study/user_model.py"]]


def test_a_broken_editor_falls_through_to_the_platform_handler(monkeypatch):
    spawned = []

    def _spawn(argv):
        spawned.append(argv)
        return (argv[0] != "no-such-editor", "No such file or directory")

    monkeypatch.setattr(editor_launch, "_spawn", _spawn)
    result = editor_launch.open_in_editor(
        "/study/user_model.py",
        env={"EDITOR": "no-such-editor", "DISPLAY": ":0"},
        platform="linux",
    )
    assert result["opened"] is True
    assert result["editor"] == "xdg-open"
    assert spawned[-1] == ["xdg-open", "/study/user_model.py"]


@pytest.mark.parametrize(
    "platform,expected",
    [("linux", "xdg-open"), ("darwin", "open")],
)
def test_the_platform_default_handler(monkeypatch, platform, expected):
    monkeypatch.setattr(editor_launch, "_spawn", lambda argv: (True, ""))
    result = editor_launch.open_in_editor(
        "/study/user_model.py", env={"DISPLAY": ":0"}, platform=platform
    )
    assert result["editor"] == expected


def test_windows_uses_the_shell_association(monkeypatch):
    opened = []
    monkeypatch.setattr(editor_launch.os, "startfile", opened.append, raising=False)
    result = editor_launch.open_in_editor("/study/user_model.py", env={}, platform="win32")
    assert result["opened"] is True
    assert opened == ["/study/user_model.py"]


def test_a_headless_linux_box_says_so_instead_of_pretending(monkeypatch):
    """xdg-open is usually installed on a headless machine and exits happily
    after doing nothing, so "did Popen work" is the wrong question."""
    monkeypatch.setattr(
        editor_launch, "_spawn", lambda argv: pytest.fail("nothing should be spawned")
    )
    result = editor_launch.open_in_editor("/study/user_model.py", env={}, platform="linux")
    assert result["opened"] is False
    assert result["editor"] is None
    assert "DISPLAY" in result["reason"]


def test_an_unparseable_editor_variable_is_skipped_not_guessed_at(monkeypatch):
    monkeypatch.setattr(editor_launch, "_spawn", lambda argv: (True, ""))
    result = editor_launch.open_in_editor(
        "/study/user_model.py",
        env={"EDITOR": 'unbalanced "quote', "DISPLAY": ":0"},
        platform="linux",
    )
    assert result["editor"] == "xdg-open"


def test_the_study_copy_has_one_home_per_suffix(tmp_path):
    """One stem for the .py and the .mmt, derived from the model kind's own
    filename -- so "where does the model source go" has a single answer."""
    assert user_funcs.model_source_path(".py", str(tmp_path)) == (
        tmp_path / "user_funcs" / "user_model.py"
    )
    assert user_funcs.model_source_path("mmt", str(tmp_path)) == (
        tmp_path / "user_funcs" / "user_model.mmt"
    )
