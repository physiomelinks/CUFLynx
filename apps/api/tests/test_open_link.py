"""``cuflynx://open?url=...`` links: parsing, fetching, staging, single instance.

A web page links to an archive; the OS starts CUFLynx with the link; CUFLynx
fetches the archive and stages it in the inbox, where the UI asks before loading
it. A second launch with a link hands it to the running app instead.
"""

from __future__ import annotations

import io
import re
import socket
import threading
import time
import urllib.error
import urllib.parse

import pytest
from conftest import RESOURCES_DIR

import inbox as inbox_mod
import open_link

EXAMPLE = RESOURCES_DIR / "3compartment.omex"


def _link(url: str) -> str:
    return "cuflynx://open?url=" + urllib.parse.quote(url, safe="")


@pytest.fixture(autouse=True)
def _clean():
    inbox_mod.inbox.clear()
    inbox_mod.inbox.take_error()
    open_link.set_token(None)
    yield
    inbox_mod.inbox.clear()
    inbox_mod.inbox.take_error()
    open_link.set_token(None)


# ---------------------------------------------------------------------------
# The link grammar
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("url", [
    "https://example.org/lib/lv.omex",
    "http://127.0.0.1:8000/a%20b.omex",
    "file:///home/me/lv.omex",
    "file://localhost/home/me/lv.omex",
])
def test_open_links_name_their_archive(url):
    assert open_link.parse_link(_link(url)) == url


@pytest.mark.parametrize("link", [
    "cuflynx:open?url=https%3A%2F%2Fexample.org%2Fx.omex",
    "cuflynx://open/?url=https%3A%2F%2Fexample.org%2Fx.omex",
    "CUFLYNX://OPEN?url=https%3A%2F%2Fexample.org%2Fx.omex",
])
def test_launcher_spellings_are_accepted(link):
    assert open_link.parse_link(link) == "https://example.org/x.omex"


@pytest.mark.parametrize("link, words", [
    ("", "empty"),
    ("https://example.org/x.omex", "not a cuflynx://"),
    ("cuflynx://delete?url=https%3A%2F%2Fexample.org%2Fx.omex", "unknown cuflynx:// action 'delete'"),
    ("cuflynx://open/more?url=https%3A%2F%2Fexample.org%2Fx.omex", "unknown cuflynx:// action"),
    ("cuflynx://open", "exactly one of url="),
    ("cuflynx://open?url=", "exactly one of url="),
    ("cuflynx://open?url=https%3A%2F%2Fa.org%2Fx&url=https%3A%2F%2Fb.org%2Fy", "exactly one of url="),
    ("cuflynx://open?url=ftp%3A%2F%2Fexample.org%2Fx.omex", "https:, http: or file:"),
    ("cuflynx://open?url=javascript%3Aalert(1)", "https:, http: or file:"),
    ("cuflynx://open?url=%2Fhome%2Fme%2Fx.omex", "https:, http: or file:"),
    ("cuflynx://open?url=https%3A%2F%2F%2Fx.omex", "names no host"),
    ("cuflynx://open?url=file%3A%2F%2Fserver%2Fshare%2Fx.omex", "on this computer"),
    ("cuflynx://open?url=" + "a" * 9000, "longer than"),
])
def test_bad_links_are_refused_with_a_reason(link, words):
    with pytest.raises(open_link.OpenLinkError) as info:
        open_link.parse_link(link)
    assert words in str(info.value)


def test_origin_and_filename_for_the_dialog():
    assert open_link.origin_of("https://physiomelinks.github.io/x/lv.omex") == (
        "https://physiomelinks.github.io"
    )
    assert open_link.origin_of("file:///tmp/lv.omex") == "A file on this computer"
    assert open_link.filename_of("https://a.org/lib/Lotka%20Volterra.omex?x=1") == "Lotka_Volterra.omex"
    assert open_link.filename_of("https://a.org/") == "study.omex"


# ---------------------------------------------------------------------------
# Fetching, with a size limit
# ---------------------------------------------------------------------------
class _Response(io.BytesIO):
    def __init__(self, data: bytes, length: str | None = None):
        super().__init__(data)
        self.headers = {"Content-Length": length} if length is not None else {}

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_a_download_is_returned_whole():
    data = EXAMPLE.read_bytes()
    seen = {}

    def opener(request, timeout):
        seen["url"], seen["timeout"] = request.full_url, timeout
        return _Response(data, str(len(data)))

    assert open_link.fetch("https://a.org/x.omex", opener=opener) == data
    assert seen["url"] == "https://a.org/x.omex"


def test_a_declared_size_over_the_limit_is_refused_before_reading():
    def opener(request, timeout):
        return _Response(b"", str(10_000))

    with pytest.raises(open_link.OpenLinkError, match="larger than"):
        open_link.fetch("https://a.org/x.omex", max_bytes=1000, opener=opener)


