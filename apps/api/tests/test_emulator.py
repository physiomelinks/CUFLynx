"""Emulator training and use (circulatory_autogen #333).

The emulator is the one feature where a wrong answer looks exactly like a right
one: every analysis still runs, still finishes, still produces indices and
posteriors -- from a surrogate that may be nonsense. So most of what is tested
here is refusal and provenance, not happy paths.
"""

import json
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# Where a bundle lives, and what CUFLynx reads out of it
# ---------------------------------------------------------------------------
def test_emulator_dir_matches_cas_own_rule(tmp_path):
    """Both sides derive the path; neither remembers it.

    CA's trainer builds <outputs>/emulators/<file_prefix>_<obs_prefix>. If
    CUFLynx computed it differently, training would write one place and every
    later run would look in another -- and report "no emulator" forever.
    """
    import ca_run_history

    got = ca_run_history.emulator_dir(
        str(tmp_path), "3compartment", "/some/where/3compartment_obs_data.json"
    )
    # By path components, not by a slash-joined string: os.path.join gives
    # backslashes on Windows, and the rule being checked is about the names, not
    # about which separator the platform writes them with.
    assert Path(got).parts[-2:] == ("emulators", "3compartment_3compartment_obs_data")


def test_emulator_dir_without_an_obs_path_still_resolves(tmp_path):
    import ca_run_history

    got = ca_run_history.emulator_dir(str(tmp_path), "model", None)
    assert Path(got).parts[-2:] == ("emulators", "model_obs")


def test_metadata_is_none_when_nothing_has_been_trained(tmp_path):
    """The normal starting state, not an error: most studies have no emulator."""
    import ca_run_history

    assert ca_run_history.emulator_metadata(str(tmp_path)) is None


def test_metadata_surfaces_the_worst_r2(tmp_path):
    """One number decides whether the emulator may be used, and it is the worst
    feature's -- not the mean, which is exactly what would hide five good
    features and one useless one."""
    import ca_run_history

    (tmp_path / "emulator_metadata.json").write_text(
        json.dumps({
            "feature_labels": ["a", "b"],
            "feature_r2": [0.999, 0.42],
            "feature_rmse": [0.01, 0.5],
            "param_entry_labels": ["p", "q"],
            "param_mins": [0.0, 0.0],
            "param_maxs": [6.0, 6.0],
            "model_name": "GaussianProcessRBF",
            "design": {"num_train_samples": 64, "sample_type": "sobol"},
            "fingerprint": {"inputs_sha256": "abc"},
        })
    )
    meta = ca_run_history.emulator_metadata(str(tmp_path))
    assert meta["worst_r2"] == pytest.approx(0.42)
    assert meta["feature_labels"] == ["a", "b"]
    assert meta["param_maxs"] == [6.0, 6.0]
    assert meta["dir"] == str(tmp_path)


def test_held_out_points_are_read_for_the_analysis_view(tmp_path):
    """The statistics say how wrong the emulator is; these say *where*.

    Read verbatim from CA's file, including its residual sign convention, so a
    positive residual means the same thing in the GUI as it does in CA.
    """
    import numpy as np

    import ca_run_history

    np.savez(
        tmp_path / "emulator_validation.npz",
        theta=np.array([[0.1], [0.9]]),
        y_true=np.array([[1.0], [2.0]]),
        y_pred=np.array([[1.5], [1.5]]),
        feature_labels=np.array(["x (max a/x)"], dtype=object),
        param_entry_labels=np.array(["a/p"], dtype=object),
    )
    points = ca_run_history.emulator_error_points(str(tmp_path))
    assert points["feature_labels"] == ["x (max a/x)"]
    # prediction minus truth: +0.5 where the emulator reads high, -0.5 where low.
    assert points["residual"] == [[0.5], [-0.5]]


