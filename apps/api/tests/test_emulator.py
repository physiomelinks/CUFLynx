"""Emulator training and use (circulatory_autogen #333).

The emulator is the one feature where a wrong answer looks exactly like a right
one: every analysis still runs, still finishes, still produces indices and
posteriors -- from a surrogate that may be nonsense. So most of what is tested
here is refusal and provenance, not happy paths.
"""

import json
import time
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
# Whether there are samples to reuse (emulator_settings.reuse_samples)
# ---------------------------------------------------------------------------
def _bundle(directory, metadata=True, samples=True):
    directory.mkdir(parents=True, exist_ok=True)
    if metadata:
        (directory / "emulator_metadata.json").write_text(json.dumps({"feature_r2": [0.99]}))
    if samples:
        (directory / "training_data.npz").write_bytes(b"not really an npz")
    return str(directory)


@pytest.mark.parametrize(
    "metadata,samples,expected",
    [(True, True, True), (True, False, False), (False, True, False), (False, False, False)],
)
def test_reuse_needs_both_files_just_as_ca_does(tmp_path, metadata, samples, expected):
    """CA raises EmulatorReuseError unless the metadata *and* the saved samples
    are there, so "an emulator exists" is not the question being asked: a bundle
    from a circulatory_autogen that predates training_data.npz is perfectly
    usable and still has nothing to refit."""
    import ca_run_history

    directory = _bundle(tmp_path / "emu", metadata=metadata, samples=samples)
    assert ca_run_history.emulator_reusable(directory) is expected


def test_reuse_is_false_for_a_directory_that_does_not_exist(tmp_path):
    import ca_run_history

    assert ca_run_history.emulator_reusable(str(tmp_path / "nope")) is False


def test_the_info_route_says_whether_samples_can_be_reused(client, tmp_path):
    """The panel disables the tick box off this, rather than letting the user ask
    for a run CA will refuse minutes later."""
    from conftest import BG_MODEL_PATH

    with open(BG_MODEL_PATH, "rb") as fh:
        model_id = client.post(
            "/api/models/upload", files={"file": (BG_MODEL_PATH.name, fh, "application/xml")}
        ).json()["model_id"]

    def info():
        return client.get(
            "/api/emulator/info",
            params={"model_id": model_id, "config_outputs_dir": str(tmp_path)},
        ).json()

    body = info()
    assert body["metadata"] is None and body["reusable"] is False

    emu_dir = Path(body["emulator_dir"])
    _bundle(emu_dir, samples=False)
    assert info()["reusable"] is False  # trained, but nothing saved to refit

    _bundle(emu_dir)
    body = info()
    assert body["reusable"] is True
    assert body["metadata"] is not None


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


# --- why the panel is unavailable, not just that it is ----------------------------------
#
# `models` going empty is the honest answer -- CA's emulator_model_names() returns [] when
# autoemulate is absent -- but it is a mute one. A user who pointed Settings at a conda env
# built for FEniCSx watched the model dropdown turn into a free-text box and had to ask why.
# The tab is orange and shows the reason instead, so the reason has to be a complete,
# actionable sentence rather than a code the client phrases.

EMULATION_SUPPORTED = {
    "emulation": {
        "label": "Emulator",
        "enable_flag": "do_emulation",
        "use_flag": "use_emulator",
        "options_key": "emulator_settings",
        "options": [{"name": "models", "type": "str", "default": "GaussianProcessRBF"}],
    }
}


def _probe_env(monkeypatch, tmp_path, *, external=(), in_process=(), analysis=None):
    """Wire solver_options' emulator probe up to fixed answers.

    Nothing here may need autoemulate or circulatory_autogen to be importable: this is the
    unit tier, and the whole subject is environments that lack them.
    """
    import solver_options

    monkeypatch.setattr(solver_options, "_ensure_ca_path", lambda: None)
    monkeypatch.setattr(
        solver_options, "_models_from_interpreter", lambda python, src: list(external)
    )
    monkeypatch.setattr(solver_options, "_models_in_process", lambda: list(in_process))
    monkeypatch.setattr(
        solver_options, "get_analysis_options",
        lambda: EMULATION_SUPPORTED if analysis is None else analysis,
    )
    monkeypatch.setenv("CIRCULATORY_AUTOGEN_SRC", str(tmp_path / "circulatory_autogen" / "src"))
    solver_options._MODEL_CACHE.clear()
    return solver_options


