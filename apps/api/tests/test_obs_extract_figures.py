"""Thumbnails, and what happens without matplotlib.

matplotlib is declared only in the ``[analysis]`` extra, so a bare install may
not have it. A picture is a convenience; the numbers are not, and an extraction
must still succeed without one.
"""

from __future__ import annotations

import builtins
import os

import pytest

from obs_extract import open_recording
from obs_extract.figures import available, save, sweep_figure
from obs_extract.windows import detect_stim_window
from obs_extract_fixtures import step, write_wcp

pytestmark = pytest.mark.unit


def _recording(tmp_path, n_sweeps=2, n=200):
    sweeps = [[step(n, -70.0, -60.0 + s, lo=50, hi=150),
               step(n, 0.0, 20.0 * (s + 1), lo=50, hi=150)]
              for s in range(n_sweeps)]
    return open_recording(write_wcp(tmp_path / "a.1.Currentsteps.1.wcp", sweeps))


def test_a_figure_is_drawn_and_saved(tmp_path):
    if not available():
        pytest.skip("matplotlib is not installed")
    rec = _recording(tmp_path)
    fig = sweep_figure(rec, range(rec.sweep_count), title="a.wcp")
    path = save(fig, str(tmp_path / "out" / "a.png"))
    assert path and os.path.isfile(path)
    assert os.path.getsize(path) > 0


def test_the_stimulus_window_and_ranges_are_drawn(tmp_path):
    """The picture a selection decision is actually made from: whether the sweep
    is typical, and whether the range lands where it was meant to."""
    if not available():
        pytest.skip("matplotlib is not installed")
    rec = _recording(tmp_path)
    t, signals = rec.sweep(0)
    window = detect_stim_window(t, signals[rec.name_for_role("current")], "current")
    fig = sweep_figure(rec, [0], window=window, ranges=[(0.001, 0.005)])
    assert save(fig, str(tmp_path / "b.png"))


def test_a_long_trace_is_decimated_not_refused(tmp_path):
    if not available():
        pytest.skip("matplotlib is not installed")
    rec = _recording(tmp_path, n_sweeps=1, n=20000)
    assert save(sweep_figure(rec, [0]), str(tmp_path / "c.png"))


def test_no_matplotlib_means_no_figure_not_an_error(tmp_path, monkeypatch):
    real_import = builtins.__import__

    def without_matplotlib(name, *args, **kwargs):
        if name.startswith("matplotlib"):
            raise ImportError("no matplotlib")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", without_matplotlib)
    rec = _recording(tmp_path)
    assert sweep_figure(rec, [0]) is None
    assert save(None, str(tmp_path / "d.png")) is None
    assert available() is False


def test_a_sweep_that_will_not_decode_does_not_kill_the_figure(tmp_path, monkeypatch):
    if not available():
        pytest.skip("matplotlib is not installed")
    rec = _recording(tmp_path, n_sweeps=2)
    real_sweep = rec.sweep

    def flaky(index):
        if index == 1:
            raise RuntimeError("bad sweep")
        return real_sweep(index)

    monkeypatch.setattr(rec, "sweep", flaky)
    assert save(sweep_figure(rec, [0, 1]), str(tmp_path / "e.png"))


def test_figures_never_imports_pyplot():
    """pyplot is global, unsynchronised state, and extraction draws on a worker
    thread while the user may be previewing another row from the same process.

    Checked on the imports rather than the text, because the module's docstring
    talks about pyplot at length in order to say why it is not used.
    """
    import ast
    import inspect

    from obs_extract import figures

    tree = ast.parse(inspect.getsource(figures))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
            imported.update(f"{node.module}.{a.name}" for a in node.names)
    assert not any("pyplot" in name for name in imported), sorted(imported)
