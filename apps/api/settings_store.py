"""Persist machine-level settings across restarts (user config file).

The packaged desktop app makes this load-bearing. Two settings are per-machine,
not per-session, and both are *required* before real work can happen:

* ``ca_dir`` — where circulatory_autogen lives. The frozen bundle has no sibling
  checkout to fall back on, so without persistence the user re-picks it on every
  launch.
* ``python_path`` — the interpreter that runs calibration / sensitivity / UQ.
  In the packaged app there is no default at all (``sys.executable`` is the
  bundle), so this must be remembered too.

Stored as JSON in the platform's user-config dir. ``CUFLYNX_CONFIG_DIR``
overrides the location (the test-suite points it at a tmp dir so tests never
touch — or inherit — a developer's real config).
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

# Only these keys round-trip. An explicit allowlist keeps a hand-edited or
# stale config file from injecting arbitrary state into the app.
PERSISTED_KEYS = (
    "ca_dir",
    "generated_model_format",
    "solver",
    "solver_info",
    "python_path",
    "seed",
)

CONFIG_FILENAME = "config.json"


def config_dir() -> Path:
    """Platform user-config directory for CUFLynx.

    Hand-rolled rather than via platformdirs: the backend has no such dependency
    and this is a handful of lines.
    """
    override = os.environ.get("CUFLYNX_CONFIG_DIR")
    if override:
        return Path(override)
    if sys.platform == "win32":
        base = os.environ.get("APPDATA") or (Path.home() / "AppData" / "Roaming")
        return Path(base) / "CUFLynx"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "CUFLynx"
    base = os.environ.get("XDG_CONFIG_HOME") or (Path.home() / ".config")
    return Path(base) / "cuflynx"


def config_path() -> Path:
    return config_dir() / CONFIG_FILENAME


def load() -> dict:
    """Persisted settings, or {} if absent/unreadable.

    Never raises: a corrupt config file must not stop the app from starting —
    the user would have no way to fix it from the UI.
    """
    path = config_path()
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {k: v for k, v in data.items() if k in PERSISTED_KEYS}


def save(settings: dict) -> None:
    """Merge ``settings`` into the persisted config. Best-effort.

    A read-only config dir shouldn't break an otherwise working session, so
    write failures are swallowed — the setting still applies in-memory.
    """
    merged = {**load(), **{k: v for k, v in settings.items() if k in PERSISTED_KEYS}}
    try:
        config_dir().mkdir(parents=True, exist_ok=True)
        config_path().write_text(json.dumps(merged, indent=2))
    except OSError:
        pass
