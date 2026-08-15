"""Extra figures an ``external_python`` model draws for itself.

A user-written solver class may implement ``extra_plots()`` — a mesh, a residual
history, whatever only the model's author knows is worth looking at. CA's
``ExternalSimulationHelper`` collects those into ``get_extra_figures() ->
list[matplotlib.figure.Figure]`` (an empty list when the class has none), and
this module is how they reach the browser: rendered to PNG under the uploads
directory and named by a URL the simulate/protocol response carries.

Three constraints shape it:

* **Agg, always.** The figures are drawn in a server process with no display,
  and a stray ``matplotlib.use('TkAgg')`` (or a default backend that opens a
  window) hangs the run. The backend is forced *before* the figures are asked
  for, not just before they are saved, because a user's ``extra_plots`` may call
  ``plt.show()``.
* **A token per run.** The same plot index is a different picture after every
  slider move, so the URL carries a per-model run counter and the browser can
  cache each one forever. Monotonic, and seeded from what is already on disk so
  a server restart cannot hand out a token that is already in a cache.
* **Two tokens kept.** The previous run's images stay readable while the new
  response is still being rendered; everything older is deleted. Nothing else
  ever cleans these up (the uploads TTL prune is a weekly backstop), so an
  afternoon of dragging sliders would otherwise be an afternoon of PNGs.

Both execution tiers converge here: in-process the engine saves the figures
itself, and with a worker the child writes the PNGs into a directory the parent
named and returns only their titles.
"""

from __future__ import annotations

import contextlib
import re
import shutil
import tempfile
from pathlib import Path

#: How many runs' images survive per model. Two, not one: the previous run's
#: URLs may still be in flight in a response the browser has not drawn yet.
KEEP_TOKENS = 2

#: Only a name that could have been produced by ``uuid4().hex`` (or a test) is
#: ever turned into a path. The route's model_id is client-supplied, and the one
#: rule for that is that a client string is never joined onto a path unchecked.
_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")

_root = Path(tempfile.gettempdir()) / "cuflynx_uploads" / "solver_plots"

#: model_id -> last token handed out. Seeded from disk on first use per model.
_tokens: dict[str, int] = {}


def set_root(path) -> None:
    """Point the store at ``path`` (``main`` puts it inside its uploads dir)."""
    global _root
    _root = Path(path)
    _tokens.clear()


def root() -> Path:
    return _root


def _model_dir(model_id: str) -> Path | None:
    if not _SAFE_ID.match(str(model_id or "")):
        return None
    return _root / str(model_id)


def next_token(model_id: str) -> int:
    """Allocate this run's token, deleting all but the newest kept runs.

    Pruning on allocation rather than after the run: the run is the thing that
    may fail, and a failed run must not leave the previous images deleted *and*
    no new ones.
    """
    model_dir = _model_dir(model_id)
    if model_dir is None:
        raise ValueError(f"unusable model id for solver plots: {model_id!r}")
    last = _tokens.get(model_id)
    if last is None:
        # Seeded from disk so a restarted server never reissues a token whose
        # images a browser still has cached under the same URL.
        last = 0
        for entry in _existing_tokens(model_dir):
            last = max(last, entry)
    token = last + 1
    _tokens[model_id] = token
    # Keep the newest KEEP_TOKENS - 1 previous runs; this run's own directory
    # makes the count up to KEEP_TOKENS.
    keep = max(KEEP_TOKENS - 1, 0)
    existing = _existing_tokens(model_dir)
    for old in (existing[:-keep] if keep else existing):
        shutil.rmtree(model_dir / str(old), ignore_errors=True)
    return token


def _existing_tokens(model_dir: Path) -> list[int]:
    """Token directories present, oldest first. Never raises."""
    tokens: list[int] = []
    try:
        entries = list(model_dir.iterdir())
    except OSError:
        return tokens
    for entry in entries:
        if entry.is_dir() and entry.name.isdigit():
            tokens.append(int(entry.name))
    return sorted(tokens)


def run_dir(model_id: str, token: int) -> Path:
    """The directory this run's PNGs live in, created."""
    model_dir = _model_dir(model_id)
    if model_dir is None:
        raise ValueError(f"unusable model id for solver plots: {model_id!r}")
    path = model_dir / str(int(token))
    path.mkdir(parents=True, exist_ok=True)
    return path


