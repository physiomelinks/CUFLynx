"""Open a file in the user's own editor, on the machine the API runs on.

"Edit source" has to end with the user's editor in front of them, and a browser
cannot open a local application. So the launch is a backend action, on the same
localhost-only assumption the rest of the app documents (see CLAUDE.md,
"Security caveats"): CUFLynx is a desktop app whose server happens to speak HTTP.

Three rules shape it:

* **The user's editor, not ours.** ``$VISUAL`` then ``$EDITOR`` first, because
  that is what "my default editor" means to anyone who has ever set one, then
  the platform's default handler for the file type (``xdg-open`` / ``open`` /
  ``os.startfile``). Each candidate is *tried* in turn, so a stale ``$EDITOR``
  naming a program that is no longer installed falls through to the handler
  rather than ending the attempt.
* **Never a shell.** The command is an argv list and the path is one element of
  it; nothing is interpolated into a string and ``shell=True`` never appears.
  The path is ours to begin with (it is built from the outputs directory), and
  that is exactly why it must not become the one place that discipline lapses.
* **Detached, and never waited on.** An editor outlives the request that started
  it by definition. The child gets its own session/process group and closed
  standard streams, so it does not die with the reply and cannot fill a pipe.

Failure is a normal outcome, not an error: a headless server has no handler to
run, and the useful half of the answer — *where the file is* — is true either
way. :func:`open_in_editor` therefore returns a result rather than raising, and
the caller tells the user the path regardless.

Dependency-free apart from ``runtime_paths`` so the unit tier imports it without
FastAPI.
"""

from __future__ import annotations

import os
import shlex
import subprocess
import sys
from pathlib import Path

from runtime_paths import subprocess_env

#: Searched in order. POSIX convention: VISUAL is the full-screen editor, EDITOR
#: the line-oriented fallback, and VISUAL wins where both are set.
EDITOR_ENV_VARS = ("VISUAL", "EDITOR")


def _configured_commands(env) -> list[list[str]]:
    """``$VISUAL`` / ``$EDITOR`` as argv prefixes, in order, skipping the unset.

    Split with ``shlex`` so ``EDITOR="code -w"`` (or a quoted path with a space)
    means what its author meant. An unparseable value is skipped rather than
    guessed at.
    """
    commands: list[list[str]] = []
    for var in EDITOR_ENV_VARS:
        raw = str(env.get(var) or "").strip()
        if not raw:
            continue
        try:
            argv = [part for part in shlex.split(raw, posix=(os.name != "nt")) if part]
        except ValueError:
            continue
        if argv:
            commands.append(argv)
    return commands


def _has_display(env) -> bool:
    """Whether an X/Wayland session exists for a desktop handler to open into.

    Only asked on the ``xdg-open`` platforms. ``xdg-open`` is usually installed
    on a headless Linux box and exits successfully after failing to do anything,
    so "did Popen work" is not the question — this is. macOS has no DISPLAY and
    ``open`` needs none, so it is never consulted there.
    """
    return bool(str(env.get("DISPLAY") or "").strip() or str(env.get("WAYLAND_DISPLAY") or "").strip())


def _spawn(argv: list[str]) -> tuple[bool, str]:
    """Start ``argv`` detached. Returns ``(started, reason_it_did_not)``."""
    kwargs: dict = {
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        # The same environment scrub the analysis runners get: in the packaged
        # app this process's loader variables point into the PyInstaller bundle,
        # and an editor that inherited them would try to load the bundle's own
        # native libraries.
        "env": subprocess_env(),
    }
    if os.name == "nt":
        # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP: no console is created for
        # it, and Ctrl-C in ours does not reach it.
        kwargs["creationflags"] = 0x00000008 | 0x00000200
    else:
        kwargs["start_new_session"] = True
    try:
        subprocess.Popen(argv, **kwargs)  # noqa: S603 - argv list, never a shell
    except (OSError, ValueError) as exc:
        return False, str(exc)
    return True, ""


def open_in_editor(path, *, env=None, platform=None) -> dict:
    """Open ``path`` in the user's editor. Never raises.

    Returns ``{"opened": bool, "editor": str | None, "reason": str}`` — ``editor``
    names what was launched (for the log and the reply), and ``reason`` collects
    what every candidate said when none of them worked.

    ``env`` / ``platform`` are injectable so the tests can exercise all three
    platforms and the headless case without spawning anything.
    """
    target = str(Path(path))
    env = os.environ if env is None else env
    platform = sys.platform if platform is None else platform
    tried: list[str] = []

    for argv in _configured_commands(env):
        started, why = _spawn([*argv, target])
        if started:
            return {"opened": True, "editor": argv[0], "reason": ""}
        tried.append(f"{argv[0]}: {why}")

    if platform.startswith("win"):
        try:
            os.startfile(target)  # type: ignore[attr-defined] # noqa: S606 - Windows-only
        except (OSError, AttributeError, ValueError) as exc:
            tried.append(f"the Windows default handler: {exc}")
        else:
            return {"opened": True, "editor": "startfile", "reason": ""}
    elif platform == "darwin":
        started, why = _spawn(["open", target])
        if started:
            return {"opened": True, "editor": "open", "reason": ""}
        tried.append(f"open: {why}")
    elif not _has_display(env):
        # Say the true thing rather than launching an xdg-open that will report
        # success and open nothing.
        tried.append("xdg-open: no desktop session (DISPLAY/WAYLAND_DISPLAY unset)")
    else:
        started, why = _spawn(["xdg-open", target])
        if started:
            return {"opened": True, "editor": "xdg-open", "reason": ""}
        tried.append(f"xdg-open: {why}")

    return {
        "opened": False,
        "editor": None,
        "reason": "; ".join(tried) or "no editor or file handler could be launched here",
    }
