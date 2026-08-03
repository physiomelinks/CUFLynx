"""The exported plotting script must actually produce the plots (issue #144).

`plot_outputs.py` ships to users as a standalone artefact and runs outside the
app entirely, so nothing here was ever executed by a test -- it could only be
checked by hand, which is why the issue asked for it to be checked thoroughly.

These tests *run* the generated script against realistic data, so "checked"
stays checked.
"""

from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
from pathlib import Path

import export_pipeline
import pytest

pytestmark = pytest.mark.filterwarnings("ignore")


def _mkdir(p: Path) -> Path:
    p.mkdir(parents=True, exist_ok=True)
    return p


def _write_script(tmp_path: Path) -> Path:
    script = tmp_path / "plot_outputs.py"
    # utf-8 explicitly, as the app now does: the script contains an em dash, and
    # on a Windows runner the default locale encoding writes it as a cp1252 byte
    # that Python then refuses to parse.
    script.write_text(export_pipeline.render_plotting_script(), encoding="utf-8")
    return script


def _run(script: Path):
    return subprocess.run(
        [sys.executable, str(script)], capture_output=True, text=True, timeout=180
    )


def _sim(out: Path, outputs: dict, time=None):
    out.mkdir(parents=True, exist_ok=True)
    time = time if time is not None else [i * 0.01 for i in range(50)]
    (out / "simulation.json").write_text(json.dumps({"time": time, "outputs": outputs}))


TRACES = {
    # Deliberately spanning the range a real model does: a pressure, a flow, and
    # a 0/1 valve state. On one shared linear axis the last two vanish.
    "aortic_root/u": [10000 + 500 * (i % 10) for i in range(50)],
    "aortic_root/v": [1e-4 * (i % 7) for i in range(50)],
    "heart/zeta_aov": [float(i % 2) for i in range(50)],
}


def test_the_script_is_valid_python():
    ast.parse(export_pipeline.render_plotting_script())


def test_it_refuses_politely_with_no_output_dir(tmp_path):
    result = _run(_write_script(tmp_path))
    assert result.returncode != 0
    assert "run_pipeline.py first" in (result.stdout + result.stderr)
    assert "Nothing to plot" in (result.stdout + result.stderr)


def _block_import(tmp_path: Path, module: str) -> None:
    """Make `import <module>` fail for a script run from tmp_path.

    The script's own directory leads sys.path, so a stub there shadows the real
    package. This reproduces a machine without the plotting stack -- which is
    what CI is, and is the environment this script most needs to behave in.
    """
    (tmp_path / f"{module}.py").write_text('raise ImportError("no %s here")' % module)


def test_the_missing_output_dir_is_reported_even_with_no_matplotlib(tmp_path):
    """The regression that broke CI. The script imported matplotlib at module
    level, so on a machine without it every run died at line 17 -- including the
    runs whose actual problem was that the pipeline had not been run yet. The
    message a user needs was unreachable exactly where it was needed most."""
    script = _write_script(tmp_path)
    _block_import(tmp_path, "matplotlib")

    result = _run(script)
    assert result.returncode != 0
    combined = result.stdout + result.stderr
    assert "run_pipeline.py first" in combined
    assert "Traceback" not in combined


def test_a_missing_matplotlib_says_what_to_install(tmp_path):
    """A user runs this in whichever Python they have. Telling them the name of
    the package beats a ModuleNotFoundError from inside a generated file."""
    script = _write_script(tmp_path)
    _sim(tmp_path / "output", TRACES)
    _block_import(tmp_path, "matplotlib")

    result = _run(script)
    assert result.returncode != 0
    combined = result.stdout + result.stderr
    assert "pip install matplotlib" in combined
    assert "Traceback" not in combined


@pytest.mark.integration
def test_it_plots_a_simulation(tmp_path):
    pytest.importorskip("matplotlib")
    _sim(tmp_path / "output", TRACES)
    result = _run(_write_script(tmp_path))
    assert result.returncode == 0, result.stderr
    assert list((tmp_path / "output" / "pyscript_plots").glob("output_plot*.png"))


