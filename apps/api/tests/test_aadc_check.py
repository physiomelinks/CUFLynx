"""AADC availability gating (issue #122).

AADC is optional, proprietary and licensed. CA imports it lazily, so a format
that needs it fails only once a run starts. CUFLynx therefore offers
``aadc_python`` only when the library is importable -- the same reasoning as the
OpenCOR exclusion -- and explains how to get it when it is not.
"""

from __future__ import annotations

import aadc_check
import solver_options as so


def test_status_reports_unavailable_without_the_library(monkeypatch):
    monkeypatch.setattr(aadc_check, "_importable", lambda module="aadc": False)
    monkeypatch.setattr(aadc_check, "_importable_in", lambda p, module="aadc": None)
    status = aadc_check.aadc_status()
    assert status["available"] is False
    # Not just "missing": how to obtain it, since it is licensed rather than pip-installable.
    assert "matlogica" in status["hint"].lower()
    assert status["licence_url"]


def test_available_when_either_interpreter_has_it(monkeypatch):
    """The live engine runs in-process while analysis runs in the user's Python,
    and a user may reasonably have AADC in only one."""
    monkeypatch.setattr(aadc_check, "_importable", lambda module="aadc": False)
    monkeypatch.setattr(aadc_check, "_importable_in", lambda p, module="aadc": True)
    assert aadc_check.aadc_status("/some/python")["available"] is True

    monkeypatch.setattr(aadc_check, "_importable", lambda module="aadc": True)
    monkeypatch.setattr(aadc_check, "_importable_in", lambda p, module="aadc": None)
    assert aadc_check.aadc_status()["available"] is True


def test_unknown_is_distinct_from_missing(monkeypatch):
    """No interpreter configured is not the same as "not installed there"."""
    monkeypatch.setattr(aadc_check, "_importable_in", lambda p, module="aadc": None)
    assert aadc_check.aadc_status(None)["in_analysis_python"] is None


def test_probe_does_not_import_the_licensed_library(monkeypatch):
    """Importing a licensed library can contact a licence server; a capability
    probe must not have side effects."""
    import importlib

    called = []
    monkeypatch.setattr(importlib, "import_module", lambda *a, **k: called.append(a))
    aadc_check._importable("aadc")
    assert called == []


def test_a_broken_install_counts_as_unavailable(monkeypatch):
    import importlib.util

    def boom(_name):
        raise ValueError("namespace clash")

    monkeypatch.setattr(importlib.util, "find_spec", boom)
    assert aadc_check._importable("aadc") is False


def test_a_bad_interpreter_is_unknown_not_false():
    assert aadc_check._importable_in("/no/such/python") is None
    assert aadc_check._importable_in("") is None


# ---------------------------------------------------------------------------
# Format gating
# ---------------------------------------------------------------------------
def test_the_format_is_hidden_without_the_library(client, monkeypatch):
    monkeypatch.setattr(aadc_check, "_importable", lambda module="aadc": False)
    monkeypatch.setattr(aadc_check, "_importable_in", lambda p, module="aadc": None)
    so.reset_cache()
    assert "aadc_python" not in client.get("/api/config").json()["model_formats"]


def test_the_format_appears_when_the_library_is_there(client, monkeypatch):
    monkeypatch.setattr(aadc_check, "_importable", lambda module="aadc": True)
    so.reset_cache()
    body = client.get("/api/config").json()
    formats = body["model_formats"]
    if "aadc_python" not in body.get("solvers_by_format", {}):
        # CA's schema drives which formats exist at all; an older CA without
        # aadc_python has nothing to offer and that is not this gate's business.
        return
    assert "aadc_python" in formats
    # Its solvers still come from CA, not from here.
    assert body["solvers_by_format"]["aadc_python"]


def test_config_reports_the_status_either_way(client):
    status = client.get("/api/config").json()["aadc"]
    assert set(status) >= {"available", "hint", "licence_url", "in_app"}
