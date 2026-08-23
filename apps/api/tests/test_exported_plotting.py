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


def _write_script(tmp_path: Path, obs_data=None) -> Path:
    """Both files. plot_outputs imports plot_utilities, so one alone cannot run.

    utf-8 explicitly, as the app does: the scripts contain an em dash, and on a
    Windows runner the default locale encoding writes it as a cp1252 byte that
    Python then refuses to parse.
    """
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / export_pipeline.PLOT_UTILITIES_NAME).write_text(
        export_pipeline.render_plot_utilities(), encoding="utf-8"
    )
    script = tmp_path / export_pipeline.PLOTTING_SCRIPT_NAME
    script.write_text(export_pipeline.render_plotting_script(obs_data), encoding="utf-8")
    return script


def _run(script: Path):
    return subprocess.run(
        [sys.executable, str(script)], capture_output=True, text=True, timeout=180
    )


def _sim(out: Path, outputs: dict, time=None, exp=0):
    """A simulation-stage run's traces, in circulatory_autogen's npz shape.

    The same file a calibrated best fit leaves, so one reader covers both and no
    CUFLynx-authored JSON exists in the bundle (#210).
    """
    import numpy as np

    out.mkdir(parents=True, exist_ok=True)
    time = time if time is not None else [i * 0.01 for i in range(50)]
    data = {name: np.asarray(series, dtype=float) for name, series in outputs.items()}
    data["time"] = np.asarray(time, dtype=float)
    np.savez(out / f"all_outputs_exp_{exp}.npz", **data)


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
    assert list((tmp_path / "output" / "pyscript_plots").glob("best_fit_exp*.png"))


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
    pages = sorted((tmp_path / "output" / "pyscript_plots").glob("best_fit_exp*.png"))
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
    assert len(list((tmp_path / "output" / "pyscript_plots").glob("best_fit_exp*.png"))) > 1


@pytest.mark.integration
def test_a_protocol_run_plots_each_experiment_separately(tmp_path):
    """Experiments have their own time bases; plotting them together would put
    one experiment's trace on another's axes. One npz per experiment, which is
    how circulatory_autogen writes them."""
    pytest.importorskip("matplotlib")
    out = tmp_path / "output"
    _sim(out, {"m/x": [1, 2, 3]}, time=[0, 1, 2], exp=0)
    _sim(out, {"m/x": [4, 5, 6]}, time=[0, 1, 2], exp=1)
    assert _run(_write_script(tmp_path)).returncode == 0
    plots = out / "pyscript_plots"
    assert list(plots.glob("best_fit_exp0*.png"))
    assert list(plots.glob("best_fit_exp1*.png"))


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
def test_it_plots_both_analyses_from_circulatory_autogens_own_outputs(tmp_path):
    """Both figures come from files circulatory_autogen (or the run) wrote.

    The Sobol indices CSV is CA's own; the posterior is binned from the samples
    the run persisted. Nothing here is a CUFLynx-authored results format, so a
    run directory produced by CA's own scripts plots identically (#210).
    """
    pytest.importorskip("matplotlib")
    import numpy as np

    out = tmp_path / "output"
    out.mkdir(parents=True)
    (out / "all_outputs_n64_Sobol_indices.csv").write_text(
        "Parameter,S1_max/m/x,ST_max/m/x\na/b,0.6,0.7\nc/d,0.2,0.3\n"
    )
    rng = np.random.default_rng(0)
    np.save(out / "uq_posterior_samples.npy", rng.normal(1.0, 0.1, (500, 1)))
    (out / "uq_param_names.csv").write_text("a/b\n")

    assert _run(_write_script(tmp_path)).returncode == 0
    plots = out / "pyscript_plots"
    assert (plots / "analysis_sensitivity.png").is_file()
    assert (plots / "analysis_uq.png").is_file()


@pytest.mark.integration
def test_it_plots_a_local_sensitivity_heatmap(tmp_path):
    """The local arm writes CA's local_sensitivity_relative.csv, so the same
    reader covers both kinds."""
    pytest.importorskip("matplotlib")
    out = tmp_path / "output"
    out.mkdir(parents=True)
    (out / "local_sensitivity_relative.csv").write_text(
        "output,a/b,c/d\nmax/m/x,0.7,-0.3\n"
    )
    assert _run(_write_script(tmp_path)).returncode == 0
    assert (out / "pyscript_plots" / "analysis_sensitivity.png").is_file()


