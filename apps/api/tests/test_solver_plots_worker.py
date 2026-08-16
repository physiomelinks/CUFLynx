"""The child half of the extra-figure pipeline (sim_worker_runner.py).

``sim_worker_runner`` is executed as a *file* by the user's interpreter and
cannot import the app's modules, so its figure-saving and title-picking are
duplicated from :mod:`solver_plots`. Duplication is only safe if something
notices when the copies drift — that is what these tests are.

Exercised in a subprocess because importing the runner points file descriptor 1
at stderr for the life of the process, which is correct for a worker and fatal
for a test session.
"""

from __future__ import annotations

import json
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

import solver_plots

API_DIR = Path(__file__).resolve().parents[1]

CHILD = '''
import json, sys
sys.path.insert(0, {api!r})
import sim_worker_runner as w


class Text:
    def __init__(self, text):
        self.text = text

    def get_text(self):
        return self.text


class Axes:
    def __init__(self, title):
        self.title = title

    def get_title(self):
        return self.title


class Fig:
    def __init__(self, suptitle=None, axes_title=None):
        self._suptitle = Text(suptitle) if suptitle is not None else None
        self.axes = [Axes(axes_title)] if axes_title is not None else []

    def savefig(self, path, **kwargs):
        with open(path, "wb") as fh:
            fh.write(b"png-bytes")


class Helper:
    def get_extra_figures(self):
        return [Fig(suptitle="Mesh"), Fig(axes_title="Residual"), Fig()]


out = {{
    "saved": w._save_extra_figures(Helper(), {outdir!r}),
    "titles": [
        w._figure_title(Fig(suptitle="Mesh"), 0),
        w._figure_title(Fig(axes_title="Residual"), 1),
        w._figure_title(Fig(), 2),
    ],
}}

# Also the verb-level guard: only the external backend is asked for figures.
tee = w._Tee(sys.stderr)
worker = w.Worker()
worker.model_type = "cellml"
cellml_result = {{}}
worker._add_extra_figures(cellml_result, Helper(), {{"solver_plots_dir": {outdir!r}}}, tee)
out["cellml_result"] = cellml_result

worker.model_type = w.EXTERNAL_MODEL_TYPE
external_result = {{}}
worker._add_extra_figures(external_result, Helper(), {{"solver_plots_dir": {outdir!r}}}, tee)
out["external_result"] = external_result

with open({report!r}, "w") as fh:
    json.dump(out, fh)
'''


@pytest.fixture
def child_output(tmp_path):
    outdir = tmp_path / "plots"
    report = tmp_path / "report.json"
    script = tmp_path / "child.py"
    script.write_text(
        textwrap.dedent(CHILD).format(
            api=str(API_DIR), outdir=str(outdir), report=str(report)
        )
    )
    proc = subprocess.run(
        [sys.executable, str(script)], capture_output=True, text=True, timeout=120
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(report.read_text()), outdir


def test_the_child_writes_indexed_pngs_and_returns_their_titles(child_output):
    out, outdir = child_output
    assert out["saved"] == [
        {"index": 0, "file": "0.png", "title": "Mesh"},
        {"index": 1, "file": "1.png", "title": "Residual"},
        {"index": 2, "file": "2.png", "title": "Extra plot 3"},
    ]
    for name in ("0.png", "1.png", "2.png"):
        assert (outdir / name).read_bytes() == b"png-bytes"


def test_the_childs_titles_agree_with_the_parents(child_output):
    """The rule (suptitle, else first axes title, else a numbered label) is
    written twice; a divergence would mean plots labelled differently depending
    on whether an interpreter is configured in Settings."""
    out, _ = child_output

    class _Text:
        def __init__(self, text):
            self._text = text

        def get_text(self):
            return self._text

    class _Axes:
        def __init__(self, title):
            self._title = title

        def get_title(self):
            return self._title

    class _Fig:
        def __init__(self, suptitle=None, axes_title=None):
            self._suptitle = _Text(suptitle) if suptitle is not None else None
            self.axes = [_Axes(axes_title)] if axes_title is not None else []

    parent = [
        solver_plots.figure_title(_Fig(suptitle="Mesh"), 0),
        solver_plots.figure_title(_Fig(axes_title="Residual"), 1),
        solver_plots.figure_title(_Fig(), 2),
    ]
    assert out["titles"] == parent


def test_the_childs_reply_is_what_the_parent_knows_how_to_convert(child_output):
    """The wire contract: the child sends indices and titles, the parent adds the
    URLs. Fed straight from the child's own output, so a change on either side
    of the pipe shows up here."""
    out, _ = child_output
    assert solver_plots.metadata("m1", 5, out["saved"]) == [
        {"index": 0, "title": "Mesh", "url": "/api/models/m1/solver_plots/5/0.png"},
        {"index": 1, "title": "Residual", "url": "/api/models/m1/solver_plots/5/1.png"},
        {"index": 2, "title": "Extra plot 3", "url": "/api/models/m1/solver_plots/5/2.png"},
    ]


def test_only_the_external_backend_is_asked_for_figures(child_output):
    """cellml must not pay a matplotlib import (or a hasattr probe) on every
    live run, and no other backend has figures to give."""
    out, _ = child_output
    assert out["cellml_result"] == {}
    assert out["external_result"]["solver_plots"] == out["saved"]