@pytest.mark.integration
def test_each_variable_gets_its_own_panel(tmp_path):
    """One shared axes made everything but the largest collapse onto zero --
    pressures ~1e4 beside flows ~1e-4 -- so the export did not show what the app
    shows. A panel each is the fix, and it must survive."""
    pytest.importorskip("matplotlib")
    import matplotlib.image as mpimg

    # Enough variables to force several rows: a grid is then unmistakably taller
    # than the single 7x4in axes this replaced (~600px at 150dpi), whereas a
    # one-row case would be ambiguous.
    many = {f"m/v{i}": [float(i)] * 50 for i in range(9)}
    many.update(TRACES)
    _sim(tmp_path / "output", many)
    assert _run(_write_script(tmp_path)).returncode == 0
    pages = sorted((tmp_path / "output" / "pyscript_plots").glob("output_plot*.png"))
    assert pages
    img = mpimg.imread(pages[0])
    assert img.shape[0] > 1000, "expected a multi-row grid, not one shared axes"


@pytest.mark.integration
def test_many_variables_are_paginated(tmp_path):
    """The pipeline logs every model variable -- 456 for 3compartment -- so one
    figure would be unusable."""
    pytest.importorskip("matplotlib")
    _sim(tmp_path / "output", {f"m/v{i}": [float(i)] * 50 for i in range(30)})
    assert _run(_write_script(tmp_path)).returncode == 0
    assert len(list((tmp_path / "output" / "pyscript_plots").glob("output_plot*.png"))) > 1


@pytest.mark.integration
def test_a_protocol_run_plots_each_experiment_separately(tmp_path):
    """Experiments have their own time bases; plotting them together would put
    one experiment's trace on another's axes. This shape used to raise KeyError
    and take the whole script down."""
    pytest.importorskip("matplotlib")
    out = tmp_path / "output"
    out.mkdir(parents=True)
    (out / "simulation.json").write_text(
        json.dumps(
            {
                "experiments": [
                    {"time": [0, 1, 2], "outputs": {"m/x": [1, 2, 3]}},
                    {"time": [0, 1, 2], "outputs": {"m/x": [4, 5, 6]}},
                ]
            }
        )
    )
    assert _run(_write_script(tmp_path)).returncode == 0
    assert (out / "pyscript_plots" / "output_plot_exp0.png").is_file()
    assert (out / "pyscript_plots" / "output_plot_exp1.png").is_file()


@pytest.mark.integration
def test_it_plots_calibration_progress(tmp_path):
    pytest.importorskip("matplotlib")
    out = tmp_path / "output"
    out.mkdir(parents=True)
    (out / "best_cost_history.csv").write_text(
        "\n".join(f"{1.0 / (g + 1)},{1.2 / (g + 1)}" for g in range(10))
    )
    (out / "best_param_vals_history.csv").write_text(
        "a/b,c/d\n" + "\n".join(f"{0.1 * g},{1 - 0.01 * g}" for g in range(10))
    )
    assert _run(_write_script(tmp_path)).returncode == 0
    assert (out / "pyscript_plots" / "progress_cost.png").is_file()
    assert (out / "pyscript_plots" / "progress_params.png").is_file()


@pytest.mark.integration
def test_a_zero_cost_does_not_silently_drop_points(tmp_path):
    """A perfect fit, or a cost that can go negative, is not plottable on a log
    axis -- matplotlib would drop those points without saying so."""
    pytest.importorskip("matplotlib")
    out = tmp_path / "output"
    out.mkdir(parents=True)
    (out / "best_cost_history.csv").write_text("1.0\n0.5\n0.0\n")
    assert _run(_write_script(tmp_path)).returncode == 0
    assert (out / "pyscript_plots" / "progress_cost.png").is_file()