def url_for(model_id: str, token: int, index: int) -> str:
    """The route that serves one image. The one place this shape is written."""
    return f"/api/models/{model_id}/solver_plots/{int(token)}/{int(index)}.png"


def force_agg() -> None:
    """Select the headless backend, before anything draws.

    ``force=True`` because the user's module may already have chosen an
    interactive backend at import time, and a figure created under one backend
    still saves fine under Agg — a window opened under the other does not close.
    """
    try:
        import matplotlib  # noqa: PLC0415

        matplotlib.use("Agg", force=True)
    except Exception:  # noqa: BLE001 - no matplotlib means no extra figures
        pass


def figures_from_helper(helper) -> list:
    """``helper.get_extra_figures()``, or ``[]`` for a helper that has none.

    hasattr-guarded: only CA's external helper offers this, and an older CA
    without it must not turn every run into an AttributeError.
    """
    getter = getattr(helper, "get_extra_figures", None)
    if getter is None:
        return []
    force_agg()
    figures = getter()
    return list(figures or [])


def figure_title(fig, index: int) -> str:
    """The figure's suptitle, else its first axes' title, else a numbered label.

    Falling through rather than insisting on one: a one-panel figure usually
    titles the axes, a multi-panel one usually has a suptitle, and a figure with
    neither still needs something to sit above it in the UI.
    """
    supt = getattr(fig, "_suptitle", None)
    text = ""
    if supt is not None:
        with contextlib.suppress(Exception):
            text = (supt.get_text() or "").strip()
    if not text:
        for axes in getattr(fig, "axes", None) or []:
            with contextlib.suppress(Exception):
                text = (axes.get_title() or "").strip()
            if text:
                break
    return text or f"Extra plot {int(index) + 1}"


def save_figures(model_id: str, token: int, figures) -> list[dict]:
    """Write ``figures`` as ``<k>.png`` and return the response metadata.

    Returns ``[{"index", "title", "url"}]`` — the shape the simulate / protocol
    responses carry — and closes each figure: they were created in a
    long-lived process and matplotlib keeps every one it is handed.
    """
    figures = list(figures or [])
    if not figures:
        return []
    force_agg()
    target = run_dir(model_id, token)
    entries: list[dict] = []
    for index, fig in enumerate(figures):
        title = figure_title(fig, index)
        try:
            fig.savefig(target / f"{index}.png", format="png", bbox_inches="tight")
        except Exception:  # noqa: BLE001 - a figure that will not render is not a failed run
            continue
        finally:
            with contextlib.suppress(Exception):
                import matplotlib.pyplot as plt  # noqa: PLC0415

                plt.close(fig)
        entries.append({"index": index, "title": title, "url": url_for(model_id, token, index)})
    return entries


def metadata(model_id: str, token: int, entries) -> list[dict]:
    """Turn a worker's ``[{index, title}]`` reply into the response shape.

    The child writes the files and names them; the URL is the parent's to build,
    because only the parent serves them.
    """
    out: list[dict] = []
    for position, entry in enumerate(entries or []):
        if not isinstance(entry, dict):
            continue
        try:
            index = int(entry.get("index", position))
        except (TypeError, ValueError):
            index = position
        title = str(entry.get("title") or "").strip() or f"Extra plot {index + 1}"
        out.append({"index": index, "title": title, "url": url_for(model_id, token, index)})
    return out


def plot_file(model_id: str, token, index) -> Path | None:
    """The PNG for one (model, token, index), or None if it is not there.

    Every component is validated rather than trusted: the token and index must
    parse as non-negative integers and the model id must look like an upload id,
    so no client string is ever concatenated into a path.
    """
    model_dir = _model_dir(model_id)
    if model_dir is None:
        return None
    try:
        token_i = int(token)
        index_i = int(index)
    except (TypeError, ValueError):
        return None
    if token_i < 0 or index_i < 0:
        return None
    path = model_dir / str(token_i) / f"{index_i}.png"
    return path if path.is_file() else None


def clear(model_id: str | None = None) -> None:
    """Drop stored images (all of them, or one model's). Used between tests."""
    if model_id is None:
        shutil.rmtree(_root, ignore_errors=True)
        _tokens.clear()
        return
    model_dir = _model_dir(model_id)
    if model_dir is not None:
        shutil.rmtree(model_dir, ignore_errors=True)
    _tokens.pop(model_id, None)