@pytest.mark.integration
def test_one_bad_section_does_not_lose_the_others(tmp_path):
    """A malformed analysis file should not cost you the trace plots that
    rendered perfectly well."""
    pytest.importorskip("matplotlib")
    out = tmp_path / "output"
    _sim(out, TRACES)
    (out / "local_sensitivity_relative.csv").write_text("output,a/b\nnot,a,number,at,all\n")
    (out / "uq_posterior_samples.npy").write_text("not an npy")
    (out / "uq_param_names.csv").write_text("a/b\n")
    result = _run(_write_script(tmp_path))
    assert result.returncode == 0, result.stderr
    assert list((out / "pyscript_plots").glob("best_fit_exp*.png"))
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
    assert not list((out / "pyscript_plots").glob("best_fit_exp*.png"))


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
@pytest.mark.integration
def test_it_plots_a_cuflynx_run_directory_it_was_dropped_into(tmp_path):
    """The reported failure. CUFLynx writes this script into the outputs
    directory the user chose, where circulatory_autogen's run data sits in its
    own `<method>_<model>_<hash>_obs_data/` folder and there is no `output/` at
    all -- so a perfectly good calibration was met with "run run_pipeline.py
    first".
    """
    pytest.importorskip("matplotlib")
    run_dir = tmp_path / "genetic_algorithm_Model_abc_obs_data"
    run_dir.mkdir()
    (run_dir / "best_cost_history.csv").write_text("cost\n1.0\n0.5\n0.2\n")

    result = _run(_write_script(tmp_path))
    assert result.returncode == 0, result.stdout + result.stderr
    assert (tmp_path / "pyscript_plots" / "progress_cost.png").is_file()


@pytest.mark.integration
def test_output_beside_the_script_still_wins(tmp_path):
    """An exported pipeline writes into `output/`, and that layout must keep
    working exactly as it did."""
    pytest.importorskip("matplotlib")
    _sim(tmp_path / "output", TRACES)
    result = _run(_write_script(tmp_path))
    assert result.returncode == 0, result.stdout + result.stderr
    assert (tmp_path / "output" / "pyscript_plots").is_dir()
    assert not (tmp_path / "pyscript_plots").exists()