def test_an_undeclared_size_is_capped_while_streaming():
    def opener(request, timeout):
        return _Response(b"x" * 5000)  # no Content-Length

    with pytest.raises(open_link.OpenLinkError, match="larger than"):
        open_link.fetch("https://a.org/x.omex", max_bytes=1000, opener=opener)


def test_http_errors_say_what_the_server_answered():
    def opener(request, timeout):
        raise urllib.error.HTTPError(request.full_url, 404, "Not Found", {}, None)

    with pytest.raises(open_link.OpenLinkError, match="404 Not Found"):
        open_link.fetch("https://a.org/x.omex", opener=opener)


def test_unreachable_hosts_say_why():
    def opener(request, timeout):
        raise urllib.error.URLError("Name or service not known")

    with pytest.raises(open_link.OpenLinkError, match="Name or service not known"):
        open_link.fetch("https://a.org/x.omex", opener=opener)


def test_a_file_url_is_read_from_disk(tmp_path):
    target = tmp_path / "my study.omex"
    target.write_bytes(EXAMPLE.read_bytes())
    assert open_link.fetch(target.as_uri()) == EXAMPLE.read_bytes()
    with pytest.raises(open_link.OpenLinkError, match="larger than"):
        open_link.fetch(target.as_uri(), max_bytes=10)
    with pytest.raises(open_link.OpenLinkError, match="could not read"):
        open_link.fetch((tmp_path / "missing.omex").as_uri())


# ---------------------------------------------------------------------------
# Staging
# ---------------------------------------------------------------------------
def test_a_link_stages_the_archive_for_the_user_to_confirm(tmp_path):
    summary = open_link.stage(
        _link("https://physiomelinks.github.io/lib/lv.omex"),
        fetcher=lambda url: EXAMPLE.read_bytes(),
    )
    assert summary["origin"] == "https://physiomelinks.github.io"
    assert summary["filename"] == "lv.omex"
    pending = inbox_mod.inbox.peek()
    assert pending["origin"] == "https://physiomelinks.github.io"
    assert inbox_mod.inbox.take_error() is None


@pytest.mark.parametrize("link, fetcher, words", [
    ("cuflynx://nope", lambda url: b"", "unknown cuflynx:// action"),
    (_link("https://a.org/x.omex"), lambda url: b"not a zip", "not a COMBINE archive"),
    (_link("https://a.org/x.omex"), lambda url: b"", "the archive is empty"),
])
def test_a_failed_link_is_reported_to_the_ui_and_stages_nothing(link, fetcher, words):
    result = open_link.stage(link, fetcher=fetcher)
    assert words in result["error"]
    assert inbox_mod.inbox.peek() is None
    error = inbox_mod.inbox.take_error()
    assert words in error["message"]
    assert inbox_mod.inbox.take_error() is None  # shown once


def test_the_inbox_route_returns_the_error_once(client):
    open_link.stage(_link("https://a.org/x.omex"), fetcher=lambda url: b"junk")
    first = client.get("/api/inbox").json()
    assert first["pending"] is None
    assert "Could not open the study from https://a.org/x.omex" in first["error"]["message"]
    assert client.get("/api/inbox").json()["error"] is None


# ---------------------------------------------------------------------------
# /api/open-link: the handoff endpoint
# ---------------------------------------------------------------------------
def test_open_link_route_is_off_without_a_token(client):
    resp = client.post("/api/open-link", json={"link": _link(EXAMPLE.as_uri())})
    assert resp.status_code == 403


def test_open_link_route_refuses_a_wrong_token(client):
    open_link.set_token("right")
    resp = client.post(
        "/api/open-link",
        json={"link": _link(EXAMPLE.as_uri())},
        headers={open_link.TOKEN_HEADER: "wrong"},
    )
    assert resp.status_code == 403
    assert inbox_mod.inbox.peek() is None


