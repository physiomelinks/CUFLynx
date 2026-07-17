"""Tests for persisted machine-level settings (ca_dir / solver / interpreter).

Persistence exists for the packaged desktop app: it has no sibling CA checkout to
fall back on and no usable default interpreter, so without a remembered config the
user would re-pick both on every double-click.
"""

from __future__ import annotations

import json
import os
import sys

import pytest

import main as main_mod
import settings_store


@pytest.fixture
def config_dir(monkeypatch, tmp_path):
    monkeypatch.setenv("CUFLYNX_CONFIG_DIR", str(tmp_path))
    return tmp_path


# ---------------------------------------------------------------------------
# load / save
# ---------------------------------------------------------------------------
def test_save_then_load_roundtrips(config_dir):
    settings_store.save({"ca_dir": "/opt/ca", "solver": "CVODE_myokit"})
    assert settings_store.load() == {"ca_dir": "/opt/ca", "solver": "CVODE_myokit"}


def test_save_merges_rather_than_replacing(config_dir):
    settings_store.save({"ca_dir": "/opt/ca"})
    settings_store.save({"python_path": "/venv/bin/python"})
    assert settings_store.load() == {
        "ca_dir": "/opt/ca",
        "python_path": "/venv/bin/python",
    }


def test_unknown_keys_are_not_persisted(config_dir):
    """An allowlist keeps a hand-edited config from injecting arbitrary state."""
    settings_store.save({"ca_dir": "/opt/ca", "evil": "rm -rf /"})
    assert "evil" not in settings_store.load()


def test_load_is_empty_when_no_config_exists(config_dir):
    assert settings_store.load() == {}


def test_corrupt_config_does_not_raise(config_dir):
    """A corrupt file must not brick startup — the user couldn't fix it from the UI."""
    (config_dir / settings_store.CONFIG_FILENAME).write_text("{not json")
    assert settings_store.load() == {}


def test_non_dict_config_does_not_raise(config_dir):
    (config_dir / settings_store.CONFIG_FILENAME).write_text('["a", "list"]')
    assert settings_store.load() == {}


def test_save_survives_an_unwritable_config_dir(monkeypatch, tmp_path):
    """A read-only config dir shouldn't break an otherwise working session."""
    monkeypatch.setenv("CUFLYNX_CONFIG_DIR", str(tmp_path / "nested"))
    monkeypatch.setattr(
        settings_store.Path, "mkdir", lambda *a, **k: (_ for _ in ()).throw(OSError())
    )
    settings_store.save({"ca_dir": "/opt/ca"})  # must not raise


def test_config_dir_is_platform_appropriate(monkeypatch):
    monkeypatch.delenv("CUFLYNX_CONFIG_DIR", raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", "/home/u/.config")
    monkeypatch.setattr(sys, "platform", "linux")
    assert settings_store.config_dir() == settings_store.Path("/home/u/.config/cuflynx")


# ---------------------------------------------------------------------------
# Startup restore
# ---------------------------------------------------------------------------
def test_restore_applies_a_saved_ca_dir(config_dir, tmp_path, monkeypatch):
    ca = tmp_path / "circulatory_autogen"
    (ca / "src").mkdir(parents=True)
    settings_store.save({"ca_dir": str(ca)})
    monkeypatch.delenv("CIRCULATORY_AUTOGEN_SRC", raising=False)

    main_mod._restore_persisted_settings()

    assert os.environ["CIRCULATORY_AUTOGEN_SRC"] == str(ca / "src")


def test_restore_ignores_a_ca_dir_that_no_longer_exists(config_dir, monkeypatch):
    """A moved/deleted CA checkout must not stop the app from starting."""
    settings_store.save({"ca_dir": "/gone/circulatory_autogen"})
    monkeypatch.delenv("CIRCULATORY_AUTOGEN_SRC", raising=False)

    main_mod._restore_persisted_settings()

    assert "CIRCULATORY_AUTOGEN_SRC" not in os.environ


def test_restore_ignores_a_python_that_no_longer_exists(config_dir, monkeypatch):
    monkeypatch.setattr(main_mod.calibration, "python", None)
    settings_store.save({"python_path": "/gone/venv/bin/python"})

    main_mod._restore_persisted_settings()

    assert main_mod.calibration.python is None


def test_restore_points_all_three_managers_at_the_saved_interpreter(
    config_dir, tmp_path, monkeypatch
):
    fake_python = tmp_path / "python"
    fake_python.write_text("#!/bin/sh\n")
    fake_python.chmod(0o755)
    settings_store.save({"python_path": str(fake_python)})

    main_mod._restore_persisted_settings()

    assert main_mod.calibration.python == str(fake_python)
    assert main_mod.sensitivity.python == str(fake_python)
    assert main_mod.uq.python == str(fake_python)


# ---------------------------------------------------------------------------
# The /api/config route
# ---------------------------------------------------------------------------
def test_setting_a_python_path_persists_it(client, config_dir, tmp_path):
    fake_python = tmp_path / "python"
    fake_python.write_text("#!/bin/sh\n")
    fake_python.chmod(0o755)

    body = client.post("/api/config", json={"python_path": str(fake_python)}).json()

    assert body["python_path"] == str(fake_python)
    assert settings_store.load()["python_path"] == str(fake_python)
    # And it reaches the managers that actually spawn the runners.
    assert main_mod.sensitivity.python == str(fake_python)


def test_setting_a_nonexistent_python_path_is_rejected(client, config_dir):
    resp = client.post("/api/config", json={"python_path": "/gone/bin/python"})
    assert resp.status_code == 422
    assert "not found or not executable" in resp.json()["detail"]


def test_config_get_reports_the_current_interpreter(client, config_dir, tmp_path):
    fake_python = tmp_path / "python"
    fake_python.write_text("#!/bin/sh\n")
    fake_python.chmod(0o755)
    client.post("/api/config", json={"python_path": str(fake_python)})

    assert client.get("/api/config").json()["python_path"] == str(fake_python)


def test_saved_config_file_only_contains_persisted_keys(client, config_dir):
    client.post("/api/config", json={})
    saved = json.loads((config_dir / settings_store.CONFIG_FILENAME).read_text())
    assert set(saved) <= set(settings_store.PERSISTED_KEYS)


def test_clearing_python_path_resets_to_default(client, config_dir, tmp_path):
    """python_path="" resets the analysis interpreter to the default (bundled when
    packaged, serving when source), so a user can switch back to "Bundled" after
    picking a venv. python_path omitted (None) leaves it unchanged."""
    import runtime_paths

    fake_python = tmp_path / "python"
    fake_python.write_text("#!/bin/sh\n")
    fake_python.chmod(0o755)

    # Pick an external interpreter, then clear it.
    client.post("/api/config", json={"python_path": str(fake_python)})
    assert main_mod.calibration.python == str(fake_python)

    body = client.post("/api/config", json={"python_path": ""}).json()
    assert main_mod.calibration.python == runtime_paths.default_python()
    assert body["python_path"] == (runtime_paths.default_python() or "")

    # Omitting it entirely must NOT change it.
    main_mod.calibration.python = str(fake_python)
    client.post("/api/config", json={"solver": ""})
    assert main_mod.calibration.python == str(fake_python)
