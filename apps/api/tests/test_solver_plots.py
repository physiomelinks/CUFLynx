"""The extra-figure pipeline for external_python models.

Unit tier: the figures are stubs and the PNGs are written directly, so the store,
the pruning rule, the URL shape and the route are all exercised without a solver.
The real helper -> figure path is covered by the integration tests.
"""

from pathlib import Path

import pytest

import engine as engine_mod
import solver_plots


class StubFigure:
    """Enough of a matplotlib Figure for the store: a title and a savefig."""

    def __init__(self, suptitle=None, axes_title=None, body=b"png-bytes"):
        self._suptitle = _Text(suptitle) if suptitle is not None else None
        self.axes = [_Axes(axes_title)] if axes_title is not None else []
        self._body = body
        self.saved_to = None

    def savefig(self, path, **_kwargs):
        self.saved_to = Path(path)
        Path(path).write_bytes(self._body)


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


@pytest.fixture(autouse=True)
def clean_store():
    solver_plots.clear()
    yield
    solver_plots.clear()


# ---------------------------------------------------------------------------
# Store: tokens, pruning, titles
# ---------------------------------------------------------------------------
def test_tokens_increase_per_model():
    assert solver_plots.next_token("m1") == 1
    assert solver_plots.next_token("m1") == 2
    # Per model, not global: two models must not share a counter.
    assert solver_plots.next_token("m2") == 1


def test_the_token_is_seeded_from_disk_after_a_restart():
    """A restarted server that reissued token 1 would collide with whatever the
    browser already cached under that URL."""
    solver_plots.run_dir("m1", 7)
    solver_plots._tokens.clear()  # the restart: memory gone, disk intact
    assert solver_plots.next_token("m1") == 8


def test_only_the_last_two_runs_survive():
    for _ in range(3):
        token = solver_plots.next_token("m1")
        solver_plots.save_figures("m1", token, [StubFigure()])
    kept = sorted(p.name for p in (solver_plots.root() / "m1").iterdir())
    assert kept == ["2", "3"]


def test_a_run_that_draws_nothing_does_not_delete_the_previous_images():
    token = solver_plots.next_token("m1")
    solver_plots.save_figures("m1", token, [StubFigure()])
    solver_plots.next_token("m1")  # the next run, which draws nothing
    assert (solver_plots.root() / "m1" / str(token) / "0.png").is_file()


def test_save_figures_returns_the_response_shape():
    token = solver_plots.next_token("m1")
    meta = solver_plots.save_figures("m1", token, [StubFigure(suptitle="Mesh"), StubFigure()])
    assert meta == [
        {"index": 0, "title": "Mesh", "url": f"/api/models/m1/solver_plots/{token}/0.png"},
        {"index": 1, "title": "Extra plot 2", "url": f"/api/models/m1/solver_plots/{token}/1.png"},
    ]


def test_no_figures_means_no_metadata_and_no_directory():
    token = solver_plots.next_token("m1")
    assert solver_plots.save_figures("m1", token, []) == []
    assert not (solver_plots.root() / "m1" / str(token)).exists()


@pytest.mark.parametrize(
    "figure,expected",
    [
        (StubFigure(suptitle="Suptitle", axes_title="Axes"), "Suptitle"),
        (StubFigure(axes_title="Axes"), "Axes"),
        (StubFigure(suptitle="  ", axes_title="Axes"), "Axes"),
        (StubFigure(), "Extra plot 1"),
    ],
)
def test_figure_title_falls_through_suptitle_axes_default(figure, expected):
    assert solver_plots.figure_title(figure, 0) == expected


def test_metadata_from_a_worker_reply():
    """The child names the files; the URL is the parent's, because only the
    parent serves them."""
    meta = solver_plots.metadata("m1", 4, [{"index": 0, "file": "0.png", "title": "Mesh"},
                                           {"index": 1, "file": "1.png", "title": ""}])
    assert meta == [
        {"index": 0, "title": "Mesh", "url": "/api/models/m1/solver_plots/4/0.png"},
        {"index": 1, "title": "Extra plot 2", "url": "/api/models/m1/solver_plots/4/1.png"},
    ]


@pytest.mark.parametrize("model_id", ["../etc", "a/b", "", ".."])
def test_an_unusable_model_id_never_becomes_a_path(model_id):
    assert solver_plots.plot_file(model_id, 1, 0) is None
    with pytest.raises(ValueError):
        solver_plots.next_token(model_id)