def test_a_bundle_without_held_out_points_is_not_an_error(tmp_path):
    """An emulator trained before CA saved them is still a usable emulator, and
    the view must distinguish that from having no emulator at all."""
    import ca_run_history

    assert ca_run_history.emulator_error_points(str(tmp_path)) is None


# ---------------------------------------------------------------------------
# "Use the emulator" reaching circulatory_autogen
# ---------------------------------------------------------------------------
def test_engine_kwargs_are_empty_when_the_box_is_off():
    """An older circulatory_autogen does not accept the keyword at all, so a
    study that never asked for an emulator must not have it passed."""
    import emulator_config

    assert emulator_config.engine_kwargs({}) == {}
    assert emulator_config.engine_kwargs({"use_emulator": False}) == {}
    assert emulator_config.describe({}) == ""


def test_engine_kwargs_carry_the_directory_and_settings():
    import emulator_config

    kwargs = emulator_config.engine_kwargs({
        "use_emulator": True,
        "emulator_dir": "/tmp/emu",
        "emulator_settings": {"min_r2": 0.95},
    })
    assert kwargs == {
        "use_emulator": True,
        "emulator_dir": "/tmp/emu",
        "emulator_settings": {"min_r2": 0.95},
    }
    assert "/tmp/emu" in emulator_config.describe({
        "use_emulator": True, "emulator_dir": "/tmp/emu",
    })


def test_asking_for_an_emulator_without_one_is_an_error():
    """Better here than as a confusing failure inside CA after a model compile."""
    import emulator_config

    with pytest.raises(ValueError, match="train an emulator"):
        emulator_config.engine_kwargs({"use_emulator": True})


def test_every_analysis_runner_puts_its_engine_on_the_emulator():
    """A study calibrated on the surrogate but analysed on the solver -- or the
    reverse -- is the confusion this feature must not create. All three runners
    go through the one helper, so this checks they call it at all."""
    from pathlib import Path

    api = Path(__file__).resolve().parents[1]
    for name in ("calibration_runner.py", "sensitivity_runner.py", "uq_runner.py"):
        source = (api / name).read_text()
        assert "emulator_config.engine_kwargs" in source, name


def test_the_local_sensitivity_arm_is_on_the_emulator_too():
    """Sobol and local in one study must measure the same forward model."""
    from pathlib import Path

    source = (Path(__file__).resolve().parents[1] / "sensitivity_runner.py").read_text()
    # Both the SA manager and the local engine, plus the calibrate-first engine.
    assert source.count("emulator_config.engine_kwargs") >= 3


# ---------------------------------------------------------------------------
# The training runner's config assembly
# ---------------------------------------------------------------------------
def test_runner_keeps_cuflynx_settings_out_of_cas_emulator_settings():
    """CA validates its own option block; a CUFLynx-level key in there would be
    forwarded into a schema that does not have it."""
    import emulator_runner

    settings = {
        "num_train_samples": 64,
        "sample_type": "sobol",
        "dt": 0.01,
        "DEBUG": True,
        "num_cores": 4,
        "python_path": "/usr/bin/python3",
        "use_emulator": True,
    }
    out = emulator_runner._emulator_settings(settings, "/tmp/emu")
    assert out["num_train_samples"] == 64
    assert out["sample_type"] == "sobol"
    assert out["emulator_dir"] == "/tmp/emu"
    for leaked in ("dt", "DEBUG", "num_cores", "python_path", "use_emulator"):
        assert leaked not in out


def test_an_unknown_ca_option_is_forwarded_untouched():
    """Forward-compatible: a new option in CA's schema reaches CA without a
    runner change, which is the whole point of building the form from it."""
    import emulator_runner

    out = emulator_runner._emulator_settings({"some_new_ca_option": 7}, "/tmp/emu")
    assert out["some_new_ca_option"] == 7


def test_the_global_seed_drives_the_training_design():
    """A design is exactly the random process the Settings seed exists for."""
    import emulator_runner

    out = emulator_runner._emulator_settings({}, "/tmp/emu", seed=42)
    assert out["random_seed"] == 42