@pytest.mark.integration
def test_a_param_history_without_a_header_keeps_its_first_row(tmp_path):
    """Treating a numeric first line as column names ate a generation of data."""
    pytest.importorskip("matplotlib")
    out = tmp_path / "output"
    out.mkdir(parents=True)
    (out / "best_param_vals_history.csv").write_text("0.1,0.2\n0.3,0.4\n0.5,0.6\n")
    assert _run(_write_script(tmp_path)).returncode == 0
    assert (out / "pyscript_plots" / "progress_params.png").is_file()


@pytest.mark.integration
def test_it_plots_a_sensitivity_heatmap(tmp_path):
    pytest.importorskip("matplotlib")
    out = tmp_path / "output"
    out.mkdir(parents=True)
    (out / "results.json").write_text(
        json.dumps(
            {
                "output_names": ["max/m/x"],
                "param_names": ["a/b", "c/d"],
                "indices": {"ST": {"max/m/x": {"a/b": 0.7, "c/d": 0.3}}},
            }
        )
    )
    assert _run(_write_script(tmp_path)).returncode == 0
    assert (out / "pyscript_plots" / "analysis_sensitivity.png").is_file()


@pytest.mark.integration
def test_one_bad_section_does_not_lose_the_others(tmp_path):
    """A malformed results.json should not cost you the simulation plots that
    rendered perfectly well."""
    pytest.importorskip("matplotlib")
    out = tmp_path / "output"
    _sim(out, TRACES)
    (out / "results.json").write_text("{not json")
    result = _run(_write_script(tmp_path))
    assert result.returncode == 0, result.stderr
    assert list((out / "pyscript_plots").glob("output_plot*.png"))
    assert "WARNING" in result.stdout


@pytest.mark.integration
def test_an_empty_directory_is_an_error_that_says_so(tmp_path):
    """This used to succeed silently, which tells a user staring at an empty
    plots folder nothing at all. An empty run directory means either the run did
    not happen or the script is pointed at the wrong place, and both are worth
    saying."""
    pytest.importorskip("matplotlib")
    (tmp_path / "output").mkdir(parents=True)
    result = _run(_write_script(tmp_path))
    assert result.returncode != 0
    combined = result.stdout + result.stderr
    assert "Nothing to plot" in combined
    assert "--output-dir" in combined


@pytest.mark.integration
def test_partial_data_plots_what_it_can_without_complaining(tmp_path):
    """A run that produced a cost history but no simulation is not an error --
    it is a calibration. Only *nothing* is a problem."""
    pytest.importorskip("matplotlib")
    out = tmp_path / "output"
    out.mkdir(parents=True)
    (out / "best_cost_history.csv").write_text("cost\n1.0\n0.4\n")

    result = _run(_write_script(tmp_path))
    assert result.returncode == 0, result.stdout + result.stderr
    assert (out / "pyscript_plots" / "progress_cost.png").is_file()
    assert not (out / "pyscript_plots" / "output_plot_exp0.png").exists()


# ---------------------------------------------------------------------------
# Encoding: the artefact has to survive being written on the user's machine
# ---------------------------------------------------------------------------
def test_the_exported_scripts_are_written_as_utf8(client, tmp_path):
    """Both scripts contain non-ASCII (an em dash in a message). Written with the
    locale encoding -- which is what Path.write_text does by default -- a Windows
    box emits cp1252 bytes, and the script the user is handed does not parse at
    all: `SyntaxError: Non-UTF-8 code starting with '\x97'`. It broke CI first,
    but a Windows user would have hit it for real."""
    resp = client.post(
        "/api/export/plotting", json={"model_id": None, "output_dir": str(tmp_path)}
    )
    assert resp.status_code == 200, resp.text
    written = Path(resp.json()["path"])
    assert written.is_file()

    raw = written.read_bytes()
    text = raw.decode("utf-8")  # must not raise
    ast.parse(text)  # and must still be a valid script
    assert "—" in text, "the em dash this guards is gone; keep or drop the test with it"