def test_emulation_is_available_when_the_training_interpreter_has_autoemulate(
    monkeypatch, tmp_path
):
    """The working case, and the one that decides the shape: available says nothing more
    than "that probe found names", so it cannot drift from the menu beside it."""
    solver_options = _probe_env(monkeypatch, tmp_path, external=["GaussianProcessRBF"])

    got = solver_options.emulator_availability("/venv/bin/python")

    assert got["available"] is True
    assert got["unavailable_reason"] is None
    assert got["models"] == ["GaussianProcessRBF"]
    assert got["interpreter"] == "/venv/bin/python"


def test_a_configured_interpreter_without_autoemulate_is_named_with_its_install_line(
    monkeypatch, tmp_path
):
    """The reported case: Settings pointed at a conda env built for FEniCSx. Naming the
    interpreter is the whole point -- "autoemulate is missing" is not actionable when the
    app runs on one interpreter and trains on another."""
    solver_options = _probe_env(monkeypatch, tmp_path)  # neither side has any

    got = solver_options.emulator_availability("/opt/conda/envs/fenicsx/bin/python")
    reason = got["unavailable_reason"]

    assert got["available"] is False
    assert got["models"] == []
    assert got["interpreter"] == "/opt/conda/envs/fenicsx/bin/python"
    assert "/opt/conda/envs/fenicsx/bin/python" in reason, "the reason must name the interpreter"
    assert 'pip install "libcuflynx[emulation]"' in reason, "give the exact install line"
    # autoemulate pins the interpreter, and a conda env built for something else is routinely
    # outside it -- so the pip line failing is the *next* thing this user would hit.
    assert ">=3.10,<3.13" in reason
    # CA declares it as an optional extra; installing CA that way is the same install.
    assert "[emulation]" in reason
    assert reason.endswith("."), "a complete sentence: the panel shows this and nothing else"


def test_the_install_hint_never_says_to_pip_install_circulatory_autogen(monkeypatch, tmp_path):
    """The engine is installed as `libcuflynx`; `circulatory_autogen` is the repository.

    The hint used to spell `pip install -e "<CA_dir>[emulation]"`, which only works from a
    checkout -- and the app that most needs this message is the packaged one, which has no
    checkout at all and bundles the engine. One command that is right in both places.
    """
    solver_options = _probe_env(monkeypatch, tmp_path)

    for python in ("/venv/bin/python", None):
        reason = solver_options.emulator_availability(python)["unavailable_reason"]
        assert "libcuflynx[emulation]" in reason
        assert "circulatory_autogen" not in reason
        assert "pip install -e" not in reason


def test_with_no_interpreter_configured_the_reason_points_at_settings(monkeypatch, tmp_path):
    """The frozen app's default: training would happen in CUFLynx's own environment, which
    does not carry autoemulate (torch/gpytorch are not bundled). Nothing to name, so the
    fix is to choose an interpreter rather than to install into one."""
    solver_options = _probe_env(monkeypatch, tmp_path)
    monkeypatch.setattr(solver_options, "default_python", lambda: None)

    got = solver_options.emulator_availability(None)
    reason = got["unavailable_reason"]

    assert got["available"] is False
    assert got["interpreter"] is None
    # The packaged app's own python is the one being described, so the fix is to choose a
    # different interpreter or to install into one -- not to point at a directory.
    assert "shipped with this CUFLynx executable" in reason
    assert 'pip install "libcuflynx[emulation]"' in reason
    # Must not pretend to name an interpreter it does not have.
    assert "None" not in reason


def test_an_engine_without_emulators_is_a_different_answer(monkeypatch, tmp_path):
    """Distinct from "autoemulate is missing": installing autoemulate would fix nothing, and
    telling this user to do it sends them down the wrong path entirely."""
    solver_options = _probe_env(
        monkeypatch, tmp_path, external=["GaussianProcessRBF"], analysis={},
    )

    got = solver_options.emulator_availability("/venv/bin/python")
    reason = got["unavailable_reason"]

    # False even though the probe found names: there is no engine-side emulator to train.
    assert got["available"] is False
    assert "libcuflynx" in reason
    assert "autoemulate" not in reason, "wrong diagnosis: the interpreter is fine"
    # The engine is a bundled package, not a directory the user has to have. Naming
    # circulatory_autogen here sends a packaged-app user looking for a checkout that
    # the app does not need and that they may not have -- reported against v0.4.1.
    assert "circulatory_autogen" not in reason, (
        "the engine is called libcuflynx; the packaged app bundles it and needs no "
        "circulatory_autogen checkout at all."
    )