def test_a_missing_autoemulate_names_the_interpreter_it_means():
    """CA names the package and the pip command; only CUFLynx knows the install
    has to happen in the interpreter chosen in Settings."""
    import emulator_runner

    text = emulator_runner._with_install_hint(
        RuntimeError("autoemulate is not installed. Install it with `pip install ...`")
    )
    assert "Settings" in text
    # An unrelated failure is passed through untouched.
    assert emulator_runner._with_install_hint(ValueError("boom")) == "boom"


# ---------------------------------------------------------------------------
# The job manager
# ---------------------------------------------------------------------------
def _fake_runner(tmp_path, body: str) -> str:
    script = tmp_path / "fake_emulator_runner.py"
    script.write_text(body)
    return str(script)


def test_a_finished_run_reports_cas_metadata(tmp_path):
    """The manager reports what CA wrote, not what the runner said about it."""
    from emulator import EmulatorManager

    emu_dir = tmp_path / "emulators" / "m_obs"
    emu_dir.mkdir(parents=True)
    (emu_dir / "emulator_metadata.json").write_text(
        json.dumps({
            "feature_labels": ["x"], "feature_r2": [0.99], "feature_rmse": [0.01],
            "param_entry_labels": ["p"], "param_mins": [0.0], "param_maxs": [1.0],
            "design": {}, "fingerprint": {},
        })
    )
    body = (
        "import json\n"
        "print('training...')\n"
        f"print('__EMULATOR_META__ ' + json.dumps({{'emulator_dir': {str(emu_dir)!r}}}))\n"
        "print('__EMULATOR_DONE__')\n"
    )
    manager = EmulatorManager()
    manager.runner_path = _fake_runner(tmp_path, body)
    job_id = manager.start({"output_dir": str(tmp_path), "num_cores": 1})

    import time

    for _ in range(200):
        status = manager.status(job_id)
        if status["state"] != "running":
            break
        time.sleep(0.05)
    assert status["state"] == "done", status
    assert status["metadata"]["worst_r2"] == pytest.approx(0.99)
    assert "training..." in "\n".join(status["lines"])


def test_a_run_that_writes_no_emulator_is_an_error(tmp_path):
    """Finishing is not succeeding: without a bundle there is nothing to use,
    and reporting 'done' would leave the tick box offering a phantom."""
    from emulator import EmulatorManager

    body = "print('__EMULATOR_META__ {\"emulator_dir\": \"/nowhere\"}')\nprint('__EMULATOR_DONE__')\n"
    manager = EmulatorManager()
    manager.runner_path = _fake_runner(tmp_path, body)
    job_id = manager.start({"output_dir": str(tmp_path), "num_cores": 1})

    import time

    for _ in range(200):
        status = manager.status(job_id)
        if status["state"] != "running":
            break
        time.sleep(0.05)
    assert status["state"] == "error"
    assert "no emulator metadata" in status["error"]


def test_training_and_sensitivity_do_not_block_each_other(tmp_path):
    """Separate slots: training is what a user does *before* an analysis."""
    from emulator import EmulatorManager
    from sensitivity import SensitivityManager

    assert EmulatorManager()._job is None
    assert SensitivityManager()._job is None
    # Distinct singletons, so `busy` on one says nothing about the other.
    import emulator
    import sensitivity

    assert emulator.emulator is not sensitivity.sensitivity


# --- the models registry is read from the interpreter that will train (#244 follow-up) ------