def test_the_rendered_script_has_no_characters_that_need_a_declaration():
    """Python 3 assumes UTF-8 source, so non-ASCII is fine -- as long as what
    reaches disk really is UTF-8. This pins the pairing."""
    text = export_pipeline.render_plotting_script()
    assert text.encode("utf-8").decode("utf-8") == text


# ---------------------------------------------------------------------------
# Finding the run data, and where the plots go
# ---------------------------------------------------------------------------
def test_it_plots_a_cuflynx_run_directory_it_was_dropped_into(tmp_path):
    """The reported failure. CUFLynx writes this script into the outputs
    directory the user chose, where circulatory_autogen's run data sits in its
    own `<method>_<model>_<hash>_obs_data/` folder and there is no `output/` at
    all -- so a perfectly good calibration was met with "run run_pipeline.py
    first".
    """
    run_dir = tmp_path / "genetic_algorithm_Model_abc_obs_data"
    run_dir.mkdir()
    (run_dir / "best_cost_history.csv").write_text("cost\n1.0\n0.5\n0.2\n")

    result = _run(_write_script(tmp_path))
    assert result.returncode == 0, result.stdout + result.stderr
    assert (tmp_path / "pyscript_plots" / "progress_cost.png").is_file()


def test_output_beside_the_script_still_wins(tmp_path):
    """An exported pipeline writes into `output/`, and that layout must keep
    working exactly as it did."""
    _sim(tmp_path / "output", TRACES)
    result = _run(_write_script(tmp_path))
    assert result.returncode == 0, result.stdout + result.stderr
    assert (tmp_path / "output" / "pyscript_plots").is_dir()
    assert not (tmp_path / "pyscript_plots").exists()