def test_emulator_models_still_answers_for_its_existing_callers(monkeypatch, tmp_path):
    """The availability probe is the same probe, not a second one beside it."""
    solver_options = _probe_env(monkeypatch, tmp_path, external=["MLP"])

    assert solver_options.emulator_models("/venv/bin/python") == ["MLP"]
    assert solver_options.emulator_availability("/venv/bin/python")["models"] == ["MLP"]


def test_the_defaults_endpoint_carries_the_reason_beside_the_form(client, monkeypatch):
    """The endpoint the Emulator tab polls. The existing keys are unchanged -- the panel
    still renders the form from them when emulation *is* available."""
    import main

    monkeypatch.setattr(main, "get_analysis_options", lambda: EMULATION_SUPPORTED)
    monkeypatch.setattr(
        main, "emulator_availability",
        lambda python: {
            "models": [],
            "available": False,
            "interpreter": "/venv/bin/python",
            "unavailable_reason": "no autoemulate over there.",
        },
    )

    body = client.get("/api/emulator/defaults").json()

    assert body["supported"] is True
    assert body["label"] == "Emulator"
    assert body["enable_flag"] == "do_emulation"
    assert body["use_flag"] == "use_emulator"
    assert body["options"] == EMULATION_SUPPORTED["emulation"]["options"]
    assert body["models"] == []
    assert body["available"] is False
    assert body["interpreter"] == "/venv/bin/python"
    assert body["unavailable_reason"] == "no autoemulate over there."


def test_the_defaults_endpoint_probes_the_interpreter_that_would_train(client, monkeypatch):
    """Not this process's. That asymmetry is the bug the availability signal explains."""
    import emulator as emulator_mod
    import main

    asked = {}

    def fake(python):
        asked["python"] = python
        return {"models": [], "available": False, "interpreter": python,
                "unavailable_reason": "..."}

    monkeypatch.setattr(main, "emulator_availability", fake)
    monkeypatch.setattr(emulator_mod.emulator, "python", "/venv/bin/python")

    client.get("/api/emulator/defaults")

    assert asked["python"] == "/venv/bin/python"


# ---------------------------------------------------------------------------
# What the emulator's prediction costs (#333)
#
# The tick box puts a calibration on the surrogate, so the best cost it reports
# is the *emulator's* -- while the cost above the Output plots is the solver's.
# Nothing said so, and the two are not comparable. These pin the second number
# onto the request that already predicts the features, so both describe one
# parameter set, and pin its silence when there is no emulator to ask.
# ---------------------------------------------------------------------------
def _study(client):
    """The Lotka-Volterra study loaded: model, obs_data and params_for_id."""
    from conftest import (
        LV_MODEL_PATH,
        LV_OBS_DATA_PATH,
        LV_PARAMS_CSV_PATH,
        upload_model,
    )

    model_id = upload_model(client, LV_MODEL_PATH)["model_id"]
    obs = json.loads(LV_OBS_DATA_PATH.read_text())
    assert client.post(
        "/api/obs_data/upload", json={"model_id": model_id, "obs_data": obs}
    ).status_code == 200
    with open(LV_PARAMS_CSV_PATH, "rb") as fh:
        resp = client.post(
            f"/api/params_for_id/upload?model_id={model_id}",
            files={"file": (LV_PARAMS_CSV_PATH.name, fh, "text/csv")},
        )
    assert resp.status_code == 200, resp.text
    return model_id, [p["qname"] for p in resp.json()["params"]]


def _ca_feature_labels_for(model_id):
    """The labels circulatory_autogen would train this study's emulator on."""
    import main
    import obs_cost

    doc = main._obs_data_document(main._models[model_id])
    pid = obs_cost._ca_engine(doc, None, main.engine.dt)
    if pid is None:
        pytest.skip("circulatory_autogen could not be reached")
    labels = obs_cost._ca_feature_labels(pid.obs_info)
    if labels is None:
        pytest.skip("this circulatory_autogen has no emulator feature labels")
    return labels


def _install_emulator(monkeypatch, labels, values, seen=None):
    """A trained bundle that predicts `values` for `labels`, without one existing."""
    import main

    monkeypatch.setattr(
        main.ca_run_history, "emulator_metadata",
        lambda _dir: {"param_entry_labels": [], "feature_labels": list(labels)},
    )

    def fake_predict(emulator_dir, theta):
        if seen is not None:
            seen["theta"] = list(theta)
        return {"labels": list(labels), "values": list(values), "in_box": True}

    monkeypatch.setattr(main.engine, "emulator_predict", fake_predict)