def test_emulator_models_asks_the_configured_interpreter_not_this_one(monkeypatch, tmp_path):
    """autoemulate is an optional extra with heavy deps. It is routinely installed in the CA
    venv a user points CUFLynx at, while the API itself runs on a plain system python -- so
    probing in-process answered "no models" about an interpreter that was never going to train
    anything, and the panel fell back to free text on a machine where the menu was knowable."""
    import solver_options

    asked = {}

    def fake_probe(python, src):
        asked["python"] = python
        return ["GaussianProcessRBF", "RandomForest"]

    monkeypatch.setattr(solver_options, "_models_from_interpreter", fake_probe)
    monkeypatch.setattr(solver_options, "_ensure_ca_path", lambda: None)
    monkeypatch.setenv("CIRCULATORY_AUTOGEN_SRC", str(tmp_path))
    solver_options._MODEL_CACHE.clear()

    models = solver_options.emulator_models("/venv/bin/python")

    assert models == ["GaussianProcessRBF", "RandomForest"]
    assert asked["python"] == "/venv/bin/python", "probed the wrong interpreter"


def test_emulator_models_is_cached_per_interpreter(monkeypatch, tmp_path):
    """The probe imports autoemulate, which costs seconds; the answer only changes when the
    interpreter or the CA directory does."""
    import solver_options

    calls = []

    monkeypatch.setattr(solver_options, "_models_from_interpreter",
                        lambda python, src: calls.append(python) or ["MLP"])
    monkeypatch.setattr(solver_options, "_ensure_ca_path", lambda: None)
    monkeypatch.setenv("CIRCULATORY_AUTOGEN_SRC", str(tmp_path))
    solver_options._MODEL_CACHE.clear()

    solver_options.emulator_models("/venv/bin/python")
    solver_options.emulator_models("/venv/bin/python")
    assert calls == ["/venv/bin/python"], "probed twice for the same interpreter"

    solver_options.emulator_models("/other/bin/python")
    assert len(calls) == 2, "a different interpreter must be probed on its own"


def test_emulator_models_is_empty_when_nothing_can_answer(monkeypatch, tmp_path):
    """Empty is honest -- the panel shows free text rather than an authoritative-looking but
    stale menu. It must not raise, and must not invent names."""
    import solver_options

    monkeypatch.setattr(solver_options, "_models_from_interpreter", lambda python, src: [])
    monkeypatch.setattr(solver_options, "_ensure_ca_path", lambda: None)
    monkeypatch.setenv("CIRCULATORY_AUTOGEN_SRC", str(tmp_path))
    solver_options._MODEL_CACHE.clear()

    assert solver_options.emulator_models("/venv/bin/python") == []


def test_runner_gives_the_root_logger_a_handler(monkeypatch):
    """autoemulate's records propagate to the root, where nothing was configured -- so Python
    fell back to logging.lastResort, which writes to stderr. Under mpiexec the non-zero ranks
    have theirs torn down, and every INFO line became a "--- Logging error --- ValueError: I/O
    operation on closed file" traceback in the run log."""
    import logging

    import emulator_runner

    root = logging.getLogger()
    saved = root.handlers[:]
    try:
        root.handlers = []
        emulator_runner.configure_logging(rank=0)
        assert root.handlers, "rank 0 must have somewhere to log"
        assert not isinstance(root.handlers[0], logging.NullHandler)

        root.handlers = []
        emulator_runner.configure_logging(rank=3)
        # A NullHandler is still a handler: it is what stops the lastResort fallback.
        assert isinstance(root.handlers[0], logging.NullHandler)

        # An application that configured its own logging keeps it.
        marker = logging.StreamHandler()
        root.handlers = [marker]
        emulator_runner.configure_logging(rank=0)
        assert root.handlers == [marker]
    finally:
        root.handlers = saved


def test_runner_reads_its_rank_from_the_launcher(monkeypatch):
    import emulator_runner

    for var in emulator_runner._RANK_VARS:
        monkeypatch.delenv(var, raising=False)
    assert emulator_runner.mpi_rank() == 0

    monkeypatch.setenv("OMPI_COMM_WORLD_RANK", "4")
    assert emulator_runner.mpi_rank() == 4
    monkeypatch.setenv("OMPI_COMM_WORLD_RANK", "not-a-rank")
    assert emulator_runner.mpi_rank() == 0
