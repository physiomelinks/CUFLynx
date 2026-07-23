"""Unit tests for scripts/run.py's frontend staleness check.

Regression for the "I checked out a branch/edited a file but the app still shows
the old code" trap: run.py used to rebuild the frontend only when dist/ was
absent, so a stale bundle was served after any source change.
"""

import importlib.util
import os
from pathlib import Path

import pytest

_RUN_PY = Path(__file__).resolve().parents[3] / "scripts" / "run.py"
_spec = importlib.util.spec_from_file_location("cuflynx_run", _RUN_PY)
run = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(run)


def _web(tmp_path, *, with_dist=True, built_at=1000.0, src_at=500.0):
    web = tmp_path / "web"
    src = web / "src"
    src.mkdir(parents=True)
    comp = src / "App.vue"
    comp.write_text("<template/>")
    os.utime(comp, (src_at, src_at))
    (web / "index.html").write_text("<html></html>")
    os.utime(web / "index.html", (src_at, src_at))
    dist = web / "dist"
    if with_dist:
        dist.mkdir()
        (dist / "index.html").write_text("built")
        os.utime(dist / "index.html", (built_at, built_at))
    return web, dist


def test_stale_when_dist_missing(tmp_path):
    web, dist = _web(tmp_path, with_dist=False)
    assert run.frontend_is_stale(web, dist) is True


def test_fresh_when_dist_newer_than_sources(tmp_path):
    web, dist = _web(tmp_path, built_at=2000.0, src_at=1000.0)
    assert run.frontend_is_stale(web, dist) is False


def test_stale_when_a_source_is_newer_than_dist(tmp_path):
    # Simulate a git checkout / edit touching a source file after the last build.
    web, dist = _web(tmp_path, built_at=1000.0, src_at=500.0)
    newer = web / "src" / "App.vue"
    os.utime(newer, (3000.0, 3000.0))
    assert run.frontend_is_stale(web, dist) is True


def test_stale_when_entry_html_newer_than_dist(tmp_path):
    web, dist = _web(tmp_path, built_at=1000.0, src_at=500.0)
    os.utime(web / "index.html", (3000.0, 3000.0))
    assert run.frontend_is_stale(web, dist) is True
