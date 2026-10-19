"""Register CUFLynx as the handler for ``cuflynx://`` links, per platform.

CUFLynx ships as one bare executable per OS (``scripts/package.py``), with no
installer, so the app registers itself for the current user when it starts
frozen (:func:`register_if_needed`), or on ``--register-url-scheme``. Every
platform ends up running ``<CUFLynx> --open-url <link>``, which hands the link to
a running CUFLynx or starts one (``apps/desktop/app.py``, ``open_link``):

* **Linux** -- ``~/.local/share/applications/cuflynx-url-handler.desktop`` with
  ``MimeType=x-scheme-handler/cuflynx;``, made the default with ``xdg-mime``.
* **Windows** -- ``HKCU\\Software\\Classes\\cuflynx`` with ``URL Protocol`` and
  ``shell\\open\\command``.
* **macOS** -- LaunchServices only routes a URL scheme to an app *bundle* whose
  ``Info.plist`` declares it in ``CFBundleURLTypes``, and delivers the URL as an
  Apple event, not in argv. The executable is not a bundle, so this writes a
  small AppleScript applet, ``~/Applications/CUFLynx Link Handler.app``, whose
  ``on open location`` handler receives that event and runs the executable with
  ``--open-url``. Its Info.plist gets ``CFBundleURLTypes``; it is re-signed
  ad hoc (editing the plist breaks the applet's signature) and registered with
  ``lsregister``.

Registration is recorded in ``<config dir>/url_scheme.json`` with the command it
points at, so it is redone only when the executable has moved. Set
``CUFLYNX_NO_URL_SCHEME=1`` to skip the automatic registration.
"""

from __future__ import annotations

import json
import os
import plistlib
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

SCHEME = "cuflynx"
LINUX_DESKTOP_ID = "cuflynx-url-handler.desktop"
MAC_APPLET_NAME = "CUFLynx Link Handler.app"
MAC_BUNDLE_ID = "org.physiomelinks.cuflynx.linkhandler"
WINDOWS_KEY = r"Software\Classes\cuflynx"
LSREGISTER = (
    "/System/Library/Frameworks/CoreServices.framework/Frameworks/"
    "LaunchServices.framework/Support/lsregister"
)
STAMP_FILENAME = "url_scheme.json"
OPT_OUT_ENV = "CUFLYNX_NO_URL_SCHEME"


def launch_command() -> list[str]:
    """How the OS should start this CUFLynx: the executable, or Python + app.py."""
    if getattr(sys, "frozen", False):
        return [sys.executable]
    return [sys.executable, str(Path(__file__).resolve().with_name("app.py"))]


# ---------------------------------------------------------------------------
# Linux
# ---------------------------------------------------------------------------
def _exec_arg(arg: str) -> str:
    """One argument of a desktop entry's ``Exec`` key, quoted per the spec."""
    arg = arg.replace("%", "%%")
    if re.search(r"[\s\"'\\><~|&;$*?#()`]", arg):
        arg = '"' + re.sub(r'(["`$\\])', r"\\\1", arg) + '"'
    # The string-value escape applies on top of the quoting rule.
    return arg.replace("\\", "\\\\")


def linux_desktop_entry(command: list[str]) -> str:
    exec_line = " ".join(_exec_arg(a) for a in command) + " --open-url %u"
    return (
        "[Desktop Entry]\n"
        "Type=Application\n"
        "Name=CUFLynx\n"
        "Comment=Open COMBINE archives from cuflynx:// links\n"
        f"Exec={exec_line}\n"
        "Terminal=false\n"
        "NoDisplay=true\n"
        f"MimeType=x-scheme-handler/{SCHEME};\n"
    )


def register_linux(command: list[str], data_home: Path | None = None, run=subprocess.run) -> Path:
    data_home = data_home or Path(
        os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share"
    )
    apps = data_home / "applications"
    apps.mkdir(parents=True, exist_ok=True)
    entry = apps / LINUX_DESKTOP_ID
    entry.write_text(linux_desktop_entry(command), encoding="utf-8")
    run(["xdg-mime", "default", LINUX_DESKTOP_ID, f"x-scheme-handler/{SCHEME}"],
        check=True, capture_output=True)
    try:
        run(["update-desktop-database", str(apps)], check=False, capture_output=True)
    except FileNotFoundError:
        pass  # optional: xdg-mime has already set the default
    return entry


# ---------------------------------------------------------------------------
# Windows
# ---------------------------------------------------------------------------
def windows_command(command: list[str]) -> str:
    return subprocess.list2cmdline(command) + ' --open-url "%1"'