def test_a_run_directory_can_be_named_on_the_command_line(tmp_path):
    script = _write_script(tmp_path)
    elsewhere = tmp_path / "some_run"
    _sim(elsewhere, TRACES)

    result = subprocess.run(
        [sys.executable, str(script), "--output-dir", str(elsewhere)],
        capture_output=True, text=True, timeout=180,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert list((elsewhere / "pyscript_plots").glob("*.png"))


def test_a_run_directory_can_come_from_the_environment(tmp_path):
    script = _write_script(tmp_path)
    elsewhere = tmp_path / "some_run"
    _sim(elsewhere, TRACES)

    env = {**os.environ, "CUFLYNX_OUTPUT_DIR": str(elsewhere)}
    result = subprocess.run(
        [sys.executable, str(script)], capture_output=True, text=True, timeout=180, env=env
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert list((elsewhere / "pyscript_plots").glob("*.png"))


def test_the_plots_do_not_land_among_the_data(tmp_path):
    """A directory of results should not gradually become a directory of results
    and pictures of results."""
    out = tmp_path / "output"
    _sim(out, TRACES)
    _run(_write_script(tmp_path))
    assert not list(out.glob("*.png")), "plots were written beside the data"
    assert list((out / "pyscript_plots").glob("*.png"))


def test_the_refusal_says_how_to_point_it_somewhere(tmp_path):
    """There is nowhere to look, so the message has to offer the way out rather
    than only naming the directory that is missing."""
    result = _run(_write_script(_mkdir(tmp_path / "empty")))
    assert result.returncode != 0
    combined = result.stdout + result.stderr
    assert "--output-dir" in combined


# ---------------------------------------------------------------------------
# Best-fit and error-bar plots, from what a calibration leaves behind
# ---------------------------------------------------------------------------
def _calibration_run(run: Path, *, module_suffix=True):
    """A run directory shaped like circulatory_autogen leaves one."""
    import numpy as np

    run.mkdir(parents=True, exist_ok=True)
    comp = "aortic_root_module" if module_suffix else "aortic_root"
    t = np.linspace(0, 2, 50)
    np.savez(
        run / "all_outputs_with_best_param_vals_exp_0.npz",
        **{
            "environment.time": t,
            f"{comp}.v": 1e-4 * (1 + np.sin(2 * np.pi * t)),
            f"{comp}.u": 12000 + 3000 * np.sin(2 * np.pi * t),
            "heart_module.q_lv": 1e-4 * np.ones_like(t),
        },
    )
    np.save(run / "percent_error_vec.npy", np.array([0.96, 4.34, -2.87]))
    np.save(run / "std_error_vec.npy", np.array([0.09, 0.43, -0.28]))
    (run / "run_obs_data_260716_105442.json").write_text(
        json.dumps(
            {
                "data_items": [
                    {"variable": "flow", "name_for_plotting": "v_{AR}", "operation": "mean",
                     "operands": ["aortic_root/v"], "value": 1e-4, "data_type": "constant"},
                    {"variable": "flow", "name_for_plotting": "v_{AR}", "operation": "max",
                     "operands": ["aortic_root/v"], "value": 5e-4, "data_type": "constant"},
                    {"variable": "pressure", "name_for_plotting": "u_{AR}", "operation": "mean",
                     "operands": ["aortic_root/u"], "value": 12000.0, "data_type": "constant"},
                ]
            }
        )
    )


@pytest.mark.integration
def test_it_plots_the_best_fit_against_the_observations(tmp_path):
    """The plots a calibration is actually judged by, drawn from the files it
    leaves behind rather than by re-running the model: the npz holds every
    variable's best-fit trace and obs_data.json says which were fitted."""
    pytest.importorskip("matplotlib")
    _calibration_run(tmp_path / "genetic_algorithm_run")

    result = _run(_write_script(tmp_path))
    assert result.returncode == 0, result.stdout + result.stderr
    assert (tmp_path / "pyscript_plots" / "best_fit_exp0.png").is_file()


@pytest.mark.integration
def test_it_plots_the_calibration_error_bars(tmp_path):
    pytest.importorskip("matplotlib")
    _calibration_run(tmp_path / "genetic_algorithm_run")

    _run(_write_script(tmp_path))
    plots = tmp_path / "pyscript_plots"
    assert (plots / "calibration_percent_error.png").is_file()
    assert (plots / "calibration_std_error.png").is_file()


@pytest.mark.integration
def test_the_module_suffix_convention_does_not_hide_the_observables(tmp_path):
    """obs_data says `aortic_root/v`; the saved npz says `aortic_root_module.v`.
    Same variable, and if the two are not reconciled every fitted observable
    silently falls out of the plot -- which is what happened first time."""
    pytest.importorskip("matplotlib")
    run = tmp_path / "genetic_algorithm_run"
    _calibration_run(run, module_suffix=True)

    _run(_write_script(tmp_path))
    # One figure for the three fitted observables, not the paginated fallback
    # that draws every variable in the file.
    assert (tmp_path / "pyscript_plots" / "best_fit_exp0.png").is_file()
    assert not list((tmp_path / "pyscript_plots").glob("best_fit_exp0_p*.png"))


@pytest.mark.integration
def test_an_unfitted_run_still_gets_its_traces(tmp_path):
    """No obs_data, or nothing matching: the traces are worth having anyway, so
    it falls back to the paginated all-variables view rather than drawing
    nothing."""
    pytest.importorskip("matplotlib")
    import numpy as np

    run = tmp_path / "run"
    run.mkdir()
    np.savez(
        run / "all_outputs_with_best_param_vals_exp_0.npz",
        **{"environment.time": np.linspace(0, 1, 20), "a.x": np.ones(20)},
    )
    result = _run(_write_script(tmp_path))
    assert result.returncode == 0, result.stdout + result.stderr
    assert list((tmp_path / "pyscript_plots").glob("best_fit_exp0*.png"))


def test_the_operation_is_part_of_the_label(tmp_path):
    """A pressure's mean, max and min are three targets on one trace. Without the
    operation the panels and bars read as three identical entries carrying
    different numbers."""
    import export_pipeline as ep

    script = ep.render_plotting_script()
    assert "_observed" in script
    # Rendered rather than executed: this is about what the label is built from.
    assert 'item.get("operation")' in script