# ---------------------------------------------------------------------------
# Route
# ---------------------------------------------------------------------------
def test_route_404s_before_anything_has_been_drawn(client):
    assert client.get("/api/models/m1/solver_plots/1/0.png").status_code == 404


def test_route_serves_a_saved_png(client):
    token = solver_plots.next_token("m1")
    solver_plots.save_figures("m1", token, [StubFigure(body=b"the-image")])
    resp = client.get(f"/api/models/m1/solver_plots/{token}/0.png")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "image/png"
    assert resp.content == b"the-image"


def test_route_404s_for_an_index_that_was_not_drawn(client):
    token = solver_plots.next_token("m1")
    solver_plots.save_figures("m1", token, [StubFigure()])
    assert client.get(f"/api/models/m1/solver_plots/{token}/1.png").status_code == 404


def test_route_404s_for_a_pruned_token(client):
    """Only the last two runs are kept, and asking for an older one is a 404
    rather than an error: it is an ordinary consequence of the prune."""
    tokens = []
    for _ in range(3):
        token = solver_plots.next_token("m1")
        solver_plots.save_figures("m1", token, [StubFigure()])
        tokens.append(token)
    assert client.get(f"/api/models/m1/solver_plots/{tokens[0]}/0.png").status_code == 404
    assert client.get(f"/api/models/m1/solver_plots/{tokens[-1]}/0.png").status_code == 200


@pytest.mark.parametrize("token,index", [("abc", "0"), ("1", "abc"), ("-1", "0")])
def test_route_404s_on_a_non_integer_segment(client, token, index):
    assert client.get(f"/api/models/m1/solver_plots/{token}/{index}.png").status_code == 404


# ---------------------------------------------------------------------------
# Engine wiring
# ---------------------------------------------------------------------------
class PlottingHelper:
    """A FakeHelper that also draws — i.e. CA's external helper's surface."""

    def __init__(self, figures=1):
        self.figures = [StubFigure(suptitle=f"Fig {i}") for i in range(figures)]
        self.extra_calls = 0

    def reset_and_clear(self):
        pass

    def update_times(self, *_a, **_k):
        pass

    def set_param_vals(self, names, vals):
        pass

    def run(self):
        return True

    def get_time(self, include_pre_time=False):
        return [0.0, 1.0]

    def get_results(self, variables, flatten=False):
        return [[1.0, 2.0]]

    def get_extra_figures(self):
        self.extra_calls += 1
        return list(self.figures)


def _simulate(model_id="mplots", outputs=("heat/T_p1",)):
    return engine_mod.engine.simulate(
        model_id=model_id,
        model_path="/tmp/user_model.py",
        params={},
        sim_time=1.0,
        pre_time=0.0,
        outputs=list(outputs),
    )


def test_simulate_attaches_solver_plots_for_an_external_model():
    helper = PlottingHelper(figures=2)
    engine_mod.engine.model_type = "external_python"
    engine_mod.engine.solver = "external"
    engine_mod.engine.helper_factory = lambda **_kw: helper
    result = _simulate()
    assert helper.extra_calls == 1
    assert [p["title"] for p in result["solver_plots"]] == ["Fig 0", "Fig 1"]
    assert result["solver_plots"][0]["url"].startswith("/api/models/mplots/solver_plots/")
    # And the files are actually there for the route to serve.
    for entry in result["solver_plots"]:
        token = entry["url"].split("/")[-2]
        assert (solver_plots.root() / "mplots" / token / f"{entry['index']}.png").is_file()


def test_a_model_that_draws_nothing_carries_no_solver_plots_field():
    """The field is present only when this run produced figures — an empty list
    would have the client render an empty gallery."""
    helper = PlottingHelper(figures=0)
    engine_mod.engine.model_type = "external_python"
    engine_mod.engine.helper_factory = lambda **_kw: helper
    assert "solver_plots" not in _simulate()


def test_other_backends_are_never_asked_for_figures(fake_helper):
    """cellml_only stays as it was: no matplotlib import, no hasattr probe on
    every live run."""
    result = _simulate()
    assert "solver_plots" not in result


def test_worker_results_are_converted_to_urls():
    """The parent's half of the worker path: the child returns titles, the
    parent turns them into the same shape the in-process path produces."""
    result = {"time": [0.0], "outputs": {},
              "solver_plots": [{"index": 0, "file": "0.png", "title": "Mesh"}]}
    engine_mod.engine._attach_remote_plots(result, "m1", 3)
    assert result["solver_plots"] == [
        {"index": 0, "title": "Mesh", "url": "/api/models/m1/solver_plots/3/0.png"}
    ]
