"""Registering ``cuflynx://`` links, and the desktop shell's link handling.

The OS side of each platform cannot run here (no LaunchServices, no registry),
so these pin what is written and which commands are run; the Linux registration
also runs against a real temporary ``XDG_DATA_HOME``. Imported by path because
``apps/desktop`` is not a package.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import plistlib
import shlex
import sys
from pathlib import Path

import pytest

DESKTOP = Path(__file__).resolve().parents[2] / "desktop"


def _load(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, DESKTOP / filename)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def url_scheme():
    return _load("cuflynx_url_scheme", "url_scheme.py")


@pytest.fixture(scope="module")
def shell():
    return _load("cuflynx_desktop_shell_links", "app.py")


class _Run:
    def __init__(self):
        self.calls = []

    def __call__(self, cmd, **kwargs):
        self.calls.append(list(cmd))
        if cmd[0] == "osacompile":  # make the bundle osacompile would make
            app = Path(cmd[cmd.index("-o") + 1])
            (app / "Contents").mkdir(parents=True)
            with (app / "Contents" / "Info.plist").open("wb") as fh:
                plistlib.dump({"CFBundleName": "applet", "CFBundleExecutable": "applet"}, fh)
            self.script = Path(cmd[-1]).read_text()


# ---------------------------------------------------------------------------
# Linux
# ---------------------------------------------------------------------------
def test_the_desktop_entry_declares_the_scheme(url_scheme):
    entry = url_scheme.linux_desktop_entry(["/opt/CUFLynx"])
    assert "MimeType=x-scheme-handler/cuflynx;\n" in entry
    assert "Exec=/opt/CUFLynx --open-url %u\n" in entry
    assert "NoDisplay=true" in entry


def test_exec_arguments_are_quoted_per_the_spec(url_scheme):
    line = url_scheme.linux_desktop_entry(['/home/me/My Apps/CUF"Lynx$', "100%"])
    exec_line = next(x for x in line.splitlines() if x.startswith("Exec="))
    # quoted for the space, `"` and `$` backslash-escaped, then every backslash
    # doubled by the string-value escape; `%` doubled.
    assert exec_line == 'Exec="/home/me/My Apps/CUF\\\\"Lynx\\\\$" 100%% --open-url %u'


def test_register_linux_writes_the_entry_and_makes_it_default(url_scheme, tmp_path):
    run = _Run()
    entry = url_scheme.register_linux(["/opt/CUFLynx"], data_home=tmp_path, run=run)
    assert entry == tmp_path / "applications" / "cuflynx-url-handler.desktop"
    assert "x-scheme-handler/cuflynx" in entry.read_text()
    assert run.calls[0] == [
        "xdg-mime", "default", "cuflynx-url-handler.desktop", "x-scheme-handler/cuflynx",
    ]
    assert run.calls[1][0] == "update-desktop-database"


# ---------------------------------------------------------------------------
# Windows
# ---------------------------------------------------------------------------
class _FakeWinreg:
    HKEY_CURRENT_USER = "HKCU"
    REG_SZ = 1

    def __init__(self):
        self.values = {}

    def CreateKey(self, root, path):  # noqa: N802 - winreg's spelling
        fake = self

        class _Key:
            def __enter__(self):
                return path

            def __exit__(self, *exc):
                return False

        fake.values.setdefault(path, {})
        return _Key()

    def SetValueEx(self, key, name, reserved, kind, value):  # noqa: N802
        self.values[key][name] = value


def test_register_windows_writes_a_url_protocol_under_hkcu(url_scheme):
    reg = _FakeWinreg()
    where = url_scheme.register_windows([r"C:\Users\me\Downloads\CUFLynx.exe"], winreg=reg)
    assert where == r"HKEY_CURRENT_USER\Software\Classes\cuflynx"
    root = reg.values[r"Software\Classes\cuflynx"]
    assert root["URL Protocol"] == ""
    assert root[""].startswith("URL:")
    command = reg.values[r"Software\Classes\cuflynx\shell\open\command"][""]
    assert command == r'C:\Users\me\Downloads\CUFLynx.exe --open-url "%1"'


def test_windows_command_quotes_a_path_with_spaces(url_scheme):
    assert url_scheme.windows_command([r"C:\Program Files\CUFLynx.exe"]) == (
        r'"C:\Program Files\CUFLynx.exe" --open-url "%1"'
    )


# ---------------------------------------------------------------------------
# macOS
# ---------------------------------------------------------------------------
def test_the_applet_handles_the_open_location_event(url_scheme):
    source = url_scheme.mac_applet_source(["/Users/me/Downloads/CUFLynx"])
    assert source.startswith("on open location this_URL\n")
    assert "quoted form of this_URL" in source
    assert "/Users/me/Downloads/CUFLynx --open-url " in source
    assert "nohup" in source and "&\"" in source  # detached, so the applet can quit


def test_the_applet_quotes_awkward_paths_for_shell_and_applescript(url_scheme):
    path = '/Users/me/My "Apps"/CUFLynx'
    source = url_scheme.mac_applet_source([path])
    literal = source.split("do shell script ", 1)[1].split(" & quoted form", 1)[0]
    # Undo the AppleScript string literal, then the shell quoting.
    shell = literal[1:-1].replace('\\"', '"').replace("\\\\", "\\")
    assert shlex.split(shell) == ["nohup", path, "--open-url"]


def test_register_mac_declares_the_scheme_in_the_applets_plist(url_scheme, tmp_path):
    run = _Run()
    applet = url_scheme.register_mac(["/Users/me/CUFLynx"], applications=tmp_path, run=run)
    assert applet == tmp_path / "CUFLynx Link Handler.app"
    with (applet / "Contents" / "Info.plist").open("rb") as fh:
        plist = plistlib.load(fh)
    assert plist["CFBundleURLTypes"] == [
        {"CFBundleURLName": "CUFLynx study link", "CFBundleURLSchemes": ["cuflynx"]}
    ]
    assert plist["CFBundleIdentifier"] == url_scheme.MAC_BUNDLE_ID
    assert plist["CFBundleExecutable"] == "applet"  # osacompile's own keys are kept
    assert plist["LSUIElement"] is True
    tools = [c[0] for c in run.calls]
    assert tools == ["osacompile", "codesign", url_scheme.LSREGISTER]
    assert run.calls[-1][-2:] == ["-f", str(applet)]
    assert "on open location" in run.script


# ---------------------------------------------------------------------------
# When to register
# ---------------------------------------------------------------------------
def test_registration_is_redone_only_when_the_executable_moved(url_scheme, tmp_path, monkeypatch):
    monkeypatch.delenv(url_scheme.OPT_OUT_ENV, raising=False)
    stamp = tmp_path / "url_scheme.json"
    calls = []

    def registrar(command):
        calls.append(command)
        return "somewhere"

    assert "Registered" in url_scheme.register_if_needed(["/a/CUFLynx"], registrar, stamp)
    assert url_scheme.register_if_needed(["/a/CUFLynx"], registrar, stamp) is None
    assert "Registered" in url_scheme.register_if_needed(["/b/CUFLynx"], registrar, stamp)
    assert calls == [["/a/CUFLynx"], ["/b/CUFLynx"]]
    assert json.loads(stamp.read_text())["command"] == ["/b/CUFLynx"]


def test_a_failed_registration_is_a_note_and_retried_next_launch(url_scheme, tmp_path, monkeypatch):
    monkeypatch.delenv(url_scheme.OPT_OUT_ENV, raising=False)
    stamp = tmp_path / "url_scheme.json"

    def registrar(command):
        raise FileNotFoundError("xdg-mime")

    note = url_scheme.register_if_needed(["/a/CUFLynx"], registrar, stamp)
    assert note.startswith("note: could not register cuflynx://")
    assert not stamp.exists()


def test_registration_can_be_switched_off(url_scheme, tmp_path, monkeypatch):
    monkeypatch.setenv(url_scheme.OPT_OUT_ENV, "1")

    def registrar(command):
        raise AssertionError("should not register")

    assert url_scheme.register_if_needed(["/a"], registrar, tmp_path / "s.json") is None


def test_from_source_the_command_runs_app_py(url_scheme):
    command = url_scheme.launch_command()
    assert command[0] == sys.executable
    assert Path(command[1]).name == "app.py"


def test_unknown_platforms_are_refused(url_scheme):
    with pytest.raises(RuntimeError, match="no cuflynx:// registration"):
        url_scheme.register(["/a"], platform="sunos5")


# ---------------------------------------------------------------------------
# The shell
# ---------------------------------------------------------------------------
def test_the_link_comes_from_open_url_or_a_bare_argument(shell):
    assert shell.link_from_args(argparse.Namespace(open_url="cuflynx://open?url=x", link=None)) == (
        "cuflynx://open?url=x"
    )
    assert shell.link_from_args(argparse.Namespace(open_url=None, link="cuflynx://open?url=y")) == (
        "cuflynx://open?url=y"
    )
    assert shell.link_from_args(argparse.Namespace(open_url=None, link=None)) is None


def test_the_shell_enables_handoffs_and_owns_the_instance_file(shell, monkeypatch, tmp_path):
    import open_link

    monkeypatch.setenv("CUFLYNX_CONFIG_DIR", str(tmp_path))
    staged = []
    monkeypatch.setattr(open_link, "stage", lambda link: staged.append(link))
    registered = []
    monkeypatch.setattr(shell.atexit, "register", lambda *a: registered.append(a))
    try:
        token = shell.start_link_handling(8790, "cuflynx://open?url=x")
        doc = open_link.read_instance()
        assert doc["port"] == 8790 and doc["token"] == token
        assert open_link.token_matches(token)
        assert registered == [(open_link.remove_instance, token)]
        for _ in range(100):
            if staged:
                break
            import time

            time.sleep(0.01)
        assert staged == ["cuflynx://open?url=x"]
    finally:
        open_link.set_token(None)


def test_a_second_plain_launch_leaves_the_live_instance_file_alone(shell, monkeypatch, tmp_path):
    import open_link

    monkeypatch.setenv("CUFLYNX_CONFIG_DIR", str(tmp_path))
    open_link.write_instance(8787, "first")
    monkeypatch.setattr(shell, "wait_for_health", lambda url, timeout=0: True)
    monkeypatch.setattr(shell.atexit, "register", lambda *a: None)
    try:
        shell.start_link_handling(8788, None)
        assert open_link.read_instance()["token"] == "first"
    finally:
        open_link.set_token(None)