def test_the_prediction_carries_the_cost_of_what_it_predicted(client, monkeypatch):
    """On this response rather than on /api/simulate: the frontend already asks
    for a prediction every time the parameters settle, so the second cost costs
    no second round trip -- and both costs then describe the parameter values of
    one settle rather than of two."""
    import main
    import obs_cost

    model_id, _ = _study(client)
    labels = _ca_feature_labels_for(model_id)
    values = [3.0] * len(labels)
    _install_emulator(monkeypatch, labels, values)

    resp = client.post(
        "/api/emulator/predict", json={"model_id": model_id, "params": {}, "settings": {}}
    )
    assert resp.status_code == 200, resp.text
    cost = resp.json()["cost"]
    assert cost is not None
    # The same function, not a second implementation of it.
    expected = obs_cost.evaluate_features(
        dict(zip(labels, values)), main._obs_data_document(main._models[model_id]),
        None, dt=main.engine.dt,
    )
    assert cost == expected
    assert cost["computed_by"] == "circulatory_autogen"


def test_a_prediction_that_cannot_be_matched_leaves_no_cost(client, monkeypatch):
    """An emulator trained before the obs_data was edited predicts features this
    study no longer has. The prediction still stands (it is drawn as an overlay);
    the cost is simply absent, because a cost over some of the observables would
    read as a better fit than the solver's over all of them."""
    model_id, _ = _study(client)
    _install_emulator(monkeypatch, ["a feature this study does not have"], [1.0])

    resp = client.post(
        "/api/emulator/predict", json={"model_id": model_id, "params": {}, "settings": {}}
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["cost"] is None


def test_the_run_route_cost_is_untouched_when_the_emulator_is_off(client, fake_helper):
    """The solver's cost is the one number this feature must not move. With no
    emulator in play the run routes answer exactly as they did -- one `cost`, and
    no second key to explain away."""
    model_id, _ = _study(client)
    resp = client.post(
        "/api/simulate",
        json={"model_id": model_id, "params": {}, "outputs": ["Lotka_Volterra_module/x"]},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "cost" in body
    assert "emulator_cost" not in body


# ---------------------------------------------------------------------------
# The same parameters, scored both ways (/api/cost_at_params)
# ---------------------------------------------------------------------------
def test_one_request_scores_the_same_parameters_both_ways(client, monkeypatch):
    """Both sides in one response so they cannot be asked at two different
    points: the errors bars in the Analysis tab describe the calibration's best
    fit, and "the model says this, the emulator says that" is only a statement
    about the surrogate if the parameters are identical."""
    import main
    import obs_cost

    model_id, qnames = _study(client)
    labels = _ca_feature_labels_for(model_id)
    seen = {}
    _install_emulator(monkeypatch, labels, [3.0] * len(labels), seen=seen)
    monkeypatch.setattr(
        main, "_solver_cost_at",
        lambda *a, **k: {"cost": 1.0, "items": [], "n_weighted": 1,
                         "incomplete": False, "computed_by": "circulatory_autogen"},
    )

    theta = {qname: 0.5 for qname in qnames}
    resp = client.post(
        "/api/cost_at_params",
        json={"model_id": model_id, "params": theta, "analysis_params": theta},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["cost"]["cost"] == pytest.approx(1.0)
    # The emulator was asked at the parameters that were sent, not at defaults.
    assert seen["theta"] == [0.5] * len(qnames)
    assert body["emulator_cost"] == obs_cost.evaluate_features(
        dict(zip(labels, [3.0] * len(labels))),
        main._obs_data_document(main._models[model_id]), None, dt=main.engine.dt,
    )
    # Both rows carry the same fields, so the bars need no special case.
    assert body["emulator_cost"]["computed_by"] == "circulatory_autogen"
    assert set(body["emulator_cost"]["items"][0]) == {
        "label", "operation", "experiment_idx", "subexperiment_idx",
        "observed", "model", "percent_error", "std_error", "cost",
    }


def test_without_an_emulator_only_the_model_answers(client, monkeypatch):
    """No bundle is the normal state of most studies, and it is not an error:
    the forward-model side is returned as usual and the toggle simply has nothing
    to offer."""
    import main

    model_id, _ = _study(client)
    monkeypatch.setattr(main.ca_run_history, "emulator_metadata", lambda _dir: None)
    monkeypatch.setattr(
        main, "_solver_cost_at",
        lambda *a, **k: {"cost": 1.0, "items": [], "n_weighted": 1,
                         "incomplete": False, "computed_by": "circulatory_autogen"},
    )

    resp = client.post("/api/cost_at_params", json={"model_id": model_id, "params": {}})
    assert resp.status_code == 200, resp.text
    assert resp.json()["cost"]["cost"] == pytest.approx(1.0)
    assert resp.json()["emulator_cost"] is None


def test_an_emulator_that_will_not_load_is_a_silence_not_a_failure(client, monkeypatch):
    """No autoemulate in the configured interpreter, a stale bundle, a joblib
    that will not unpickle: the model's own cost is still correct, and an error
    banner over a missing second opinion would be worse than not offering one."""
    import main

    model_id, _ = _study(client)
    monkeypatch.setattr(
        main.ca_run_history, "emulator_metadata", lambda _dir: {"param_entry_labels": []})

    def boom(*_a, **_k):
        raise RuntimeError("emulator predictions need autoemulate")

    monkeypatch.setattr(main.engine, "emulator_predict", boom)
    monkeypatch.setattr(
        main, "_solver_cost_at",
        lambda *a, **k: {"cost": 1.0, "items": [], "n_weighted": 1,
                         "incomplete": False, "computed_by": "circulatory_autogen"},
    )

    resp = client.post("/api/cost_at_params", json={"model_id": model_id, "params": {}})
    assert resp.status_code == 200, resp.text
    assert resp.json()["emulator_cost"] is None


# ---------------------------------------------------------------------------
# A protocol-less obs_data is still an obs_data
# ---------------------------------------------------------------------------
# An obs_data may say what to measure without saying how to drive the model --
# CA then builds the timeline from sim_time/pre_time. The heat_fenics example
# ships exactly that. _obs_data_document used to return None for it, so a
# protocol-less study was reported as having no obs_data at all: the solver's
# cost quietly fell back to the local walk, and the emulator's cost -- which
# has no fallback, deliberately -- never appeared, saying "there is no obs_data
# loaded to score the emulator against" while one was plainly loaded.
_BARE_ITEM = {
    "data_item_name": "probe 1 mean", "trace_name_for_plotting": "mean(T_{p1})",
    "data_type": "constant", "operation": "mean", "operands": ["heat/T_p1"],
    "unit": "dimensionless", "weight": 1.0, "value": 0.4, "std": 0.05,
    "cost_type": "gaussian_MLE",
}


class _ObsStub:
    def __init__(self, protocol_info):
        self.protocol_info = protocol_info
        self.data_items = [dict(_BARE_ITEM)]
        self.prediction_items = []


class _RecordStub:
    def __init__(self, protocol_info):
        self.obs_data = _ObsStub(protocol_info)


def test_a_protocol_less_obs_data_still_produces_a_document():
    import main

    doc = main._obs_data_document(_RecordStub(None))
    assert doc is not None, 'a protocol-less obs_data is not "no obs_data"'
    assert doc["data_items"] == [dict(_BARE_ITEM)]
    # Omitted, not None: CA's parser accepts the absence and refuses an explicit None.
    assert "protocol_info" not in doc


def test_a_protocol_is_carried_through_when_there_is_one():
    import main

    proto = {"pre_times": [0.0], "sim_times": [[1.0]]}
    assert main._obs_data_document(_RecordStub(proto))["protocol_info"] == proto


def test_no_obs_data_at_all_is_still_none():
    import main

    class _Empty:
        obs_data = None

    assert main._obs_data_document(_Empty()) is None


def test_the_chosen_interpreter_is_probed_even_with_no_ca_directory(monkeypatch, tmp_path):
    """Choosing an interpreter that has autoemulate must change the answer, bundle or not.

    The probe used to require a configured CA *directory* as well as an interpreter. Since
    the app bundles libcuflynx (#18) a directory is optional, so in the ordinary packaged
    case there is none -- and the probe was skipped entirely. Picking a python that had
    autoemulate installed then changed nothing: the answer still came from the bundle's own
    environment, which never has it, and the emulator stayed unavailable with no way to fix
    it from inside the app.
    """
    import solver_options as so

    monkeypatch.delenv("CIRCULATORY_AUTOGEN_SRC", raising=False)
    monkeypatch.setattr(so, "_ensure_ca_path", lambda: None)
    monkeypatch.setattr(so, "_models_in_process", lambda: [])  # the bundle has none
    so._MODEL_CACHE.clear()

    probed = []

    def fake_probe(python, src):
        probed.append((python, src))
        return ["GaussianProcessRBF"]

    monkeypatch.setattr(so, "_models_from_interpreter", fake_probe)

    models, interpreter = so._probe_models("/envs/emu/bin/python")

    assert probed == [("/envs/emu/bin/python", "")], "the interpreter was never probed"
    assert models == ["GaussianProcessRBF"]
    assert interpreter == "/envs/emu/bin/python"

    # And it reaches the user-visible answer. `supported` is pinned rather than read: it
    # comes from CA's analysis options, which a machine with no circulatory_autogen at all
    # (CI's "no Myokit" job) answers from a fallback that predates emulators -- so reading it
    # would make this test's subject depend on whether the machine happens to have CA.
    monkeypatch.setattr(so, "get_analysis_options",
                        lambda *a, **k: {"emulation": {"options": [{"name": "model"}]}})
    monkeypatch.setattr(so, "analysis_options_introspected", lambda: True)
    assert so.emulator_availability("/envs/emu/bin/python")["available"] is True


# ---------------------------------------------------------------------------
# Choosing between several emulators trained on one design
# ---------------------------------------------------------------------------
def _family(directory, model_name, r2, samples=True):
    """A bundle that reports a family and a set of held-out scores."""
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "emulator_metadata.json").write_text(json.dumps({
        "model_name": model_name,
        "feature_r2": r2,
        "feature_labels": [f"f{i}" for i in range(len(r2))],
        "design": {"num_used": 12000, "num_stages": 3},
    }))
    if samples:
        (directory / "training_data.npz").write_bytes(b"not really an npz")
    return str(directory)


def test_a_directory_of_bundles_enumerates_them(tmp_path):
    """A study that fits one design with several families keeps them side by side."""
    import ca_run_history

    root = tmp_path / "emulators"
    _family(root / "s_two_phase_RBF", "two_phase_RadialBasisFunctions", [0.9])
    _family(root / "s_two_phase_MLP", "two_phase_MLP", [0.8])

    found = [Path(p).name for p in ca_run_history.emulator_bundles(str(root))]
    assert sorted(found) == ["s_two_phase_MLP", "s_two_phase_RBF"]


def test_a_bundle_names_only_itself(tmp_path):
    """The container shape must not swallow the one-bundle case it also has to serve."""
    import ca_run_history

    bundle = _family(tmp_path / "emu", "two_phase_MLP", [0.7])
    assert ca_run_history.emulator_bundles(bundle) == [bundle]


def test_bundles_of_a_missing_directory_is_empty_not_an_error(tmp_path):
    import ca_run_history

    assert ca_run_history.emulator_bundles(str(tmp_path / "nope")) == []


def test_a_summary_carries_what_a_chooser_needs(tmp_path):
    import ca_run_history

    bundle = _family(tmp_path / "emu", "two_phase_MLP", [0.5, 0.9, 0.7])
    row = ca_run_history.emulator_summary(bundle)
    assert row["model_name"] == "two_phase_MLP"
    assert row["worst_r2"] == 0.5
    assert row["median_r2"] == 0.7
    assert row["num_features"] == 3
    assert row["num_samples"] == 12000
    assert row["reusable"] is True


def test_choices_are_ordered_by_the_worst_feature(tmp_path):
    """Worst-first, because that is the number that bounds what a bundle can be
    trusted for -- an emulator misleads a calibration where it is weakest."""
    import ca_run_history

    root = tmp_path / "emulators"
    _family(root / "s_two_phase_RBF", "two_phase_RadialBasisFunctions", [0.30, 0.95])
    _family(root / "s_two_phase_MLP", "two_phase_MLP", [0.28, 0.99])
    _family(root / "s_two_phase_RandomForest", "two_phase_RandomForest", [0.21, 0.85])

    rows = ca_run_history.emulator_choices(str(tmp_path), "s", None,
                                           declared=str(root))
    assert [r["model_name"] for r in rows] == [
        "two_phase_RadialBasisFunctions", "two_phase_MLP", "two_phase_RandomForest",
    ]


def test_choices_without_a_declaration_still_find_the_conventional_bundle(tmp_path):
    """The list can never omit the bundle the old search would have chosen."""
    import ca_run_history

    conventional = ca_run_history.emulator_dir(str(tmp_path), "model", None)
    _family(Path(conventional), "two_phase_MLP", [0.6])

    rows = ca_run_history.emulator_choices(str(tmp_path), "model", None)
    assert [Path(r["dir"]).name for r in rows] == [Path(conventional).name]


def test_a_bundle_whose_scores_cannot_be_read_sorts_last_not_worst(tmp_path):
    """Unknown is not the same as known-bad, and must not be ranked as though it were."""
    import ca_run_history

    root = tmp_path / "emulators"
    _family(root / "s_scored", "two_phase_MLP", [-5.0])
    _family(root / "s_unscored", "two_phase_RandomForest", [])

    rows = ca_run_history.emulator_choices(str(tmp_path), "s", None, declared=str(root))
    assert rows[0]["model_name"] == "two_phase_MLP"
    assert rows[-1]["worst_r2"] is None


def test_a_directory_name_can_disagree_with_the_reported_family(tmp_path):
    """A refit that died after its metadata was copied in looks exactly like this.

    CUFLynx does not repair it -- it cannot know which is right -- but the summary
    must carry both, because the name alone and the family alone each read as a
    healthy bundle and only the pair shows the problem.
    """
    import ca_run_history

    bundle = _family(tmp_path / "s_two_phase_MLP", "two_phase_RadialBasisFunctions", [0.4])
    row = ca_run_history.emulator_summary(bundle)
    assert row["name"] == "s_two_phase_MLP"
    assert row["model_name"] == "two_phase_RadialBasisFunctions"


def test_a_chosen_emulator_must_be_one_that_was_offered(tmp_path):
    """The panel has no free-text path, and the chooser must not become one.

    Deriving the location on both sides is what stops training writing to one
    directory and a later run reading from another. A choice constrained to the
    discovered set keeps that property; a choice taken at its word would hand the
    caller an arbitrary path to read a bundle out of.
    """
    import main

    offered = _family(tmp_path / "emulators" / "s_two_phase_MLP", "two_phase_MLP", [0.7])
    choices = [{"dir": offered}]

    assert main._chosen_emulator_dir({"emulator_dir": offered}, choices) == offered
    assert main._chosen_emulator_dir({"emulator_dir": "/etc"}, choices) is None
    assert main._chosen_emulator_dir({"emulator_dir": ""}, choices) is None
    assert main._chosen_emulator_dir({}, choices) is None


def test_a_manifest_may_declare_the_container_rather_than_one_bundle(tmp_path):
    """Naming one of several would tell a reader the others are not this study's."""
    import json as _json

    import load_outputs

    root = tmp_path / "study"
    emulators = root / "emulators"
    _family(emulators / "s_two_phase_RBF", "two_phase_RadialBasisFunctions", [0.30])
    _family(emulators / "s_two_phase_MLP", "two_phase_MLP", [0.28])
    (root / "cuflynx_study.json").write_text(_json.dumps({
        "schema": 1, "file_prefix": "s", "emulator": "emulators",
    }))

    result = load_outputs.load_outputs(str(root))
    names = sorted(Path(row["dir"]).name for row in result["emulator"]["choices"])
    assert names == ["s_two_phase_MLP", "s_two_phase_RBF"]
    # Best-first, so the default is the safest rather than the most recent.
    assert Path(result["emulator"]["dir"]).name == "s_two_phase_RBF"


# ---------------------------------------------------------------------------
# End to end: train one, then use it
#
# Everything above tests the wiring around an emulator -- where its directory is,
# what its metadata says, which kwargs a runner passes. Nothing trained one and
# then ran an analysis on it, so the question a user actually asks ("I trained an
# emulator; can I calibrate against it?") had no answer in this suite. It is also
# the shape of the failures that reached users: a bundle that trains and then
# cannot reload, an engine whose fingerprint no longer matches the study.
#
# Deliberately tiny. The design is the expensive part and its size decides only
# how *good* the emulator is; what these assert is that the pipeline runs, the
# bundle lands, and the three analyses accept it.
# ---------------------------------------------------------------------------
EMULATOR_SAMPLES = 12


def _setup_3compartment(client, out_dir=None):
    """The three files of the shipped example study, loaded as a user would.

    The archive rather than the loose files: it is one call, and it is the path the
    Start dialog uses -- so a study set up here is the study a user has.
    """
    from conftest import RESOURCES_DIR

    blob = (RESOURCES_DIR / "3compartment.omex").read_bytes()
    params = {"output_dir": str(out_dir)} if out_dir else {}
    resp = client.post("/api/omex/upload", params=params,
                       files={"file": ("3compartment.omex", blob, "application/zip")})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert not (body.get("obs_data") or {}).get("error"), body["obs_data"]
    return body["model_id"]


@pytest.fixture
def requires_autoemulate():
    """Training needs autoemulate in *this* interpreter (the tests run in-process)."""
    pytest.importorskip("autoemulate", reason="autoemulate is not installed here")


def _train_emulator(client, model_id, out_dir, num_cores=1, timeout=900):
    """Train an emulator through the app; returns ``(state, log lines)``.

    Polls through ``test_run_matrix._wait``, which already speaks the
    offset/next_offset protocol every analysis status route uses -- a second
    hand-rolled poller here would be one more thing to keep in step with it.
    """
    from test_run_matrix import _wait

    resp = client.post("/api/emulator/train", json={
        "model_id": model_id,
        "settings": {
            "config_outputs_dir": str(out_dir),
            "num_train_samples": EMULATOR_SAMPLES,
            "num_cores": num_cores,
            "dt": 0.01,
        },
    })
    assert resp.status_code == 200, resp.text
    return _wait(client, "emulator", resp.json()["job_id"], timeout)


@pytest.mark.integration
def test_a_trained_emulator_can_drive_sensitivity_calibration_and_uq(
    client, requires_simulation, requires_autoemulate, tmp_path
):
    """Train once, then run all three analyses against it.

    The reuse check is CA's (`EmulatorBundle.check_matches`), and it compares a
    fingerprint of the model file, the parameter bounds and the protocol. So a run
    that accepts the bundle is evidence that the study the app hands each analysis
    is the study the emulator was trained on -- which is exactly what breaks when
    the app renames a study, reopens it against a different model, or writes its
    obs_data under a name that changes between uploads.
    """
    from test_run_matrix import _wait

    model_id = _setup_3compartment(client, tmp_path)

    trained, train_log = _train_emulator(client, model_id, tmp_path)
    assert trained.get("state") == "done", (
        f"training ended {trained.get('state')}: {trained.get('error')}\n"
        + "\n".join(train_log[-40:]))

    info = client.get("/api/emulator/info", params={
        "model_id": model_id, "config_outputs_dir": str(tmp_path)}).json()
    assert info.get("metadata"), f"no bundle written: {info}"

    # Each analysis, with the emulator ticked on. What is under test is that CA
    # accepts the bundle for this study -- a mismatch raises EmulatorQualityError
    # inside the run and the job ends in error, which is what these would catch.
    for kind, route, settings in (
        ("sensitivity", "/api/sensitivity/run",
         {"method": "local", "gradient_method": "FD", "nominal": "current",
          "rel_step": 0.05, "num_cores": 1}),
        ("calibration", "/api/calibration/run",
         {"num_calls_to_function": 6, "num_cores": 1}),
        ("uq", "/api/uq/run",
         {"num_steps": 20, "num_walkers": 8, "num_cores": 1,
          "run_calibration_first": True}),
    ):
        payload = {"model_id": model_id,
                   "settings": {**settings, "config_outputs_dir": str(tmp_path),
                                "use_emulator": True, "dt": 0.01}}
        resp = client.post(route, json=payload)
        assert resp.status_code == 200, f"{kind} refused the emulator up front: {resp.text}"
        state, lines = _wait(client, kind, resp.json()["job_id"], 900)
        log = "\n".join(lines)
        assert state.get("state") == "done", (
            f"{kind} on the emulator ended {state.get('state')}: {state.get('error')}\n"
            f"{log[-2000:]}")
        assert "EmulatorQualityError" not in log, (
            f"{kind} rejected the bundle as stale -- the study it was handed is not the "
            f"study the emulator was trained on:\n{log[-2000:]}")


@pytest.mark.integration
def test_training_runs_across_ranks_when_asked_for_more_than_one(
    client, requires_simulation, requires_autoemulate, tmp_path, recorded_commands
):
    """Training spreads its simulations across MPI ranks, and says so on the argv.

    The design is the expensive half of training and CA splits it across ranks, so
    this is the parallel path users reach for. Asserted on the launched command as
    well as on the result: a run that quietly drops to one core finishes and reports
    success just the same.
    """
    from test_run_matrix import PARALLEL_CORES, _assert_parallelism

    model_id = _setup_3compartment(client, tmp_path)
    state, lines = _train_emulator(client, model_id, tmp_path, num_cores=PARALLEL_CORES)

    log = "\n".join(lines)
    assert state.get("state") == "done", (
        f"a {PARALLEL_CORES}-rank training ended {state.get('state')}:\n{log[-3000:]}")
    assert "MPI_ABORT" not in log, f"a rank aborted:\n{log[-3000:]}"
    _assert_parallelism(recorded_commands, PARALLEL_CORES)