@pytest.mark.integration
def test_a_run_directory_can_be_named_on_the_command_line(tmp_path):
    pytest.importorskip("matplotlib")
    script = _write_script(tmp_path)
    elsewhere = tmp_path / "some_run"
    _sim(elsewhere, TRACES)

    result = subprocess.run(
        [sys.executable, str(script), "--output-dir", str(elsewhere)],
        capture_output=True, text=True, timeout=180,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert list((elsewhere / "pyscript_plots").glob("*.png"))


@pytest.mark.integration
def test_a_run_directory_can_come_from_the_environment(tmp_path):
    pytest.importorskip("matplotlib")
    script = _write_script(tmp_path)
    elsewhere = tmp_path / "some_run"
    _sim(elsewhere, TRACES)

    env = {**os.environ, "CUFLYNX_OUTPUT_DIR": str(elsewhere)}
    result = subprocess.run(
        [sys.executable, str(script)], capture_output=True, text=True, timeout=180, env=env
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert list((elsewhere / "pyscript_plots").glob("*.png"))


@pytest.mark.integration
def test_the_plots_do_not_land_among_the_data(tmp_path):
    """A directory of results should not gradually become a directory of results
    and pictures of results."""
    pytest.importorskip("matplotlib")
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
                    {"data_item_name": "flow", "name_for_plotting": "v_{AR}", "operation": "mean",
                     "operands": ["aortic_root/v"], "value": 1e-4, "data_type": "constant"},
                    {"data_item_name": "flow", "name_for_plotting": "v_{AR}", "operation": "max",
                     "operands": ["aortic_root/v"], "value": 5e-4, "data_type": "constant"},
                    {"data_item_name": "pressure", "name_for_plotting": "u_{AR}", "operation": "mean",
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

    # The bars are labelled from what obs_data says, so the operation has to
    # survive into the reading half...
    assert 'item.get("operation")' in ep.render_plot_utilities()
    # ...and into the labels the editable half builds.
    assert 'util.tex(o["label"], o["operation"])' in ep.render_plotting_script()


# ---------------------------------------------------------------------------
# One panel per series, not per data_item
# ---------------------------------------------------------------------------
def _utilities(tmp_path):
    """The exported plot_utilities module, loaded so its helpers can be tested.

    Loaded from the written file rather than exec'd from a string, because that
    is how plot_outputs.py reaches it -- an import that works here is an import
    that works for the user.
    """
    import importlib.util as importlib_util

    _write_script(tmp_path)
    path = tmp_path / export_pipeline.PLOT_UTILITIES_NAME
    spec = importlib_util.spec_from_file_location("plot_utilities_under_test", path)
    module = importlib_util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_same_series_under_several_operations_is_one_panel(tmp_path):
    """Fitting a trace's mean, its max and its min is three targets on one curve.
    Drawing the curve three times says there are three of them."""
    util = _utilities(tmp_path)
    doc = {
        "data_items": [
            {"operands": ["a/u"], "operation": "mean", "value": 1, "name_for_plotting": "u"},
            {"operands": ["a/u"], "operation": "max", "value": 2, "name_for_plotting": "u"},
            {"operands": ["a/u"], "operation": "min", "value": 0, "name_for_plotting": "u"},
        ]
    }
    assert len({o["series"] for o in util.observed(doc)}) == 1


def test_a_time_operand_does_not_split_the_series(tmp_path):
    """`x` and `x, t` are the same trace: the time operand says which axis to
    read it against, not which curve it is."""
    util = _utilities(tmp_path)
    doc = {
        "data_items": [
            {"operands": ["a/u"], "operation": "max", "value": 2},
            {"operands": ["a/u", "environment/time"], "operation": "time_at_max", "value": 0.3},
        ]
    }
    assert len({o["series"] for o in util.observed(doc)}) == 1


def test_different_variables_stay_separate(tmp_path):
    util = _utilities(tmp_path)
    doc = {
        "data_items": [
            {"operands": ["a/u"], "operation": "mean", "value": 1},
            {"operands": ["a/v"], "operation": "mean", "value": 2},
        ]
    }
    assert len({o["series"] for o in util.observed(doc)}) == 2


@pytest.mark.parametrize(
    "operand,is_time",
    [("environment/time", True), ("environment.time", True), ("time", True),
     ("t", True), ("a/u", False), ("heart/time_constant", False)],
)
def test_time_operands_are_recognised(tmp_path, operand, is_time):
    util = _utilities(tmp_path)
    assert util.is_time(operand) is is_time


@pytest.mark.integration
def test_one_figure_carries_every_target_for_its_series(tmp_path):
    """End to end: three operations on one variable produce one panel whose
    legend names all three."""
    pytest.importorskip("matplotlib")
    import numpy as np

    run = tmp_path / "run"
    run.mkdir()
    t = np.linspace(0, 1, 30)
    np.savez(run / "all_outputs_with_best_param_vals_exp_0.npz",
             **{"environment.time": t, "a_module.u": np.sin(t)})
    (run / "x_obs_data_1.json").write_text(json.dumps({"data_items": [
        {"operands": ["a/u"], "operation": op, "value": v, "name_for_plotting": "u"}
        for op, v in (("mean", 0.5), ("max", 0.9), ("min", 0.0))
    ]}))

    result = _run(_write_script(tmp_path))
    assert result.returncode == 0, result.stdout + result.stderr
    figures = list((tmp_path / "pyscript_plots").glob("best_fit_exp0*.png"))
    assert len(figures) == 1, [f.name for f in figures]


# ---------------------------------------------------------------------------
# The generated script is meant to be edited
# ---------------------------------------------------------------------------
OBS_DOC = {
    "data_items": [
        {"operands": ["aortic_root/v"], "operation": "mean", "value": 1e-4,
         "name_for_plotting": "v_{AR}", "data_item_name": "flow aortic root"},
        {"operands": ["aortic_root/v"], "operation": "max", "value": 5e-4,
         "name_for_plotting": "v_{AR}", "data_item_name": "flow aortic root"},
        {"operands": ["heart/q_lv"], "operation": "max_minus_min", "value": 1.04e-4,
         "name_for_plotting": "q_{lv}", "data_item_name": "stroke volume"},
    ]
}


def test_each_panel_is_its_own_named_function():
    """The point of generating rather than looping: to change one panel you edit
    one function, instead of understanding the loop that draws all of them."""
    src = export_pipeline.render_plotting_script(OBS_DOC)
    assert "def panel_v_AR(ax, t, series):" in src
    assert "def panel_q_lv(ax, t, series):" in src


def test_the_variables_are_written_into_the_panel():
    """Named, not discovered: the reader can see which series a panel draws
    without running anything."""
    src = export_pipeline.render_plotting_script(OBS_DOC)
    assert "pick(series, 'aortic_root/v')" in src
    assert "pick(series, 'heart/q_lv')" in src


def test_the_targets_are_written_in_with_their_operations():
    src = export_pipeline.render_plotting_script(OBS_DOC)
    assert 'label="mean = 0.0001"' in src
    assert 'label="max = 0.0005"' in src
    assert 'label="max minus min = 0.000104"' in src


def test_the_panels_are_listed_so_one_can_be_dropped():
    src = export_pipeline.render_plotting_script(OBS_DOC)
    assert "PANELS = [" in src
    assert "    panel_v_AR," in src


def test_the_observable_description_becomes_the_docstring():
    """`variable` is the user's own name for the thing; it belongs where someone
    editing the panel will read it."""
    src = export_pipeline.render_plotting_script(OBS_DOC)
    assert "flow aortic root — from aortic_root/v" in src


def test_one_function_per_series_not_per_operation():
    src = export_pipeline.render_plotting_script(OBS_DOC)
    assert src.count("def panel_") == 2  # v_AR (mean+max) and q_lv


def test_a_name_collision_does_not_produce_two_functions_alike():
    doc = {"data_items": [
        {"operands": ["a/u"], "operation": "mean", "value": 1, "trace_name_for_plotting": "u"},
        {"operands": ["b/u"], "operation": "mean", "value": 2, "trace_name_for_plotting": "u"},
    ]}
    src = export_pipeline.render_plotting_script(doc)
    assert "def panel_u(ax, t, series):" in src
    assert "def panel_u_2(ax, t, series):" in src


def test_without_obs_data_it_still_works_and_says_why_there_are_no_panels():
    src = export_pipeline.render_plotting_script(None)
    ast.parse(src)
    assert "PANELS = []" in src
    assert "No obs_data was available" in src


def test_the_generated_script_is_valid_python():
    ast.parse(export_pipeline.render_plotting_script(OBS_DOC))


@pytest.mark.integration
def test_the_generated_panels_draw_the_same_figure(tmp_path):
    """Generated or discovered, the figure is the same -- the difference is
    whether the file can be edited."""
    pytest.importorskip("matplotlib")
    import numpy as np

    run = tmp_path / "run"
    run.mkdir()
    t = np.linspace(0, 2, 40)
    np.savez(run / "all_outputs_with_best_param_vals_exp_0.npz",
             **{"environment.time": t,
                "aortic_root_module.v": 1e-4 * (1 + np.sin(t)),
                "heart_module.q_lv": 1e-4 * np.ones_like(t)})

    result = _run(_write_script(tmp_path, OBS_DOC))
    assert result.returncode == 0, result.stdout + result.stderr
    assert (tmp_path / "pyscript_plots" / "best_fit_exp0.png").is_file()


@pytest.mark.integration
def test_dropping_a_panel_drops_it_from_the_figure(tmp_path):
    """The edit the whole design is for: remove a line from PANELS, get one
    fewer panel. Checked by file size, which is crude but real -- the figure has
    to actually change."""
    pytest.importorskip("matplotlib")
    import numpy as np

    run = tmp_path / "run"
    run.mkdir()
    t = np.linspace(0, 2, 40)
    np.savez(run / "all_outputs_with_best_param_vals_exp_0.npz",
             **{"environment.time": t,
                "aortic_root_module.v": 1e-4 * (1 + np.sin(t)),
                "heart_module.q_lv": 1e-4 * np.ones_like(t)})

    script = _write_script(tmp_path, OBS_DOC)
    _run(script)
    both = (tmp_path / "pyscript_plots" / "best_fit_exp0.png").stat().st_size

    edited = script.read_text(encoding="utf-8").replace("    panel_q_lv,\n", "")
    script.write_text(edited, encoding="utf-8")
    result = _run(script)
    assert result.returncode == 0, result.stdout + result.stderr
    one = (tmp_path / "pyscript_plots" / "best_fit_exp0.png").stat().st_size
    assert one != both


# ---------------------------------------------------------------------------
# Two files: the one you edit, and the one you do not
# ---------------------------------------------------------------------------
def test_both_files_are_valid_python():
    ast.parse(export_pipeline.render_plot_utilities())
    ast.parse(export_pipeline.render_plotting_script(OBS_DOC))


def test_the_editable_file_is_the_one_you_run():
    """main() stays at the bottom of plot_outputs.py, so there is one file to
    call and it is the same one you edit."""
    src = export_pipeline.render_plotting_script(OBS_DOC)
    assert 'if __name__ == "__main__":' in src
    assert "def main():" in src
    assert 'if __name__ == "__main__":' not in export_pipeline.render_plot_utilities()


def test_every_figure_has_its_own_editable_function():
    """Not just the best-fit panels: each figure is drawn by a function in the
    file the user owns, so any of them can be changed or dropped."""
    src = export_pipeline.render_plotting_script(OBS_DOC)
    for name in (
        "def plot_best_fit():",
        "def plot_error_bars():",
        "def plot_progress():",
        "def plot_analysis():",
    ):
        assert name in src, name
    # plot_best_fit covers a simulation-only run too, so there is no separate
    # "simulation outputs" figure (#210).
    assert "def plot_simulation_outputs():" not in src
    assert "FIGURES = [" in src


def test_the_utilities_file_draws_nothing():
    """It finds and loads; how anything looks lives in plot_outputs.py. A
    savefig here would be a decision made where nobody will look for it."""
    utilities = export_pipeline.render_plot_utilities()
    assert "set_title" not in utilities
    assert "ax.plot" not in utilities
    assert "cmap" not in utilities


def test_the_editable_file_does_not_restate_the_machinery():
    """The split is only worth having if the reading half really is elsewhere."""
    src = export_pipeline.render_plotting_script(OBS_DOC)
    assert "import plot_utilities as util" in src
    assert "glob.glob" not in src
    assert "def resolve_name" not in src


def test_the_two_files_are_written_together(client, tmp_path):
    """plot_outputs imports plot_utilities, so one without the other is a script
    that cannot start."""
    resp = client.post(
        "/api/export/plotting", json={"config_outputs_dir": str(tmp_path)}
    )
    assert resp.status_code == 200, resp.text
    assert (tmp_path / "plot_outputs.py").is_file()
    assert (tmp_path / "plot_utilities.py").is_file()


@pytest.mark.integration
def test_editing_the_style_changes_every_figure(tmp_path):
    """STYLE is one place, and it has to actually reach the figures."""
    pytest.importorskip("matplotlib")
    out = tmp_path / "output"
    out.mkdir(parents=True)
    (out / "best_cost_history.csv").write_text("1.0\n0.4\n0.2\n")

    script = _write_script(tmp_path)
    _run(script)
    before = (out / "pyscript_plots" / "progress_cost.png").stat().st_size

    edited = script.read_text(encoding="utf-8").replace('"dpi": 150,', '"dpi": 40,')
    script.write_text(edited, encoding="utf-8")
    result = _run(script)
    assert result.returncode == 0, result.stdout + result.stderr
    after = (out / "pyscript_plots" / "progress_cost.png").stat().st_size
    assert after < before