def register_windows(command: list[str], winreg=None) -> str:
    if winreg is None:
        import winreg  # noqa: PLC0415 - Windows only
    root = winreg.HKEY_CURRENT_USER
    with winreg.CreateKey(root, WINDOWS_KEY) as key:
        winreg.SetValueEx(key, "", 0, winreg.REG_SZ, "URL:CUFLynx study link")
        winreg.SetValueEx(key, "URL Protocol", 0, winreg.REG_SZ, "")
    with winreg.CreateKey(root, WINDOWS_KEY + r"\DefaultIcon") as key:
        winreg.SetValueEx(key, "", 0, winreg.REG_SZ, f'"{command[0]}",0')
    with winreg.CreateKey(root, WINDOWS_KEY + r"\shell\open\command") as key:
        winreg.SetValueEx(key, "", 0, winreg.REG_SZ, windows_command(command))
    return "HKEY_CURRENT_USER\\" + WINDOWS_KEY


# ---------------------------------------------------------------------------
# macOS
# ---------------------------------------------------------------------------
def _applescript_string(text: str) -> str:
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def mac_applet_source(command: list[str]) -> str:
    """The applet: hand each ``cuflynx://`` URL to the executable, detached."""
    shell = "nohup " + " ".join(shlex.quote(a) for a in command) + " --open-url "
    return (
        "on open location this_URL\n"
        f"\tdo shell script {_applescript_string(shell)} & quoted form of this_URL"
        ' & " > /dev/null 2>&1 &"\n'
        "end open location\n"
    )


def mac_info_plist(plist: dict) -> dict:
    """``plist`` with the URL type, identity and no Dock icon."""
    out = dict(plist)
    out["CFBundleIdentifier"] = MAC_BUNDLE_ID
    out["CFBundleName"] = "CUFLynx Link Handler"
    out["LSUIElement"] = True
    out["CFBundleURLTypes"] = [
        {"CFBundleURLName": "CUFLynx study link", "CFBundleURLSchemes": [SCHEME]}
    ]
    return out


def register_mac(command: list[str], applications: Path | None = None, run=subprocess.run) -> Path:
    applications = applications or Path.home() / "Applications"
    applications.mkdir(parents=True, exist_ok=True)
    applet = applications / MAC_APPLET_NAME
    with tempfile.TemporaryDirectory() as td:
        script = Path(td) / "handler.applescript"
        script.write_text(mac_applet_source(command), encoding="utf-8")
        if applet.exists():
            shutil.rmtree(applet)
        run(["osacompile", "-o", str(applet), str(script)], check=True, capture_output=True)
    info = applet / "Contents" / "Info.plist"
    with info.open("rb") as fh:
        plist = plistlib.load(fh)
    with info.open("wb") as fh:
        plistlib.dump(mac_info_plist(plist), fh)
    run(["codesign", "--force", "--sign", "-", str(applet)], check=False, capture_output=True)
    run([LSREGISTER, "-f", str(applet)], check=True, capture_output=True)
    return applet


# ---------------------------------------------------------------------------
# Entry points
# ---------------------------------------------------------------------------
def register(command: list[str] | None = None, platform: str | None = None) -> str:
    """Register for this platform; returns where. Raises on failure."""
    command = command or launch_command()
    platform = platform or sys.platform
    if platform.startswith("linux"):
        return str(register_linux(command))
    if platform == "win32":
        return register_windows(command)
    if platform == "darwin":
        return str(register_mac(command))
    raise RuntimeError(f"no {SCHEME}:// registration for platform {platform!r}")


def _stamp_path() -> Path:
    import settings_store  # noqa: PLC0415 - honours CUFLYNX_CONFIG_DIR at call time

    return settings_store.config_dir() / STAMP_FILENAME


def register_if_needed(command: list[str] | None = None, registrar=register,
                       stamp: Path | None = None) -> str | None:
    """Register unless already registered for this command. Never raises.

    Returns a line for the console: where it registered, or why it did not;
    None when nothing needed doing.
    """
    if os.environ.get(OPT_OUT_ENV) == "1":
        return None
    command = command or launch_command()
    stamp = stamp or _stamp_path()
    wanted = {"platform": sys.platform, "command": command}
    try:
        if json.loads(stamp.read_text(encoding="utf-8")) == wanted:
            return None
    except (OSError, ValueError):
        pass
    try:
        where = registrar(command)
    except Exception as exc:  # noqa: BLE001 - links are a convenience; the app still runs
        return f"note: could not register {SCHEME}:// links ({exc}); the app works without them."
    try:
        stamp.parent.mkdir(parents=True, exist_ok=True)
        stamp.write_text(json.dumps(wanted), encoding="utf-8")
    except OSError:
        pass
    return f"Registered {SCHEME}:// links: {where}"
