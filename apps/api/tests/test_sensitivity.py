from conftest import set_ca_module


# ---------------------------------------------------------------------------
# Where the GUI's sensitivity plots land
# ---------------------------------------------------------------------------
def test_sa_plots_are_collected_into_their_own_directory(tmp_path):
    """circulatory_autogen draws its Sobol figures straight into the run
    directory, beside the indices, the samples and the npy files -- so what a
    user actually goes looking for ends up mixed in with what they do not."""
    import sensitivity_runner as sr

    (tmp_path / "S1_Sobol_Heatmap.png").write_bytes(b"\x89PNG\r\n")
    (tmp_path / "u_lv_n64_First_order_idx.png").write_bytes(b"\x89PNG\r\n")
    (tmp_path / "results.json").write_text("{}")

    sr._collect_plots(str(tmp_path))

    plots = tmp_path / sr.SA_PLOTS_DIRNAME
    assert sorted(p.name for p in plots.glob("*.png")) == [
        "S1_Sobol_Heatmap.png",
        "u_lv_n64_First_order_idx.png",
    ]
    # Moved, not copied: two of everything is its own kind of mess.
    assert not list(tmp_path.glob("*.png"))
    # And the data is left exactly where every reader of it expects.
    assert (tmp_path / "results.json").is_file()


def test_collecting_plots_is_a_no_op_when_there_are_none(tmp_path):
    import sensitivity_runner as sr

    sr._collect_plots(str(tmp_path))
    assert not (tmp_path / sr.SA_PLOTS_DIRNAME).exists()


def test_a_failure_to_collect_plots_does_not_fail_the_run(tmp_path, monkeypatch):
    """The analysis is done and its numbers are written. Failing the run over
    where a PNG sits would be absurd."""
    import sensitivity_runner as sr

    (tmp_path / "plot.png").write_bytes(b"\x89PNG\r\n")
    monkeypatch.setattr(
        sr.Path, "mkdir", lambda *a, **k: (_ for _ in ()).throw(OSError("read-only"))
    )
    sr._collect_plots(str(tmp_path))  # must not raise


# ---------------------------------------------------------------------------
# The runner and compute_local_sensitivity must agree on what a run *is*
# ---------------------------------------------------------------------------
def _run_local_runner(monkeypatch, tmp_path, gradient_method):
    """Drive ``sensitivity_runner.run`` through the local path with CA stubbed
    out, returning (engines_built, kwargs compute_local_sensitivity received)."""
    import sys
    import types

    import local_sensitivity as ls
    import sensitivity_runner as sr

    fake_pkg = types.ModuleType("sensitivity_analysis")
    fake_mod = types.ModuleType("sensitivity_analysis.sensitivityAnalysis")
    fake_mod.SensitivityAnalysis = lambda **kwargs: object()
    set_ca_module(monkeypatch, "sensitivity_analysis", fake_pkg)
    set_ca_module(monkeypatch, "sensitivity_analysis.sensitivityAnalysis", fake_mod)
    monkeypatch.setattr(sr, "_ensure_ca_on_path", lambda: None)

    built = []
    monkeypatch.setattr(
        sr, "_build_local_engine", lambda *a, **k: built.append("engine") or "ENGINE"
    )
    received = {}

    def fake_compute(sa, settings, **kwargs):
        received.update(kwargs)
        # The full payload shape: the runner now writes it out in CA's
        # local-sensitivity CSV format, so a stub missing a key would fail there
        # rather than in what this test is actually about.
        return {
            "indices": {"local": {}},
            "method": "local",
            "param_names": [],
            "output_names": [],
            "gradient_method": settings.get("gradient_method"),
            "nominal": [],
            "nominal_source": "test",
        }

    monkeypatch.setattr(ls, "compute_local_sensitivity", fake_compute)

    sr.run({
        "model_path": "model.cellml",
        "obs_path": "obs.json",
        "params_path": str(tmp_path / "params.csv"),
        "output_dir": str(tmp_path / "out"),
        "model_type": "cellml",
        "settings": {"method": "local", "gradient_method": gradient_method},
    })
    return built, received


def test_the_runner_builds_the_fsa_engine_for_cas_auto_spelling(tmp_path, monkeypatch):
    """On cellml, CA's 'AUTO' resolves to FSA -- so the runner must build the
    param-id engine. It used to test the raw string against ("FSA", "CVODES"),
    while resolve_gradient_method mapped 'AUTO' to FSA only afterwards: 'AUTO'
    skipped engine construction and then failed with "needs a param-id engine".
    A test of the resolver alone passes with that bug live; only running the
    runner catches the two sites disagreeing.
    """
    built, received = _run_local_runner(monkeypatch, tmp_path, "AUTO")
    assert built, "AUTO on cellml means FSA, so the engine must be built"
    assert received["engine"] == "ENGINE"


def test_the_runner_builds_the_engine_for_fd_too(tmp_path, monkeypatch):
    """FD is computed by circulatory_autogen's accessor like the other two.

    It used to run off the SA manager through CUFLynx's own difference loop --
    a reimplementation of CA's ``fd_backend``. One code path means one engine,
    built for every gradient method rather than only the analytic ones.
    """
    built, received = _run_local_runner(monkeypatch, tmp_path, "FD")
    assert built == ["engine"]
    assert received["engine"] == "ENGINE"


def test_a_local_run_never_touches_the_sobol_managers_helper(monkeypatch, tmp_path):
    """One simulation helper per local SA, not two (#216).

    ``sobol_SA`` and ``ParamID`` each parse the same study and each own a
    helper, and building one compiles the model. circulatory_autogen made
    ``sobol_SA.sim_helper`` lazy precisely so a local run never pays for the half
    it does not use -- so reading the study from the SA manager, as this used to,
    silently reinstated the second compile.

    Asserted by making ``SA_manager`` explode on any attribute read: the property
    is lazy, so counting compiles is invisible, but *touching it at all* is the
    thing that must not happen.
    """
    import local_sensitivity as ls

    class _Detonate:
        def __getattr__(self, name):
            raise AssertionError(
                f"the local path read SA_manager.{name}; it must take the study "
                f"from the param-id engine, or the model compiles twice (#216)"
            )

    captured = {}

    def fake_ca(pid, param_names, nominal, mins, maxs, **kwargs):
        captured["pid"] = pid
        return {}, []

    monkeypatch.setattr(ls, "_ca_local_sensitivity", fake_ca)
    monkeypatch.setattr(ls, "resolve_gradient_method", lambda *a, **k: "FD")

    class _Sa:
        SA_manager = _Detonate()

    class _Pid:
        sim_helper = type("H", (), {"get_init_param_vals": lambda self, n: [[1.0]]})()
        param_id_info = {
            "param_names": [["a/x"]], "param_mins": [0.0], "param_maxs": [2.0],
        }

    engine = type("E", (), {"param_id": _Pid()})()

    payload = ls.compute_local_sensitivity(
        _Sa(), {"method": "local", "gradient_method": "FD", "nominal": "current"},
        model_type="cellml", engine=engine,
    )

    assert payload["param_names"] == ["a/x"]
    assert captured["pid"] is engine.param_id
