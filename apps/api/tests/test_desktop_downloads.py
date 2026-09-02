"""Downloads must be enabled on the webview before the window opens.

pywebview ships ``ALLOW_DOWNLOADS`` **off**, and that default silently breaks
every download the app offers in the packaged build -- "Download calibrated
model" (#114) among them -- on all three platforms. Each backend refuses in its
own way and none of them raises anything the frontend could report:

    cocoa.py         decisionHandler(WKNavigationResponsePolicyCancel)
    edgechromium.py  args.Cancel = True
    gtk.py           never connects `download-started` at all

So the failure is invisible from the web side, and only the shell can fix it.
This pins that the shell does, and that it does so **before** ``start()`` --
GTK reads the setting in the ``BrowserView`` constructor, which runs under
``start()``, so setting it afterwards would be too late on Linux while looking
fine on macOS and Windows.

Imported by path because ``apps/desktop`` is not a package and the shell is
executed as a script -- the same approach as ``test_desktop_port.py``.
"""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

import pytest

DESKTOP_APP = Path(__file__).resolve().parents[2] / "desktop" / "app.py"


@pytest.fixture
def shell():
    """`apps/desktop/app.py`, under a name that cannot collide with `main`."""
    spec = importlib.util.spec_from_file_location("cuflynx_desktop_downloads", DESKTOP_APP)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    yield module
    sys.modules.pop(spec.name, None)


class _FakeWebview(types.ModuleType):
    """Enough of pywebview to record what the shell configured, and when."""

    def __init__(self):
        super().__init__("webview")
        self.settings = {"ALLOW_DOWNLOADS": False, "OPEN_EXTERNAL_LINKS_IN_BROWSER": True}
        self.created = []
        #: settings as they stood at the moment start() was called -- the only
        #: moment that matters for GTK.
        self.settings_at_start = None

    def create_window(self, *args, **kwargs):
        self.created.append((args, kwargs))

    def start(self, *args, **kwargs):
        self.settings_at_start = dict(self.settings)


@pytest.fixture
def fake_webview(monkeypatch):
    module = _FakeWebview()
    monkeypatch.setitem(sys.modules, "webview", module)
    return module


def _run(shell, monkeypatch, argv=("cuflynx",)):
    """Drive `main()` far enough to reach the webview, with no real server."""
    monkeypatch.setattr(sys, "argv", list(argv))
    monkeypatch.setattr(shell, "choose_port", lambda: (8787, True))
    monkeypatch.setattr(shell, "start_server", lambda port: types.SimpleNamespace(should_exit=False))
    monkeypatch.setattr(shell, "wait_for_health", lambda url: True)
    monkeypatch.setattr(shell, "warn_if_no_compiler", lambda: None)
    return shell.main()


def test_downloads_are_enabled_before_the_window_starts(shell, fake_webview, monkeypatch):
    """The regression guard: #114's download is dead in the packaged app without it."""
    assert _run(shell, monkeypatch) == 0

    assert fake_webview.settings_at_start is not None, "webview.start() was never reached"
    assert fake_webview.settings_at_start["ALLOW_DOWNLOADS"] is True


def test_external_links_still_open_in_the_system_browser(shell, fake_webview, monkeypatch):
    """OPEN_EXTERNAL_LINKS_IN_BROWSER must stay True.

    Flipping it navigates the CUFLynx window itself to the target instead of
    handing it to the browser, so sending a study to PhLynx would replace the app
    with PhLynx. Sending relies on this default (#340), which makes it a contract
    rather than an incidental setting."""
    assert _run(shell, monkeypatch) == 0

    assert fake_webview.settings_at_start["OPEN_EXTERNAL_LINKS_IN_BROWSER"] is True