def test_open_link_route_stages_the_linked_file(client):
    open_link.set_token("t0ken")
    resp = client.post(
        "/api/open-link",
        json={"link": _link(EXAMPLE.as_uri())},
        headers={open_link.TOKEN_HEADER: "t0ken"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["url"] == EXAMPLE.as_uri()
    pending = _wait_for_pending()
    assert pending["filename"] == "3compartment.omex"
    assert pending["origin"] == "A file on this computer"


def test_open_link_route_reports_a_bad_link_in_the_ui_too(client):
    open_link.set_token("t0ken")
    resp = client.post(
        "/api/open-link",
        json={"link": "cuflynx://open?url=ftp%3A%2F%2Fa.org%2Fx.omex"},
        headers={open_link.TOKEN_HEADER: "t0ken"},
    )
    assert resp.status_code == 422
    assert "https:, http: or file:" in client.get("/api/inbox").json()["error"]["message"]


def _wait_for_pending(timeout: float = 10.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        pending = inbox_mod.inbox.peek()
        if pending:
            return pending
        time.sleep(0.05)
    raise AssertionError(f"nothing was staged; error: {inbox_mod.inbox.take_error()}")


# ---------------------------------------------------------------------------
# Single instance
# ---------------------------------------------------------------------------
def test_the_instance_file_round_trips_and_is_only_removed_by_its_owner(tmp_path):
    path = tmp_path / "instance.json"
    open_link.write_instance(8787, "mine", path)
    assert open_link.read_instance(path)["port"] == 8787
    open_link.remove_instance("someone-else", path)
    assert path.exists()
    open_link.remove_instance("mine", path)
    assert not path.exists()


def test_a_garbled_instance_file_is_ignored(tmp_path):
    path = tmp_path / "instance.json"
    path.write_text("{not json")
    assert open_link.read_instance(path) is None
    assert open_link.hand_off(_link("https://a.org/x.omex"), path) is False


def test_the_default_instance_file_is_in_the_config_dir(monkeypatch, tmp_path):
    monkeypatch.setenv("CUFLYNX_CONFIG_DIR", str(tmp_path))
    assert open_link.instance_file() == tmp_path / "instance.json"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def running_app():
    """The real API on a real port, as a first CUFLynx would serve it."""
    import uvicorn

    import main

    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(main.app, host="127.0.0.1", port=port, log_level="error"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 20
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.05)
    assert server.started
    yield port
    server.should_exit = True
    thread.join(timeout=10)


def test_a_second_launch_hands_its_link_to_the_running_app(running_app, tmp_path):
    path = tmp_path / "instance.json"
    open_link.set_token("live-token")
    open_link.write_instance(running_app, "live-token", path)

    assert open_link.hand_off(_link(EXAMPLE.as_uri()), path) is True
    assert _wait_for_pending()["filename"] == "3compartment.omex"


def test_a_handoff_the_running_app_refuses_still_counts(running_app, tmp_path):
    """It has shown the error in its own window; a second window would not help."""
    path = tmp_path / "instance.json"
    open_link.set_token("live-token")
    open_link.write_instance(running_app, "live-token", path)
    assert open_link.hand_off("cuflynx://nope", path) is True
    assert inbox_mod.inbox.take_error() is not None


def test_a_stale_instance_file_means_start_a_new_app(tmp_path):
    path = tmp_path / "instance.json"
    open_link.write_instance(_free_port(), "gone", path)  # nothing listens there
    assert open_link.hand_off(_link(EXAMPLE.as_uri()), path, timeout=1) is False


def test_a_token_mismatch_means_start_a_new_app(running_app, tmp_path):
    path = tmp_path / "instance.json"
    open_link.set_token("the-real-one")
    open_link.write_instance(running_app, "an-old-one", path)
    assert open_link.hand_off(_link(EXAMPLE.as_uri()), path) is False


# ---------------------------------------------------------------------------
# cuflynx://open?study=... -- a study directory on this machine
# ---------------------------------------------------------------------------
def _study_link(url: str) -> str:
    return "cuflynx://open?study=" + urllib.parse.quote(url, safe="")


def _write_study(directory, name="SN_cuflynx_study.json", **fields):
    import json  # noqa: PLC0415

    directory.mkdir(parents=True, exist_ok=True)
    manifest = {"schema": 1, "study_name": "SN_ox1", "file_prefix": "SN",
                "model": "SN.cellml", "emulator": "emulators/bundle",
                "sensitivity": [{"kind": "sobol", "dir": "sa"}], **fields}
    path = directory / name
    path.write_text(json.dumps(manifest), encoding="utf-8")
    return path


@pytest.mark.parametrize("url", [
    "file:///home/me/sa/cuflynx_study.json",
    "file://localhost/home/me/sa/SN_cuflynx_study.json",
    "file:///home/me/sa",
])
def test_study_links_name_a_local_study(url):
    target = open_link.parse(_study_link(url))
    assert (target.kind, target.url) == ("study", url)


def test_archive_links_still_parse_as_archives():
    assert open_link.parse(_link("https://a.org/x.omex")) == open_link.Link("archive", "https://a.org/x.omex")


@pytest.mark.parametrize("link, words", [
    (_study_link("https://a.org/cuflynx_study.json"), "on this computer with a file: URL"),
    (_study_link("http://a.org/sa"), "on this computer with a file: URL"),
    (_study_link("file://elsewhere/sa/cuflynx_study.json"), "must be on this computer"),
    (_study_link("file:///a") + "&url=" + urllib.parse.quote("https://a.org/x.omex", safe=""),
     "exactly one of url="),
    (_study_link("file:///a") + "&study=" + urllib.parse.quote("file:///b", safe=""),
     "exactly one of url="),
    ("cuflynx://open?study=", "exactly one of url="),
    ("cuflynx://open", "exactly one of url="),
])
def test_bad_study_links_are_refused_with_a_reason(link, words):
    with pytest.raises(open_link.OpenLinkError, match=re.escape(words)):
        open_link.parse(link)


@pytest.mark.parametrize("by", ["file", "directory"])
def test_a_study_link_stages_the_manifest_naming_its_path(tmp_path, by):
    path = _write_study(tmp_path / "sa_ox1")
    url = (path if by == "file" else path.parent).as_uri()
    summary = open_link.stage(_study_link(url))
    assert summary["kind"] == "study"
    assert summary["path"] == str(path)
    assert summary["filename"] == path.name
    assert summary["origin"] == "A cuflynx:// link"
    assert "emulator: emulators/bundle" in summary["members"]
    assert "sensitivity: 1 recorded" in summary["members"]
    assert inbox_mod.inbox.peek() == summary
    assert inbox_mod.inbox.take_error() is None


def test_a_directory_with_a_bare_manifest_opens_it(tmp_path):
    path = _write_study(tmp_path / "s", name="cuflynx_study.json")
    assert open_link.stage(_study_link(path.parent.as_uri()))["path"] == str(path)


def _failing_study(tmp_path, case):
    if case == "missing":
        return (tmp_path / "nope" / "cuflynx_study.json").as_uri(), "does not exist"
    if case == "no manifest in dir":
        (tmp_path / "empty").mkdir()
        return (tmp_path / "empty").as_uri(), "holds no cuflynx_study.json"
    if case == "wrong name":
        other = tmp_path / "obs_data.json"
        other.write_text("{}")
        return other.as_uri(), "is not a study manifest"
    if case == "not json":
        bad = tmp_path / "cuflynx_study.json"
        bad.write_text("{nope")
        return bad.as_uri(), "it is not JSON"
    if case == "newer schema":
        return _write_study(tmp_path / "new", schema=99).as_uri(), "declares schema 99"
    if case == "no schema":
        return _write_study(tmp_path / "old", schema=None).as_uri(), "does not declare an integer 'schema'"
    raise AssertionError(case)


@pytest.mark.parametrize("case", [
    "missing", "no manifest in dir", "wrong name", "not json", "newer schema", "no schema",
])
def test_a_study_link_that_cannot_open_is_reported_and_stages_nothing(tmp_path, case):
    url, words = _failing_study(tmp_path, case)
    result = open_link.stage(_study_link(url))
    assert words in result["error"]
    assert inbox_mod.inbox.peek() is None
    error = inbox_mod.inbox.take_error()
    assert error["message"].startswith(f"Could not open the study from {url}")
    assert words in error["message"]


def test_accepting_a_study_link_answers_what_the_manifest_route_answers(client, tmp_path):
    """One way to open a manifest: the accept path goes through ``/api/outputs/manifest``."""
    path = _write_study(tmp_path / "sa_ox1")
    dropped = client.post("/api/outputs/manifest",
                          json={"content": path.read_text(), "path": str(path)}).json()

    open_link.stage(_study_link(path.as_uri()))
    accepted = client.post("/api/inbox/accept")
    assert accepted.status_code == 200, accepted.text
    body = accepted.json()
    assert body == {"kind": "study", **dropped, "path": str(path),
                    "delivered_from": "A cuflynx:// link"}
    assert body["dir"] == str(path.parent)
    assert client.get("/api/inbox").json()["pending"] is None


def test_a_study_moved_away_after_staging_is_refused_at_accept(client, tmp_path):
    """Accept re-runs the manifest route, so a study gone since staging says so."""
    path = _write_study(tmp_path / "sa")
    open_link.stage(_study_link(path.as_uri()))
    path.unlink()
    resp = client.post("/api/inbox/accept")
    assert resp.status_code == 422
    assert "study_dir" in resp.json()["detail"]


def test_open_link_route_stages_a_study(client, tmp_path):
    path = _write_study(tmp_path / "sa")
    open_link.set_token("t0ken")
    resp = client.post("/api/open-link", json={"link": _study_link(path.parent.as_uri())},
                       headers={open_link.TOKEN_HEADER: "t0ken"})
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"accepted": True, "kind": "study", "url": path.parent.as_uri()}
    assert _wait_for_pending()["path"] == str(path)


def test_open_link_route_refuses_a_web_study_link(client):
    open_link.set_token("t0ken")
    resp = client.post("/api/open-link",
                       json={"link": _study_link("https://a.org/cuflynx_study.json")},
                       headers={open_link.TOKEN_HEADER: "t0ken"})
    assert resp.status_code == 422
    assert "on this computer" in client.get("/api/inbox").json()["error"]["message"]
